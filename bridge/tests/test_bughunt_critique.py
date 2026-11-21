"""Critique fix Task 4 (2026-11-21): tool bugs the robot project hit in real use.

4a fnv_logs failed whole (INTERNAL_ERROR) when the game held one log file open.
4d fnv_photo label rects came back in screen pixels while the picture is smaller.
4e fnv_plugin_build planned hook prologues from the running game, where a loaded module's hook had already
   replaced them, and said nothing when a module asks for hooks that the build did not plan."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from fnv_mcp import fnv_eyes, fnv_game, fnv_plugin_build  # noqa: E402
from fnv_mcp.fnv_conv import ConvError  # noqa: E402

PROLOGUE = b'\x55\x8b\xec\x83\xec\x10' + b'\x90' * 18
HOOKED = b'\xe9\x00\x10\x00\x00\x90' + b'\x90' * 18


class LockedLogTests(unittest.TestCase):
    def test_locked_log_does_not_fail_the_call(self):
        fake = FakeLive()
        self.addCleanup(fake.close)
        game = fnv_game.Game(fake.project)
        with tempfile.TemporaryDirectory() as tmp:
            locked, free = Path(tmp) / 'falloutnv_error.log', Path(tmp) / 'nvse.log'
            locked.write_text('held\n', encoding='utf-8')
            free.write_text('one\ntwo\n', encoding='utf-8')
            game.log_paths = lambda: {'error': locked, 'nvse': free}
            real = Path.read_bytes

            def read_bytes(path):
                if path == locked:
                    raise PermissionError(13, 'The process cannot access the file', str(path))
                return real(path)
            with mock.patch.object(fnv_game.Path, 'read_bytes', read_bytes), \
                    mock.patch.object(fnv_game, 'read_shared', side_effect=PermissionError(13, 'locked')):
                out = game.logs()
            self.assertTrue(out['error']['locked'])
            self.assertIn('fnv_game quit', out['error']['note'])
            self.assertEqual(out['nvse']['lines'], ['one', 'two'])
            with mock.patch.object(fnv_game.Path, 'read_bytes', read_bytes), \
                    mock.patch.object(fnv_game, 'read_shared', side_effect=PermissionError(13, 'locked')):
                again = game.logs()
            self.assertEqual(again['nvse']['lines'], [])          # its cursor moved
            self.assertTrue(again['error']['locked'])

    def test_a_file_the_plain_open_cannot_read_is_read_shared(self):
        fake = FakeLive()
        self.addCleanup(fake.close)
        game = fnv_game.Game(fake.project)
        with tempfile.TemporaryDirectory() as tmp:
            locked = Path(tmp) / 'falloutnv_havok.log'
            locked.write_text('a\nb\n', encoding='utf-8')
            game.log_paths = lambda: {'havok': locked}

            real = Path.read_bytes

            def read_bytes(path):
                if path == locked:
                    raise PermissionError(13, 'locked', str(path))
                return real(path)
            with mock.patch.object(fnv_game.Path, 'read_bytes', read_bytes):
                out = game.logs()
            self.assertEqual(out['havok']['lines'], ['a', 'b'])


class LabelTests(unittest.TestCase):
    def test_label_rects_are_in_image_pixels(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'view.png'
            Image.new('RGB', (960, 540)).save(path)
            eyes = fnv_eyes.EyesTools.__new__(fnv_eyes.EyesTools)
            eyes._hud_off = lambda hide: (lambda: None)
            eyes._shot = lambda name, size: {'path': str(path), 'size': [960, 540], 'method': 'engine'}
            eyes.look = lambda limit=30: {'refs': [{'formId': '0001ABCD', 'name': 'Crate', 'rect': [128, 72, 256, 144]}],
                                          'camera': {'screen': [1280, 720]}}
            out = eyes._photo_view(True, True, 960, 'view')
        self.assertEqual(out['labels'][0]['rect'], [96, 54, 192, 108])
        self.assertEqual(out['rectSpace'], 'image')


class PluginPlanTests(unittest.TestCase):
    def tools(self, game_running=True, image=True, hooked=()):
        link = SimpleNamespace(one=lambda *op, **kw: {'ok': True, 'hooks': [
            {'id': 1, 'target': '0x%08X' % t, 'stolen': 6} for t in hooked]} if op[0] == 'hooks' else {'ok': True})
        live = SimpleNamespace(link=link, game=SimpleNamespace(process=lambda fresh=False: (1, 1) if game_running else None),
                               reads=lambda spans: [HOOKED if hooked else PROLOGUE],
                               engine=lambda: SimpleNamespace(resolve=lambda n: 0x00401000),
                               project=SimpleNamespace(state_dir=tempfile.gettempdir()))
        t = fnv_plugin_build.PluginTools(live)
        if image:
            t._image_code = lambda address, length=24: PROLOGUE
        else:
            def none(address, length=24):
                raise ConvError('NO_CODE', 'no image')
            t._image_code = none
        return t

    def test_plan_reads_the_clean_image_even_when_the_game_runs(self):
        _, plans = self.tools(hooked=[0x00401000])._plans(['0x00401000'])
        self.assertEqual(plans[0]['from'], 'local game image')
        self.assertEqual(plans[0]['stolen'], 6)

    def test_hooked_function_in_the_running_game_is_refused(self):
        with self.assertRaises(ConvError) as cm:
            self.tools(image=False, hooked=[0x00401000])._plans(['0x00401000'])
        self.assertEqual(cm.exception.code, 'PROLOGUE_HOOKED')
        self.assertIn('fnv.clean_exe', str(cm.exception))
        self.assertIn('unload', str(cm.exception))

    def test_unhooked_running_game_is_still_used_without_an_image(self):
        _, plans = self.tools(image=False)._plans(['0x00401000'])
        self.assertEqual(plans[0]['from'], 'running game')

    def test_build_without_hooks_says_none_were_planned(self):
        t = self.tools()
        self.assertIn('hooks', t._hooks_warning('void f(void){ api->hook(FNV_ADDR_X, h, 0, 0); }', None))
        self.assertIsNone(t._hooks_warning('void f(void){ api->log("x"); }', None))
        self.assertIsNone(t._hooks_warning('api->hook(1, h, 0, 0);', ['0x00401000']))


class GroundTests(unittest.TestCase):
    """4b: fnv_raycast ground_at returned one value per cell (6360.0 at two points 2,000 units apart, live)."""

    def ai(self, hits):
        from fnv_mcp import fnv_engine_ai
        ai = fnv_engine_ai.EngineAITools(SimpleNamespace(live=SimpleNamespace(in_game=lambda: None), link=None))
        self.cast = []

        class Rays:
            def cast(inner, segments, refs=False):
                # One segment per cast; `hits` is one hit for every cast, or a list taken in order.
                self.cast.append(segments)
                return [hits.pop(0) if isinstance(hits, list) else hits for _ in segments]
        return ai, Rays()

    def test_ground_at_not_found_says_so(self):
        ai, rays = self.ai({'hit': False})
        out = ai.ground_height(100.0, 200.0, z=50.0, rays=rays)
        self.assertFalse(out['found'])
        self.assertIsNone(out['terrainHeight'])
        self.assertTrue(out['note'])

    def test_ground_at_is_a_ray_straight_down_from_above(self):
        ai, rays = self.ai({'hit': True, 'point': (100.0, 200.0, 40.5), 'ref': 0})
        out = ai.ground_height(100.0, 200.0, z=50.0, rays=rays)
        (start, end) = self.cast[0][0]
        self.assertEqual((start[0], start[1], end[0], end[1]), (100.0, 200.0, 100.0, 200.0))
        self.assertGreater(start[2], 50.0)
        self.assertLess(end[2], 40.5)
        self.assertEqual(out['terrainHeight'], 40.5)

    def test_a_hit_on_an_object_is_not_terrain(self):
        ai, rays = self.ai({'hit': True, 'point': (1.0, 2.0, 300.0), 'ref': 0x12345678})
        out = ai.ground_height(1.0, 2.0, z=0.0, rays=rays)
        self.assertIsNone(out['terrainHeight'])
        self.assertEqual(out['groundHeight'], 300.0)
        self.assertEqual(out['onRef'], '0x12345678')

    PLAYER = 0x18532BE0

    def test_a_hit_on_the_player_is_passed_through(self):
        # Live 2026-11-21: straight down at the player's own (x, y), the first hit was the player's capsule
        # 128.7 units over its feet (ref = the PlayerCharacter); 30 units aside the floor was found.
        ai, rays = self.ai([{'hit': True, 'point': (1.0, 2.0, 129.3), 'ref': self.PLAYER},
                            {'hit': True, 'point': (1.0, 2.0, 0.0), 'ref': 0x41192B60}])
        out = ai.ground_height(1.0, 2.0, z=0.6, rays=rays, player=(self.PLAYER, (1.0, 2.0, 0.6)))
        self.assertEqual(out['groundHeight'], 0.0)
        self.assertEqual(out['onRef'], '0x41192B60')
        self.assertLess(self.cast[1][0][0][2], 129.3)

    def test_the_next_ray_starts_inside_the_capsule(self):
        # Live 2026-11-21, Goodsprings: one unit under the first player hit the ray met the player again (101 over
        # its feet); four casts later it was still on the player. The next ray starts just over the feet.
        ai, rays = self.ai([{'hit': True, 'point': (1.0, 2.0, 6510.0), 'ref': self.PLAYER},
                            {'hit': True, 'point': (1.0, 2.0, 6380.0), 'ref': 0}])
        out = ai.ground_height(1.0, 2.0, z=6380.9, rays=rays, player=(self.PLAYER, (1.0, 2.0, 6380.9)))
        self.assertLessEqual(self.cast[1][0][0][2], 6380.9 + 2.0)
        self.assertEqual(out['terrainHeight'], 6380.0)

    def test_a_hit_in_the_players_column_without_a_ref_is_passed_through(self):
        # Live 2026-11-21, Goodsprings: the same capsule hit came back with no reference (6500.64 over feet at
        # 6367.08) and was reported as the terrain.
        ai, rays = self.ai([{'hit': True, 'point': (1.0, 2.0, 6500.64), 'ref': 0},
                            {'hit': True, 'point': (1.0, 2.0, 6366.5), 'ref': 0}])
        out = ai.ground_height(1.0, 2.0, z=6367.08, rays=rays, player=(self.PLAYER, (1.0, 2.0, 6367.08)))
        self.assertEqual(out['terrainHeight'], 6366.5)

    def test_away_from_the_player_the_first_hit_stands(self):
        ai, rays = self.ai([{'hit': True, 'point': (900.0, 2.0, 120.0), 'ref': 0}])
        out = ai.ground_height(900.0, 2.0, z=0.0, rays=rays, player=(self.PLAYER, (1.0, 2.0, 0.0)))
        self.assertEqual(out['terrainHeight'], 120.0)
        self.assertEqual(len(self.cast), 1)

class UnfocusedInputTests(unittest.TestCase):
    """4c, found live 2026-11-21: a duration hold (xNVSE HoldKey) said accepted and moved nothing while the game
    window was not focused (DirectInput ignores keys then); the frame injector works either way."""

    def live(self, focused):
        from fnv_mcp import fnv_live
        sent = self.sent = []

        class Live(fnv_live.LiveService):
            link = SimpleNamespace(batch=lambda ops: sent.append(ops) or [{'accepted': True}])

        live = Live.__new__(Live)
        live.in_game = lambda: None
        live.changing = lambda reason: None
        live.journal = SimpleNamespace(add=lambda e: {'id': 1})
        live._scan_of = lambda control: 0x11
        live._game_focused = lambda: focused
        self.injected = []
        live.inject = lambda *a: self.injected.append(a) or {'injected': True}
        return live

    def test_unfocused_hold_goes_through_the_injector(self):
        out = self.live(False).input('hold', control='forward', duration_ms=500)
        self.assertEqual(self.injected, [('hold', None, 0x11, 30, None, None)])
        self.assertEqual(self.sent, [])
        self.assertIn('not focused', out['via'])

    def test_unfocused_tap_goes_through_the_injector(self):
        self.live(False).input('tap', control='activate')
        self.assertEqual(self.injected, [('tap', None, 0x11, 2, None, None)])

    def test_focused_hold_keeps_the_key_commands(self):
        out = self.live(True).input('hold', control='forward', duration_ms=500)
        self.assertEqual(self.injected, [])
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(out['accepted'])


if __name__ == '__main__':
    unittest.main()
