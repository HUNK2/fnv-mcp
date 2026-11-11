"""Who changed this (Plan 2, W2): fnv_blame. Three captures and one report.

  field   a hardware write watchpoint (the companion's `dbg` op) on one address for N seconds: each hit names the
          writing function, its return path and the thread; a script runner on the stack adds the statement the
          thread last ran (approximate, see below)
  ref     watch hooks with a record on the preset engine functions of B-debug section 3 (disable, enable, move,
          delete, stage, items, actor values, ownership), filtered to one reference (the hook's filter on `this`)
  report  the hits of this session grouped by culprit: script and line, or the engine function that called

Design: plan2/B-debug.md section 3; the record layout is the one hook records use today (fnv_deep.py: words 2 thread,
3 this, 5 caller, 12.. arguments, 22 frame). Preset names are resolved through the engine map; a preset the map does
not know is listed as skipped, never guessed. `form` (NotifyFormIDChanged) is not built.

The script context of a hit is approximate: the last statement the same thread ran, read from the EvaluateLine
record ring when fnv_script_log or fnv_script_trace keeps one. It is not the statement that made the write.
"""
import re
import time

from .fnv_conv import ConvError, verdict
from .fnv_debugger import Debugger
from .fnv_deep import HF_RECORD, SELECTORS
from .fnv_engine import EXE_RANGE
from .fnv_flow import hook_at
from .fnv_link import word
from .fnv_watch import ACCESS

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
MAX_HITS = 2000
FIELD_RE = re.compile(r'^(.*?)\s*\+\s*(0x[0-9A-Fa-f]+|\d+)$')
SCRIPT_LABELS = ('script_line_dbg', 'script_line')


def _hx(value):
    return '0x%08X' % value


def _int(value):
    """A hex text (with or without 0x) or an int, as the companion and the console write numbers."""
    if isinstance(value, int):
        return value
    return int(str(value).strip(), 16)


class BlameTools:
    """fnv_blame. Keeps the hits it has read (bounded) so `report` can group them; the rings are read from the game."""

    def __init__(self, live, debugger=None, sleep=time.sleep, clock=time.monotonic):
        self.live = live
        self.sleep = sleep
        self.dbg = debugger or Debugger(live, sleep=sleep, clock=clock)
        self.hits = []
        self.seen = {}
        self.armed = {}

    @property
    def link(self):
        return self.live.link

    def run(self, action, **args):
        if action == 'field':
            return self.field(args.get('address'), length=args.get('length') or 4, access=args.get('access') or 'write',
                              scope=args.get('scope') or 'main', seconds=args.get('seconds') or 5.0)
        if action == 'ref':
            return self.ref(args.get('ref'), args.get('what'), seconds=args.get('seconds') or 0.0)
        if action == 'report':
            return self.report()
        if action == 'form':
            raise ConvError('NOT_YET', 'form (the change flags of NotifyFormIDChanged) is not built. Next: use field on '
                            'the form field, or ref for references.')
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
        result = self.link.one('dbg', 'set', '0', word(address), word(int(length)), word(ACCESS[access]),
                               '1' if scope == 'all' else '0')
        if not result.get('ok'):
            raise ConvError('WATCH_REFUSED', 'The companion could not set the watchpoint (%s). Next: the debug registers '
                            'may be full (four in use).' % (result.get('error') or result), result)
        seq = result['seq']
        try:
            self.sleep(float(seconds))
            raw = self.link.one('dbg', 'read', word(seq)).get('hits') or []
        finally:
            self.link.one('dbg', 'clear', 'f')
        rows = [self._field_hit(h, address) for h in raw[:MAX_HITS]]
        self._keep(rows)
        return verdict('%d writes to %s in %s s.' % (len(rows), _hx(address), seconds),
                       'Each hit names the writer; a script runner on the stack adds the last statement its thread ran.'
                       if rows else 'No hits: the field was not written in that time. Make the game write it, then run '
                       'field again.', address=_hx(address), access=access, scope=scope, hits=rows[:200],
                       total=len(rows))

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
            row['script'] = self._script_context(_int(thread) if str(thread).lower().startswith('0x') else int(thread))
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
            if status in ('proven', 'class proven'):
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
            return 'script %s line %s' % (s.get('script') or '?', s.get('line') or '?')
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
            since = self.seen.get(label, 0)
            seq, recs, _ = self.live.deep.records(h, since, limit=RING_SCAN)
            self.seen[label] = seq
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
                    script = self.live.form_ids([r[12]])[0] if r[12] else None  # EvaluateLine arg0
                    return {'script': (script or {}).get('formId') or _hx(r[12]), 'line': r[18],  # arg6 is the line
                            'reference': _hx(r[14]) if r[14] else None, 'approximate': True}
        return None


def _thread_label(thread):
    return 'thread %s' % thread if thread is not None else None
