"""fnv_debug (W2): breaks, stops, stacks, locals, eval, set, stepping and process control, against a fake companion
and a fake game process, on the tiny symbol database of test_fnv_symdb. No game is needed."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp import fnv_conv, fnv_engine, fnv_procmem, fnv_symdb  # noqa: E402
from fnv_mcp import fnv_debugger  # noqa: E402
from test_fnv_symdb import build_db  # noqa: E402

CODE = 0x401000          # Actor::ApplyDamage: 6 bytes of prologue, lines at offsets 0, 6, 0x10, 0x20 (inline.h), 0x30
LINE7 = 0x401006         # src/actor.cpp:7
CONTEXT = 0x2000000
STACK_TOP = 0x19F000     # esp; ebp = STACK_TOP + 0x100
EBP = STACK_TOP + 0x100
OBJECT = 0x30000000      # the 'this' object
HOOK_BLOCK = 0x2100000
CALLER = 0x402000        # a return address inside the exe range


class FakeLink:
    """A companion that answers the ops the debugger sends; memory is a list of (base, bytearray) regions."""

    def __init__(self, regions):
        self.regions = regions
        self.ops = []
        self.stops = []
        self.bps = {}
        self.hooks = []
        self.preds = {}
        self.on_resume = None

    def region(self, address, length):
        for base, data in self.regions:
            if base <= address and address + length <= base + len(data):
                return data, address - base
        return None, 0

    def one(self, *op, timeout=None):
        self.ops.append(op)
        name = op[0]
        if name == 'stops':
            return {'stops': [dict(s) for s in self.stops]}
        if name == 'rd':
            address, length = int(op[1], 16), int(op[2], 16)
            data, at = self.region(address, length)
            if data is None:
                return {'ok': False, 'error': 'unreadable'}
            return {'ok': True, 'hex': bytes(data[at:at + length]).hex()}
        if name == 'wr':
            address, raw = int(op[1], 16), bytes.fromhex(op[2])
            data, at = self.region(address, len(raw))
            if data is None:
                return {'ok': False, 'error': 'unwritable'}
            data[at:at + len(raw)] = raw
            return {'ok': True}
        if name == 'bp' and op[1] == 'set':
            kind = op[4]
            self.bps[int(op[2])] = {'id': int(op[2]), 'addr': op[3], 'kind': kind, 'hits': 0, 'pred': op[5],
                                    'after': getattr(self, 'pending_after', 0)}
            self.pending_after = 0
            return {'ok': True, 'id': int(op[2]), 'kind': kind}
        if name == 'bp' and op[1] == 'after':
            self.pending_after = int(op[3], 16)
            return {'ok': True, 'id': int(op[2], 16), 'after': int(op[3], 16), 'pending': True}
        if name == 'bp' and op[1] == 'list':
            return {'bps': list(self.bps.values())}
        if name == 'bp' and op[1] == 'clear':
            if op[2] == 'ff':
                n = len(self.bps)
                self.bps.clear()
                return {'ok': True, 'cleared': n}
            self.bps.pop(int(op[2]), None)
            return {'ok': True}
        if name == 'pred':
            if op[1] == 'set':
                self.preds[int(op[2], 16)] = op[3]
                return {'ok': True, 'id': int(op[2], 16)}
            if op[1] == 'clear':
                self.preds.pop(int(op[2], 16), None) if op[2] != 'ff' else self.preds.clear()
                return {'ok': True}
            return {'preds': [{'id': i} for i in sorted(self.preds)]}
        if name == 'hooks':
            return {'ok': True, 'hooks': list(self.hooks)}
        if name == 'hook':
            hook = {'id': 3, 'label': 'dbg', 'target': op[1], 'stolen': int(op[2], 16), 'calls': 0}
            self.hooks.append(hook)
            return {'ok': True, 'hook': hook}
        if name == 'hookact':
            return {'ok': True}
        if name == 'unhook':
            self.hooks = [h for h in self.hooks if h['id'] != int(op[1], 16)]
            return {'ok': True}
        if name == 'faulthold':
            return {'ok': True}
        if name == 'resume':
            if self.on_resume:
                self.on_resume(op)
            return {'ok': True}
        return {'ok': False, 'error': 'unknown op %s' % name}

    def sent(self, name):
        return [op for op in self.ops if op[0] == name]


class FakeEngine:
    functions = {}

    def __init__(self, db):
        self.symdb = db

    def resolve(self, ref):
        for fn in self.symdb._all('SELECT addr, name FROM functions'):
            if fn['name'] == ref:
                return fn['addr']
        raise fnv_engine.EngineMapError('NOT_FOUND', 'No function named %s' % ref)

    def containing(self, address):
        fn = self.symdb.function_containing(address)
        return (fn['addr'], address - fn['addr']) if fn else None

    def summary(self, address, with_location=True):
        fn = self.symdb.function(address)
        if not fn:
            raise KeyError(address)
        return {'name': fn['name']}

    def symbolize(self, address, modules=None, return_address=False):
        probe = address - 1 if return_address else address
        fn = self.symdb.function_containing(probe)
        if not fn:
            return {'kind': 'unknown'}
        out = {'kind': 'function', 'name': fn['name'], 'offset': '0x%X' % (probe - fn['addr'])}
        line = self.symdb.line_at(probe)
        if line:
            out['source'] = '%s:%d' % (line['file'], line['line'])
            out['sourceLine'] = 'line %d' % line['line']
        return out

    def _lines(self, rel):
        if rel != 'src/actor.cpp':
            raise fnv_engine.EngineMapError('NO_SOURCE', 'no source %s' % rel)
        return ['}' if i == 12 else '// line %d' % i for i in range(1, 21)]   # line 12 closes the function

    def vtable_at(self, address):
        return None


def context(eip, esp, ebp, **regs):
    raw = bytearray(fnv_procmem.WOW64_CONTEXT_SIZE)
    values = dict(eip=eip, esp=esp, ebp=ebp, **regs)
    for name, value in values.items():
        struct.pack_into('<I', raw, fnv_procmem.CONTEXT_OFFSETS[name], value)
    return raw


def stop_at(seq, eip, kind='bp', stop_id=0):
    return {'seq': seq, 'kind': kind, 'id': stop_id, 'thread': '1000', 'main': True, 'eip': '%x' % eip,
            'frame': None, 'regs': None, 'context': '%x' % CONTEXT, 'exception': None}


def float_bytes(value):
    return struct.pack('<f', value)


class DebugTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db_path = Path(self.tmp.name) / 'symdb.sqlite'
        build_db(db_path)
        self.addCleanup(fnv_symdb.clear_cache)
        self.db = fnv_symdb.load(db_path)
        self.engine = FakeEngine(self.db)

        code = bytearray(b'\x55\x8b\xec\x83\xec\x20' + b'\x90' * 18)      # push ebp; mov ebp,esp; sub esp,0x20
        stack = bytearray(0x1000)
        struct.pack_into('<I', stack, 0x100, 0)                           # saved ebp: end of the chain
        struct.pack_into('<I', stack, 0x104, CALLER)                      # return address
        stack[0x108:0x10C] = float_bytes(2.5)                             # afAmount (ebp+8)
        struct.pack_into('<I', stack, 0xFC, OBJECT)                       # owner (ebp-4)
        struct.pack_into('<I', stack, 0x118, 7)                           # slot (ebp+0x18), an int
        obj = bytearray(0x100)
        obj[0x10:0x14] = float_bytes(80.0)                                # m_fHealth
        self.ctx = context(LINE7, STACK_TOP, EBP, ecx=OBJECT, eax=0x11, eflags=0x202)
        self.link = FakeLink([(CODE, code), (CONTEXT, bytearray(self.ctx)), (STACK_TOP, stack),
                              (OBJECT, obj), (CALLER, bytearray(16)), (HOOK_BLOCK, bytearray(0x60))])
        self.live = SimpleNamespace(link=self.link, engine=lambda: self.engine, ready=lambda: None,
                                    game=SimpleNamespace(process=lambda: (1234, 0)),
                                    project=SimpleNamespace(state_dir=Path(self.tmp.name)))
        self.dbg = fnv_debugger.Debugger(self.live, sleep=lambda s: None, clock=lambda: 0.0)
        self.dbg.pid = 1234      # a process already seen, so the breaks a test seeds are kept

    def run_tool(self, action, **args):
        return self.dbg.run(action, **args)

    def bp_sets(self):
        return [op for op in self.link.sent('bp') if op[1] == 'set']

    def stop_bp(self, number=1):
        self.link.stops = [stop_at(number, LINE7)]


class BreakTests(DebugTestBase):
    def test_unknown_action(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('jump')
        self.assertEqual(cm.exception.code, 'UNKNOWN_ACTION')

    def test_function_break_is_a_hook_hold(self):
        out = self.run_tool('break', op='set', location='Actor::ApplyDamage')
        self.assertEqual(out['break']['mechanism'], 'hook')
        self.assertEqual(out['break']['address'], '0x00401000')
        self.assertTrue(self.link.sent('hook'))
        # E24: thread=main (the default) holds on the shared `main` predicate (slot 55 = 0x37), so a worker is not parked
        self.assertEqual(self.link.sent('hookact')[0][2:], ('hold', '37', '4'))   # E2: and the 4 bytes it pops

    def test_line_break_is_a_hardware_breakpoint(self):
        out = self.run_tool('break', op='set', location='src/actor.cpp:7')
        self.assertEqual(out['break']['mechanism'], 'hw')
        self.assertEqual(self.bp_sets()[0][1:6], ('set', '0', '401006', 'hw', 'ff'))

    def test_line_break_falls_back_to_int3_when_hw_is_full(self):
        self.link.bps = {i: {'id': i, 'kind': 'hw'} for i in range(4)}
        self.dbg.breaks = {i: {'number': i, 'mechanism': 'hw', 'id': i} for i in range(4)}
        self.dbg.next_number = 5
        out = self.run_tool('break', op='set', location='src/actor.cpp:7')
        self.assertEqual(out['break']['mechanism'], 'int3')

    def test_hits_on_a_function_uses_a_trap_with_bp_after(self):
        out = self.run_tool('break', op='set', location='Actor::ApplyDamage', hits=5)
        self.assertIn(out['break']['mechanism'], ('hw', 'int3'))          # not a hook hold: it cannot count
        self.assertFalse(self.link.sent('hook'))
        after = [i for i, op in enumerate(self.link.ops) if op[:2] == ('bp', 'after')]
        sets = [i for i, op in enumerate(self.link.ops) if op[:2] == ('bp', 'set')]
        self.assertEqual(self.link.ops[after[0]], ('bp', 'after', '0', '5'))
        self.run_tool('break', op='set', location='src/actor.cpp:7', hits=120)
        self.assertIn(('bp', 'after', '1', '78'), self.link.ops)       # wire numbers are hex
        self.assertLess(after[0], sets[0])                              # armed before the breakpoint exists
        self.assertEqual(self.link.bps[0]['after'], 5)

    def test_log_break_takes_one_expression(self):
        out = self.run_tool('break', op='set', location='src/actor.cpp:7', do='log', log=['frame'])
        self.assertEqual(out['break']['do'], 'log')
        self.assertEqual(self.bp_sets()[0][6], '1')
        with self.assertRaises(fnv_conv.ConvError):
            self.run_tool('break', op='set', location='src/actor.cpp:7', do='log')

    def test_list_and_clear(self):
        self.run_tool('break', op='set', location='src/actor.cpp:7')
        self.run_tool('break', op='set', location='Actor::ApplyDamage')
        listed = self.run_tool('break', op='list')
        self.assertEqual(listed['total'], 2)
        self.run_tool('break', op='clear', break_id=1)
        self.assertEqual(self.run_tool('break', op='list')['total'], 1)
        self.assertEqual(self.run_tool('break', break_id=None, op='clear')['cleared'], [2])
        self.assertEqual(self.run_tool('break', op='list')['total'], 0)

    def test_clear_unknown_break(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('break', op='clear', break_id=9)
        self.assertEqual(cm.exception.code, 'NO_BREAK')

    def test_fault_break_turns_on_the_crash_hold(self):
        out = self.run_tool('break', op='set', location='fault')
        self.assertEqual(out['break']['mechanism'], 'fault')

    def test_unknown_location(self):
        with self.assertRaises(fnv_conv.ConvError):
            self.run_tool('break', op='set', location='Nope::Missing')
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('break', op='set', location='src/actor.cpp:99')
        self.assertEqual(cm.exception.code, 'NO_CODE')


class StopTests(DebugTestBase):
    def test_status_running_without_stops(self):
        out = self.run_tool('status')
        self.assertEqual(out['state'], 'running')

    def test_status_names_function_source_and_reason(self):
        self.run_tool('break', op='set', location='src/actor.cpp:7')
        self.stop_bp()
        out = self.run_tool('status')
        self.assertEqual(out['state'], 'stopped')
        stop = out['stop']
        self.assertEqual(stop['function'], 'Actor::ApplyDamage')
        self.assertEqual(stop['source'], 'src/actor.cpp:7')
        self.assertEqual(stop['why'], 'break 1 (trap)')
        current = [line for line in stop['lines'] if line['current']]
        self.assertEqual([line['line'] for line in current], [7])

    def test_hook_stop_reason(self):
        self.link.stops = [{'seq': 2, 'kind': 'hook', 'id': 3, 'thread': '1000', 'main': True, 'eip': '%x' % CODE,
                            'frame': None, 'regs': '%x' % HOOK_BLOCK, 'context': None, 'exception': None}]
        self.dbg.breaks = {1: {'number': 1, 'mechanism': 'hook', 'id': 3, 'function': None, 'address': None,
                               'source': None, 'location': 'x', 'when': None, 'do': 'stop', 'log': None,
                               'thread': 'main'}}
        out = self.run_tool('status')
        self.assertEqual(out['stop']['why'], 'break 1 (hook on the function entry)')

    def test_stack_walks_the_ebp_chain(self):
        self.stop_bp()
        out = self.run_tool('stack')
        self.assertEqual(out['frames'][0]['function'], 'Actor::ApplyDamage')
        self.assertEqual(out['frames'][0]['source'], 'src/actor.cpp:7')
        self.assertEqual(out['frames'][1]['address'], '0x00402000')
        self.assertIsNone(out['frames'][1]['function'])

    def test_locals_are_decoded(self):
        self.stop_bp()
        names = {row['name']: row for row in self.run_tool('locals', frame=0)['locals']}
        self.assertEqual(names['afAmount']['value'], 2.5)
        self.assertEqual(names['owner']['value'], '0x30000000')
        self.assertIn('not decoded', names['pos']['note'])
        self.assertNotIn('count', names)
        self.assertEqual(names['this']['value'], '0x30000000')

    def test_eval_reads_a_field_through_the_layout(self):
        self.stop_bp()
        out = self.run_tool('eval', expression='this->m_fHealth')
        self.assertEqual(out['value'], 80.0)
        self.assertEqual(out['type'], 'float')

    def test_eval_rejects_calls(self):
        self.stop_bp()
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('eval', expression='Actor::Make()')
        self.assertEqual(cm.exception.code, 'CALL_UNSUPPORTED')

    def test_eval_register_and_memory(self):
        self.stop_bp()
        self.assertEqual(self.run_tool('eval', expression='$eax')['value'], 0x11)
        self.link.regions[0][1][0:4] = b'\x2a\x00\x00\x00'
        self.assertEqual(self.run_tool('eval', expression='[0x401000]:u32')['value'], 0x2A)

    def test_set_writes_a_local(self):
        self.stop_bp()
        out = self.run_tool('set', expression='afAmount', value='3')
        self.assertEqual(out['value'], 3)
        self.assertEqual(self.link.sent('wr')[-1][2], float_bytes(3.0).hex())

    def test_set_register_writes_the_context(self):
        self.stop_bp()
        self.run_tool('set', expression='$eax', value='0x55')
        self.assertEqual(self.link.sent('wr')[-1][1], '%x' % (CONTEXT + fnv_procmem.CONTEXT_OFFSETS['eax']))

    def test_continue_resumes_every_stop(self):
        self.stop_bp()
        out = self.run_tool('continue')
        self.assertEqual(out['state'], 'running')
        self.assertEqual(self.link.sent('resume')[-1][1:], ('ff', 'run'))

    def test_step_instruction_stops_again(self):
        self.stop_bp()
        self.link.on_resume = lambda op: setattr(self.link, 'stops', [stop_at(2, LINE7 + 1, kind='step')])
        out = self.run_tool('step', mode='instruction')
        self.assertEqual(out['stop']['kind'], 'step')
        self.assertEqual(self.link.sent('resume')[-1][1:3], ('1', 'step'))

    def test_return_at_the_first_instruction_uses_resume_ret(self):
        self.link.stops = [stop_at(1, CODE)]
        out = self.run_tool('return', value='5')
        self.assertEqual(out['state'], 'running')
        self.assertEqual(self.link.sent('resume')[-1][1:], ('1', 'ret', '5', '4'))   # afAmount is the only stack argument

    def test_return_inside_the_function_runs_its_epilogue(self):
        self.stop_bp()                                   # stopped at line 7, frame built
        out = self.run_tool('return', value='5')
        self.assertEqual(out['state'], 'running')
        self.assertEqual(out['epilogue'], '0x00401030')  # the closing brace (line 12)
        self.assertEqual(self.link.sent('resume')[-1][1:], ('1', 'eip', '401030'))

    def test_skip_needs_a_location_in_the_same_function(self):
        self.stop_bp()
        self.link.on_resume = None
        self.run_tool('skip', location='src/actor.cpp:9')
        self.assertEqual(self.link.sent('resume')[-1][2], 'eip')
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('skip', location='Actor::ApplyLimbDamage')
        self.assertEqual(cm.exception.code, 'NOT_SAME_FUNCTION')

    def test_hook_stop_reads_arguments_from_the_saved_block(self):
        block = bytearray(0x60)
        struct.pack_into('<I', block, 0x0C, 0)                       # ebp
        struct.pack_into('<I', block, 0x28, CALLER)                  # return address
        block[0x2C:0x30] = float_bytes(2.5)                          # argument 0: afAmount
        self.link.regions[-1] = (HOOK_BLOCK, block)
        self.link.stops = [{'seq': 4, 'kind': 'hook', 'id': 3, 'thread': '1000', 'main': True, 'eip': '%x' % CODE,
                            'frame': None, 'regs': '%x' % HOOK_BLOCK, 'context': None, 'exception': None}]
        names = {row['name']: row for row in self.run_tool('locals', frame=0)['locals']}
        self.assertEqual(names['afAmount']['value'], 2.5)
        self.assertIn('not set up', names['owner']['note'])
        frames = self.run_tool('stack')['frames']
        self.assertEqual(frames[1]['address'], '0x00402000')

    def test_frame_out_of_range(self):
        self.stop_bp()
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('locals', frame=9)
        self.assertEqual(cm.exception.code, 'NO_FRAME')


class ProcessTests(DebugTestBase):
    def test_threads_and_freeze_thaw(self):
        proc = mock.MagicMock()
        proc.__enter__.return_value = proc
        proc.suspend_all.return_value = [1001, 1002]
        proc.resume_all.return_value = [1001, 1002]
        proc.threads.return_value = [1000, 1001, 1002]
        proc.main_thread.return_value = 1000
        proc.thread_context.return_value = {'eip': CODE}
        with mock.patch('fnv_mcp.fnv_procmem.GameProcess', return_value=proc):
            self.assertEqual(self.run_tool('freeze')['state'], 'frozen')
            self.assertEqual(self.run_tool('status')['state'], 'frozen')
            self.assertEqual(self.run_tool('thaw')['state'], 'running')
            rows = self.run_tool('threads')['rows']
        self.assertEqual([r['main'] for r in rows], [True, False, False])
        self.assertEqual(rows[0]['eip'], '0x00401000')

    def test_dump_writes_a_minidump_into_the_state_folder(self):
        proc = mock.MagicMock()
        proc.__enter__.return_value = proc
        proc.suspend_all.return_value = []
        proc.minidump.return_value = 2048
        with mock.patch('fnv_mcp.fnv_procmem.GameProcess', return_value=proc):
            out = self.run_tool('dump', file='crash one')
        self.assertEqual(out['bytes'], 2048)
        self.assertTrue(out['path'].endswith('crash_one.dmp'))
        self.assertIn('dumps', out['path'])


if __name__ == '__main__':
    unittest.main()
