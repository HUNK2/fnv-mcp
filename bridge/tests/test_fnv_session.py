"""fnv_session (start, mark, end, status) against the fake companion: the setup check, the launch or load through
ScenarioRunner.prepare (stubbed), the warning log, the deterministic start, the mark, and the end that reverts this
session's journal entries and releases the frame control."""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w1_support import W1Live  # noqa: E402
from geck_mcp import fnv_time  # noqa: E402
from geck_mcp.fnv_conv import ConvError  # noqa: E402
from geck_mcp.fnv_scenario import ScenarioRunner  # noqa: E402
from geck_mcp.fnv_session import SessionTools  # noqa: E402

READY = {'ready': True, 'missing': [], 'checks': []}


class SessionTestCase(unittest.TestCase):
    def setUp(self):
        self.live = W1Live()
        self.tools = SessionTools(self.live)
        self.warnings = mock.MagicMock()
        self.warnings.from_game.return_value.status.return_value = {'enabled': True}
        self.warnings.from_game.return_value.summary.return_value = {'byCategory': {}}
        self.warnings.from_game.return_value.enable.return_value = {'changed': False}
        for target, kwargs in (
                ('geck_mcp.fnv_session.setup_check', {'return_value': READY}),
                ('geck_mcp.fnv_session.warnings_for_live', {'new': self.warnings.from_game}),
                ('geck_mcp.fnv_deep.context_files', {'return_value': []})):
            patcher = mock.patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.prepare = mock.patch.object(ScenarioRunner, 'prepare', return_value={'launched': True})
        self.prepare.start()
        self.addCleanup(self.prepare.stop)


class SessionTests(SessionTestCase):
    def test_status_without_a_session_says_so(self):
        out = self.tools.run('status')
        self.assertFalse(out['open'])
        self.assertIn('fnv_session start', out['reason'])

    def test_start_opens_the_session_and_records_the_baseline(self):
        out = self.tools.run('start', start='continue', timeout_seconds=60)
        self.assertIsNotNone(self.live.session_state)
        self.assertTrue(out['game']['launched'])
        self.assertEqual(ScenarioRunner.prepare.call_args[0][0], {'start': 'continue', 'timeout': 60})
        self.assertEqual(out['baseline']['refs'], 0)
        self.assertEqual(self.live.session_state['journal'], 0)

    def test_start_twice_is_refused(self):
        self.tools.run('start')
        with self.assertRaises(ConvError) as cm:
            self.tools.run('start')
        self.assertEqual(cm.exception.code, 'SESSION_OPEN')
        self.assertIn('Next:', str(cm.exception))

    def test_start_refuses_when_the_setup_is_missing(self):
        with mock.patch('geck_mcp.fnv_session.setup_check',
                        return_value={'ready': False, 'missing': ['xNVSE'], 'checks': []}):
            with self.assertRaises(ConvError) as cm:
                self.tools.run('start')
        self.assertEqual(cm.exception.code, 'SETUP_MISSING')
        self.assertIsNone(getattr(self.live, 'session_state', None))

    def test_start_turns_the_warning_log_on_and_says_when_it_takes_effect(self):
        self.warnings.from_game.return_value.status.return_value = {'enabled': False}
        self.warnings.from_game.return_value.enable.return_value = {'changed': True, 'restartNeeded': True}
        out = self.tools.run('start')
        self.assertEqual(out['warnings'], {'enabled': False, 'changed': True, 'restartNeeded': True, 'capture': True})
        self.warnings.from_game.return_value.enable.assert_called_once()
        self.warnings.from_game.return_value.clear.assert_called()

    def test_a_deterministic_start_fixes_the_step_and_seeds_the_rng(self):
        agent = mock.MagicMock()
        with mock.patch.object(ScenarioRunner, 'agent', new_callable=mock.PropertyMock, return_value=agent):
            out = self.tools.run('start', deterministic=True)
        agent.rng.assert_called_with(seed=1)
        self.assertEqual(out['deterministic'], {'fps': 60, 'seed': 1})
        self.assertEqual(struct.unpack("<f", struct.pack("<I", self.live.link.u32(fnv_time.TIME_GLOBAL + 4)))[0],
                         struct.unpack("<f", struct.pack("<f", 1000.0 / 60))[0])   # a float: 1000 / 60 ms

    def test_mark_moves_the_mark_and_clears_the_warning_log(self):
        self.tools.run('start')
        out = self.tools.run('mark', label='after load')
        self.assertEqual(out['label'], 'after load')
        self.assertEqual(self.live.session_state['marks'][-1]['label'], 'after load')
        self.assertGreaterEqual(self.warnings.from_game.return_value.clear.call_count, 2)

    def test_mark_without_a_label_is_numbered(self):
        self.tools.run('start')
        self.assertEqual(self.tools.run('mark')['label'], 'mark 2')

    def test_end_reverts_this_sessions_journal_and_releases_the_frame_control(self):
        self.tools.run('start')
        out = self.tools.run('end')
        self.assertEqual(self.live.reverted, [0])
        self.assertIsNone(self.live.session_state)
        self.assertEqual(out['reverted'], {'reverted': 0})
        self.assertTrue(out['released']['released'])
        self.assertTrue(out['ok'])

    def test_end_quits_the_game_only_when_asked(self):
        self.tools.run('start')
        self.tools.run('end')
        self.assertEqual(self.live.game.quit_calls, 0)
        self.tools.run('start')
        self.tools.run('end', quit_game=True)
        self.assertEqual(self.live.game.quit_calls, 1)

    def test_end_reports_what_health_found(self):
        self.tools.run('start')
        self.warnings.from_game.return_value.summary.return_value = {'byCategory': {'missing file': 1}}
        out = self.tools.run('end')
        self.assertFalse(out['ok'])
        self.assertEqual(out['health']['items'][0]['kind'], 'warnings')

    def test_mark_and_end_without_a_session_need_a_start(self):
        for action in ('mark', 'end'):
            with self.subTest(action=action):
                with self.assertRaises(ConvError) as cm:
                    self.tools.run(action)
                self.assertEqual(cm.exception.code, 'NO_SESSION')

    def test_status_with_a_session_names_the_last_mark(self):
        self.tools.run('start')
        self.tools.run('mark', label='loaded')
        out = self.tools.run('status')
        self.assertTrue(out['open'])
        self.assertEqual(out['lastMark'], 'loaded')

    def test_an_unknown_action_lists_the_valid_ones(self):
        with self.assertRaises(ConvError) as cm:
            self.tools.run('pause')
        self.assertEqual(cm.exception.code, 'UNKNOWN_ACTION')
        self.assertIn('start', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
