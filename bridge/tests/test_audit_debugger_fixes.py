"""Audit fixes in the debugger, flow tracing and engine companion area (GitHub issues K5-K13, Q3, Q4).

The bridge side runs against the fake companion of test_fnv_debug; the companion side (K6, K7, K13) runs the real DLL
in the fake xNVSE host (skipped when no Zig is found). K8 and K11 have no offline test: K8 needs a fault stop whose
thread then hits a debug register, K11 a shadow stack of 256 nested hooked calls."""
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp import fnv_conv, fnv_flow, fnv_link  # noqa: E402
from fnv_mcp.fnv_debugger import MAIN_SLOT, compile_predicate  # noqa: E402
from fnv_mcp.fnv_link import word  # noqa: E402
from test_fnv_debug import CODE, DebugTestBase  # noqa: E402
from engine_host import StopHost, StopTestCase, build, hexint, needs_zig  # noqa: E402

PROLOGUE_LEN = 6  # hookme: 55 89 e5 83 ec 08


class TrapBreakThreadTests(DebugTestBase):
    """K5: a thread=main trap break that becomes int3 carries `main` in its condition."""

    def fill_hw(self):
        self.link.bps = {i: {'id': i, 'kind': 'hw'} for i in range(4)}
        self.dbg.breaks = {i: {'number': i, 'mechanism': 'hw', 'id': i} for i in range(4)}
        self.dbg.next_number = 5

    def test_int3_main_break_without_condition_uses_the_main_slot(self):
        self.fill_hw()
        out = self.run_tool('break', op='set', location='src/actor.cpp:7')
        self.assertEqual(out['break']['mechanism'], 'int3')
        self.assertEqual(self.bp_sets()[-1][5], word(MAIN_SLOT))
        self.assertIsNone(self.dbg.breaks[out['break']['number']]['predSlot'])

    def test_int3_main_break_with_condition_ands_main(self):
        self.fill_hw()
        out = self.run_tool('break', op='set', location='src/actor.cpp:7', when='frame > 5')
        slot = self.dbg.breaks[out['break']['number']]['predSlot']
        self.assertIsNotNone(slot)
        self.assertEqual(self.bp_sets()[-1][5], word(slot))
        self.assertEqual(out['break']['when'], 'frame > 5')      # the record keeps the user's condition
        main_and = compile_predicate('main and (frame > 5)')['hex']
        self.assertEqual(self.link.preds[slot], main_and)

    def test_hw_refused_falls_back_to_int3_with_main(self):
        original = self.link.one

        def one(*op, timeout=None):
            if op[:2] == ('bp', 'set') and op[4] == 'hw':
                self.link.ops.append(op)
                return {'ok': False, 'error': 'no_debug_register'}
            return original(*op, timeout=timeout)
        self.link.one = one
        out = self.run_tool('break', op='set', location='src/actor.cpp:7', when='frame > 5')
        self.assertEqual(out['break']['mechanism'], 'int3')
        sets = self.bp_sets()
        self.assertEqual([s[4] for s in sets], ['hw', 'int3'])
        slot = self.dbg.breaks[out['break']['number']]['predSlot']
        self.assertEqual(sets[1][5], word(slot))
        # the hw condition was given back and a `main and` one stored (in the same, freed slot)
        self.assertEqual(self.link.preds[slot], compile_predicate('main and (frame > 5)')['hex'])
        self.assertEqual(len(self.link.preds), 1)
        self.assertEqual(self.dbg._claimed_slots, {slot})

    def test_hw_main_break_keeps_its_plain_condition(self):
        self.run_tool('break', op='set', location='src/actor.cpp:7')
        self.assertEqual(self.bp_sets()[-1][4:6], ('hw', 'ff'))

    def test_thread_any_int3_has_no_main_condition(self):
        self.run_tool('break', op='set', location='src/actor.cpp:7', thread='any')
        self.assertEqual(self.bp_sets()[-1][4:7], ('int3', 'ff', '2'))


class RemovedHookTests(DebugTestBase):
    """Q3: a removed hook (stolen 0) still listed at the address does not turn a function break into a trap."""

    def test_function_break_after_a_removed_hook_is_a_hook_hold(self):
        self.link.hooks = [{'id': 9, 'label': 'dbg 1 Actor::ApplyDamage', 'target': '%x' % CODE, 'stolen': 0}]
        out = self.run_tool('break', op='set', location='Actor::ApplyDamage')
        self.assertEqual(out['break']['mechanism'], 'hook')
        self.assertNotIn('hookRefused', out['break'])

    def test_a_live_hook_still_refuses(self):
        self.link.hooks = [{'id': 9, 'label': 'watch', 'target': '%x' % CODE, 'stolen': 6}]
        out = self.run_tool('break', op='set', location='Actor::ApplyDamage')
        self.assertNotEqual(out['break']['mechanism'], 'hook')


class FailedUnhookTests(DebugTestBase):
    """K6 (bridge side): a hook hold whose unhook fails stays listed."""

    def fail_unhook(self, error='busy'):
        original = self.link.one

        def one(*op, timeout=None):
            if op[0] == 'unhook':
                self.link.ops.append(op)
                return {'ok': False, 'error': error}
            return original(*op, timeout=timeout)
        self.link.one = one

    def test_clear_one_keeps_the_break_and_says_so(self):
        out = self.run_tool('break', op='set', location='Actor::ApplyDamage', when='frame > 5')
        number, slot = out['break']['number'], self.dbg.breaks[out['break']['number']]['predSlot']
        self.fail_unhook()
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('break', op='clear', break_id=number)
        self.assertEqual(cm.exception.code, 'UNHOOK_FAILED')
        self.assertIn(number, self.dbg.breaks)
        self.assertIn(slot, self.link.preds)                      # its condition is still in place

    def test_clear_all_reports_the_break_left_armed(self):
        hold = self.run_tool('break', op='set', location='Actor::ApplyDamage', when='frame > 5')['break']['number']
        self.run_tool('break', op='set', location='src/actor.cpp:7')
        slot = self.dbg.breaks[hold]['predSlot']
        self.fail_unhook('overwritten_by_another_patch')
        out = self.run_tool('break', op='clear')
        self.assertEqual(list(self.dbg.breaks), [hold])
        self.assertEqual(out['notCleared'][0]['break'], hold)
        self.assertIn(slot, self.link.preds)                      # the sweep left its condition alone

    def test_an_already_removed_hook_is_cleared(self):
        number = self.run_tool('break', op='set', location='Actor::ApplyDamage')['break']['number']
        self.fail_unhook('already_removed')
        self.run_tool('break', op='clear', break_id=number)
        self.assertNotIn(number, self.dbg.breaks)


class FlowFixTests(DebugTestBase):
    """K9, K10, Q4 in fnv_flow."""

    FN = {'addr': CODE, 'size': 0x40, 'name': 'Actor::ApplyDamage'}

    def setUp(self):
        super().setUp()
        self.flow = fnv_flow.FlowTools(self.live, debugger=self.dbg, sleep=lambda s: None, clock=lambda: 0.0)

    def armed(self, seen=(), arm_wait=0):
        return self.flow._trace_armed(None, self.FN, 0, CODE + 0x40, None, 0, 10, arm_wait, None, [], [], '',
                                      seen)

    def test_k9_a_hit_just_before_the_clear_is_released(self):
        original = self.link.one
        late = {'seq': 41, 'kind': 'bp', 'id': 0, 'thread': '1000', 'main': True, 'eip': '%x' % CODE}

        def one(*op, timeout=None):
            result = original(*op, timeout=timeout)
            if op[:2] == ('bp', 'clear'):
                self.link.stops.append(late)          # parked between the last look and the clear
            if op[0] == 'resume':
                self.link.stops = [s for s in self.link.stops if s['seq'] != int(op[1], 16)]
            return result
        self.link.one = one
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.armed()
        self.assertEqual(cm.exception.code, 'NOT_CALLED')
        self.assertIn(('resume', word(41), 'run'), self.link.ops)
        self.assertEqual(self.link.stops, [])

    def test_k9_an_older_stop_with_the_id_is_left_alone(self):
        old = {'seq': 7, 'kind': 'bp', 'id': 0, 'thread': '1000', 'main': True, 'eip': '%x' % CODE}
        self.link.stops = [old]
        with self.assertRaises(fnv_conv.ConvError):
            self.armed(seen={7})
        self.assertFalse(self.link.sent('resume'))

    def test_k10_the_trace_ends_at_its_own_threads_step_stop(self):
        self.link.stops = [{'seq': 6, 'kind': 'step', 'id': 0, 'thread': '2000', 'eip': '%x' % CODE},
                           {'seq': 8, 'kind': 'step', 'id': 0, 'thread': '1000', 'eip': '%x' % CODE}]
        original = self.link.one

        def one(*op, timeout=None):
            if op[:2] == ('trace', 'read'):
                self.link.ops.append(op)
                return {'entries': [], 'done': True, 'reason': 'returned'}
            return original(*op, timeout=timeout)
        self.link.one = one
        ended = {}
        _, reason, done = self.flow._read_trace(5, ended, thread='1000')
        self.assertEqual((reason, done, ended['end']['seq']), ('returned', True, 8))
        self.assertEqual(self.link.sent('trace')[0][2], word(8))

    def test_q4_the_trace_uses_its_own_snapshot_not_the_shared_one(self):
        self.flow._seen_stops = {40}                  # what another trace's snapshot would have left
        looked = []
        self.flow._await_bp = lambda bp_id, wait, addr=None, seen=(): looked.append(set(seen))
        with self.assertRaises(fnv_conv.ConvError):
            self.armed(seen={3})
        self.assertTrue(looked)
        self.assertTrue(all(s == {3} for s in looked), looked)

    def test_q4_trace_keeps_no_snapshot_on_the_object(self):
        import inspect
        self.assertNotIn('self._seen_stops', inspect.getsource(fnv_flow.FlowTools))


@needs_zig
class CompanionAuditTests(StopTestCase):
    """K6, K7, K13 against the real companion in the fake host. One host per test (a test leaves hooks behind)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.receipt = build(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.host = StopHost(self.receipt, Path(self.receipt['dll']).parent, self.tmp.name, 120)
        self.addCleanup(self.host.close)
        self.target = self.host.t['hookme']
        result = self.host.link.one('hook', hexint(self.target), hexint(PROLOGUE_LEN), '-', hexint(0), hexint(0),
                                    hexint(0), hexint(0), hexint(1), '0', '0', hexint(0), hexint(0),
                                    fnv_link.hexs('hookme'))
        self.assertTrue(result.get('ok'), result)
        self.hook_id = result['hook']['id']

    def hook_row(self):
        return next(h for h in self.host.link.one('hooks')['hooks'] if h['id'] == self.hook_id)

    def skip(self, value):
        result = self.host.link.one('hookact', hexint(self.hook_id), '1', hexint(value), '0', '0', '-')
        self.assertTrue(result.get('ok'), result)

    def test_k6_a_failed_unhook_keeps_the_action(self):
        from test_engine_races import poke
        self.skip(77)
        jump = self.host.link.one('rd', hexint(self.target), '4')['hex']
        poke(self.host.process, self.target, 0x90909090)          # another patch over the hook's jump
        result = self.host.link.one('unhook', hexint(self.hook_id))
        self.assertEqual(result.get('error'), 'overwritten_by_another_patch')
        row = self.hook_row()
        self.assertEqual((row['action'], row['active'], row['stolen']), (1, True, PROLOGUE_LEN))
        poke(self.host.process, self.target, int.from_bytes(bytes.fromhex(jump), 'little'))
        self.host.link.schedule(0, 'hookme 5')
        self.assertTrue(self.wait_line('hookme 77'))               # the skip still runs
        self.assertTrue(self.host.link.one('unhook', hexint(self.hook_id)).get('ok'))

    def test_k7_a_new_skip_value_takes_effect(self):
        self.skip(77)
        self.host.link.schedule(0, 'hookme 5')
        self.assertTrue(self.wait_line('hookme 77'))
        self.skip(88)
        self.host.link.schedule(0, 'hookme 6')
        self.assertTrue(self.wait_line('hookme 88'))
        self.host.link.one('hookact', hexint(self.hook_id), '0', '0', '0', '0', '-')
        self.host.link.schedule(0, 'hookme 7')
        self.assertTrue(self.wait_line('hookme 1007'))

    def test_k13_a_hold_sent_while_a_main_stop_is_parked_survives_its_release(self):
        self.assertFalse(self.host.link.ping()['held'])
        self.hold(self.hook_id)
        self.host.link.schedule(0, 'hookme 5')
        stop = self.wait_stops()[0]
        self.assertTrue(stop['main'])
        self.host.link.one('hold', 'off')
        self.host.link.one('hold', 'on')
        self.disarm(self.hook_id)
        self.assertTrue(self.host.link.one('resume', hexint(stop['seq']), 'run')['ok'])
        self.assertTrue(self.wait_line('hookme 1005'))
        time.sleep(0.2)
        self.assertTrue(self.host.link.ping()['held'])             # the user's hold, not opened by the release
        self.host.link.one('hold', 'off')


if __name__ == '__main__':
    unittest.main()
