"""PLAN-3 S3.3: undos that read the state they restore (R7, N3, N9)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from fnv_mcp import fnv_eyes  # noqa: E402
from fnv_mcp.fnv_eyes import EyesTools  # noqa: E402

OSG = 0x00700000


class Cam:
    fov = 75.0


class CameraUndoTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live
        link = self.fake.link
        link.put(fnv_eyes.OS_GLOBALS, OSG.to_bytes(4, 'little'))
        link.put(OSG + fnv_eyes.FLY_FLAG, b'\x00')
        link.put(fnv_eyes.MENUS_SHOWN_FLAG, b'\x01')
        self.lines = []
        real = self.live.console

        def console(line, ref=0):
            self.lines.append(line)
            if line.startswith('tfc'):
                link.put(OSG + fnv_eyes.FLY_FLAG, b'\x00' if self.fly() else b'\x01')
            if line == 'tm':
                link.put(fnv_eyes.MENUS_SHOWN_FLAG, b'\x00' if link.get(fnv_eyes.MENUS_SHOWN_FLAG, 1)[0] else b'\x01')
            return real(line, ref)
        self.live.console = console

    def fly(self):
        return bool(self.fake.link.get(OSG + fnv_eyes.FLY_FLAG, 1)[0])

    def eyes(self):
        e = EyesTools(self.live, sleep=lambda s: None)
        e.camera = lambda: Cam()
        return e

    def test_camera_revert_after_reload_leaves_fly_off(self):
        first = self.eyes()
        first._remember()
        self.live.console('tfc 1')
        self.assertTrue(self.fly())
        second = self.eyes()                     # a supervisor reload: a new server, the same game
        second.restore()
        self.assertFalse(self.fly())
        self.live.revert_all()
        self.assertFalse(self.fly(), 'revert_all must not turn the fly camera back on (R7)')

    def test_revert_without_restore_turns_fly_off_once(self):
        self.eyes()._remember()
        self.live.console('tfc 1')
        self.eyes()                              # reload, no restore
        out = self.live.revert_all()
        self.assertEqual(out['reverted'], 1)
        self.assertFalse(self.fly())
        self.live.console('tfc 1')               # the user turns it on again: a repeated undo never toggles blind
        from fnv_mcp.fnv_live import UNDO_OPS
        UNDO_OPS['fly_set'](self.live, {}, {'op': 'fly_set', 'on': True})
        self.assertTrue(self.fly())

    def test_camera_change_after_undo_is_journaled(self):
        eyes = self.eyes()
        eyes._remember()
        self.live.undo()
        eyes._remember()
        cams = [e for e in self.live.journal.entries() if e['kind'] == 'camera']
        self.assertEqual(len(cams), 2, 'the camera change after an undo has its own entry (N3)')
        self.assertFalse(cams[1]['undone'])

    def test_hud_set_reads_the_flag(self):
        from fnv_mcp.fnv_live import UNDO_OPS
        UNDO_OPS['hud_set'](self.live, {}, {'op': 'hud_set', 'on': True})
        self.assertNotIn('tm', self.lines)       # already shown: nothing to do
        self.live.console('tm')
        UNDO_OPS['hud_set'](self.live, {}, {'op': 'hud_set', 'on': True})
        self.assertEqual(self.lines.count('tm'), 2)
        self.assertEqual(self.fake.link.get(fnv_eyes.MENUS_SHOWN_FLAG, 1), b'\x01')


class ActorUndoTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        live = self.live = self.fake.live
        self.state = {'disabled': 0, 'unconscious': 0, 'restrained': 0, 'count': 3}
        live.in_game = lambda: None
        live.resolve = lambda spec: {'ptr': 0x1000, 'formId': '00001000', 'expr': '00001000'}
        live.values = lambda exprs, ref=0: [self.read(e) for e in exprs]
        live.console = self.console

    def read(self, expr):
        if expr.startswith('GetItemCount'):
            return self.state['count']
        return {'GetDisabled': self.state['disabled'], 'GetUnconscious': self.state['unconscious'],
                'GetRestrained': self.state['restrained']}[expr]

    def console(self, line, ref=0):
        words = line.split()
        if line in ('Enable', 'Disable'):
            self.state['disabled'] = int(line == 'Disable')
        elif words[0] == 'SetUnconscious':
            self.state['unconscious'] = int(words[1])
        elif words[0] == 'SetRestrained':
            self.state['restrained'] = int(words[1])
        elif words[0] in ('AddItem', 'RemoveItem'):
            self.state['count'] += int(words[2]) * (1 if words[0] == 'AddItem' else -1)
        return {'line': line, 'accepted': True, 'compileError': False, 'output': []}

    def test_enable_undo_restores_prior_state_not_toggle(self):
        self.live.actor_control('00001000', 'enable')          # already enabled
        self.live.undo()
        self.assertEqual(self.state['disabled'], 0, 'undo of an enable that changed nothing must not disable')

    def test_disable_undo_enables(self):
        self.live.actor_control('00001000', 'disable')
        self.assertEqual(self.state['disabled'], 1)
        self.live.undo()
        self.assertEqual(self.state['disabled'], 0)

    def test_unconscious_undo_reads_the_flag(self):
        self.state['unconscious'] = 1
        self.live.actor_control('00001000', 'unconscious_on')
        self.live.undo()
        self.assertEqual(self.state['unconscious'], 1, 'it was unconscious before: stays so')

    def test_add_item_undo_restores_count(self):
        self.live.actor_control('00001000', 'add_item', target='Stimpak', value=2)
        self.assertEqual(self.state['count'], 5)
        self.state['count'] = 4                              # one was used since
        self.live.undo()
        self.assertEqual(self.state['count'], 3)


if __name__ == '__main__':
    unittest.main()
