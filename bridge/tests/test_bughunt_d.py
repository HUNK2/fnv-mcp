"""Bug hunt 2026-11-18, hunter D: game control, layout, setup, save reader, ESP builder and asset reader."""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_game, fnv_layout, fnv_save, fnv_setup  # noqa: E402
from geck_mcp.config import Project  # noqa: E402
from geck_mcp.esp import assets, build as builder, dialogue  # noqa: E402
from geck_mcp.esp.records import SpecError  # noqa: E402

from test_fnv_save_and_engine_ai import make_save  # noqa: E402


def good_spec():
    return {'schema': 1, 'project_id': 'hunt', 'plugin': 'Hunt.esp', 'masters': ['FalloutNV.esm'], 'records': {}}


class SpecShape(unittest.TestCase):
    def test_non_string_master_is_a_spec_error(self):
        for masters in ([5], [None], [['FalloutNV.esm']], ['FalloutNV.esm', 3]):
            spec = good_spec()
            spec['masters'] = masters
            with self.assertRaises(SpecError, msg=masters):
                builder.check_spec(spec)

    def test_unhashable_record_type_is_a_spec_error(self):
        for kind in (['MISC'], {'a': 1}, 7):
            spec = good_spec()
            spec['records'] = {'Thing': {'type': kind}}
            with self.assertRaises(SpecError, msg=kind):
                builder.check_spec(spec)

    def test_line_flags_must_be_a_list_of_names(self):
        for flags in (5, [['goodbye']], 'goodbye', [3]):
            rec = {'type': 'DIAL', 'infos': [{'key': 'a', 'quest': '@Q', 'flags': flags}]}
            with self.assertRaises(SpecError, msg=flags):
                dialogue.check_lines('Topic', rec, 'records.Topic')


class TruncatedFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_truncated_bsa_is_an_asset_error(self):
        # header says 3 folders, the file ends right after it
        header = b'BSA\0' + struct.pack('<8I', 104, 36, 3, 3, 3, 10, 10, 0)
        path = self.tmp / 'cut.bsa'
        path.write_bytes(header)
        with self.assertRaises(assets.AssetError):
            assets.bsa_names(path)

    def test_save_with_a_cut_off_change_body_is_refused(self):
        path = make_save(self.tmp / 'a.fos')
        good = fnv_save.Save(path)
        last = good.changes[-1]
        raw = bytearray(path.read_bytes())
        # the last change form claims 255 body bytes, far more than the file has left
        at = raw.index(last['body'], good.ofs_changes) - 1
        raw[at] = 0xFF
        bad = self.tmp / 'bad.fos'
        bad.write_bytes(bytes(raw))
        with self.assertRaises(fnv_save.SaveFormatError):
            fnv_save.Save(bad)


class GameHelpers(unittest.TestCase):
    def test_mo2_processes_ignores_a_sibling_folder_with_the_same_prefix(self):
        tasklist = SimpleNamespace(stdout='"ModOrganizer.exe","11","Console","1","1 K"\r\n'
                                          '"ModOrganizer.exe","22","Console","1","1 K"\r\n')
        paths = {11: r'C:\Games\MO2-other\ModOrganizer.exe', 22: r'C:\Games\MO2\ModOrganizer.exe'}
        with mock.patch.object(fnv_game.subprocess, 'run', return_value=tasklist), \
                mock.patch.object(fnv_game, 'image_path', side_effect=lambda pid: paths[pid]):
            self.assertEqual(fnv_game.mo2_processes(r'C:\Games\MO2'), [22])
            self.assertEqual(fnv_game.mo2_processes('C:\\Games\\MO2\\'), [22])

    def test_logs_with_zero_max_lines_returns_no_lines(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'game').mkdir()
            (root / 'game' / 'nvse.log').write_text('one\ntwo\n', encoding='utf-8')
            project = Project.model_validate({'project_id': 'hunt', 'game_root': str(root / 'game'),
                                              'state_dir': str(root / 'state'), 'fnv': {}})
            out = fnv_game.Game(project).logs(['nvse'], since=0, max_lines=0)
            self.assertEqual(out['nvse']['lines'], [])


class IniReading(unittest.TestCase):
    def test_ini_value_reads_a_file_with_a_byte_order_mark(self):
        with tempfile.TemporaryDirectory() as folder:
            ini = Path(folder) / 'Fallout.ini'
            ini.write_bytes(b'\xef\xbb\xbf[General]\r\nbAlwaysActive=1\r\n')
            self.assertEqual(fnv_layout.ini_value(ini, 'General', 'bAlwaysActive'), '1')

    def test_setup_check_accepts_spaces_around_the_equals_sign(self):
        from test_setup_rows import fnv_settings, project, row, run
        with tempfile.TemporaryDirectory() as folder:
            p = project(folder, fnv_settings(folder))
            fake = SimpleNamespace(user_dir=Path(folder) / 'docs', appdata_dir=Path(folder) / 'appdata',
                                   changes_allowed=False)
            fake.user_dir.mkdir()
            (fake.user_dir / 'Fallout.ini').write_text('[General]\n  bAlwaysActive = 1\n', encoding='utf-8')
            with mock.patch.object(fnv_layout, 'current', return_value=fake):
                report = run(p)
        self.assertEqual(row(report, 'keep running unfocused')['state'], 'ok')


if __name__ == '__main__':
    unittest.main()
