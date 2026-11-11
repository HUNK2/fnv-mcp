"""fnv_flow (W2): trace and call refusals, argument checks, the engine-map guard, the hook guard and the predicate
text of the locals, against a fake companion. A full trace round trip needs the companion's trace op and a game, so
it is covered by the live checks; the bridge side of the round trip (the trace filed under the ending step stop) is
tested with a scripted fake."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp import fnv_conv, fnv_engine, fnv_flow  # noqa: E402
from test_fnv_debug import FakeLink  # noqa: E402

FUNC = 0x00401000
PROLOGUE = b'\x55\x8b\xec\x83\xec\x20' + b'\x90' * 18


class FlowEngine:
    def __init__(self, names):
        self.names = names

    def resolve(self, name):
        if name in self.names:
            return self.names[name]
        raise fnv_engine.EngineMapError('NOT_FOUND', 'No function named %s' % name)


def make_live(hooks=None):
    link = FakeLink([(FUNC, bytearray(PROLOGUE))])
    link.hooks = list(hooks or [])
    live = SimpleNamespace(link=link, engine=lambda: FlowEngine({'Actor::ApplyDamage': FUNC}),
                           ready=lambda: None, game=SimpleNamespace(process=lambda: (1234, 0)))
    live.reads = lambda spans: [bytes(link.region(a, n)[0][link.region(a, n)[1]:link.region(a, n)[1] + n])
                                if link.region(a, n)[0] is not None else None for a, n in spans]
    return live


class FlowTests(unittest.TestCase):
    def run_tool(self, live, action, **args):
        return fnv_flow.FlowTools(live, sleep=lambda s: None, clock=lambda: 0.0).run(action, **args)

    def test_unknown_action(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool(make_live(), 'jump')
        self.assertEqual(cm.exception.code, 'UNKNOWN_ACTION')

    def test_call_needs_a_function(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool(make_live(), 'call', function='')
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')

    def test_trace_depth_two_is_refused(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool(make_live(), 'trace', function='Actor::ApplyDamage', depth=2)
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')

    def test_call_fills_the_module_slot_then_arms_it(self):
        writes = []
        live = make_live()
        live.link.write = lambda address, data: writes.append((address, bytes(data)))
        live.resolve = lambda name: {'ptr': 0x12345678}
        tool = fnv_flow.FlowTools(live, sleep=lambda s: None, clock=lambda: 0.0,
                                  native=SimpleNamespace(flow_slot=lambda: 0x20000000))
        slot = tool._arm_call({'addr': FUNC}, ('0x00ABCDEF', [3, 1.5, 'player']))
        self.assertEqual(slot, 0x20000000)
        (body_at, body), (armed_at, armed) = writes
        self.assertEqual((body_at, armed_at, armed), (0x20000004, 0x20000000, struct.pack('<I', 1)))
        function, this, nargs = struct.unpack_from('<III', body, 0)
        self.assertEqual((function, this, nargs), (FUNC, 0x00ABCDEF, 3))
        self.assertEqual(struct.unpack_from('<3I', body, 12), (3, struct.unpack('<I', struct.pack('<f', 1.5))[0],
                                                               0x12345678))

    def test_depth_one_ranges_are_the_callees_with_lines(self):
        live = make_live()
        live.engine = lambda: SimpleNamespace(calls={FUNC: [0x500000, 0x600000, FUNC]})
        db = SimpleNamespace(
            function=lambda a: {0x500000: {'addr': 0x500000, 'size': 0x40, 'name': 'A'},
                                0x600000: {'addr': 0x600000, 'size': 0x20, 'name': 'B'},
                                FUNC: {'addr': FUNC, 'size': 0x10, 'name': 'F'}}.get(a),
            lines_of=lambda a: [] if a == 0x600000 else [(a, 1)])
        tool = fnv_flow.FlowTools(live, sleep=lambda s: None, clock=lambda: 0.0)
        self.assertEqual(tool._callee_ranges(db, {'addr': FUNC}), [(0x500000, 0x500040, 'A')])

    def test_trace_needs_a_function(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool(make_live(), 'trace', function='')
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')

    def test_trace_max_steps_out_of_range_refused(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool(make_live(), 'trace', function='Actor::ApplyDamage', max_steps=fnv_flow.MAX_TRACE + 1)
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')

    def test_engine_address_mismatch_refused(self):
        engine = FlowEngine({'Actor::ApplyDamage': FUNC + 0x10})
        with self.assertRaises(fnv_conv.ConvError) as cm:
            fnv_flow.engine_address(engine, 'Actor::ApplyDamage', FUNC)
        self.assertEqual(cm.exception.code, 'ENGINE_MAP_MISMATCH')

    def test_engine_address_missing_refused(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            fnv_flow.engine_address(FlowEngine({}), 'Nope::Nope', FUNC)
        self.assertEqual(cm.exception.code, 'ENGINE_MAP_MISMATCH')

    def test_hook_at_refuses_a_target_that_already_has_a_hook(self):
        live = make_live(hooks=[{'id': 4, 'label': 'watch', 'target': '%x' % FUNC, 'stolen': 6}])
        with self.assertRaises(fnv_conv.ConvError) as cm:
            fnv_flow.hook_at(live, FUNC, 'flow', 2)
        self.assertEqual(cm.exception.code, 'ALREADY_HOOKED')

    def test_hook_at_ignores_a_removed_hook_on_the_target(self):
        # the companion keeps removed hooks in its list with stolen = 0 (found live: re-hook after unhook refused)
        live = make_live(hooks=[{'id': 4, 'label': 'watch', 'target': '%x' % FUNC, 'stolen': 0}])
        hook = fnv_flow.hook_at(live, FUNC, 'flow', 2)
        self.assertIsNotNone(hook.get('id'))

    def test_hook_at_installs_with_the_label_and_argument_count(self):
        live = make_live()
        hook = fnv_flow.hook_at(live, FUNC, 'flow', 2)
        self.assertIsNotNone(hook.get('id'))
        op = live.link.sent('hook')[0]
        self.assertEqual(op[8], '2')
        self.assertEqual(bytes.fromhex(op[13]).decode('latin-1'), 'flow')

    def test_load_text_reads_a_local_at_its_ebp_offset(self):
        self.assertEqual(fnv_flow.load_text({'offset': -8}, 'f32'), 'reg.ebp.[0-8]:f32')
        self.assertEqual(fnv_flow.load_text({'offset': 8}, 'f32'), 'reg.ebp.[0+8]:f32')

    def test_scalar_decodes_float_bit_patterns(self):
        self.assertEqual(fnv_flow.scalar(2.5, 'f32'), 2.5)
        self.assertIsNone(fnv_flow.scalar(None, 'f32'))


class ScalarTests(unittest.TestCase):
    def test_hex_words_from_the_trace_ring(self):
        self.assertEqual(fnv_flow.scalar('0xffffffff', 'i32'), -1)
        self.assertEqual(fnv_flow.scalar('0x3f800000', 'f32'), 1.0)
        self.assertEqual(fnv_flow.scalar(7, 'u8'), 7)


class TraceRoundTripTests(unittest.TestCase):
    def test_trace_is_read_under_the_step_stop_that_ends_it(self):
        live = make_live()
        link = live.link
        stops = {'bp': [{'seq': 4, 'kind': 'bp', 'id': 0, 'main': True, 'thread': 1, 'eip': '0x00401000'}]}
        reads = []
        base_one = link.one

        def one(*op, timeout=None):
            if op[0] == 'stops':
                return {'stops': stops['bp']}
            if op[0] == 'resume' and op[2] == 'trace':
                self.assertEqual(op[1], '4')
                stops['bp'] = [{'seq': 5, 'kind': 'step', 'id': 255, 'main': True, 'thread': 1, 'eip': '0x00401010'}]
                return {'ok': True}
            if op[:2] == ('trace', 'read'):
                reads.append(op[2])
                if op[2] != '5':
                    return {'ok': False, 'error': 'no_trace'}
                return {'entries': [{'n': 1, 'eip': '0x00401000', 'values': []},
                                    {'n': 2, 'eip': '0x00401003', 'values': []}], 'next': 2, 'done': True,
                        'reason': 'returned'}
            if op[0] == 'resume':
                stops['bp'] = []
                return {'ok': True}
            return base_one(*op, timeout=timeout)
        link.one = one
        tools = fnv_flow.FlowTools(live, sleep=lambda s: None, clock=lambda: 0.0)
        db = SimpleNamespace(function=lambda a: {'addr': FUNC, 'size': 0x20, 'name': 'Actor::ApplyDamage'},
                             lines_of=lambda a: [{'addr': FUNC, 'line': 7, 'file': 'src/actor.cpp'}],
                             locals_of=lambda a: [], line_at=lambda a: None)
        tools.dbg._symdb = lambda required=False: db
        tools.dbg._resolve = lambda f: (FUNC, FUNC, None)
        tools._lines_of = lambda db, fn, entries, locals_: ([{'line': 7}], {'steps': len(entries)})
        out = tools.run('trace', function='Actor::ApplyDamage', max_steps=100)
        self.assertEqual(reads, ['5'])
        self.assertEqual(out['endedBy'], 'returned')
        self.assertEqual(out['reason'], 'The function returned.')
        self.assertTrue(out['done'])


if __name__ == '__main__':
    unittest.main()
