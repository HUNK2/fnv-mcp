"""Call flow (Plan 2, W2): fnv_flow trace. Runs one function under single-step in the engine companion and maps the
steps to decomp lines, with the values of its locals.

Design: plan2/B-debug.md section 2; companion ops: docs/FNV-COMPANION-V2.md 8.2 (bp) and 8.4 (trace). The bridge arms a
one-shot breakpoint at the function's first byte, waits for the game to call it, sends `resume <seq> trace`, then
reads `trace read` pages until the companion says done. Locals are read with a log program: one load per local at
its EBP offset (`reg.ebp.[off]:type`), so the trace entries carry the values in the order of `locals`.

`call` runs the function ourselves under the same trace. The companion ignores stops while it serves an engine call
(8.2, serving_request), so the call is made by a small native module (native/modules/fnvmcp_flowcall.c) from its frame
callback: the bridge fills the module's slot, arms the breakpoint, and the next frame makes the call.
`depth 1` also traces the direct callees that have source lines (up to 16, from the engine map's call table): the
companion takes them as extra ranges (`trace ranges`). Locals are only read for lines of the traced function itself.
The helpers here are shared with fnv_blame and fnv_script_debug.
"""
import struct
import time

from . import fnv_symdb
from .fnv_conv import ConvError, verdict
from .fnv_debugger import Debugger
from .fnv_deep import HookError, decode_prologue
from .fnv_engine import EngineMapError
from .fnv_link import hexs, word
from .fnv_pred import compile_predicate

MAX_TRACE = 200000
DEFAULT_STEPS = 5000
LOCAL_LIMIT = 16        # the companion's value stack is 16 deep
ARM_WAIT = 20.0         # seconds to wait for the game to call the function
RUN_WAIT = 60.0         # seconds to read the trace before it is reported as still running
READ_PAGE = 2048
HW_SLOTS = 4
FLAG_ONCE = 4
LOAD_CODES = {'u8': 'u8', 'i8': 'i8', 'u16': 'u16', 'i16': 'i16', 'u32': 'u32', 'i32': 'i32', 'f32': 'f32'}
BITS = {'u8': 8, 'i8': 8, 'u16': 16, 'i16': 16, 'u32': 32, 'i32': 32}

PRED_SLOT = 57   # no longer used: a trace's when condition takes a free slot of the debugger's pool (fnv_debug's)


def hook_at(live, address, label, nargs, flags=0, filter_sel=0, filter_value=0):
    """Install a companion hook at a function's entry under a label: the call the watch hooks and the debugger make.
    Refuses when the target already carries a hook."""
    for h in live.link.one('hooks').get('hooks', []):
        # The companion keeps removed hooks in its list with stolen = 0: those no longer patch the function.
        if h.get('stolen') and int(str(h.get('target')), 16) == address:
            raise ConvError('ALREADY_HOOKED', 'The function at 0x%08X already carries a hook (%s). Next: remove it with '
                            'fnv_hooks first.' % (address, h.get('label')), {'hook': h.get('label')})
    raw = live.reads([(address, 24)])[0]
    if raw is None:
        raise ConvError('UNREADABLE', 'Cannot read the function at 0x%08X.' % address)
    try:
        stolen, relocs = decode_prologue(raw)
    except HookError as e:
        raise ConvError('HOOK_REFUSED', str(e))
    result = live.link.one('hook', word(address), word(stolen), ','.join('%x' % r for r in relocs) or '-',
                           word(flags), word(filter_sel), word(filter_value), '0', word(nargs), '0', '0', '0', '0',
                           hexs(label))
    if not result.get('ok'):
        raise ConvError('HOOK_REFUSED', 'The companion refused the hook (%s).' % result.get('error'), result)
    return result['hook']


def engine_address(engine, name, expected):
    """The address the engine map gives a name; refuses when it is missing or disagrees with a known address."""
    try:
        found = engine.resolve(name)
    except (EngineMapError, KeyError, ValueError) as e:
        raise ConvError('ENGINE_MAP_MISMATCH', '%s is not in the engine map (%s). Next: check the engine map before '
                        'using this tool.' % (name, getattr(e, 'code', None) or e))
    if found != expected:
        raise ConvError('ENGINE_MAP_MISMATCH', '%s is at 0x%08X in the engine map, not the 0x%08X this tool expects. The '
                        'tool and the map disagree: refresh the engine map before using it.' % (name, found, expected),
                        {'mapped': found, 'expected': expected})
    return found


def local_code(local):
    """Load type of a scalar local for the log program, or None (structs, arrays, doubles)."""
    text = (local.get('type') or '').strip()
    size = local.get('size')
    if fnv_symdb.is_pointer_text(text):
        return 'u32' if size in (None, 4) else None
    if text == 'float' and size == 4:
        return 'f32'
    if size not in (1, 2, 4):
        return None
    unsigned = 'unsigned' in text or text.startswith(('uint', 'bool', 'size_t', 'u'))
    code = {1: 'u8', 2: 'u16', 4: 'u32'}[size]
    if not unsigned and size == 1 and 'char' in text:
        return 'i8'
    if not unsigned and size == 2 and 'short' in text:
        return 'i16'
    if not unsigned and size == 4 and text in ('int', 'long', 'signed int', 'long int'):
        return 'i32'
    return code


def load_text(local, code):
    """The predicate text that reads one local at its EBP offset (negative offsets as 0-N)."""
    off = local['offset'] or 0
    return 'reg.ebp.[0%+d]:%s' % (off, code)


def scalar(raw, code):
    """A companion value as the Python value of its load type (floats from their bit pattern)."""
    if raw is None:
        return None
    if isinstance(raw, str):        # trace and log entries carry the raw word as "0x%08x" (seen live)
        raw = int(raw, 16) if raw.lower().startswith('0x') else (float(raw) if '.' in raw else int(raw))
    if code == 'f32':
        if isinstance(raw, float):
            return raw
        return struct.unpack('<f', struct.pack('<I', int(raw) & 0xFFFFFFFF))[0]
    bits = BITS[code]
    value = int(raw) & ((1 << bits) - 1)
    if code.startswith('i') and value >> (bits - 1):
        value -= 1 << bits
    return value


class FlowTools:
    """fnv_flow: trace (and, later, call). Shares the debugger's breakpoint ids and stop reader."""

    def __init__(self, live, debugger=None, sleep=time.sleep, clock=time.monotonic, native=None):
        self.live = live
        self.sleep = sleep
        self.clock = clock
        self.dbg = debugger or Debugger(live, sleep=sleep, clock=clock)
        self.native = native

    @property
    def link(self):
        return self.live.link

    def run(self, action, **args):
        if action == 'call':
            return self.trace(args.get('function'), None, args.get('depth') or 0, args.get('max_steps'),
                              call=(args.get('this'), list(args.get('args') or [])))
        if action != 'trace':
            raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid actions: trace, call.' % action,
                            {'actions': ['trace', 'call']})
        return self.trace(args.get('function'), args.get('when'), args.get('depth') or 0, args.get('max_steps'))

    # ================================================================ trace
    def trace(self, function, when=None, depth=0, max_steps=None, arm_wait=ARM_WAIT, call=None):
        if not function or not str(function).strip():
            raise ConvError('BAD_ARGUMENTS', 'function is needed: a function name, an address, or file:line.')
        if depth not in (0, 1, None):
            raise ConvError('BAD_ARGUMENTS', 'depth is 0 (calls stepped over) or 1 (direct callees with source lines '
                            'traced too).')
        steps_limit = int(max_steps or DEFAULT_STEPS)
        if not 1 <= steps_limit <= MAX_TRACE:
            raise ConvError('BAD_ARGUMENTS', 'max_steps must be 1 to %d.' % MAX_TRACE)
        self.live.ready()
        if not self.live.game.process():
            raise ConvError('NOT_RUNNING', 'Tracing needs the game running.')
        db = self.dbg._symdb(required=True)
        _, start, _ = self.dbg._resolve(function)
        fn = db.function(start)
        if fn is None:
            raise ConvError('NOT_FOUND', 'No function at 0x%08X in the symbol database.' % start)
        lines = db.lines_of(fn['addr'])
        if not lines:
            raise ConvError('NO_LINES', '%s has no source lines in the symbol database. Next: trace a function that '
                            'has decomp source.' % fn['name'])
        locals_, skipped = self._trace_locals(db, fn['addr'])
        log_hex = ''.join(compile_predicate(load_text(local, local['code']))['hex'] for local in locals_)
        end = fn['addr'] + max(fn['size'] or 1, 1)
        # Every check of the call (12 words, argument names, the module's edx slot) runs before the breakpoint is
        # armed: a refusal after arming left a FLAG_ONCE breakpoint that parked the next caller (R5).
        prepared = self._prepare_call(fn, call) if call is not None else None
        pred_slot = None
        try:
            # One lock with fnv_debug: two calls must not pick the same bp id or predicate slot.
            with self.dbg.lock:
                pred = 'ff'
                if when:   # breakpoints take a predicate slot (wire 2): store the program first
                    pred_slot = self.dbg._store_pred(when)
                    pred = word(pred_slot)
                bp_id = self.dbg._free_bp_id()
                mechanism = 'hw' if self._hw_used() < HW_SLOTS else 'int3'
                try:
                    armed = self.dbg._set_bp(bp_id, fn['addr'], mechanism, pred, FLAG_ONCE, None)
                except ConvError:
                    if mechanism != 'hw':
                        raise
                    armed = self.dbg._set_bp(bp_id, fn['addr'], 'int3', pred, FLAG_ONCE, None)
            return self._trace_armed(db, fn, bp_id, end, when, depth, steps_limit, arm_wait, prepared, locals_,
                                     skipped, log_hex)
        finally:
            if pred_slot is not None:
                self.dbg.release_pred_slot(pred_slot)

    def _trace_armed(self, db, fn, bp_id, end, when, depth, steps_limit, arm_wait, prepared, locals_, skipped,
                     log_hex):
        slot = None
        try:
            callees = self._callee_ranges(db, fn) if depth == 1 else []
            slot = self._write_call(prepared) if prepared is not None else None
            stop = self._await_bp(bp_id, arm_wait)
        except BaseException:
            # Nothing armed may outlive the call: the breakpoint, and the module's slot (R5).
            try:
                self.link.one('bp', 'clear', word(bp_id))
                if slot:
                    self.link.write(slot, struct.pack('<I', 0))
            except Exception:  # noqa: BLE001 - the original error is the one to report
                pass
            raise
        if stop is None:
            self.link.one('bp', 'clear', word(bp_id))
            if slot:                        # the module must not make the call later, into another tool's break
                self.link.write(slot, struct.pack('<I', 0))
            raise ConvError('NOT_CALLED', '%s was not called within %d s, so nothing was traced; the breakpoint is '
                            'cleared. Next: make the game call it, then trace again.' % (fn['name'], arm_wait))
        packed = ''.join(struct.pack('<II', lo, hi).hex() for lo, hi, _ in callees) or '-'
        ranged = self.link.one('trace', 'ranges', packed)
        if callees and not ranged.get('ok'):
            callees = []        # an older companion: depth 0 only
        args = ['trace', word(steps_limit), word(fn['addr']), word(end)] + ([log_hex] if log_hex else [])
        ended = {}
        try:
            result = self.link.one('resume', word(stop['seq']), *args)
            if not result.get('ok'):
                raise ConvError('COMPANION_REFUSED', 'The companion refused the trace (%s).' % result.get('error'),
                                result)
            entries, reason, done = self._read_trace(stop['seq'], ended)
        except BaseException:
            # Nothing may stay parked: the bp stop when the trace was refused, the stop the trace ended in when its
            # entries could not be read. (A stop that is already gone answers no_stop; that is ignored.)
            self._release_quietly(stop['seq'], ended.get('end'))
            raise
        if done:
            self._release(ended.get('end'))
        called = self._call_result(slot) if slot else None
        rows, summary = self._lines_of(db, fn, entries, locals_)
        if callees:
            spans = [(fn['addr'], fn['addr'] + max(fn.get('size') or 1, 1), fn['name'])] + list(callees)
            for row in rows:
                eip = row.get('_eip') or 0
                row['function'] = next((name for lo, hi, name in spans if lo <= eip < hi), None)
            summary['calleesTraced'] = sorted({name for _, _, name in callees})
        if called is not None:
            called['note'] = 'eax as returned; a function returning a float leaves it in st0 instead (see the '                              'last values of its locals in the trace)'
        for row in rows:
            row.pop('_eip', None)
        note = {'max': 'The trace stopped at max_steps; raise max_steps to see the rest.',
                'fault': 'The thread faulted inside the function; the trace ends there.',
                'returned': 'The function returned.',
                'timeout': 'The trace is still running in the game; the thread is not released yet. When it ends the '
                           'thread parks as a step stop: fnv_debug continue releases it.'}.get(reason)
        return verdict('Traced %s: %d steps over %d source lines.' % (fn['name'], len(entries), len(rows)),
                       note or 'The trace is complete.', function=fn['name'], address='0x%08X' % fn['addr'],
                       endedBy=reason, done=done, lines=rows, steps=summary, locals=[x['name'] for x in locals_],
                       localsNotTraced=skipped, when=when or None, depth=1 if callees else 0, call=called)

    def _callee_ranges(self, db, fn, limit=16):
        """(lo, hi, name) of the direct callees of fn that have source lines (engine map call table)."""
        try:
            calls = self.live.engine().calls.get(fn["addr"], [])
        except Exception:  # noqa: BLE001 - no call table
            return []
        out = []
        for target in calls:
            callee = db.function(target)
            if not callee or callee['addr'] == fn['addr'] or not db.lines_of(callee['addr']):
                continue
            out.append((callee['addr'], callee['addr'] + max(callee['size'] or 1, 1), callee['name']))
            if len(out) >= limit:
                break
        return out

    def _arm_call(self, fn, call):
        """Fill the flow-call module's slot so the next frame calls fn (the breakpoint is already armed)."""
        return self._write_call(self._prepare_call(fn, call))

    def _prepare_call(self, fn, call):
        """Everything the call needs, checked and resolved, nothing written: (slot, body, edx, write_edx)."""
        if self.native is None:
            raise ConvError('NOT_AVAILABLE', 'call needs the native module tools.')
        slot = self.native.flow_slot()
        this, args = call
        words = []
        for a in args:
            if isinstance(a, float):
                words.append(struct.unpack('<I', struct.pack('<f', a))[0])
            elif isinstance(a, str) and not a.lower().startswith('0x') and not a.lstrip('-').isdigit():
                words.append(self.live.resolve(a)['ptr'])
            else:
                words.append(int(str(a), 0) & 0xFFFFFFFF)
        if len(words) > 12:
            raise ConvError('BAD_ARGUMENTS', 'call takes at most 12 argument words.')
        this_ptr = 0
        if this:
            this_ptr = int(this, 16) if str(this).lower().startswith('0x') else self.live.resolve(this)['ptr']
        edx = 0
        if self._is_fastcall(fn['addr']):
            # fastcall: ecx and edx are the first two arguments (`this`, when given, is the first), the rest are
            # stack words; the slot's edx word carries the second.
            regs = ([this_ptr] if this else []) + words
            this_ptr, edx = (regs + [0, 0])[:2]
            words = regs[2:]
        body = struct.pack('<III', fn['addr'], this_ptr, len(words)) + struct.pack(
            '<12I', *(words + [0] * (12 - len(words))))
        return slot, body, edx, self._slot_has_edx(required=bool(edx))

    def _write_call(self, prepared):
        slot, body, edx, write_edx = prepared
        self.link.write(slot + 4, body + struct.pack('<II', 0, 0))
        if write_edx:                       # a stale edx from an earlier fastcall call is cleared too
            self.link.write(slot + 4 * 18, struct.pack('<I', edx))
        self.link.write(slot, struct.pack('<I', 1))
        return slot

    def _is_fastcall(self, address):
        try:
            _, _record, signature, _ = self.live._signature('0x%08X' % address)
        except Exception:  # noqa: BLE001 - no signature known: the slot's default (ecx = this) applies
            return False
        return '__fastcall' in (signature or '')

    def _slot_has_edx(self, required):
        exports = (getattr(self.native, 'loaded', {}) or {}).get('fnvmcp_flowcall', {}).get('exports', {})
        if 'fnvmcp_flow_version' in exports:
            return True
        if required:
            raise ConvError('OLD_MODULE', 'The loaded flow-call module is too old for a fastcall function. Next: '
                            'fnv_native unload name=fnvmcp_flowcall, then call again.')
        return False

    def _call_result(self, slot):
        deadline = self.clock() + 5.0
        while self.clock() < deadline:
            raw = self.live.reads([(slot + 4 * 16, 8)])[0]
            eax, done = struct.unpack('<II', raw) if raw else (0, 0)
            if done:
                return {'eax': '0x%08X' % eax, 'int': eax - (1 << 32) if eax >= 1 << 31 else eax}
            self.sleep(0.05)
        return {'eax': None, 'note': 'the call had not returned when read'}

    def _hw_used(self):
        return sum(1 for b in self.link.one('bp', 'list').get('bps', []) if b.get('kind') == 'hw')

    def _trace_locals(self, db, start):
        """Scalar parameters and locals with their load types (at most LOCAL_LIMIT), and the names left out."""
        out, skipped = [], []
        for local in db.locals_of(start):
            if local['kind'] != 'bprel':
                skipped.append(local['name'])
                continue
            code = local_code(local)
            if code is None:
                skipped.append(local['name'])
                continue
            if len(out) >= LOCAL_LIMIT:
                skipped.append(local['name'])
                continue
            out.append(dict(local, code=code))
        return out, skipped

    def _await_bp(self, bp_id, wait):
        deadline = self.clock() + wait
        while True:
            for s in self.dbg._stops():
                if s.get('kind') == 'bp' and int(s.get('id')) == bp_id:
                    return s
            if self.clock() >= deadline:
                return None
            self.sleep(0.05)

    def _read_trace(self, start_seq, ended=None):
        """The trace is filed under the step stop that ends it (the thread parks again when it leaves the
        function or reaches max): wait for that stop, then read the entries with its seq (seen live: reading with the
        breakpoint's seq gives no_trace)."""
        deadline = self.clock() + RUN_WAIT
        end = None
        while end is None:
            parked = [s for s in self.dbg._stops() if s.get('kind') == 'step' and int(s.get('seq', 0)) > start_seq]
            if parked:
                end = min(parked, key=lambda s: int(s['seq']))
                if ended is not None:
                    ended['end'] = end
                break
            if self.clock() >= deadline:
                return [], 'timeout', False
            self.sleep(0.05)
        entries, since = [], 0
        while True:
            page = self.link.one('trace', 'read', word(end['seq']), word(since), word(READ_PAGE))
            if 'entries' not in page:
                raise ConvError('TRACE_FAILED', 'The companion did not return the trace (%s).'
                                % (page.get('error') or 'no reason given'), page)
            entries.extend(page['entries'])
            since = page.get('next', since)
            if page.get('done'):
                return entries, page.get('reason'), True
            if self.clock() >= deadline:
                return entries, 'timeout', False
            self.sleep(0.05)

    def _release(self, end):
        """Resume the thread the trace parked: only the step stop this trace ended in, never another tool's stop."""
        if end is not None:
            self.link.one('resume', word(end['seq']), 'run')

    def _release_quietly(self, bp_seq, end):
        for seq in (bp_seq, end['seq'] if end else None):
            if seq is None:
                continue
            try:
                self.link.one('resume', word(seq), 'run')
            except Exception:  # noqa: BLE001 - the stop may already be gone; nothing else can be done here
                pass

    def _lines_of(self, db, fn, entries, locals_):
        """Entries to source lines: order of first run, visits (hits), steps, and the locals whose value changed on
        the line that ran before the change (a step records the state before its instruction)."""
        names = [x['name'] for x in locals_]
        codes = [x['code'] for x in locals_]
        rows, prev_key, prev_vals, skipped_prologue, unmapped = {}, None, None, 0, 0
        for e in entries:
            eip = int(str(e['eip']), 16) if isinstance(e['eip'], str) else int(e['eip'])
            inside = fn['addr'] <= eip < fn['addr'] + max(fn.get('size') or 1, 1)
            if inside and eip - fn['addr'] < (fn.get('debug_start') or 0):
                skipped_prologue += 1
                continue
            pos = db.line_at(eip)
            if not pos or pos.get('line') is None:
                unmapped += 1
                continue
            key = (pos['file'], pos['line'])
            raw = e.get('values') or []
            vals = [scalar(raw[i] if i < len(raw) else None, codes[i]) for i in range(len(codes))]
            row = rows.get(key)
            if row is None:
                row = rows[key] = {'file': key[0], 'line': key[1], 'order': len(rows) + 1, 'hits': 0, 'steps': 0,
                                   'changed': {}, '_eip': eip}
            if not fn['addr'] <= eip < fn['addr'] + max(fn.get('size') or 1, 1):
                if key != prev_key:
                    row['hits'] += 1
                row['steps'] += 1          # a callee's line (depth 1): its frame is not the function's, no locals
                prev_key = key
                continue
            row['steps'] += 1
            if key != prev_key:
                row['hits'] += 1
            if prev_vals is not None and vals != prev_vals and prev_key in rows:
                changed = rows[prev_key]['changed']
                for i, name in enumerate(names):
                    if prev_vals[i] != vals[i]:
                        item = changed.setdefault(name, {'from': prev_vals[i], 'to': vals[i]})
                        item['to'] = vals[i]
            prev_key, prev_vals = key, vals
        out = []
        for row in sorted(rows.values(), key=lambda r: r['order']):
            row['text'] = self._text(row['file'], row['line'])
            out.append(row)
        summary = {'entries': len(entries), 'prologue': skipped_prologue, 'unmapped': unmapped}
        return out, summary

    def _text(self, file, line):
        try:
            lines = self.live.engine()._lines(file)
        except Exception:      # no decomp checkout: the line numbers are still there
            return None
        return lines[line - 1].rstrip()[:200] if 0 < line <= len(lines) else None
