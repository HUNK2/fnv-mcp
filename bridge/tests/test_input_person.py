"""Input as a person gives it (tool gap 7, 2026-11-24): a frame-exact control hold also holds the key or mouse button
bound to it, in the same companion batch (GetControlState and IsControlPressed read the key bytes, not the forced
state); fnv_window background loads the bundled nopause module and foreground unloads it. No game, no pipe."""
from pathlib import Path
from unittest import mock
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from w1_support import W1Live  # noqa: E402
from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_inject import Injector  # noqa: E402
from fnv_mcp.fnv_live import LiveError, LiveService  # noqa: E402
from fnv_mcp.fnv_world import WorldTools  # noqa: E402

NOPAUSE = Path(__file__).resolve().parents[1] / 'native' / 'modules' / 'fnvmcp_nopause.c'


class BoundKeyTests(unittest.TestCase):
    def setUp(self):
        self.live = W1Live()
        self.inj = Injector.of(self.live)
        self.link = self.live.link

    def test_a_control_hold_holds_its_bound_key_in_the_same_batch(self):
        batches = []
        real = self.link.batch
        self.link.batch = lambda ops, timeout=None: (batches.append(list(ops)), real(ops, timeout=timeout))[1]
        out = self.inj.control(0, 5, wait=False, scan=17)          # forward, bound to W (DIK 0x11)
        self.assertEqual([(j['kind'], j['args']) for j in self.link.inp_jobs],
                         [('control', ['0', '5']), ('key', ['11', '5'])])
        self.assertEqual(len(batches[-1]), 2)
        self.assertEqual(out['boundKey'], 17)

    def test_a_control_bound_to_a_mouse_button_holds_the_button(self):
        out = self.inj.control(4, 3, wait=False, scan=256)          # attack, left mouse button
        self.assertEqual([j['kind'] for j in self.link.inp_jobs], ['control', 'button'])
        self.assertEqual(self.link.inp_jobs[1]['args'], ['0', '3'])
        self.assertEqual(out['boundKey'], 'mouse button 0')

    def test_an_unbound_control_is_the_force_call_alone(self):
        out = self.inj.control(5, 2, wait=False)
        self.assertEqual([j['kind'] for j in self.link.inp_jobs], ['control'])
        self.assertIsNone(out['boundKey'])

    def test_a_refused_bound_key_clears_the_control_too(self):
        real = self.link.op

        def op(name, a):
            if name == 'inp' and a and a[0] == 'key':
                return {'ok': False, 'error': 'too_many_jobs'}
            return real(name, a)
        self.link.op = op
        with self.assertRaises(ConvError) as cm:
            self.inj.control(0, 5, wait=False, scan=17)
        self.assertEqual(cm.exception.code, 'INPUT_REFUSED')
        self.assertEqual(self.link.inp_jobs, [])

    def test_inject_looks_up_the_bound_key_of_a_control(self):
        live = mock.Mock()
        live._scan_of.return_value = 17
        injector = mock.Mock()
        injector.control.return_value = {'queued': True}
        with mock.patch('fnv_mcp.fnv_inject.Injector.of', return_value=injector):
            LiveService.inject(live, 'hold', 'forward', None, 5, None, None)
        injector.control.assert_called_once_with(0, 5, scan=17)
        live._scan_of.side_effect = LiveError('UNBOUND_CONTROL', 'x')
        injector.reset_mock()
        with mock.patch('fnv_mcp.fnv_inject.Injector.of', return_value=injector):
            LiveService.inject(live, 'hold', 'activate', None, 5, None, None)
        injector.control.assert_called_once_with(5, 5, scan=None)


class BackgroundWindowTests(unittest.TestCase):
    def world(self, loaded=False):
        agent = mock.Mock()
        agent.game.process.return_value = (1, 'FalloutNV.exe')
        agent.menus.return_value = {'visible': []}
        agent.live.u32s.return_value = [0]
        world = WorldTools(agent)
        world.native = mock.Mock()
        world.native.is_loaded.return_value = loaded
        world.native.bundled.return_value = not loaded
        world._game_window = lambda: 0
        world._read_background_mouse = lambda: None
        return world

    def test_background_loads_the_bundled_module(self):
        world = self.world()
        out = world.window('background')
        world.native.bundled.assert_called_once_with('fnvmcp_nopause')
        self.assertIn('loaded', out['noPause'])
        self.assertNotIn('note', out)

    def test_background_twice_says_already_on(self):
        out = self.world(loaded=True).window('background')
        self.assertEqual(out['noPause'], 'already on')

    def test_foreground_unloads_it(self):
        world = self.world(loaded=True)
        out = world.window('foreground')
        world.native.unload.assert_called_once_with('fnvmcp_nopause')
        self.assertIn('unloaded', out['noPause'])
        self.assertTrue(out['state']['background'])        # the mock still says loaded; status reads it

    def test_foreground_when_off_changes_nothing(self):
        world = self.world()
        out = world.window('foreground')
        world.native.unload.assert_not_called()
        self.assertEqual(out['note'], 'background was not on')
        self.assertFalse(out['state']['background'])

    def test_background_closes_an_open_pause_menu(self):
        world = self.world()
        world.agent.menus.return_value = {'visible': [{'id': 1013, 'name': 'StartMenu'}]}
        out = world.window('background')
        world.agent.menu_control.assert_called_once_with('close', menu=1013)
        self.assertEqual(out['closed'], 'StartMenu (pause)')

    def test_an_unknown_action_is_refused(self):
        with self.assertRaises(ValueError):
            self.world().window('minimise')

    def test_the_module_skips_only_the_inactive_activate_message(self):
        text = NOPAUSE.read_text(encoding='utf-8')
        self.assertIn('0x0086A0A0u', text)
        self.assertIn('f->arg[1] == WM_ACTIVATE_MSG && (f->arg[2] & 0xFFFFu) == 0', text)
        self.assertIn('A->hook(FN_MAIN_WNDPROC, on_wndproc, 0, 16)', text)     # stdcall: 4 arguments popped


if __name__ == '__main__':
    unittest.main()
