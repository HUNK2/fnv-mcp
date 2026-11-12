"""N21: fnv_launch sent the coc / save load in the main menu's first frames; the main thread then sat ~170 s in
DirectSound calls (measured 2026-11-13: coc at the menu's frame ~2-20 -> ~190-200 s, three times; at frame 149 (3 s)
and 506 (10 s) -> in the cell at once; disk reads had settled in both cases). The launch now lets the menu run first."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from geck_mcp import fnv_game  # noqa: E402


class Clock:
    def __init__(self, fps=60.0):
        self.now = 0.0
        self.fps = fps

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def ping(self, timeout=3.0):
        return {'frame': 20 + int(self.now * self.fps), 'deferredInit': True}


class SettleTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.game = fnv_game.Game(self.fake.project)
        self.game.link = self.fake.link

    def settle(self, clock, deadline=1000):
        self.game.ping = clock.ping
        return self.game.wait_menu_settled(deadline, clock=clock, sleep=clock.sleep)

    def test_waits_for_the_menu_frames(self):
        clock = Clock(fps=60)
        out = self.settle(clock)
        self.assertTrue(out['settled'])
        self.assertGreaterEqual(out['frames'], fnv_game.Game.MENU_SETTLE_FRAMES)
        self.assertLess(clock.now, 6)

    def test_slow_frames_stop_at_the_time_cap(self):
        clock = Clock(fps=5)
        out = self.settle(clock)
        self.assertFalse(out['settled'])
        self.assertAlmostEqual(clock.now, fnv_game.Game.MENU_SETTLE_SECONDS, delta=0.6)

    def test_stops_at_the_deadline(self):
        clock = Clock(fps=1)
        self.settle(clock, deadline=2)
        self.assertLessEqual(clock.now, 2.5)

    def test_no_frame_number_does_not_block(self):
        clock = Clock()
        self.game.ping = lambda timeout=3.0: {'error': 'COMPANION_UNREACHABLE'}
        out = self.game.wait_menu_settled(1000, clock=clock, sleep=clock.sleep)
        self.assertFalse(out['settled'])
        self.assertLessEqual(clock.now, fnv_game.Game.MENU_SETTLE_SECONDS + 0.6)

    def test_start_from_menu_waits_before_sending_the_coc(self):
        order = []
        self.game.wait_menu_settled = lambda deadline, **kw: order.append('settle') or {'settled': True}
        real = self.fake.link.batch

        def batch(ops, timeout=None):
            order.extend(op[0] for op in ops)
            return real(ops, timeout)
        self.fake.link.batch = batch
        self.game.wait_in_game = lambda deadline, pid=None, start=None, **kw: {'phase': 'in_game'}
        out = self.game.start_from_menu('coc', 'HelloCourierRoom', deadline=10 ** 9, pid=4242)
        self.assertEqual(order[:3], ['settle', 'every', 'sched'])
        self.assertEqual(out['menuSettle'], {'settled': True})

    def test_save_start_waits_too(self):
        order = []
        self.game.wait_menu_settled = lambda deadline, **kw: order.append('settle') or {'settled': True}
        self.game.link.schedule = lambda slot, line: order.append('schedule') or {'ok': True}
        self.game.wait_in_game = lambda deadline, pid=None, start=None, **kw: {'phase': 'in_game'}
        self.game.start_from_menu('save', 'fnvmcp_test_x', deadline=10 ** 9, pid=4242)
        self.assertEqual(order, ['settle', 'schedule'])

    def test_menu_start_does_not_wait(self):
        self.game.wait_menu_settled = lambda *a, **kw: self.fail('a menu start has nothing to wait for')
        self.assertEqual(self.game.start_from_menu('menu', None, deadline=10 ** 9, pid=4242)['phase'], 'main_menu')


if __name__ == '__main__':
    unittest.main()
