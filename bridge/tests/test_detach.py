"""Detach from one PC (2026-11-26): every location the tools need is found on the user's machine (a setting, an
environment variable or detection), never a path that only holds on the machine the tools were written on."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from fnv_mcp import locate

LIBRARY = '\t"%d"\n\t{\n\t\t"path"\t\t"%s"\n\t\t"apps"\n\t\t{\n\t\t\t"228980"\t\t"1"\n\t\t}\n\t}\n'


def no_registry(hive, key, name):
    return None


class GameFolderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def game(self, library):
        folder = library / 'steamapps' / 'common' / 'Fallout New Vegas'
        folder.mkdir(parents=True)
        (folder / 'FalloutNV.exe').write_bytes(b'MZ')
        return folder

    def steam(self, *libraries):
        steam = self.root / 'Steam'
        (steam / 'steamapps').mkdir(parents=True)
        text = '"libraryfolders"\n{\n%s}\n' % ''.join(
            LIBRARY % (i, str(p).replace('\\', '\\\\')) for i, p in enumerate(libraries))
        (steam / 'steamapps' / 'libraryfolders.vdf').write_text(text, encoding='utf-8')
        return steam

    def test_the_game_in_a_second_steam_library_is_found(self):
        second = self.root / 'D' / 'SteamLibrary'
        game = self.game(second)
        steam = self.steam(self.root / 'Steam', second)

        def registry(hive, key, name):
            return str(steam) if name == 'SteamPath' else None
        self.assertEqual(locate.find_game_root(registry), game)

    def test_the_games_own_registry_key_wins(self):
        game = self.game(self.root / 'elsewhere')

        def registry(hive, key, name):
            return str(game) + '\\' if name == 'Installed Path' else None
        self.assertEqual(locate.find_game_root(registry), game)

    def test_nothing_found_is_none_not_a_guess(self):
        self.assertIsNone(locate.find_game_root(no_registry))

    def test_a_library_without_the_game_is_skipped(self):
        steam = self.steam(self.root / 'Steam')

        def registry(hive, key, name):
            return str(steam) if name == 'SteamPath' else None
        self.assertIsNone(locate.find_game_root(registry))


class ProjectAutoTests(unittest.TestCase):
    def write(self, folder, game_root):
        path = Path(folder) / 'p.json'
        path.write_text(json.dumps({'project_id': 'p', 'game_root': game_root, 'state_dir': 'state'}), encoding='utf-8')
        return path

    def test_auto_takes_the_detected_folder(self):
        from fnv_mcp.config import Project
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(locate, 'find_game_root', return_value=Path(folder)):
                project = Project.load(self.write(folder, 'auto'))
            self.assertEqual(project.game_root, Path(folder).resolve())

    def test_auto_without_a_game_says_what_to_set(self):
        from fnv_mcp.config import Project
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(locate, 'find_game_root', return_value=None):
                with self.assertRaisesRegex(ValueError, 'game_root.*FalloutNV.exe'):
                    Project.load(self.write(folder, 'auto'))

    def test_the_examples_name_no_machine_path(self):
        projects = Path(__file__).resolve().parents[1] / 'projects'
        for path in sorted(projects.glob('example-*.json')):
            with self.subTest(path=path.name):
                data = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(data['game_root'], 'auto')
                for value in (data.get('fnv') or {}).values():
                    if isinstance(value, str):
                        self.assertFalse(Path(value).is_absolute(), value)


class ProjectFileTests(unittest.TestCase):
    def test_order_env_then_my_fnv_then_the_only_one(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            (folder / 'example-fnv.json').write_text('{}')
            with mock.patch.dict(os.environ, {'FNV_LIVE_PROJECT': ''}):
                self.assertIsNone(locate.project_file(folder))
                (folder / 'mine.json').write_text('{}')
                self.assertEqual(locate.project_file(folder), folder / 'mine.json')
                (folder / 'other.json').write_text('{}')
                self.assertIsNone(locate.project_file(folder))          # two: no guess
                (folder / 'my-fnv.json').write_text('{}')
                self.assertEqual(locate.project_file(folder), folder / 'my-fnv.json')
            with mock.patch.dict(os.environ, {'FNV_LIVE_PROJECT': str(folder / 'other.json')}):
                self.assertEqual(locate.project_file(folder), folder / 'other.json')

    def test_the_live_tools_default_names_no_private_file(self):
        root = Path(__file__).resolve().parents[1]
        for name in ('tools/fnv_live_regress.py', 'tests/live/base.py', 'tests/test_stab_s6b.py',
                     'tests/test_bugfix_a_c_s.py'):
            with self.subTest(name=name):
                self.assertNotIn('.windows.json', (root / name).read_text(encoding='utf-8'))


class ToolLocationTests(unittest.TestCase):
    def test_zig_on_path_is_found(self):
        from fnv_mcp import fnv_link
        with tempfile.TemporaryDirectory() as folder:
            zig = Path(folder) / 'zig.exe'
            zig.write_bytes(b'')
            env = {k: v for k, v in os.environ.items() if k not in ('FNV_MCP_ZIG', 'GECK_MCP_ZIG')}
            env['PATH'] = folder
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(fnv_link.find_zig(None), zig)

    def test_7zip_from_its_registry_key(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / '7z.exe').write_bytes(b'')

            def registry(hive, key, name):
                return folder + '\\' if key == r'SOFTWARE\7-Zip' else None
            with mock.patch.dict(os.environ, {'PATH': ''}):
                self.assertEqual(locate.find_7zip(registry), Path(folder) / '7z.exe')

    def test_clean_image_order(self):
        with tempfile.TemporaryDirectory() as home:
            env = {'FNV_CLEAN_EXE': '', 'USERPROFILE': home}
            with mock.patch.dict(os.environ, env):
                self.assertEqual(locate.clean_image(None), Path(home) / '.fnvdecomp' / 'FalloutNV.exe')
                own = SimpleNamespace(fnv=SimpleNamespace(clean_exe='X:/own.exe'))
                self.assertEqual(locate.clean_image(own), Path('X:/own.exe'))
            with mock.patch.dict(os.environ, {'FNV_CLEAN_EXE': 'Y:/env.exe'}):
                self.assertEqual(locate.clean_image(None), Path('Y:/env.exe'))

    def test_setup_check_reads_the_same_image_as_the_builder(self):
        """The unpacked-exe row finds FNV_CLEAN_EXE too, not only fnv.clean_exe."""
        from fnv_mcp import fnv_setup
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'FalloutNV.exe'
            image.write_bytes(b'MZ' + b'\0' * 100)
            project = SimpleNamespace(game_root=Path(folder), state_dir=Path(folder), fnv=None)
            with mock.patch.dict(os.environ, {'FNV_CLEAN_EXE': str(image), 'FNVSYM_ROOT': ''}):
                rows = {r['check']: r for r in fnv_setup.setup_check(project)['checks']}
            self.assertEqual(rows['unpacked exe (patch map)']['state'], 'ok')


class CompilerKitTests(unittest.TestCase):
    def test_a_missing_kit_names_its_variable(self):
        with mock.patch.dict(os.environ, {'FNV_MSVC9_ROOT': ''}):
            self.assertIn('FNV_MSVC9_ROOT', locate.compiler_problem('sp1', os_name='nt'))

    def test_a_kit_with_cl_is_fine(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'VC' / 'bin').mkdir(parents=True)
            (Path(folder) / 'VC' / 'bin' / 'cl.exe').write_bytes(b'')
            with mock.patch.dict(os.environ, {'FNV_MSVC9_RTM_ROOT': folder}):
                self.assertIsNone(locate.compiler_problem('rtm', os_name='nt'))
            with mock.patch.dict(os.environ, {'FNV_MSVC9_RTM_ROOT': folder + '-not'}):
                self.assertIn('cl.exe', locate.compiler_problem('rtm', os_name='nt'))

    def test_fnv_code_checks_the_kit_before_compiling(self):
        source = (Path(__file__).resolve().parents[1] / 'fnv_mcp' / 'fnv_code.py').read_text(encoding='utf-8')
        self.assertIn("compiler_problem(unit['compiler'])", source)
        self.assertIn("'COMPILER_MISSING'", source)

    def test_setup_check_has_a_kit_row(self):
        from fnv_mcp import fnv_setup
        with tempfile.TemporaryDirectory() as folder:
            project = SimpleNamespace(game_root=Path(folder), state_dir=Path(folder), fnv=None)
            with mock.patch.dict(os.environ, {'FNV_MSVC9_ROOT': '', 'FNVSYM_ROOT': ''}):
                rows = {r['check']: r for r in fnv_setup.setup_check(project)['checks']}
        self.assertEqual(rows['compiler kit (fnv_code)']['state'], 'optional')
        self.assertIn('FNV_MSVC9_ROOT', rows['compiler kit (fnv_code)']['fix'])


if __name__ == '__main__':
    unittest.main()
