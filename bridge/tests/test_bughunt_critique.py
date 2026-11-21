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
from geck_mcp import fnv_eyes, fnv_game, fnv_plugin_build  # noqa: E402
from geck_mcp.fnv_conv import ConvError  # noqa: E402

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


if __name__ == '__main__':
    unittest.main()
