"""Who changed this (Plan 2, W2): fnv_blame. Three captures and one report.

  field   a hardware write watchpoint (the companion's `dbg` op) on one address for N seconds: each hit names the
          writing function, its return path and the thread; a script runner on the stack adds the statement the
          thread last ran (approximate, see below)
  ref     watch hooks with a record on the preset engine functions of B-debug section 3 (disable, enable, move,
          delete, stage, items, actor values, ownership), filtered to one reference (the hook's filter on `this`)
  report  the hits of this session grouped by culprit: script and line, or the engine function that called

Design: plan2/B-debug.md section 3; the record layout is the one hook records use today (fnv_deep.py: words 2 thread,
3 this, 5 caller, 12.. arguments, 22 frame). Preset names are resolved through the engine map; a preset the map does
not know is listed as skipped, never guessed. `form` hooks BGSSaveLoadGame::NotifyFormIDChanged 0x0084A690 (every
tracked change of a form passes it: form, change flags, force) filtered to one form, for N seconds; each flag is named
by the engine's own GetChangeFlagName 0x0083FEF0.

The script context of a hit: the companion stamps each main-thread watch hit with the newest hook record the main
thread wrote (wire: hit words 8 and 9). When that record is a statement of ScriptRunner::EvaluateLine (the ring
fnv_script_debug, fnv_script_log or fnv_script_trace keeps; field arms it when none is), it is the statement that
was running when the write happened: exact, read while watching because the ring wraps in about a second. Without
the stamp (older companion) or once the record is gone, the context is the last statement the thread ran
(approximate).
"""
import re
import time

from .fnv_conv import ConvError, verdict
from .fnv_debugger import Debugger
from .fnv_deep import HF_RECORD, SELECTORS
from .fnv_engine import EXE_RANGE
from .fnv_flow import hook_at
from .fnv_link import word
from .fnv_watch import ACCESS, claim_debug_register, hits_of_slot, release_debug_register

PRESETS = (
    ('disable', ('TESObjectREFR::Disable',)),
    ('enable', ('TESObjectREFR::Enable',)),
    ('move', ('MobileObject::SetPosition', 'TESObjectREFR::SetPosition')),
    ('delete', ('TESObjectREFR::Delete',)),
    ('stage', ('TESQuest::SetStage', 'TESQuest::SetCurrentStage')),
    ('item_added', ('TESObjectREFR::AddItem', 'Actor::AddItem')),
    ('item_removed', ('TESObjectREFR::RemoveItem', 'Actor::RemoveItem')),
    ('actor_value', ('Actor::SetActorValue', 'PlayerCharacter::SetActorValue', 'Creature::SetActorValue')),
    ('ownership', ('TESObjectREFR::SetOwner', 'TESObjectREFR::SetOwnership')),
)
PRESET_NAMES = tuple(name for name, _ in PRESETS)
PRESET_ARGS = 4
LABEL = 'blame.'
RING_SCAN = 200
POLL = 0.2              # field reads its hits this often: the statement ring wraps in about a second
MAX_HITS = 2000
FIELD_RE = re.compile(r'^(.*?)\s*\+\s*(0x[0-9A-Fa-f]+|\d+)$')
SCRIPT_LABELS = ('script_line_dbg', 'script_line')
NOTIFY_FORM_CHANGED, CHANGE_FLAG_NAME = 0x0084A690, 0x0083FEF0


def _hx(value):
    return '0x%08X' % value


def _int(value):
    """A hex text (with or without 0x) or an int, as the companion and the console write numbers."""
    if isinstance(value, int):
        return value
    return int(str(value).strip(), 16)


class BlameTools:
    """fnv_blame. Keeps the hits it has read (bounded) so `report` can group them; the rings are read from the game."""

    def __init__(self, live, debugger=None, sleep=time.sleep, clock=time.monotonic, line_of=None, statement_hook=None):
        self.live = live
        self.sleep = sleep
        self.clock = clock
        self.line_of = line_of                # (script form id, statement, script ptr) -> (source line, text)
        self.statement_hook = statement_hook  # the EvaluateLine hook (fnv_script_debug's), armed for exact context
        self.dbg = debugger or Debugger(live, sleep=sleep, clock=clock)
        self.hits = []
        self.seen = {}          # hook label -> (hook id, last ring sequence): a new hook starts its ring at 0
        self.armed = {}
        self._proc = None       # (pid, start) the hits belong to

    @property
    def link(self):
        return self.live.link

    def _follow(self):
        """A new game process drops the hits and ring positions of the old one."""
        try:
            found = self.live.game.process()
        except Exception:  # noqa: BLE001 - no process lookup: nothing to follow
            return
        if not found:
            return
        key = (found[0], found[1] if len(found) > 1 else None)
        if self._proc is not None and key != self._proc:
            self.hits, self.seen, self.armed = [], {}, {}
        self._proc = key

    def run(self, action, **args):
        self._follow()
        if action == 'field':
            return self.field(args.get('address'), length=args.get('length') or 4, access=args.get('access') or 'write',
                              scope=args.get('scope') or 'main', seconds=args.get('seconds') or 5.0)
        if action == 'ref':
            return self.ref(args.get('ref'), args.get('what'), seconds=args.get('seconds') or 0.0)
        if action == 'report':
            return self.report()
        if action == 'form':
            return self.form(args.get('ref'), seconds=args.get('seconds') or 5.0)
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid actions: field, ref, report, form.' % action,
                        {'actions': ['field', 'ref', 'report', 'form']})

    # ================================================================ field
    def field(self, path, length=4, access='write', scope='main', seconds=5.0):
        if access not in ACCESS:
            raise ConvError('BAD_ARGUMENTS', 'access must be write or readwrite.')
        if scope not in ('main', 'all'):
            raise ConvError('BAD_ARGUMENTS', 'scope must be main or all.')
        if not 1 <= int(length) <= 64 or not 0 < float(seconds) <= 600:
            raise ConvError('BAD_ARGUMENTS', 'length is 1 to 64 bytes; seconds is above 0 and at most 600.')
        address = self._address(path)
        self.live.ready()
        if not self.live.game.process():
            raise ConvError('NOT_RUNNING', 'Watching a field needs the game running.')
        installed = False
        if self.statement_hook is not None:
            try:
                existing = getattr(self.statement_hook, 'existing', None)
                installed = existing is not None and existing() is None     # field installs it: field removes it
                self.statement_hook.ensure()    # statement records, so a script's write names its line
            except Exception:  # noqa: BLE001 - without it the script context is approximate
                pass
        try:
            with self.dbg.lock:     # a hardware break must not take the register between the check and the set
                slot = claim_debug_register(self.link)
                if slot is None:
                    raise ConvError('WATCH_REFUSED', 'All four debug registers are in use (watches or hardware breaks). '
                                    'Next: clear a hardware break (fnv_debug break clear) or wait for the other watch.')
                try:
                    result = self.link.one('dbg', 'set', str(slot), word(address), word(int(length)),
                                           word(ACCESS[access]), '1' if scope == 'all' else '0')
                except BaseException:
                    release_debug_register(self.link, slot)
                    raise
                if not result.get('ok'):
                    release_debug_register(self.link, slot)
                    raise ConvError('WATCH_REFUSED', 'The companion could not set the watchpoint (%s).'
                                    % (result.get('error') or result), result)
        except BaseException:
            self._drop_statement_hook(installed)
            raise
        seq = result['seq']
        rows = []
        self._hooks_cache = None
        try:
            end = self.clock() + float(seconds)
            while True:
                self.sleep(min(POLL, max(0.0, end - self.clock())))
                raw = hits_of_slot(self.link.one('dbg', 'read', word(seq)).get('hits') or [], slot)
                for h in raw:
                    seq = max(seq, int(h[0]))
                    if len(rows) < MAX_HITS:
                        rows.append(self._field_hit(h, address))
                if self.clock() >= end:
                    break
        finally:
            release_debug_register(self.link, slot)        # only this watch's register; another watch keeps its own
            self._drop_statement_hook(installed)
        self._keep(rows)
        return verdict('%d writes to %s in %s s.' % (len(rows), _hx(address), seconds),
                       'Each hit names the writer; a script runner on the stack adds the last statement its thread ran.'
                       if rows else 'No hits: the field was not written in that time. Make the game write it, then run '
                       'field again.', address=_hx(address), access=access, scope=scope, hits=rows[:200],
                       total=len(rows))

    def _drop_statement_hook(self, installed):
        """Remove the statement hook field installed, unless a break or the log needs it now (it would record every
        statement of every script for the rest of the session)."""
        hook = self.statement_hook
        if not installed or hook is None or getattr(hook, 'holding', False) or getattr(hook, 'log_on', False):
            return
        try:
            hook.remove()
        except Exception:  # noqa: BLE001 - the hook stays; fnv_hooks can remove it
            pass

    def _address(self, path):
        text = str(path or '').strip()
        if not text:
            raise ConvError('BAD_ARGUMENTS', 'An address or ref + offset is needed, such as 0x011DEA0C or player + 0x10.')
        if re.fullmatch(r'0x[0-9A-Fa-f]+', text):
            return int(text, 16)
        m = FIELD_RE.match(text)
        if not m:
            raise ConvError('BAD_ARGUMENTS', 'Cannot read %r as an address. Next: pass 0x... or ref + 0x offset.' % text)
        return self._ptr(m.group(1)) + _int(m.group(2))

    def _ptr(self, ref):
        ptr = self.live.resolve(ref)['ptr']
        return _int(ptr)

    def _field_hit(self, h, address):
        eip, thread, value, ret, caller = _int(h[2]), h[3], _int(h[4]), _int(h[5]), _int(h[6])
        stack = [self.dbg._symbol(eip, return_address=True)]
        if ret:
            stack.append(self.dbg._symbol(ret, return_address=True))
        if caller:
            stack.append(self.dbg._symbol(caller, return_address=True))
        row = {'source': 'field', 'address': _hx(address), 'writer': stack[0].get('function'),
               'line': stack[0].get('source'), 'thread': _thread_label(thread), 'value': value, 'stack': stack}
        if any((s.get('function') or '').startswith('ScriptRunner') for s in stack):
            # h[8] is the hook id (0 is a valid id: the statement hook is often the first of a session); h[9] is the
            # record sequence, 0 when the thread wrote no record
            exact = self._statement_at(int(h[8]), int(h[9])) if len(h) >= 10 and h[9] else None
            row['script'] = exact or self._script_context(_int(thread) if str(thread).lower().startswith('0x')
                                                          else int(thread))
        return row

    def _statement_at(self, hook_id, record_seq):
        """The statement record the main thread wrote last before the hit, if it is an EvaluateLine record still in
        the ring: the statement that made the write."""
        hooks = self._hooks_cache if getattr(self, '_hooks_cache', None) is not None else self.live.deep.hooks()
        self._hooks_cache = hooks
        for h in hooks:
            if int(h['id']) != hook_id or h.get('label') not in SCRIPT_LABELS:
                continue
            r = self.live.deep.record(h, record_seq)
            if r is None:
                return None
            return self._script_row(r, approximate=False)
        return None

    def _script_row(self, r, approximate):
        script = self.live.form_ids([r[12]])[0] if r[12] else None  # EvaluateLine arg0
        form = (script or {}).get('formId')
        row = {'script': form or _hx(r[12]), 'statement': r[18], 'line': r[18],  # arg6: the statement number
               'reference': _hx(r[14]) if r[14] else None, 'approximate': approximate}
        if form and self.line_of is not None:
            try:
                line, text = self.line_of(int(form, 16), r[18], r[12])
            except Exception:  # noqa: BLE001 - no source: the statement number stays
                line, text = None, None
            if line is not None:
                row['line'], row['sourceLine'] = line, text
        return row

    # ================================================================ ref
    def ref(self, ref, what=None, seconds=0.0):
        if not ref:
            raise ConvError('BAD_ARGUMENTS', 'A reference is needed: player, a form id, or Plugin.esp:LOCALID.')
        names = list(what or PRESET_NAMES)
        unknown = [n for n in names if n not in PRESET_NAMES]
        if unknown:
            raise ConvError('BAD_ARGUMENTS', 'Unknown preset(s) %s. Valid presets: %s.' % (', '.join(unknown),
                                                                                       ', '.join(PRESET_NAMES)))
        self.live.ready()
        ptr = self._ptr(ref)
        engine = self.live.engine()
        armed, skipped = [], []
        for name, candidates in PRESETS:
            if name not in names:
                continue
            address, function = self._preset_target(engine, candidates)
            if address is None:
                skipped.append({'preset': name, 'reason': 'none of %s is in the engine map as a proven function'
                                % ', '.join(candidates)})
                continue
            label = LABEL + name
            hook = self._hook(address, label, ptr)
            self.armed[label] = {'preset': name, 'function': function, 'address': _hx(address), 'hook': hook['id']}
            armed.append({'preset': name, 'function': function, 'address': _hx(address), 'hook': hook['id']})
        if seconds:
            self.sleep(float(seconds))
            self._collect()
        return verdict('Watching %s on %s: %d hook(s) armed.' % (', '.join(a['preset'] for a in armed) or 'nothing',
                                                                  ref, len(armed)),
                       'Run report to see the hits; a preset not listed in the engine map was skipped.',
                       reference=_hx(ptr), armed=armed, skipped=skipped)

    def _preset_target(self, engine, candidates):
        for name in candidates:
            try:
                address = engine.resolve(name)
                status = (engine.summary(address, with_location=False) or {}).get('status')
            except Exception:      # noqa: BLE001 - a missing name is an answer here, not an error
                continue
            if status in ('proven', 'class proven', 'class_proven'):      # the engine map writes class_proven
                return address, name
        return None, None

    def _hook(self, address, label, ptr):
        for h in self.live.deep.hooks():
            if int(str(h['target']), 16) == address:
                if h.get('label') != label:
                    raise ConvError('ALREADY_HOOKED', '%s already carries the hook %s. Next: remove it with fnv_hooks.'
                                    % (_hx(address), h.get('label')), {'hook': h.get('label')})
                if h.get('filterValue') is not None and _int(h.get('filterValue')) != ptr:
                    result = self.link.one('hookfilter', word(h['id']), word(SELECTORS['ecx']), word(ptr))
                    if not result.get('ok'):
                        raise ConvError('HOOK_REFUSED', 'Cannot change the filter (%s).' % result.get('error'), result)
                return h
        return hook_at(self.live, address, label, PRESET_ARGS, flags=HF_RECORD, filter_sel=SELECTORS['ecx'],
                       filter_value=ptr)

    # ================================================================ form
    def form(self, form, seconds=5.0):
        """Which change flags the game sets on a form meanwhile, and who set them."""
        if not form:
            raise ConvError('BAD_ARGUMENTS', 'form needs ref (a form or reference: player, a form id, Plugin.esp:ID).')
        self.live.ready()
        ptr = self._ptr(form)
        form_type = (self.live.reads([(ptr + 4, 1)])[0] or b'\0')[0]
        label = LABEL + 'form'
        for h in self.live.deep.hooks():
            if h.get('label') == label:
                self.link.one('unhook', word(h['id']))
        hook = hook_at(self.live, NOTIFY_FORM_CHANGED, label, 3, flags=HF_RECORD, filter_sel=SELECTORS['arg0'],
                       filter_value=ptr)
        try:
            self.sleep(float(seconds))
            h = next((x for x in self.live.deep.hooks() if x.get('label') == label), None)
            _, recs, lost = self.live.deep.records(h, 0, limit=RING_SCAN) if h else (0, [], 0)
        finally:
            self.link.one('unhook', word(hook['id']))
        names, rows = {}, []
        for r in recs:
            flags = r[13]
            bits = [1 << i for i in range(32) if flags & (1 << i)]
            for b in bits:
                if b not in names:
                    names[b] = self._flag_name(b, form_type)
            caller = self.dbg._symbol(r[5], return_address=True) if r[5] else {}
            row = {'frame': r[22], 'flags': '0x%08X' % flags, 'names': [names[b] for b in bits],
                   'force': bool(r[14] & 0xFF), 'thread': _thread_label(r[2]), 'caller': caller.get('function'),
                   'line': caller.get('source')}
            rows.append(row)
            self._keep([{'source': 'form', 'what': ','.join(row['names']), 'function': caller.get('function'),
                         'reference': _hx(ptr), 'thread': row['thread'], 'caller': caller, 'frame': r[22]}])
        flags_seen = sorted({n for row in rows for n in row['names']})
        summary = {}
        for row in rows:
            key = (', '.join(row['names']), row['caller'])
            item = summary.setdefault(key, {'flags': key[0], 'caller': key[1], 'line': row['line'], 'count': 0,
                                            'firstFrame': row['frame']})
            item['count'] += 1
            item['lastFrame'] = row['frame']
        return verdict('%d changes to %s in %s s: %s' % (len(rows), form, seconds, ', '.join(flags_seen) or 'none'),
                       'Record hook on BGSSaveLoadGame::NotifyFormIDChanged 0x0084A690 filtered to the form (removed '
                       'after); flag names from GetChangeFlagName 0x0083FEF0; the caller is the engine function that '
                       'marked the change. Changes made by memory writes (the MCP\'s own) do not pass here.',
                       form=_hx(ptr), formType=form_type, summary=list(summary.values()), changes=rows[:50],
                       total=len(rows), lost=lost)

    def _flag_name(self, bit, form_type):
        try:
            out = self.live.engine_call('0x%08X' % CHANGE_FLAG_NAME, None, [{'type': 'int', 'value': bit},
                                                                              {'type': 'int', 'value': form_type},
                                                                              {'type': 'int', 'value': 1}],
                                        'ptr', 'cdecl', True)
            address = int(out.get('result') or '0', 16)
            raw = self.live.reads([(address, 64)])[0] if address else None
            text = raw.split(b'\0', 1)[0].decode('latin-1') if raw else ''
            return text or '0x%X' % bit
        except Exception:  # noqa: BLE001 - the bit is still an answer
            return '0x%X' % bit

    # ================================================================ report
    def report(self):
        self._collect()
        groups = {}
        for hit in self.hits:
            key = self._culprit(hit)
            g = groups.setdefault(key, {'culprit': key, 'hits': 0, 'what': set(), 'threads': set(), 'examples': []})
            g['hits'] += 1
            g['what'].add(hit.get('what') or 'field')
            g['threads'].add(hit.get('thread'))
            if len(g['examples']) < 3:
                g['examples'].append(hit)
        rows = []
        for g in sorted(groups.values(), key=lambda x: -x['hits']):
            rows.append({'culprit': g['culprit'], 'hits': g['hits'], 'what': sorted(g['what']),
                         'threads': sorted(g['threads']), 'examples': g['examples']})
        return verdict('%d hits in %d groups.' % (len(self.hits), len(rows)),
                       'Groups are the culprit of each write: the script and line when one was running, else the '
                       'engine function that called. Hits are kept until the game exits.' if rows else
                       'No hits yet. Arm fnv_blame field or ref, make the game act, then report.',
                       groups=rows, total=len(self.hits))

    def _culprit(self, hit):
        if hit.get('script'):
            s = hit['script']
            text = ': %s' % s['sourceLine'] if s.get('sourceLine') else ''
            return 'script %s line %s%s%s' % (s.get('script') or '?', s.get('line') or '?', text,
                                              ' (approximate)' if s.get('approximate') else '')
        if hit.get('source') == 'field':
            return 'writer %s' % (hit.get('writer') or 'unknown')
        caller = hit.get('caller') or {}
        if caller.get('function'):
            return 'engine %s' % caller['function']
        return 'plugin or injected code at %s' % (caller.get('address') or '?')

    def _collect(self):
        """New records of every blame hook since the last read; they join self.hits."""
        new = []
        for h in self.live.deep.hooks():
            label = h.get('label') or ''
            if not label.startswith(LABEL):
                continue
            meta = self.armed.get(label, {})
            last = self.seen.get(label)
            since = last[1] if last and last[0] == h.get('id') else 0       # another hook: its ring starts at 0
            seq, recs, _ = self.live.deep.records(h, since, limit=RING_SCAN)
            self.seen[label] = (h.get('id'), seq)
            for r in recs:
                caller = self.dbg._symbol(r[5], return_address=True) if r[5] else {}
                hit = {'source': 'ref', 'what': label[len(LABEL):], 'function': meta.get('function'),
                       'reference': _hx(r[3]), 'thread': _thread_label(r[2]), 'caller': caller,
                       'frame': r[22], 'args': [_hx(r[12 + i]) for i in range(PRESET_ARGS)]}
                if caller.get('source'):
                    hit['line'] = caller['source']
                new.append(hit)
        self._keep(new)
        return new

    def _keep(self, rows):
        self.hits.extend(rows)
        del self.hits[:-MAX_HITS]

    def _script_context(self, thread):
        """The last statement a thread ran, from the EvaluateLine ring if one is kept (approximate)."""
        for h in self.live.deep.hooks():
            if h.get('label') not in SCRIPT_LABELS:
                continue
            seq = self.live.u32s([int(h['seqAddress'], 16)])[0] or 0
            _, recs, _ = self.live.deep.records(h, max(0, seq - RING_SCAN), limit=RING_SCAN)
            for r in reversed(recs):
                if r[2] == thread:
                    return self._script_row(r, approximate=True)
        return None


def _thread_label(thread):
    return 'thread %s' % thread if thread is not None else None
