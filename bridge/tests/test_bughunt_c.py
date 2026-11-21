"""Bug hunt 2026-11-18, hunter C: scenario runner, agent, time, replay, coverage, control and render fixes."""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w1_support import W1Live  # noqa: E402
from fnv_mcp import fnv_time  # noqa: E402
from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_scenario import ScenarioError, ScenarioRunner, compare, _equal  # noqa: E402


class ScenarioLoadTests(unittest.TestCase):
    def setUp(self):
        self.runner = ScenarioRunner(W1Live())

    def _bad(self, data):
        with self.assertRaises(ScenarioError) as cm:
            self.runner.load(data=data)
        self.assertEqual(cm.exception.code, 'BAD_SCENARIO')

    def test_unknown_or_multi_key_step_is_refused_at_load(self):
        self._bad({'steps': [{'frobnicate': 1}]})
        self._bad({'steps': [{'wait': 1, 'step': 1}]})
        self._bad({'steps': ['wait']})

    def test_non_list_sections_and_bad_timeout_are_refused(self):
        self._bad({'steps': {'wait': 1}})
        self._bad({'steps': [], 'teardown': 'x'})
        self._bad({'steps': [], 'timeout': 0})
        self._bad({'steps': [], 'timeout': 'soon'})


class CompareTests(unittest.TestCase):
    def test_numeric_string_actual_equals_number(self):
        self.assertTrue(_equal('100', 100))
        self.assertFalse(_equal('abc', 100))

    def test_approx_with_text_actual_fails_cleanly(self):
        result = compare('abc', {'approx': 1, 'tolerance': 0.1})
        self.assertFalse(result[0] if isinstance(result, tuple) else result.get('passed'))

    def test_ordering_with_text_actual_fails_cleanly(self):
        result = compare('abc', {'gt': 1})
        self.assertFalse(result[0] if isinstance(result, tuple) else result.get('passed'))


class ScenarioRunTests(unittest.TestCase):
    def setUp(self):
        self.live = W1Live()
        self.runner = ScenarioRunner(self.live)
        for name, value in (('prepare', {'launched': False}), ('save_report', None), ('screenshot', None),
                            ('debug_bundle', {})):
            p = mock.patch.object(ScenarioRunner, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def test_wait_over_120_seconds_fails_instead_of_passing_capped(self):
        report = self.runner.run(data={'name': 'w', 'allow_menus': True, 'steps': [{'wait': {'seconds': 300}}]})
        self.assertFalse(report['passed'])

    def test_a_failed_teardown_step_fails_the_report(self):
        data = {'name': 't', 'allow_menus': True, 'steps': [],
                'teardown': [{'wait': {'seconds': 300}}]}
        report = self.runner.run(data=data)
        self.assertFalse(report['passed'])
        self.assertEqual(report['failures'][0]['phase'], 'teardown')

    def test_step_action_leaves_no_gate_held_after_the_run(self):
        self.runner.run(data={'name': 's', 'allow_menus': True, 'steps': [{'step': {'frames': 2}}]})
        self.assertFalse(self.live.link.held)

    def test_revert_all_without_a_marker_does_not_revert_everything(self):
        out = self.runner._dispatch('revert_all', True, None)
        self.assertTrue(out['passed'])
        self.assertEqual(self.live.reverted, [])


class TimeValidationTests(unittest.TestCase):
    def setUp(self):
        self.live = W1Live()
        self.tc = self.live.time_control

    def test_fractional_frames_are_refused_clearly(self):
        with self.assertRaises(Exception) as cm:
            self.tc.step(2.5)
        self.assertNotIsInstance(cm.exception, TypeError)

    def test_fractional_fps_is_refused_clearly(self):
        with self.assertRaises(Exception) as cm:
            self.tc.fixed(30.5)
        self.assertNotIsInstance(cm.exception, TypeError)

    def test_hold_timeout_must_be_positive(self):
        with self.assertRaises(Exception):
            self.tc.hold(timeout_seconds=-1)
        self.assertFalse(self.live.link.held)


class InjectWaitTests(unittest.TestCase):
    def test_wait_frames_at_the_job_maximum_stays_in_step_range(self):
        from fnv_mcp.fnv_inject import Injector
        live = W1Live()
        inj = Injector.of(live)
        waiter = mock.MagicMock()
        with mock.patch('fnv_mcp.fnv_inject.shared_time', return_value=(None, waiter)):
            inj.wait_frames(10000)
        self.assertLessEqual(waiter.run.call_args.kwargs['frames'], fnv_time.STEP_MAX)


class ReplayTests(unittest.TestCase):
    def test_play_refuses_a_path_name(self):
        from fnv_mcp.fnv_replay import ReplayTools
        tools = ReplayTools(W1Live())
        with self.assertRaises(ConvError) as cm:
            tools.play('..\\x')
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')


class ControlPatchTests(unittest.TestCase):
    def _tools(self):
        from fnv_mcp.fnv_control import ControlTools
        agent = SimpleNamespace(live=mock.MagicMock(), deep=mock.MagicMock())
        t = ControlTools(agent)
        t.live.reads.return_value = [b'\x90' * 5]
        t.deep.hooks.return_value = []
        t.deep._clean_bytes.return_value = b'\x90' * 5
        t.link.write.return_value = b'\x90' * 5
        t.live.engine.return_value.symbolize.return_value = {}
        t.live.journal.add.return_value = {'id': 1}
        return t

    def test_ret_out_of_range_is_a_value_error(self):
        with self.assertRaises(ValueError):
            self._tools().patch('0x00401000', ret=70000)

    def test_far_jump_wraps_rel32(self):
        t = self._tools()
        t.patch('0x00401000', jmp='0xF0000000', force=True)
        data = t.link.write.call_args.args[1]
        self.assertEqual(data[0], 0xE9)
        self.assertEqual(int.from_bytes(data[1:], 'little'), (0xF0000000 - 0x00401005) & 0xFFFFFFFF)

    def test_a_write_reaching_into_a_hook_is_refused(self):
        from fnv_mcp.fnv_live import LiveError
        t = self._tools()
        t.deep.hooks.return_value = [{'target': '0x00401003', 'stolen': 5, 'label': 'h'}]
        with self.assertRaises(LiveError) as cm:
            t.patch('0x00401000', bytes_hex='9090909090')
        self.assertEqual(cm.exception.code, 'HOOKED_HERE')


class RenderTokenTests(unittest.TestCase):
    def test_modifier_with_console_text_is_refused(self):
        from fnv_mcp.fnv_render import PLAIN_WORD
        self.assertIsNone(PLAIN_WORD.fullmatch('x\nquit'))
        self.assertIsNone(PLAIN_WORD.fullmatch('a;b'))
        self.assertIsNotNone(PLAIN_WORD.fullmatch('0011C532'))


if __name__ == '__main__':
    unittest.main()
