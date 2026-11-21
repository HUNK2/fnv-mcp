"""Critique fix Task 3 (2026-11-21): a launch whose game hangs at startup (the companion's pipe answers, frame 0, no
deferredInit: the monitors-asleep deadlock in KNOWN-ISSUES) is ended after hang_after() seconds and started once
more, instead of waiting the whole timeout (6 of 95 real launches took 240 s each)."""
import sys
import time as real_time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from geck_mcp import fnv_display, fnv_game, fnv_layout  # noqa: E402


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def time(self):
        return 1.7e9 + self.t

    def sleep(self, seconds):
        self.t += max(seconds, 0.01)

    def __getattr__(self, name):
        return getattr(real_time, name)


HUNG = {'frame': 0, 'deferredInit': False, 'msSinceFrame': 0}
READY = {'frame': 500, 'deferredInit': True, 'msSinceFrame': 16}
NO_PIPE = {'error': 'PIPE_MISSING', 'message': 'no pipe'}


class HangTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.game = fnv_game.Game(self.fake.project)
        self.game.link = self.fake.link
        self.clock = FakeClock()
        self.alive = set()
        self.terminated = []
        self.current = None
        self.spawned = []
        self.behaviours = []          # one per start: function(seconds since that start) -> ping reply
        self.other_game = None
        g = self.game
        g.direct_program = lambda: Path('C:/Games/FalloutNV.exe')
        g.start_program = self._start
        g.process = lambda fresh=False: (self.current, 1) if self.current in self.alive else None
        g.ping = lambda timeout=3.0: self.behaviours[len(self.spawned) - 1](self.clock.t - self.spawned[-1][1])
        g.phase = lambda ping=None: ('main_menu', {})
        g.start_from_menu = lambda kind, value, deadline, pid: {'phase': 'main_menu', 'start': 'menu'}
        g.not_ready_checks = lambda layout, launched, pid: {'crash_log': None, 'companion_present': True,
                                                             'pipe_exists': True, 'window_hung': False,
                                                             'idle_seconds': 400}
        self.held, self.released = [], []
        for patch in (mock.patch.object(fnv_game, 'time', self.clock),
                      mock.patch.object(fnv_game, 'process_alive', side_effect=lambda pid: pid in self.alive),
                      mock.patch.object(fnv_game, 'terminate', side_effect=self._terminate),
                      mock.patch.object(fnv_game, 'window_hung', return_value=False),
                      mock.patch.object(fnv_display, 'hold', side_effect=lambda: self.held.append(1) or True),
                      mock.patch.object(fnv_display, 'release', side_effect=lambda: self.released.append(1))):
            patch.start()
            self.addCleanup(patch.stop)
        self.layout = SimpleNamespace(kind=fnv_layout.DIRECT, describe=lambda: {})

    def _start(self, exe, args, cwd, link_name=None):
        pid = 5000 + len(self.spawned)
        self.spawned.append((pid, self.clock.t))
        self.alive.add(pid)
        self.current = pid
        return 'test'

    def _terminate(self, pid):
        self.terminated.append(pid)
        self.alive.discard(pid)

    def launch(self, **kw):
        return self.game._launch('menu', kw.pop('timeout', 180), 'menu', None, layout=self.layout, **kw)

    # ------------------------------------------------------------------ the threshold
    def test_hang_after_has_a_floor(self):
        self.assertEqual(fnv_game.hang_after([]), 45)
        self.assertEqual(fnv_game.hang_after([12, 11, 14]), 45)

    def test_hang_after_follows_slow_machines(self):
        self.assertEqual(fnv_game.hang_after([40, 42, 38]), 120)

    def test_hang_after_uses_the_last_five(self):
        self.assertEqual(fnv_game.hang_after([100, 100, 100, 10, 10, 10, 10, 10]), 45)

    # ------------------------------------------------------------------ the launch
    def test_hung_start_is_ended_and_started_again_once(self):
        self.behaviours = [lambda s: HUNG, lambda s: READY if s > 12 else HUNG]
        out = self.launch()
        self.assertTrue(out['retried'])
        events = [e['event'] for e in out['timeline']]
        self.assertLess(events.index('startup_hung'), events.index('ready'))
        self.assertEqual(self.terminated, [5000])
        self.assertLess(out['seconds'], 90)

    def test_second_hang_fails_fast(self):
        self.behaviours = [lambda s: HUNG, lambda s: HUNG]
        start = self.clock.t
        with self.assertRaises(fnv_game.GameError) as cm:
            self.launch()
        self.assertEqual(cm.exception.code, 'COMPANION_NOT_READY')
        self.assertEqual(cm.exception.evidence['cause'], 'not_drawing')
        self.assertLess(self.clock.t - start, 2 * 45 + 20)

    def test_slow_start_inside_its_history_is_not_ended(self):
        self.game.save_session(readyTimes=[40, 42, 38])
        self.behaviours = [lambda s: READY if s > 100 else HUNG]
        out = self.launch()
        self.assertEqual(self.terminated, [])
        self.assertFalse(out['retried'])

    def test_no_pipe_is_not_a_hang(self):
        self.behaviours = [lambda s: READY if s > 60 else NO_PIPE]
        out = self.launch()
        self.assertEqual(self.terminated, [])
        self.assertFalse(out['retried'])

    def test_hang_recovery_ends_only_its_own_process(self):
        self.alive.add(4242)            # another FalloutNV.exe, not this launch's
        self.behaviours = [lambda s: HUNG, lambda s: READY if s > 5 else HUNG]
        self.launch()
        self.assertNotIn(4242, self.terminated)

    def test_retry_off_fails_at_the_threshold(self):
        self.behaviours = [lambda s: HUNG, lambda s: READY]
        start = self.clock.t
        with self.assertRaises(fnv_game.GameError) as cm:
            self.launch(retry_hang=False)
        self.assertEqual(cm.exception.code, 'COMPANION_NOT_READY')
        self.assertEqual(len(self.spawned), 1)
        self.assertLess(self.clock.t - start, 60)

    def test_project_setting_zero_turns_the_early_end_off(self):
        self.game.hang_seconds = lambda: 0
        self.behaviours = [lambda s: READY if s > 150 else HUNG]
        out = self.launch()
        self.assertEqual(self.terminated, [])
        self.assertFalse(out['retried'])

    def test_ready_times_are_kept(self):
        self.behaviours = [lambda s: READY if s > 10 else HUNG]
        self.launch()
        times = self.game.session()['readyTimes']
        self.assertEqual(len(times), 1)
        self.assertGreater(times[0], 9)

    def test_display_hold_is_released_on_quit(self):
        self.behaviours = [lambda s: READY]
        self.launch()
        self.assertEqual(self.held, [1])
        self.game.owns = lambda proc: True
        self.game._exited = lambda pid, seconds: (self.alive.discard(pid) or True)
        self.game.quit()
        self.assertEqual(self.released, [1])


class DisplayTests(unittest.TestCase):
    def test_hold_and_release_do_not_raise(self):
        try:
            fnv_display.hold()
        finally:
            fnv_display.release()
        self.assertFalse(fnv_display.held())


if __name__ == '__main__':
    unittest.main()
