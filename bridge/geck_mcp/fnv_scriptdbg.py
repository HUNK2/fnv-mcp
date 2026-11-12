"""Script debugger and script log (Plan 2, W2): fnv_script_debug and fnv_script_log.

Both sit on ScriptRunner::EvaluateLine 0x005E1550, the per-statement call of the script runner (C-scripts.md 1). One
companion hook carries the statement record ring and, when a break is armed, a hold. Its arguments (docs
FNV-COMPANION-V2.md 8.1): arg0 script, arg1 opcode, arg2 reference, arg4 bytecode offset, arg6 line, arg7 skip flag;
ecx is the runner.

A break is a hook hold with one predicate: the script (its form id, read through arg0), the line (`arg6:u32`) and
the opcode (`arg1:u16`), each optional, with an optional `when`; several breaks are joined with `or`. The predicate
text reads an argument's own value with `argN:type` (FNV-COMPANION-V2.md 2, base 4). A `when` that names the script's
variables (`HelloBugLevel > 50 and bDone == 0`) is checked by the bridge at each stop: the variables are doubles,
which the predicate language cannot read, so the hold stops on the script, line and opcode and a stop whose
condition is false is passed on (resume run), like the refused stops inside save/load.

Refused stops: a statement reached inside save/load (the locals loader runs the scripts' init pass) is passed on
with resume next and counted, not shown.
"""
import ast
import operator
import re
import struct
import threading
import time

from .fnv_conv import ConvError, verdict
from .fnv_debugger import Debugger
from .fnv_deep import HF_RECORD, SELECTORS
from .fnv_flow import engine_address, hook_at
from .fnv_link import word
from .fnv_pred import ProcState, compile_predicate

EVAL_NAME, EVAL_ADDR, EVAL_ARGS = 'ScriptRunner::EvaluateLine', 0x005E1550, 8
EVAL_LABEL = 'script_line_dbg'
PROCESS_SCRIPTS = 0x0118C685        # byte gating TES::RunScripts (C-scripts.md 1); 1 = scripts run
SET_ACTION_FLAG = ('Script_SetActionFlag', 0x005AC750, 3)
LOCALS_ACTION_FLAG = ('ScriptLocals::SetActionFlag', 0x005A8E20, 2)
FLAG_LABELS = ('script_flag', 'locals_flag')
FORM_ID = 0x0C                      # TESForm::iFormID
RUNNER_LOCALS, RUNNER_SCRIPT, RUNNER_IF_DEPTH = 0x08, 0x14, 0x20
SCRIPT_VARS = 0x4C                  # Script::m_kVariableList (BSSimpleList of ScriptVariableInfo*)
INFO_TYPE = 0x10                    # ScriptVariableInfo type: 0 float, 1 integer (short/long); a local is 16 bytes
VAR_VALUE = 0x08                    # ScriptLocalVariable::fValue (double)
LOCALS_VARS, LOCALS_OWNER = 0x0C, 0x00
NAME_PTR, NAME_LEN = 0x18, 0x1C     # ScriptVariableInfo::m_kName (BSStringT: pointer, u16 length)
REF_EXTRA_LIST = 0x44               # TESObjectREFR extra data list (the ext argument of Script_SetActionFlag)
SAVE_LOAD = re.compile(r'LoadGame|SaveGame|SaveLoad|ScriptLocals::Load|ScriptLocals::Save|FinishLoading')
STEP_LIMIT = 4096
STOP_WAIT = 10.0
SETTLE_WAIT = 30.0       # most a continue spends passing on stops whose condition is false
RING = 200
LIST_LIMIT = 256
FLAGS = {0x1: 'OnAdd', 0x2: 'OnEquip', 0x4: 'OnDrop', 0x8: 'OnUnequip', 0x10: 'OnDeath', 0x20: 'OnMurder',
         0x40: 'OnCombatEnd', 0x80: 'OnHit', 0x100: 'OnHitWith', 0x200: 'OnPackageStart', 0x400: 'OnPackageDone',
         0x800: 'OnPackageChange', 0x1000: 'OnLoad', 0x2000: 'OnMagicEffectHit', 0x4000: 'OnSell',
         0x8000: 'OnStartCombat', 0x10000000: 'OnTrigger', 0x20000000: 'OnTriggerEnter',
         0x40000000: 'OnTriggerLeave'}
FLAG_MASK = {name: mask for mask, name in FLAGS.items()}
MUTATING = {'enable', 'disable', 'moveto', 'setpos', 'setstage', 'additem', 'removeitem', 'setactorvalue',
            'modactorvalue', 'setownership', 'kill', 'startcombat', 'delete', 'setessential'}

PRED_SLOT = 56   # predicate slot of the script breaks (fnv_debug uses 32-55, waits 62-63)


def _int(value):
    if isinstance(value, int):
        return value
    return int(str(value).strip(), 16)


def _hx(value):
    return '0x%08X' % value


def _u32(raw, offset=0):
    return struct.unpack_from('<I', raw, offset)[0]


class EvalHook:
    """The EvaluateLine hook both tools share: installed once, its hold and filter set by the debugger."""

    def __init__(self, live):
        self.live = live
        self.hook_id = None
        self.holding = False            # a hold predicate is set (breaks armed); the companion has no release
        self.log_on = False             # fnv_script_log commands mode=start asked for the ring: keep the hook
        self.lock = threading.RLock()   # replaced by the Debugger's lock, so slot 56 and the hook are set one at a time

    @property
    def link(self):
        return self.live.link

    def address(self):
        return engine_address(self.live.engine(), EVAL_NAME, EVAL_ADDR)

    def existing(self):
        address = self.address()
        for h in self.live.deep.hooks():
            if int(str(h['target']), 16) == address:
                if h.get('label') != EVAL_LABEL:
                    raise ConvError('HOOKED_ELSEWHERE', 'EvaluateLine already carries the hook %s (fnv_script_trace uses '
                                    'one). Next: stop that tool first (fnv_script_trace action stop).' % h.get('label'),
                                    {'hook': h.get('label')})
                self.hook_id = h['id']
                return h
        return None

    def ensure(self):
        h = self.existing()
        if h is None:
            h = hook_at(self.live, self.address(), EVAL_LABEL, EVAL_ARGS, flags=HF_RECORD)
        self.hook_id = h['id']
        return h

    def remove(self):
        h = self.existing()
        if h is not None:
            self.link.one('unhook', word(h['id']))
        self.hook_id = None
        self.holding = False

    def reset(self):
        """Drop the hold by re-hooking: the companion's hookact has no release, a fresh hook holds nothing."""
        self.remove()
        self.ensure()

    def hold(self, pred_hex):
        # The hold takes a predicate slot (wire 2): store the program in this tool's slot first.
        with self.lock:
            stored = self.link.one('pred', 'set', word(PRED_SLOT), pred_hex)
            if not stored.get('ok'):
                raise ConvError('COMPANION_REFUSED', 'The companion refused the break condition (%s).'
                                % stored.get('error'), stored)
            result = self.link.one('hookact', word(self.hook_id), 'hold', word(PRED_SLOT))
            if not result.get('ok'):
                raise ConvError('COMPANION_REFUSED', 'The companion refused the statement hold (%s).'
                                % result.get('error'), result)
            self.holding = True

    def filter(self, selector, value):
        result = self.link.one('hookfilter', word(self.hook_id), word(selector), word(value))
        if not result.get('ok'):
            raise ConvError('COMPANION_REFUSED', 'The companion refused the statement filter (%s).'
                            % result.get('error'), result)


DECLARATIONS = ('short', 'int', 'long', 'float', 'ref', 'reference', 'string_var', 'array_var')


def statement_lines(source):
    """Source line (1-based) of each compiled statement, in order: the game's runner counts statements, not source
    lines (seen live 2026-11-11: its "line 7" was the 7th statement, an endif on source line 10, as in its runtime
    error messages). Blank lines, comments and variable declarations compile to nothing; every other line is one
    statement (scn, begin and end included)."""
    out = []
    for n, line in enumerate(source.replace('\r', '').split('\n'), 1):
        code = line.split(';', 1)[0].strip()
        if not code or code.split()[0].lower() in DECLARATIONS:
            continue
        out.append(n)
    return out


class ScriptDebugger:
    """fnv_script_debug: breaks on statements, the stopped script's state, variables, eval and stepping. Lines are
    source lines when the script's text is known (kept by the game, or the SCTX of its plugin record), else the
    game's statement numbers."""

    def __init__(self, live, eval_hook=None, dbg=None, sleep=time.sleep, clock=time.monotonic, data=None):
        self.live = live
        self.data = data
        self._sources = {}
        self._types = {}                # variable id -> float | integer, filled by _names
        self.eval = eval_hook or EvalHook(live)
        self.dbg = dbg or Debugger(live, sleep=sleep, clock=clock)
        self.lock = getattr(self.dbg, 'lock', None) or threading.RLock()   # one lock for every tool that
        try:                                                                 # allocates ids and slots
            self.eval.lock = self.lock
        except AttributeError:
            pass
        self.state = ProcState(live)
        self.pid = self.start = None
        self.breaks = {}
        self.next_number = 1
        self.saved_scripts = None
        self.skipped = 0
        self.refused = 0                # stops the last settle passed on

    @property
    def link(self):
        return self.live.link

    def run(self, action, **args):
        table = {
            'break': lambda: self.break_(args.get('op'), args.get('script'), args.get('line'), args.get('opcode'),
                                         args.get('when'), args.get('break_id')),
            'status': self.status,
            'vars': self.vars_,
            'set': lambda: self.set_(args.get('name'), args.get('value')),
            'eval': lambda: self.eval_(args.get('expression')),
            'step': lambda: self.step(args.get('mode') or 'line'),
            'continue': self.continue_,
            'freeze': self.freeze,
            'thaw': self.thaw,
        }
        self.live.ready()
        self._sync()
        return _dispatch(action, table)

    # ================================================================ state kept across restarts
    def _sync(self):
        """Follow the game process. A new game (pid or start time changed) starts clean: breaks, the hook id and the
        script-freeze flag of the old one are dropped. The same game after a server restart gets its breaks and its
        script-freeze flag back from the state file."""
        try:
            found = self.live.game.process()
        except Exception:  # noqa: BLE001 - no process lookup: nothing to follow
            return
        if not found:
            return
        pid, start = found[0], (found[1] if len(found) > 1 else None)
        if pid == self.pid and start == self.start:
            return
        self.pid, self.start = pid, start
        self.eval.hook_id = None
        self.eval.holding = False
        saved = self.state.load('sdbg_breaks', pid, start) or {}
        self.breaks = {int(b['number']): b for b in saved.get('breaks', []) if isinstance(b, dict) and 'number' in b}
        self.next_number = max([int(saved.get('next') or 1)] + [n + 1 for n in self.breaks])
        self.saved_scripts = self.state.load('sdbg_freeze', pid, start)
        self.eval.holding = bool(self.breaks)

    def _persist(self):
        if self.pid is None:
            return
        records = [dict(b) for _, b in sorted(self.breaks.items())]
        self.state.save('sdbg_breaks', self.pid, self.start,
                        {'next': self.next_number, 'breaks': records} if records else None)
        self.state.save('sdbg_freeze', self.pid, self.start, self.saved_scripts)

    # ================================================================ breaks
    def break_(self, op, script, line, opcode, when, break_id):
        if op is None:
            op = 'set' if any(x is not None for x in (script, line, opcode)) or when else 'list'
        if op == 'list':
            return verdict('%d script breaks' % len(self.breaks), 'Breaks stay armed until cleared or the game exits.',
                           rows=[_public(b) for _, b in sorted(self.breaks.items())], total=len(self.breaks))
        if op == 'clear':
            return self._clear(break_id)
        if op != 'set':
            raise ConvError('BAD_ARGUMENTS', 'op must be set, list or clear.')
        if line is None and opcode is None and script is None and not when:
            raise ConvError('BAD_ARGUMENTS', 'A break needs a script, a line, an opcode or a when. Next: pass script and '
                            'line, for example script="QuestScript" line=12.')
        filt = ('line', int(line)) if line is not None else ('opcode', int(opcode)) if opcode is not None else None
        fid, script_name = self._script(script) if script else (None, None)
        statement, note = (int(line) if line is not None else None), None
        if line is not None and fid is not None:
            statement, note = self._statement_of(fid, int(line))
        var_when = None
        if when and var_names(when):
            if fid is None:
                raise ConvError('BAD_ARGUMENTS', 'A when that names script variables (%s) needs the script. Next: pass '
                                'script too.' % ', '.join(sorted(var_names(when))))
            known = {n.lower() for n in self._names(self.live.deep.script_form('%08X' % fid)[0]).values() if n}
            unknown = sorted(n for n in var_names(when) if n.lower() not in known)
            if unknown:
                raise ConvError('NO_SUCH_VARIABLE', '%s: not a variable of the script (it has %s).' % (
                    ', '.join(unknown), ', '.join(sorted(known)) or 'none'))
            compile_var_when(when)
            var_when, when = when, None
        with self.lock:
            record = {'number': self.next_number, 'script': fid, 'scriptName': script_name, 'line': line,
                      'opcode': opcode, 'when': when or None, 'varWhen': var_when, 'filter': filt,
                      'statement': statement, 'lineNote': note}
            self.breaks[record['number']] = record
            try:
                self._arm()
            except ConvError:
                del self.breaks[record['number']]
                self._arm()
                raise
            self.next_number += 1
            self._persist()
        return verdict('Break %d set on %s.' % (record['number'], _where(record)),
                       record['lineNote'] or 'The game stops when that statement runs; status shows the line, continue '
                       'runs to the next one.', **{'break': _public(record)})

    def _clear(self, break_id):
        with self.lock:
            targets = sorted(self.breaks) if break_id is None else [int(break_id)]
            cleared = []
            for n in targets:
                if n not in self.breaks:
                    raise ConvError('NO_BREAK', 'No script break %s. Next: call fnv_script_debug break list.' % n)
                del self.breaks[n]
                cleared.append(n)
            self._arm()
            self._persist()
        return verdict('Cleared %d script break(s).' % len(cleared), None, cleared=cleared)

    def _arm(self):
        """Hold and filter of the shared hook follow the breaks. With no break left the hook goes (it would record every
        statement of every script for the rest of the session), unless the statement log asked for its ring."""
        with self.lock:
            self._arm_locked()

    def _arm_locked(self):
        if not self.breaks:
            if self.eval.log_on:
                if self.eval.holding:
                    self.eval.reset()
            else:
                self.eval.remove()
            return
        self.eval.ensure()
        parts = []
        for b in self.breaks.values():
            conds = []
            if b['script'] is not None:
                conds.append('arg0.[%d]:u32 == %d' % (FORM_ID, b['script']))
            if b.get('line') is not None:
                conds.append('arg6:u32 == %d' % int(b['statement'] if b.get('statement') is not None else b['line']))
            if b.get('opcode') is not None:
                conds.append('arg1:u16 == %d' % int(b['opcode']))
            if b['when']:
                conds.append('(%s)' % b['when'])
            parts.append('(%s)' % (' and '.join(conds) or '1'))
        pred = compile_predicate(' or '.join(parts))['hex']
        self.eval.filter(SELECTORS['none'], 0)   # every condition is in the predicate now
        self.eval.hold(pred)

    def _source(self, form_id, ptr=None):
        """The script's source: the text the game kept, else its plugin record's SCTX. None when neither is known."""
        if form_id in self._sources:
            return self._sources[form_id]
        text = None
        try:
            ptr = ptr or self.live.deep.script_form('%08X' % form_id)[0]
            text = self.live.deep.script_source(ptr) if ptr else None
        except Exception:  # noqa: BLE001 - the game keeps the text only sometimes
            text = None
        if not text and self.data is not None:
            try:
                text = self.data.script_text('%08X' % form_id)
            except Exception:  # noqa: BLE001 - no record index, or not a plugin script
                text = None
        self._sources[form_id] = text or None
        return self._sources[form_id]

    def _lead(self, ptr):
        """1 when the script's code starts with two scriptname statements: a compile in the running game (a hot swap)
        keeps the one xNVSE writes in front, so each statement number is one higher than in the source."""
        if not ptr:
            return 0
        try:
            from .fnv_data import SCRIPT_DATA
            data = self.live.u32s([ptr + SCRIPT_DATA])[0]
            head = self.live.reads([(data, 8)])[0] if data else None
        except Exception:  # noqa: BLE001 - unreadable: assume the plugin's own layout
            return 0
        return 1 if head == bytes.fromhex('1d0000001d000000') else 0

    def _statement_of(self, form_id, line, ptr=None):
        """Source line -> the game's statement number, with a note when the line itself has no statement."""
        source = self._source(form_id, ptr)
        if not source:
            return line, ('The script\'s source is not known: line %d is taken as the game\'s statement number.' % line)
        lines = statement_lines(source)
        later = [n for n in lines if n >= line]
        if not later:
            raise ConvError('NO_STATEMENT', 'Line %d is after the last statement (line %d).' % (line, lines[-1] if lines else 0))
        statement = lines.index(later[0]) + 1 + self._lead(ptr or self.live.deep.script_form('%08X' % form_id)[0])
        if later[0] != line:
            return statement, ('Line %d has no statement (blank, comment or declaration): the break is on line %d, the '
                               'next statement.' % (line, later[0]))
        return statement, None

    def _line_of(self, form_id, statement, ptr=None):
        """The game's statement number -> (source line, its text), or (None, None)."""
        source = self._source(form_id, ptr) if form_id is not None else None
        if not source:
            return None, None
        lines = statement_lines(source)
        statement -= self._lead(ptr)
        if not 0 < statement <= len(lines):
            return None, None
        n = lines[statement - 1]
        return n, source.replace('\r', '').split('\n')[n - 1].strip()

    def _script(self, spec):
        _ptr, info, _ = self.live.deep.script_form(spec)
        form = (info or {}).get('formId')
        if not form:
            raise ConvError('NOT_FOUND', 'No script form for %r.' % spec)
        return int(form, 16), spec

    # ================================================================ stopped state
    def _ours(self, stop):
        if stop.get('kind') != 'hook':
            return False
        if self.eval.hook_id is None:
            # After a server restart the hook is still in the game: find it by its label before judging the stop.
            try:
                self.eval.existing()
            except ConvError:
                return False
        return self.eval.hook_id is not None and int(stop.get('id')) == self.eval.hook_id

    def _stopped(self):
        """The newest stop on the statement hook, or None."""
        mine = [s for s in self.dbg._stops() if self._ours(s)]
        return max(mine, key=lambda s: s['seq']) if mine else None

    def _current(self):
        stop = self._stopped()
        if stop is None:
            raise ConvError('NOT_STOPPED', 'No script is stopped by the debugger. Next: set a break with fnv_script_debug '
                            'break, then wait for it, or call status.')
        return stop

    def _state(self, stop):
        top = self.dbg._top(stop)
        args = top['args']
        return {'script': args[0], 'opcode': args[1], 'reference': args[2], 'offset': args[4], 'line': args[6],
                'skip': args[7], 'runner': top['regs']['ecx'], 'seq': stop['seq']}

    def _in_save_load(self, stop):
        for frame in self.dbg.frames(stop, 16):
            name = self.dbg._symbol(frame['address'], return_address=frame['how'] == 'return').get('function') or ''
            if SAVE_LOAD.search(name):
                return True
        return False

    def _settle(self, stop, wait=False):
        """A stop inside save/load, or one whose variable condition is false, is passed on with resume run and counted
        (the refused stop)."""
        self.refused = 0
        deadline = self.dbg.clock() + SETTLE_WAIT
        while stop is not None and (self._in_save_load(stop) or not self._wanted(stop)):
            self.skipped += 1
            self.refused += 1
            # run, not next: next parks at the very next statement and ignores the hold's condition
            self.dbg._resume(stop, 'run')
            if self.refused >= STEP_LIMIT or self.dbg.clock() >= deadline:
                return None         # the script keeps producing stops that are not wanted: give up, the game runs on
            stop = self._await_ours(stop['seq'], wait=wait)
        return stop

    def _wanted(self, stop):
        """False when every break that matches this stop has a variable condition and none holds now."""
        if not any(b.get('varWhen') for b in self.breaks.values()):
            return True
        st = self._state(stop)
        info = self.live.form_ids([st['script']])[0] or {}
        form = int(info['formId'], 16) if info.get('formId') else None
        values = None
        for b in self.breaks.values():
            if b['script'] is not None and b['script'] != form:
                continue
            if b.get('statement') is not None and b.get('line') is not None and b['statement'] != st['line']:
                continue
            if b.get('opcode') is not None and int(b['opcode']) != (st['opcode'] & 0xFFFF):
                continue
            if not b.get('varWhen'):
                return True
            if values is None:
                names = self._names(st['script'])
                values = {(names.get(rec['id']) or '').lower(): rec['value']
                          for _, rec in self._var_items(st['runner'])}
            if eval_var_when(compile_var_when(b['varWhen']), values):
                return True
        return False

    def _await_ours(self, after, wait=True):
        deadline = self.dbg.clock() + STOP_WAIT
        while True:
            fresh = [s for s in self.dbg._stops() if s['seq'] > after and self._ours(s)]
            if fresh:
                return max(fresh, key=lambda s: s['seq'])
            if not wait or self.dbg.clock() >= deadline:
                return None
            self.dbg.sleep(0.05)

    # ================================================================ status and variables
    def status(self):
        if self.saved_scripts is not None:
            return verdict('Frozen by the script debugger (process scripts off).', 'Call thaw to run scripts again.',
                           state='frozen')
        stop = self._stopped()
        if stop is None:
            others = [s for s in self.dbg._stops()]
            state = 'stopped elsewhere' if others else 'running'
            return verdict('Not stopped on a statement (%s).' % state, '%d script breaks armed.' % len(self.breaks),
                           state=state, breaks=[_public(b) for _, b in sorted(self.breaks.items())])
        if self._in_save_load(stop) or not self._wanted(stop):
            stop = self._settle(stop)
            if stop is None:
                return verdict('Passed a stop that is not wanted (inside save/load, or its variable condition is false).',
                               'Refused stops are counted; call status again.', state='running', skipped=self.skipped)
        return self._view(stop)

    def _view(self, stop):
        st = self._state(stop)
        info = self.live.form_ids([st['script']])[0] or {}
        form = int(info['formId'], 16) if info.get('formId') else None
        line, text = self._line_of(form, st['line'], st['script'])
        depth = self.live.u32s([st['runner'] + RUNNER_IF_DEPTH])[0] if st['runner'] else None
        view = {'seq': stop['seq'], 'script': info.get('formId'), 'scriptPointer': _hx(st['script']),
                'line': line if line is not None else st['line'], 'statement': st['line'],
                'opcode': self._opcode_at(st),
                'reference': _hx(st['reference']) if st['reference'] else None,
                'ifDepth': depth, 'sourceLine': text, 'skipped': st['skip'], 'skippedRefused': self.skipped}
        return verdict('Stopped in script %s at line %s.' % (info.get('formId') or _hx(st['script']), view['line']),
                       'The statement is about to run. vars lists the script variables; step runs one statement.',
                       stop=view)

    def _opcode_at(self, st):
        """The statement's opcode from the compiled script at the stop's offset (arg1 is the runner's output mode and
        differs inside a block the runner skips)."""
        try:
            from .fnv_data import SCRIPT_DATA
            data = self.live.u32s([st['script'] + SCRIPT_DATA])[0] if st['script'] else 0
            # the offset is past the statement's header (u16 opcode, u16 length)
            at = data + st['offset'] - 4 if data and st['offset'] and st['offset'] >= 4 else 0
            raw = self.live.reads([(at, 2)])[0] if at else None
            if raw:
                return struct.unpack('<H', raw)[0]
        except Exception:  # noqa: BLE001 - fall back to the hook argument
            pass
        return st['opcode'] & 0xFFFF

    def vars_(self):
        stop = self._current()
        st = self._state(stop)
        names = self._names(st['script'])
        out = []
        for item, record in self._var_items(st['runner']):
            uid = record['id']
            out.append({'name': names.get(uid), 'id': uid, 'value': record['value'],
                        'type': self._types.get(uid)})
        return verdict('%d script variables.' % len(out), 'Names come from the script; a None name is a variable the '
                       'script does not name.', variables=out, total=len(out))

    def _var_items(self, runner):
        """(address, {id, value, string}) for each ScriptLocalVariable of the runner's locals (BSSimpleList)."""
        locals_ptr = self.live.u32s([runner + RUNNER_LOCALS])[0] if runner else 0
        if not locals_ptr:
            return
        node = self.live.u32s([locals_ptr + LOCALS_VARS])[0]
        for _ in range(LIST_LIMIT):
            if not node:
                return
            raw_node = self.live.reads([(node, 8)])[0]
            if raw_node is None:
                return
            item, nxt = _u32(raw_node, 0), _u32(raw_node, 4)
            raw = self.live.reads([(item, 0x10)])[0] if item else None   # id, pad, double
            if raw is not None:
                value = struct.unpack_from('<d', raw, VAR_VALUE)[0]
                yield item, {'id': _u32(raw, 0), 'value': value}
            node = nxt

    def _names(self, script):
        names = {}
        node = script + SCRIPT_VARS if script else 0     # the list's first node is inside the Script (item, next)
        for _ in range(LIST_LIMIT):
            if not node:
                break
            raw_node = self.live.reads([(node, 8)])[0]
            if raw_node is None:
                break
            info = _u32(raw_node, 0)
            node = _u32(raw_node, 4)
            raw = self.live.reads([(info, 0x20)])[0] if info else None
            if raw is None:
                continue
            ptr, length = _u32(raw, NAME_PTR), struct.unpack_from('<H', raw, NAME_LEN)[0]
            text = self.live.reads([(ptr, length)])[0] if ptr and length else None
            names[_u32(raw, 0)] = text.decode('latin-1') if text else None
            self._types[_u32(raw, 0)] = {0: 'float', 1: 'integer'}.get(raw[INFO_TYPE], 'type %d' % raw[INFO_TYPE])
        return names

    def set_(self, name, value):
        if not name:
            raise ConvError('BAD_ARGUMENTS', 'name is needed: a script variable name, as vars lists it.')
        stop = self._current()
        st = self._state(stop)
        names = self._names(st['script'])
        target = [item for item, rec in self._var_items(st['runner'])
                  if names.get(rec['id']) == name]
        if not target:
            raise ConvError('NO_SUCH_VARIABLE', 'No script variable %r. Next: call fnv_script_debug vars.' % name)
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ConvError('BAD_VALUE', 'value must be a number.')
        result = self.link.one('wr', word(target[0] + VAR_VALUE), struct.pack('<d', number).hex())
        if not result.get('ok'):
            raise ConvError('MEMORY_UNWRITABLE', 'Cannot write the variable (%s).' % result.get('error'), result)
        return verdict('Set %s to %s.' % (name, number), 'It takes effect at the next statement that reads it. The '
                       'engine\'s own set also marks the reference changed; this writes the value only.', name=name,
                       value=number)

    def eval_(self, expression):
        if not expression:
            raise ConvError('BAD_EXPRESSION', 'An expression is needed, such as GetStage QuestName.')
        stop = self._current()
        st = self._state(stop)
        reply = self.link.eval(expression, st['reference'] or 0)
        if not reply.get('ok'):
            raise ConvError('EVAL_REFUSED', 'The companion refused the snippet (%s).' % reply.get('error'), reply)
        return verdict('Ran %s on the stopped reference.' % expression, 'A snippet gets its own runner, so the stopped '
                       'script keeps its locals. Do not run the same script on the same reference.',
                       expression=expression, result=reply.get('result'))

    # ================================================================ running
    def step(self, mode):
        """line / over: the next statement of the same run (a function the line calls runs in its own runner, so it
        is stepped over). out: the next statement the game runs outside this run: the caller after a function, or
        else the next script that runs."""
        if mode not in ('line', 'over', 'out'):
            raise ConvError('BAD_ARGUMENTS', 'step is line, over or out.')
        stop = self._current()
        here = self._state(stop)
        if not self._ours(stop):
            raise ConvError('BAD_MODE', 'Only a script stop can step.')
        if mode == 'out':
            return self._step_out(stop, here)
        script_pred = compile_predicate('arg0.[%d]:u32 == %d' % (FORM_ID, self._form_of(here['script'])))['hex']
        with self.lock:
            self.eval.filter(SELECTORS['none'], 0)
            self.eval.hold(script_pred)
        try:
            cur = stop
            for _ in range(STEP_LIMIT):
                self.dbg._resume(cur, 'run')     # the hold now passes every statement of this script
                new = self._await_ours(cur['seq'])
                if new is None:
                    return verdict('No next statement within %d s: the script may have ended.' % STOP_WAIT,
                                   'The game runs on; status shows where it is.', state='running')
                st = self._state(new)
                if st['runner'] == here['runner'] and st['line'] != here['line']:
                    view = self._view(new)['stop']
                    return verdict('Stepped to line %s.' % view['line'], 'The next statement of the same run.',
                                   stop=view)
                cur = new
        finally:
            self._arm()
        raise ConvError('STEP_LIMIT', 'The line did not change within %d statements. Next: continue, or set a break on the '
                        'line.' % STEP_LIMIT)

    def _step_out(self, stop, here):
        with self.lock:
            self.eval.filter(SELECTORS['none'], 0)
            self.eval.hold(compile_predicate('1')['hex'])
        try:
            cur, last = stop, here['offset']
            for _ in range(STEP_LIMIT):
                self.dbg._resume(cur, 'run')
                new = self._await_ours(cur['seq'])
                if new is None:
                    return verdict('No statement ran within %d s after this run.' % STOP_WAIT,
                                   'The game runs on; status shows where it is.', state='running')
                st = self._state(new)
                # The runner is a stack object: scripts the game runs one after another reuse its address, so a run
                # also ends when another script runs or this one starts again (its offset goes back; a loop's jump
                # back looks the same).
                if st['runner'] != here['runner'] or st['script'] != here['script'] or st['offset'] < last:
                    view = self._view(new)['stop']
                    return verdict('Stepped out to %s line %s.' % (view.get('script'), view.get('line')),
                                   'The first statement outside the run that was stopped (its caller after a '
                                   'function, else the next script the game ran).', stop=view)
                cur, last = new, st['offset']
        finally:
            self._arm()
        raise ConvError('STEP_LIMIT', 'The run did not end within %d statements. Next: continue.' % STEP_LIMIT)

    def _form_of(self, script_ptr):
        form = self.live.form_ids([script_ptr])[0] or {}
        if not form.get('formId'):
            raise ConvError('NO_SCRIPT_FORM', 'The stopped script has no form id to filter on.')
        return int(form['formId'], 16)

    def continue_(self):
        stop = self._stopped()
        if stop is None:
            self.link.one('resume', 'ff', 'run')
            return verdict('Resumed the game.', 'The game runs until a break.', state='running')
        if self._in_save_load(stop):
            stop = self._settle(stop)
            if stop is None:
                return verdict('Passed a statement inside save/load.', 'Refused stops are counted.', state='running',
                               skipped=self.skipped)
        self.dbg._resume(stop, 'run')         # the hold's condition (the breaks) decides the next stop
        new = self._await_ours(stop['seq'])
        new = self._settle(new, wait=True) if new is not None else None
        if new is None:
            note = 'The next break stops the game; status shows it.'
            if self.refused:
                note = ('%d stops were passed on (their condition was false, or they were inside save/load); the game '
                        'runs on. ' % self.refused) + note
            return verdict('Running: no break was reached within %d s.' % STOP_WAIT, note, state='running',
                           skipped=self.skipped, refused=self.refused)
        return self._view(new)

    def freeze(self):
        if self.saved_scripts is not None:
            return verdict('Scripts are already off.', 'Call thaw to run them again.', state='frozen')
        prev = self._byte()
        if prev not in (0, 1):
            raise ConvError('UNEXPECTED_VALUE', 'The process-scripts byte is %s, not 0 or 1; refusing to change it.' % prev)
        self._set_byte(0)
        self.saved_scripts = prev
        self._persist()
        return verdict('Scripts are off: the frame no longer runs them.', 'Call thaw before continuing a long run.',
                       state='frozen', previous=prev)

    def thaw(self):
        if self.saved_scripts is None:
            if self._byte() == 0:
                # Nothing on record (a freeze from before a restart without a state file, or another tool), yet scripts
                # are off: the byte is 1 whenever the game runs scripts, so thaw sets it.
                self._set_byte(1)
                return verdict('Scripts run again.', 'Nothing was on record as frozen, but the process-scripts byte was '
                               '0: it was set to 1.', state='running', restored=1, forced=True)
            return verdict('Scripts were not frozen by the debugger.', None, state='running')
        self._set_byte(self.saved_scripts)
        prev, self.saved_scripts = self.saved_scripts, None
        self._persist()
        return verdict('Scripts run again.', None, state='running', restored=prev)

    def _byte(self):
        raw = self.live.reads([(PROCESS_SCRIPTS, 1)])[0]
        if raw is None:
            raise ConvError('MEMORY_UNREADABLE', 'Cannot read the process-scripts byte at 0x%08X.' % PROCESS_SCRIPTS)
        return raw[0]

    def _set_byte(self, value):
        result = self.link.one('wr', word(PROCESS_SCRIPTS), '%02x' % value)
        if not result.get('ok'):
            raise ConvError('MEMORY_UNWRITABLE', 'Cannot write the process-scripts byte (%s).' % result.get('error'), result)


class ScriptLog:
    """fnv_script_log: commands from the statement ring, history of a reference, block events, raise and errors."""

    def __init__(self, live, eval_hook=None, debugger=None):
        self.live = live
        self.eval = eval_hook or EvalHook(live)
        self.dbg = debugger or Debugger(live)

    @property
    def link(self):
        return self.live.link

    def run(self, action, **args):
        mode = args.get('mode') or 'read'
        table = {
            'commands': lambda: self.commands(mode, args.get('script'), args.get('ref'), args.get('opcode'),
                                              args.get('since') or 0, args.get('limit') or 200),
            'history': lambda: self.history(args.get('ref'), args.get('since') or 0, args.get('limit') or 200),
            'events': lambda: self.events(mode, args.get('since') or 0, args.get('limit') or 200),
            'raise': lambda: self.raise_(args.get('ref'), args.get('flag')),
            'errors': lambda: self.errors(args.get('since') or 'last'),
        }
        self.live.ready()
        return _dispatch(action, table)

    def _ring(self, since, limit):
        h = self.eval.existing()
        if h is None:
            raise ConvError('NOT_ARMED', 'The statement log is off. Next: fnv_script_log commands with mode start, then play '
                            'and read.')
        seq, recs, lost = self.live.deep.records(h, since, limit=min(max(1, limit), RING * 20))
        return seq, recs, lost

    def _rows(self, recs, script_ptr=None, ref_ptr=None, names=None):
        names = names or {}
        rows = []
        for r in recs:
            if script_ptr is not None and r[12] != script_ptr:
                continue
            if ref_ptr is not None and r[14] != ref_ptr:
                continue
            name = names.get(r[13]) or '0x%04X' % r[13]
            rows.append({'seq': r[0], 'frame': r[22], 'thread': r[2], 'script': _hx(r[12]), 'opcode': r[13],
                         'command': name, 'reference': _hx(r[14]) if r[14] else None, 'offset': r[16], 'line': r[18]})
        return rows

    def commands(self, mode, script, ref, opcode, since, limit):
        if mode == 'start':
            self.eval.ensure()
            self.eval.log_on = True
            return verdict('The statement log is on.', 'Every statement of every script is recorded in a ring of 200 '
                           'records; read it with mode read.', armed=True)
        if mode == 'stop':
            if self.eval.holding:
                return verdict('Breaks from fnv_script_debug still use the statement hook; it stays on.',
                               'Clear the breaks first, then stop the log.', armed=True)
            self.eval.remove()
            self.eval.log_on = False
            return verdict('The statement log is off.', None, armed=False)
        if mode != 'read':
            raise ConvError('BAD_ARGUMENTS', 'mode must be start, read or stop.')
        seq, recs, lost = self._ring(since, limit)
        script_ptr = self.dbg_pointer(script) if script else None
        ref_ptr = self.live.resolve(ref)['ptr'] if ref else None
        ref_ptr = _int(ref_ptr) if ref_ptr is not None else None
        names = self._command_names()
        rows = self._rows(recs, script_ptr, ref_ptr, names)
        if opcode is not None:
            rows = [x for x in rows if x['opcode'] == int(opcode)]
        return verdict('%d statements.' % len(rows), 'The ring keeps the last records only; read more often for a long run. '
                       'Dropped: %d.' % lost, rows=rows[:limit], next=seq, dropped=lost, total=len(rows))

    def history(self, ref, since, limit):
        if not ref:
            raise ConvError('BAD_ARGUMENTS', 'A reference is needed.')
        ptr = _int(self.live.resolve(ref)['ptr'])
        seq, recs, lost = self._ring(since, limit)
        names = self._command_names()
        rows = [x for x in self._rows(recs, None, ptr, names) if (x['command'] or '').lower() in MUTATING]
        return verdict('%d changes to %s.' % (len(rows), ref), 'Only commands that change the world are listed; this uses '
                       'the statement ring, so a reference nobody ran a script on shows nothing.',
                       rows=rows[:limit], next=seq, dropped=lost)

    def events(self, mode, since, limit):
        if mode == 'start':
            engine = self.live.engine()
            for label, (name, address, nargs) in zip(FLAG_LABELS, (SET_ACTION_FLAG, LOCALS_ACTION_FLAG)):
                addr = engine_address(engine, name, address)
                if not any(int(str(h['target']), 16) == addr for h in self.live.deep.hooks()):
                    hook_at(self.live, addr, label, nargs, flags=HF_RECORD)
            return verdict('Block-event hooks are on.', 'Flags are recorded as they are raised.', armed=True)
        if mode == 'stop':
            for h in self.live.deep.hooks():
                if h.get('label') in FLAG_LABELS:
                    self.link.one('unhook', word(h['id']))
            return verdict('Block-event hooks are off.', None, armed=False)
        if mode != 'read':
            raise ConvError('BAD_ARGUMENTS', 'mode must be start, read or stop.')
        rows, nexts = [], {}
        for h in self.live.deep.hooks():
            label = h.get('label')
            if label not in FLAG_LABELS:
                continue
            seq, recs, lost = self.live.deep.records(h, since, limit=limit)
            nexts[label] = (seq, lost)
            for r in recs:
                rows.append(self._event(label, r))
        rows.sort(key=lambda x: x['seq'])
        return verdict('%d block events.' % len(rows), 'A flag is raised on the target; the block runs when that script '
                       'next runs.', rows=rows[-limit:], next=nexts)

    def _event(self, label, r):
        caller = self.dbg._symbol(r[5], return_address=True) if r[5] else {}
        if label == 'script_flag':
            form = self.live.form_ids([r[12]])[0] if r[12] else None
            row = {'source': 'Script_SetActionFlag', 'form': (form or {}).get('formId'), 'flagMask': r[14]}
        else:
            owner = self.live.u32s([r[3] + LOCALS_OWNER])[0] if r[3] else 0
            form = self.live.form_ids([owner])[0] if owner else None
            row = {'source': 'ScriptLocals::SetActionFlag', 'form': (form or {}).get('formId'), 'flagMask': r[12]}
        row.update(seq=r[0], frame=r[22], thread=r[2], flag=FLAGS.get(row['flagMask'], _hx(row['flagMask'])),
                   raisedBy=caller.get('function'), line=caller.get('source'))
        return row

    def raise_(self, ref, flag):
        if not ref or not flag:
            raise ConvError('BAD_ARGUMENTS', 'A reference and a flag are needed, such as OnTriggerEnter.')
        if flag not in FLAG_MASK:
            raise ConvError('BAD_FLAG', '%s is not a block event flag. Valid: %s. OnActivate and OnNPCActivate are not '
                            'flags: use the activation path.' % (flag, ', '.join(sorted(FLAG_MASK))))
        ptr = _int(self.live.resolve(ref)['ptr'])
        result = self.live.engine_call('Script_SetActionFlag', args=[{'type': 'ptr', 'value': _hx(ptr)},
                                                                    {'type': 'ptr', 'value': _hx(ptr + REF_EXTRA_LIST)},
                                                                    {'type': 'int', 'value': FLAG_MASK[flag]}])
        return verdict('Raised %s on %s.' % (flag, ref), 'Only the script is marked; the block runs when the scripts '
                       'next run.', flag=flag, reference=_hx(ptr), result=result)

    def errors(self, since):
        return self.live.script_errors('start' if since == 'start' else 'last')

    def dbg_pointer(self, spec):
        return _int(self.live.deep.script_form(spec)[0])

    def _command_names(self):
        names = {}
        for c in self.live.engine().commands:
            if c.get('opcode'):
                names[int(c['opcode'], 16)] = c['name']
        return names

def _dispatch(action, table):
    handler = table.get(action)
    if handler is None:
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid actions: %s.' % (action, ', '.join(sorted(table))),
                        {'actions': sorted(table)})
    return handler()


def _filter_of(b):
    return b.get('filter')


def _where(b):
    parts = []
    if b.get('scriptName'):
        parts.append('script %s' % b['scriptName'])
    if b.get('line') is not None:
        parts.append('line %s' % b['line'])
    if b.get('opcode') is not None:
        parts.append('opcode %s' % b['opcode'])
    if b.get('when'):
        parts.append('when %s' % b['when'])
    return ', '.join(parts) or 'every statement'


def _public(b):
    keep = ('number', 'script', 'scriptName', 'line', 'statement', 'opcode', 'when')
    out = {k: b.get(k) for k in keep}
    if b.get('varWhen'):
        out['when'] = b['varWhen']
        out['whenChecked'] = 'by the bridge at each stop (script variables)'
    return out


PRED_WORDS = {'and', 'or', 'not', 'frame', 'main', 'this', 'reg', 'eax', 'ecx', 'edx', 'ebx', 'esp', 'ebp', 'esi',
              'edi', 'u8', 'i8', 'u16', 'i16', 'u32', 'i32', 'f32'}
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt,
        ast.GtE: operator.ge}


def var_names(text):
    """The identifiers of a `when` that are not predicate words (argN, this, reg, types, and/or/not, frame...).
    Number literals (0x0C, 12, 1.5) are dropped first, or the x of a hex literal would read as a name."""
    text = re.sub(r'(?<![\w.])(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?)(?!\w)', ' ', text or '')
    return {n for n in re.findall(r'[A-Za-z_]\w*', text) if n.lower() not in PRED_WORDS
            and not re.fullmatch(r'arg\d{1,3}', n.lower())}


def compile_var_when(text):
    """Parse a variable condition (names, numbers, + - * /, comparisons, and/or/not, parentheses) into a checked
    expression tree. Raises ConvError on anything else."""
    try:
        tree = ast.parse(text.replace('&&', ' and ').replace('||', ' or '), mode='eval')
    except SyntaxError as exc:
        raise ConvError('BAD_PREDICATE', 'The condition %r does not parse (%s). Next: use names, numbers, comparisons '
                        'and and/or/not.' % (text, exc.msg))
    allowed = (ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.BinOp, ast.Compare,
               ast.Name, ast.Load, ast.Constant) + tuple(_BIN) + tuple(_CMP)
    for node in ast.walk(tree):
        if not isinstance(node, allowed) or (isinstance(node, ast.Constant) and not isinstance(node.value, (int, float))):
            raise ConvError('BAD_PREDICATE', 'The condition %r uses %s; only names, numbers, + - * /, comparisons and '
                            'and/or/not are allowed.' % (text, type(node).__name__))
    return tree


def eval_var_when(tree, values):
    """Evaluate a compiled condition with {lower-case name: value}; an unknown name raises ConvError."""
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            key = node.id.lower()
            if key not in values:
                raise ConvError('NO_SUCH_VARIABLE', 'The condition names %s, which the script does not have.' % node.id)
            return values[key]
        if isinstance(node, ast.UnaryOp):
            v = ev(node.operand)
            return (not v) if isinstance(node.op, ast.Not) else -v
        if isinstance(node, ast.BinOp):
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.BoolOp):
            vals = [ev(v) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, right in zip(node.ops, node.comparators):
                r = ev(right)
                if not _CMP[type(op)](left, r):
                    return False
                left = r
            return True
        raise ConvError('BAD_PREDICATE', 'Unsupported condition element %s.' % type(node).__name__)
    try:
        return bool(ev(tree))
    except (ZeroDivisionError, OverflowError, ValueError):
        return False        # a condition that cannot be computed (a / 0) does not hold
