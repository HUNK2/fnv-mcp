"""A game started without Mod Organizer 2 (Steam's Play button, or nvse_loader.exe in the game folder).

The tools work out which kind of install a game is (fnv_layout): the MO2 dev profile, or the direct install in the
game folder with the user's own Documents\\My Games\\FalloutNV and %LOCALAPPDATA%\\FalloutNV. A running game is
judged by whether MO2's file system DLL is loaded in it; with no game running, by the project file. A direct install
is the user's real game, so the tools that write into its Data folder, its plugins.txt or ini, or load or overwrite
its saves refuse unless fnv.allow_direct_install_changes is true."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp import fnv_game, fnv_layout, fnv_link  # noqa: E402
from geck_mcp.config import Project  # noqa: E402


def write_plugin(path, masters=(), esm=False, mtime=None):
    """A minimal TES4 header (FNV layout: 24-byte record header) with MAST subrecords."""
    import struct
    body = b''
    for m in masters:
        name = m.encode() + b'\0'
        body += b'MAST' + struct.pack('<H', len(name)) + name + b'DATA' + struct.pack('<H', 8) + b'\0' * 8
    flags = 1 if esm else 0
    head = b'TES4' + struct.pack('<IIIII', len(body), flags, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + body)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def direct_project(root, allow=False, control='full', mo2=False):
    root = Path(root)
    game = root / 'game'
    (game / 'Data').mkdir(parents=True, exist_ok=True)
    user = root / 'mygames'
    (user / 'Saves').mkdir(parents=True, exist_ok=True)
    appdata = root / 'appdata'
    appdata.mkdir(parents=True, exist_ok=True)
    fnv = {'engine_control': control, 'game_user_dir': str(user), 'game_appdata_dir': str(appdata),
           'allow_direct_install_changes': allow}
    if mo2:
        (root / 'mo2/profiles/Dev/saves').mkdir(parents=True, exist_ok=True)
        (root / 'mo2/profiles/Dev/modlist.txt').write_text('+Other Mod\n', encoding='utf-8')
        fnv.update(mo2_root=str(root / 'mo2'), mo2_profile='Dev')
    return Project.model_validate({'project_id': 'direct', 'game_root': str(game), 'state_dir': str(root / 'state'),
                                   'fnv': fnv})


class Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        fnv_layout.forget()

    def tearDown(self):
        fnv_layout.forget()
        self._tmp.cleanup()


# ------------------------------------------------------------------------------------------------ the layout
class ConfiguredLayoutTests(Tmp):
    def test_a_project_without_mo2_is_a_direct_install(self):
        layout = fnv_layout.configured(direct_project(self.root))
        self.assertEqual(layout.kind, 'direct')
        self.assertEqual(layout.saves_dir, self.root / 'mygames' / 'Saves')
        self.assertEqual(layout.ini, self.root / 'mygames' / 'Fallout.ini')
        self.assertEqual(layout.plugins_txt, self.root / 'appdata' / 'plugins.txt')
        self.assertEqual(layout.nvse_plugins_dir, self.root / 'game' / 'Data' / 'NVSE' / 'Plugins')
        self.assertIsNone(layout.modlist)
        self.assertEqual(layout.loose_roots(), [self.root / 'game' / 'Data'])

    def test_a_project_with_mo2_is_the_dev_profile(self):
        layout = fnv_layout.configured(direct_project(self.root, mo2=True))
        self.assertEqual(layout.kind, 'mo2')
        profile = self.root / 'mo2' / 'profiles' / 'Dev'
        self.assertEqual(layout.saves_dir, profile / 'saves')
        self.assertEqual(layout.ini, profile / 'Fallout.ini')
        self.assertEqual(layout.plugins_txt, profile / 'plugins.txt')
        self.assertEqual(layout.modlist, profile / 'modlist.txt')

    def test_the_ini_save_folder_setting_is_followed(self):
        project = direct_project(self.root)
        (self.root / 'mygames' / 'Fallout.ini').write_text('[General]\nSLocalSavePath=Saves\\Dev\\\n', encoding='utf-8')
        self.assertEqual(fnv_layout.configured(project).saves_dir, self.root / 'mygames' / 'Saves' / 'Dev')

    def test_the_default_folders_are_the_users_own(self):
        project = Project.model_validate({'project_id': 'p', 'game_root': str(self.root), 'state_dir': str(self.root)})
        with mock.patch.dict(os.environ, {'LOCALAPPDATA': str(self.root / 'lad')}), \
                mock.patch.object(fnv_layout, 'documents_dir', lambda: self.root / 'docs'):
            layout = fnv_layout.configured(project)
        self.assertEqual(layout.user_dir, self.root / 'docs' / 'My Games' / 'FalloutNV')
        self.assertEqual(layout.appdata_dir, self.root / 'lad' / 'FalloutNV')

    def test_game_no_longer_needs_mo2_settings(self):
        game = fnv_game.Game(direct_project(self.root))
        self.assertIsNone(game.mo2_root)
        self.assertEqual(game.saves_dir, self.root / 'mygames' / 'Saves')


class RunningLayoutTests(Tmp):
    def running(self, project, modules, image=None):
        image = image or str(Path(project.game_root) / 'FalloutNV.exe')
        with mock.patch.object(fnv_layout, 'process_image', lambda pid: image), \
                mock.patch.object(fnv_layout, 'process_modules', lambda pid: modules):
            return fnv_layout.for_running(project, (77, 123))

    def test_a_game_with_usvfs_loaded_runs_under_mo2(self):
        layout = self.running(direct_project(self.root, mo2=True), ['FalloutNV.exe', 'usvfs_x86.dll', 'nvse_1_4.dll'])
        self.assertEqual(layout.kind, 'mo2')
        self.assertIn('usvfs', layout.detected_by)

    def test_a_game_without_usvfs_is_the_direct_install_even_with_mo2_configured(self):
        layout = self.running(direct_project(self.root, mo2=True), ['FalloutNV.exe', 'nvse_1_4.dll'])
        self.assertEqual(layout.kind, 'direct')
        self.assertEqual(layout.saves_dir, self.root / 'mygames' / 'Saves')

    def test_a_game_from_another_folder_is_not_judged(self):
        layout = self.running(direct_project(self.root), ['usvfs_x86.dll'], image=r'C:\elsewhere\FalloutNV.exe')
        self.assertIsNone(layout)

    def test_mo2_without_settings_names_the_missing_keys(self):
        layout = self.running(direct_project(self.root), ['usvfs_x86.dll'])
        self.assertEqual(layout.kind, 'mo2')
        with self.assertRaises(fnv_layout.LayoutError) as caught:
            layout.saves_dir
        self.assertEqual(caught.exception.code, 'NOT_CONFIGURED')
        self.assertIn('mo2_root', str(caught.exception))

    def test_the_answer_is_kept_per_process(self):
        project = direct_project(self.root)
        calls = []

        def modules(pid):
            calls.append(pid)
            return ['FalloutNV.exe']
        with mock.patch.object(fnv_layout, 'process_image', lambda pid: str(Path(project.game_root) / 'FalloutNV.exe')), \
                mock.patch.object(fnv_layout, 'process_modules', modules):
            fnv_layout.for_running(project, (77, 123))
            fnv_layout.for_running(project, (77, 123))
            fnv_layout.for_running(project, (77, 999))
        self.assertEqual(calls, [77, 77])

    def test_game_layout_follows_the_running_game(self):
        project = direct_project(self.root, mo2=True)
        game = fnv_game.Game(project)
        game.process = lambda fresh=False: (77, 123)
        with mock.patch.object(fnv_layout, 'process_image', lambda pid: str(Path(project.game_root) / 'FalloutNV.exe')), \
                mock.patch.object(fnv_layout, 'process_modules', lambda pid: ['FalloutNV.exe']):
            self.assertEqual(game.layout().kind, 'direct')
            self.assertEqual(game.saves_dir, self.root / 'mygames' / 'Saves')
        game.process = lambda fresh=False: None
        self.assertEqual(game.layout().kind, 'mo2')

    def test_the_real_module_list_of_this_process_works(self):
        names = [n.lower() for n in fnv_layout.process_modules(os.getpid())]
        self.assertIn('kernel32.dll', names)


# ------------------------------------------------------------------------------------------------ companion
class CompanionInstallTests(Tmp):
    def receipt(self, data=b'MZ companion'):
        dll = self.root / 'build' / fnv_link.DLL_NAME
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_bytes(data)
        return {'dll': str(dll), 'sha256': fnv_link.file_digest(dll), 'build_id': 'b1'}

    def test_install_into_the_game_folder_with_a_receipt(self):
        project = direct_project(self.root)
        out = fnv_link.install_game(project.game_root, self.receipt(), project.state_dir)
        target = self.root / 'game' / 'Data' / 'NVSE' / 'Plugins' / fnv_link.DLL_NAME
        self.assertTrue(out['installed'])
        self.assertEqual(target.read_bytes(), b'MZ companion')
        receipt = json.loads((Path(project.state_dir) / 'engine' / 'install-game.json').read_text())
        self.assertEqual(receipt['target'], str(target))
        again = fnv_link.install_game(project.game_root, self.receipt(), project.state_dir)
        self.assertTrue(again['unchanged'])

    def test_a_replaced_file_is_backed_up(self):
        project = direct_project(self.root)
        target = self.root / 'game' / 'Data' / 'NVSE' / 'Plugins' / fnv_link.DLL_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'old build')
        out = fnv_link.install_game(project.game_root, self.receipt(), project.state_dir)
        self.assertEqual(Path(out['backup']).read_bytes(), b'old build')

    def test_a_test_build_is_never_installed(self):
        project = direct_project(self.root)
        with self.assertRaises(fnv_link.LinkError) as caught:
            fnv_link.install_game(project.game_root, {**self.receipt(), 'defines': ['FNVMCP_TEST_SEAM']},
                                  project.state_dir)
        self.assertEqual(caught.exception.code, 'TEST_BUILD')

    def test_uninstall_removes_only_the_file_it_installed(self):
        project = direct_project(self.root)
        fnv_link.install_game(project.game_root, self.receipt(), project.state_dir)
        target = self.root / 'game' / 'Data' / 'NVSE' / 'Plugins' / fnv_link.DLL_NAME
        out = fnv_link.uninstall_game(project.game_root, project.state_dir)
        self.assertTrue(out['removed'])
        self.assertFalse(target.exists())
        target.write_bytes(b'someone else put this here')
        with self.assertRaises(fnv_link.LinkError) as caught:
            fnv_link.uninstall_game(project.game_root, project.state_dir)
        self.assertEqual(caught.exception.code, 'NOT_OURS')
        self.assertTrue(target.exists())

    def test_uninstall_with_nothing_installed(self):
        project = direct_project(self.root)
        self.assertFalse(fnv_link.uninstall_game(project.game_root, project.state_dir)['removed'])

    def test_the_installed_check_looks_in_the_game_folder(self):
        project = direct_project(self.root)
        game = fnv_game.Game(project)
        game.process = lambda fresh=False: None
        with self.assertRaises(fnv_game.GameError) as caught:
            game.check_companion_installed()
        self.assertEqual(caught.exception.code, 'COMPANION_NOT_INSTALLED')
        self.assertIn('target', caught.exception.evidence.get('fix', ''))
        fnv_link.install_game(project.game_root, self.receipt(), project.state_dir)
        game.check_companion_installed()


# ------------------------------------------------------------------------------------------------ launch
class DirectLaunchTests(Tmp):
    def game(self, project, loader=True):
        if loader:
            (Path(project.game_root) / 'nvse_loader.exe').write_bytes(b'MZ')
        (Path(project.game_root) / 'FalloutNV.exe').write_bytes(b'MZ')
        game = fnv_game.Game(project)
        game.check_companion_installed = lambda layout=None: None
        started = []
        state = {'running': False}

        def start_program(exe, args, cwd):
            started.append((Path(exe).name, args, Path(cwd)))
            state['running'] = True
            return 'shell_execute'
        game.start_program = start_program
        game.process = lambda fresh=False: (55, 1) if state['running'] else None
        game.ping = lambda timeout=3.0: {'deferredInit': True, 'companion': 3}
        game.phase = lambda ping=None: ('main_menu', {})
        game.close_stale_mo2 = mock.Mock(side_effect=AssertionError('MO2 is not used'))
        alive = mock.patch.object(fnv_game, 'process_alive', lambda pid: True)
        alive.start()
        self.addCleanup(alive.stop)
        return game, started

    def test_launch_runs_nvse_loader_from_the_game_folder(self):
        game, started = self.game(direct_project(self.root))
        with mock.patch.object(fnv_layout, 'for_running', lambda project, proc: None):
            out = game.launch('menu', 30)
        self.assertEqual(started, [('nvse_loader.exe', '', self.root / 'game')])
        self.assertEqual(out['phase'], 'main_menu')
        self.assertEqual(out['via'], 'direct')

    def test_a_hand_over_to_a_second_process_is_followed(self):
        """Steam's DRM can end the first FalloutNV.exe and start the game again through Steam."""
        game, started = self.game(direct_project(self.root))
        procs = iter([(55, 1), (55, 1), None, None, (66, 2)])
        last = [(55, 1)]

        def process(fresh=False):
            if not started:
                return None
            last[0] = next(procs, last[0])
            return last[0]
        game.process = process
        pings = iter([{}, {}])
        game.ping = lambda timeout=3.0: next(pings, {'deferredInit': True})
        with mock.patch.object(fnv_layout, 'for_running', lambda project, proc: None), \
                mock.patch.object(fnv_game, 'process_alive', lambda pid: pid != 55):
            out = game.launch('menu', 30)
        self.assertEqual(out['pid'], 66)
        self.assertIn('handed_over', [e['event'] for e in out['timeline']])

    def test_without_the_loader_it_runs_the_game(self):
        game, started = self.game(direct_project(self.root), loader=False)
        with mock.patch.object(fnv_layout, 'for_running', lambda project, proc: None):
            game.launch('menu', 30)
        self.assertEqual(started[0][0], 'FalloutNV.exe')

    def test_via_direct_with_mo2_configured(self):
        game, started = self.game(direct_project(self.root, mo2=True))
        with mock.patch.object(fnv_layout, 'for_running', lambda project, proc: None):
            out = game.launch('menu', 30, via='direct')
        self.assertEqual(started[0][0], 'nvse_loader.exe')
        self.assertEqual(out['via'], 'direct')

    def test_via_mo2_without_settings_says_what_is_missing(self):
        game, _ = self.game(direct_project(self.root))
        with self.assertRaises(fnv_game.GameError) as caught:
            game.launch('menu', 30, via='mo2')
        self.assertEqual(caught.exception.code, 'NOT_CONFIGURED')

    def test_a_save_start_on_the_direct_install_needs_the_setting(self):
        project = direct_project(self.root)
        write_plugin(self.root / 'mygames' / 'Saves' / 'Mine.fos')
        game, started = self.game(project)
        with self.assertRaises(fnv_game.GameError) as caught:
            game.launch('save:Mine', 30)
        self.assertEqual(caught.exception.code, 'DIRECT_INSTALL_PROTECTED')
        self.assertEqual(started, [])


class StatusTests(Tmp):
    def test_status_names_the_install_and_its_paths(self):
        project = direct_project(self.root)
        game = fnv_game.Game(project)
        game.process = lambda fresh=False: None
        out = game.status()
        self.assertEqual(out['install']['kind'], 'direct')
        self.assertEqual(out['install']['paths']['saves'], str(self.root / 'mygames' / 'Saves'))
        self.assertFalse(out['install']['changesAllowed'])
        self.assertNotIn('profile', out)

    def test_status_of_the_mo2_profile_keeps_its_profile_key(self):
        game = fnv_game.Game(direct_project(self.root, mo2=True))
        game.process = lambda fresh=False: None
        out = game.status()
        self.assertEqual(out['install']['kind'], 'mo2')
        self.assertEqual(out['profile']['name'], 'Dev')


# ------------------------------------------------------------------------------------------------ files
class DirectLoadOrderTests(Tmp):
    def test_masters_first_then_plugins_by_file_time(self):
        project = direct_project(self.root)
        data = self.root / 'game' / 'Data'
        now = time.time()
        write_plugin(data / 'FalloutNV.esm', esm=True, mtime=now - 1000)
        write_plugin(data / 'DeadMoney.esm', ['FalloutNV.esm'], esm=True, mtime=now - 900)
        write_plugin(data / 'Late.esp', ['FalloutNV.esm'], mtime=now - 10)
        write_plugin(data / 'Early.esp', ['FalloutNV.esm', 'Missing.esm'], mtime=now - 500)
        write_plugin(data / 'Off.esp', ['FalloutNV.esm'], mtime=now - 400)
        (self.root / 'appdata' / 'plugins.txt').write_text('FalloutNV.esm\r\nLate.esp\r\nEarly.esp\r\nDeadMoney.esm\r\n',
                                                         encoding='utf-8')
        from geck_mcp.fnv_data import load_order
        with mock.patch.object(fnv_layout, 'running_game', lambda project: None):
            entries = load_order(project)
        self.assertEqual([e['name'] for e in entries],
                         ['FalloutNV.esm', 'DeadMoney.esm', 'Early.esp', 'Off.esp', 'Late.esp'])
        active = [(e['name'], e.get('prefix')) for e in entries if e['active']]
        self.assertEqual(active, [('FalloutNV.esm', '00'), ('DeadMoney.esm', '01'), ('Early.esp', '02'),
                                  ('Late.esp', '03')])
        self.assertIsNone(entries[0]['mod'])
        self.assertEqual(next(e for e in entries if e['name'] == 'Early.esp')['missingMasters'], ['Missing.esm'])


class ProtectionTests(Tmp):
    def plugin(self):
        return write_plugin(self.root / 'built' / 'Mine.esp', ['FalloutNV.esm'], mtime=1_000_000)

    def game(self, allow):
        game = fnv_game.Game(direct_project(self.root, allow=allow))
        game.process = lambda fresh=False: None
        return game

    def test_installing_a_plugin_needs_the_setting(self):
        with self.assertRaises(fnv_game.GameError) as caught:
            self.game(False).install_plugin(str(self.plugin()))
        self.assertEqual(caught.exception.code, 'DIRECT_INSTALL_PROTECTED')
        self.assertIn('allow_direct_install_changes', str(caught.exception))
        self.assertFalse((self.root / 'game' / 'Data' / 'Mine.esp').exists())

    def test_with_the_setting_the_plugin_goes_into_data_and_loads_last(self):
        game = self.game(True)
        (self.root / 'appdata' / 'plugins.txt').write_text('FalloutNV.esm\n', encoding='utf-8')
        before = time.time() - 1
        out = game.install_plugin(str(self.plugin()))
        target = self.root / 'game' / 'Data' / 'Mine.esp'
        self.assertEqual(out['target'], str(target))
        self.assertGreaterEqual(target.stat().st_mtime, before)
        self.assertIn('Mine.esp', (self.root / 'appdata' / 'plugins.txt').read_text(encoding='utf-8').splitlines())
        self.assertTrue(Path(out['listsBackup']).is_dir())

    def test_profile_set_on_the_direct_install(self):
        from geck_mcp.fnv_data import DataTools
        from geck_mcp.fnv_records import RecordsTools
        project = direct_project(self.root)
        write_plugin(self.root / 'game' / 'Data' / 'FalloutNV.esm', esm=True)
        write_plugin(self.root / 'game' / 'Data' / 'A.esp', ['FalloutNV.esm'])
        plugins = self.root / 'appdata' / 'plugins.txt'
        plugins.write_text('FalloutNV.esm\n', encoding='utf-8')
        records = RecordsTools(DataTools(project, None))
        with mock.patch.object(fnv_layout, 'running_game', lambda project: None):
            dry = records.profile_set(None, {'A.esp': True}, None, dry_run=True)
            self.assertEqual(dry['changed'][0]['now'], 'active')
            self.assertEqual(plugins.read_text(encoding='utf-8'), 'FalloutNV.esm\n')
            with self.assertRaises(Exception) as caught:
                records.profile_set(None, {'A.esp': True}, None)
            self.assertEqual(caught.exception.code, 'DIRECT_INSTALL_PROTECTED')
            with self.assertRaises(Exception) as caught:
                records.profile_set({'Some Mod': True}, None, None, dry_run=True)
            self.assertEqual(caught.exception.code, 'NO_MODS')

    def test_test_saves_need_the_setting(self):
        from fakes import FakeLink
        from geck_mcp.fnv_live import LiveService
        project = direct_project(self.root)
        live = LiveService(project)
        live._game = fnv_game.Game(project)
        live._game.link = FakeLink()
        live._game.process = lambda fresh=False: (4242, 1)
        from geck_mcp.fnv_agent import AgentTools
        agent = AgentTools(live, None, None, None)
        self.assertEqual(agent.test_save('list')['folder'], str(self.root / 'mygames' / 'Saves'))
        for action in ('save', 'load', 'delete'):
            with self.subTest(action=action):
                with self.assertRaises(fnv_game.GameError) as caught:
                    agent.test_save(action, 'one')
                self.assertEqual(caught.exception.code, 'DIRECT_INSTALL_PROTECTED')

    def test_the_mo2_profile_is_never_held_back(self):
        game = fnv_game.Game(direct_project(self.root, mo2=True))
        game.process = lambda fresh=False: None
        game.guard_direct('install a plugin')       # no error


class SetupRowsTests(Tmp):
    def test_rows_for_a_project_without_mo2(self):
        from geck_mcp.fnv_setup import setup_check
        project = direct_project(self.root)
        plugins = self.root / 'game' / 'Data' / 'NVSE' / 'Plugins'
        plugins.mkdir(parents=True)
        (plugins / 'jip_nvse.dll').write_bytes(b'MZ')
        (self.root / 'mygames' / 'Fallout.ini').write_text('[General]\nbAlwaysActive=1\n', encoding='utf-8')
        with mock.patch.object(fnv_layout, 'running_game', lambda project: None):
            rows = {r['check']: r for r in setup_check(project)['checks']}
        self.assertNotIn('MO2 instance', rows)
        self.assertEqual(rows['install']['state'], 'ok')
        self.assertIn('direct', rows['install']['detail'])
        self.assertEqual(rows['JIP LN NVSE']['state'], 'ok')
        self.assertEqual(rows['crash logger']['state'], 'missing')
        self.assertIn(str(plugins), rows['crash logger']['fix'])
        self.assertEqual(rows['keep running unfocused']['state'], 'ok')
        self.assertEqual(rows['engine companion']['state'], 'missing')
        self.assertIn("target='game'", rows['engine companion']['fix'])


class OtherPathsTests(Tmp):
    def test_the_saves_tools_read_the_real_saves(self):
        from geck_mcp.fnv_tools import FnvService
        with mock.patch.object(fnv_layout, 'running_game', lambda project: None):
            self.assertEqual(FnvService(direct_project(self.root)).saves_dir(), self.root / 'mygames' / 'Saves')

    def test_read_roots_include_the_users_game_folders(self):
        from geck_mcp.safe_paths import read_roots
        roots = read_roots(direct_project(self.root))
        self.assertIn(self.root / 'mygames', roots)
        self.assertIn(self.root / 'appdata', roots)

    def test_loose_roots_of_the_records_tools(self):
        from geck_mcp.fnv_data import DataTools
        from geck_mcp.fnv_records import RecordsTools
        records = RecordsTools(DataTools(direct_project(self.root), None))
        with mock.patch.object(fnv_layout, 'running_game', lambda project: None):
            _, roots = records._asset_index([])
        self.assertEqual(roots, [self.root / 'game' / 'Data'])


if __name__ == '__main__':
    unittest.main()
