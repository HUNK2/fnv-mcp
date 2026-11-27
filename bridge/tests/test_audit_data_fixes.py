"""Audit fixes in the plugin record index and the asset and start paths, offline.

Z1: a second copy of a plugin with a non-ASCII name kept the first copy's rows (SQLite lower() folds A-Z only).
Z2: a plugin that left the load order kept its rows, and lookups by owner served its EditorIDs.
Z4: one unreadable active plugin (0 bytes, a 6-byte header) broke every index-based record tool and fnv_loadorder.
X8: the build's asset check did not read the BSAs inside enabled MO2 mods.
W6: the audio cache flag stayed off when its restore write failed after a stalled start.
"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_bugfix_a_c_s import tes4_plugin  # noqa: E402
from fnv_mcp import fnv_data, fnv_game, fnv_layout, fnv_records, fnv_save  # noqa: E402
from fnv_mcp.fnv_data import DataTools, RecordIndex  # noqa: E402


class Tmp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)   # per-thread connections close on GC
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)


class NonAsciiCopyTests(Tmp):
    """Z1"""

    def test_second_copy_of_a_non_ascii_name_drops_the_first(self):
        a, b = self.root / 'A' / 'Ärger.esp', self.root / 'B' / 'Ärger.esp'
        a.parent.mkdir()
        b.parent.mkdir()
        tes4_plugin(a, [(0x000800, 'STAT', 'CopyA')])
        tes4_plugin(b, [(0x000800, 'STAT', 'CopyBB')])
        index = RecordIndex(self.root / 'state')
        self.addCleanup(lambda: index.db.close())
        index.ensure(a)
        index.ensure(b)
        db = index.db
        self.assertEqual([r[0] for r in db.execute('select path from files')], [str(b)])
        self.assertEqual([r[0] for r in db.execute("select edid from records where owner = 'ärger.esp'")],
                         ['CopyBB'])
        row = db.execute("select edid from records where owner = 'ärger.esp' and local = ? limit 1",
                         (0x800,)).fetchone()
        self.assertEqual(row[0], 'CopyBB')


class DisabledPluginRowsTests(Tmp):
    """Z2: rows of a plugin no longer active must not label lookups."""

    def setUp(self):
        super().setUp()
        self.data = DataTools(SimpleNamespace(state_dir=str(self.root / 'state')))
        self.addCleanup(lambda: self.data.index.db.close())
        base, gone = self.root / 'Base.esm', self.root / 'Gone.esp'
        tes4_plugin(base, [(0x000800, 'STAT', 'Rock'), (0x000801, 'STAT', 'Stone')])
        # an override that renames the record, indexed while it was active (first, so its row comes first)
        tes4_plugin(gone, [(0x000800, 'STAT', 'StaleRock')], masters=['Base.esm'])
        self.gone_id = self.data.index.ensure(gone)
        self.entries = [{'index': 0, 'name': 'Base.esm', 'path': str(base), 'active': True, 'masters': [],
                         'fileId': self.data.index.ensure(base)}]
        self.data._active = lambda: (self.entries, None)

    def test_the_disabled_plugins_rows_are_still_indexed(self):
        # the premise: the rows stay; the fix filters the lookups
        self.assertTrue(self.data.index.db.execute('select 1 from records where file = ?', (self.gone_id,)).fetchone())

    def test_form_label_uses_the_active_version(self):
        form = self.data._form(self.entries, self.entries[0], 0x00000800)
        self.assertEqual(form['editorId'], 'Rock')

    def test_form_of_a_record_only_in_the_disabled_plugin_is_unknown(self):
        tes4_plugin(self.root / 'Gone.esp', [(0x000800, 'STAT', 'StaleRock'), (0x01000900, 'STAT', 'OnlyGone')],
                    masters=['Base.esm'])
        self.data.index.ensure(self.root / 'Gone.esp')
        entry = {'name': 'Patch.esp', 'masters': ['Base.esm', 'Gone.esp']}     # a plugin whose master was disabled
        self.assertIsNone(self.data._form(self.entries, entry, 0x01000900))

    def test_save_namer_uses_the_active_version(self):
        namer = fnv_save.Namer(self.data)
        self.assertEqual(namer.edid(SimpleNamespace(plugins=['Base.esm']), 0x00000800), 'Rock')

    def test_conflict_editor_id_uses_the_active_versions(self):
        patch = self.root / 'Patch.esp'
        tes4_plugin(patch, [(0x000800, 'STAT', 'PatchedRock')], masters=['Base.esm'])
        self.entries.append({'index': 1, 'name': 'Patch.esp', 'path': str(patch), 'active': True,
                             'masters': ['Base.esm'], 'fileId': self.data.index.ensure(patch)})
        tools = fnv_records.RecordsTools(self.data)
        out = tools.conflicts(include_identical=True)
        self.assertEqual(out['count'], 1)
        self.assertEqual(out['conflicts'][0]['editorId'], 'PatchedRock')     # the winner's, not StaleRock


class UnreadablePluginTests(Tmp):
    """Z4: a 0-byte and a 6-byte active plugin."""

    def setUp(self):
        super().setUp()
        from test_direct_install import direct_project
        self.project = direct_project(self.root)
        data = self.root / 'game' / 'Data'
        tes4_plugin(data / 'FalloutNV.esm', [(0x000800, 'STAT', 'Rock')])
        (data / 'empty.esp').write_bytes(b'')
        (data / 'short.esp').write_bytes(b'TES4\0\0')
        (self.root / 'appdata' / 'plugins.txt').write_text('FalloutNV.esm\nempty.esp\nshort.esp\n', encoding='utf-8')
        patcher = mock.patch.object(fnv_layout, 'running_game', lambda project: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_header_checks_raise_format_error(self):
        for name in ('empty.esp', 'short.esp'):
            path = self.root / 'game' / 'Data' / name
            with self.assertRaises(fnv_data.codec.FormatError):
                fnv_data.plugin_masters(path)
            with self.assertRaises(fnv_data.codec.FormatError):
                fnv_data.scan_plugin(path)

    def test_loadorder_reports_them(self):
        data = DataTools(self.project, None)
        out = data.loadorder()
        self.assertEqual(sorted(p['name'] for p in out['problems']), ['empty.esp', 'short.esp'])

    def test_record_tools_skip_them_and_say_so(self):
        data = DataTools(self.project, None)
        self.addCleanup(lambda: data.index.db.close())
        out = data.record_find('Rock')
        self.assertEqual([r['editorId'] for r in out['results']], ['Rock'])
        self.assertEqual(sorted(s['plugin'] for s in out['indexBuilt']['skipped']), ['empty.esp', 'short.esp'])
        self.assertEqual(data.record_show('Rock')['editorId'], 'Rock')

    def test_a_scan_failure_is_skipped_too(self):
        data = DataTools(self.project, None)
        self.addCleanup(lambda: data.index.db.close())
        real = fnv_data.scan_plugin

        def scan(path):
            if Path(path).name == 'FalloutNV.esm':
                return real(path)
            raise struct.error('cut short')
        with mock.patch.object(fnv_data, 'load_order', lambda project, live=None: [
                {'name': 'FalloutNV.esm', 'active': True, 'index': 0, 'masters': [],
                 'path': str(self.root / 'game' / 'Data' / 'FalloutNV.esm')},
                {'name': 'odd.esp', 'active': True, 'index': 1, 'masters': [],
                 'path': str(self.root / 'game' / 'Data' / 'FalloutNV.esm').replace('FalloutNV.esm', 'empty.esp')}]), \
                mock.patch.object(fnv_data, 'scan_plugin', scan):
            entries, built = data._active()
        self.assertEqual([e['name'] for e in entries], ['FalloutNV.esm'])
        self.assertEqual(built['skipped'][0]['plugin'], 'odd.esp')


class ModBsaBuildCheckTests(Tmp):
    """X8: a model in an enabled mod's BSA is found by the build's asset check, and its manifest reads that BSA."""

    def test_mod_bsa_is_searched_and_read(self):
        from test_validate import make_bsa
        from fnv_mcp.authoring import Authoring
        game, mo2 = self.root / 'game', self.root / 'mo2'
        (game / 'Data').mkdir(parents=True)
        (mo2 / 'mods' / 'SomeMod').mkdir(parents=True)
        (mo2 / 'profiles' / 'Dev').mkdir(parents=True)
        (mo2 / 'profiles' / 'Dev' / 'modlist.txt').write_text('+SomeMod\n', encoding='utf-8')
        make_bsa(game / 'Data' / 'Fallout - Meshes.bsa', {'meshes\\vanilla\\rock.nif': b'x'})
        make_bsa(mo2 / 'mods' / 'SomeMod' / 'SomeMod - Main.bsa', {'meshes\\somemod\\crate.nif': b'y'})
        project = SimpleNamespace(project_id='x', spec='x.spec.json', state_dir=self.root / 'state', game_root=game,
                                  plugin='Mine.esp', fnv=SimpleNamespace(mo2_root=str(mo2), mo2_profile='Dev'))
        auth = Authoring(project)
        assets = auth.asset_index()
        self.assertEqual(assets.find('meshes\\somemod\\crate.nif'), {'where': 'bsa'})
        self.assertEqual(assets.find('meshes\\vanilla\\rock.nif'), {'where': 'bsa'})
        modl = b'somemod\\crate.nif\0'
        edid = b'Crate\0'
        body = b'EDID' + struct.pack('<H', len(edid)) + edid + b'MODL' + struct.pack('<H', len(modl)) + modl
        record = b'STAT' + struct.pack('<III', len(body), 0, 0x01000800) + b'\0' * 8 + body
        group = b'GRUP' + struct.pack('<I', 24 + len(record)) + b'STAT' + struct.pack('<I', 0) + b'\0' * 8 + record
        plugin = b'TES4' + struct.pack('<IIII', 0, 0, 0, 0) + struct.pack('<I', 0) + group
        manifest = auth.asset_manifest(plugin, assets)
        self.assertEqual(manifest['files']['meshes\\somemod\\crate.nif']['where'], 'bsa')
        self.assertIsNotNone(manifest['files']['meshes\\somemod\\crate.nif']['sha256'])
        self.assertEqual(sorted(a['name'] for a in manifest['archives']), ['Fallout - Meshes.bsa', 'SomeMod - Main.bsa'])


class AudioCacheRetryTests(unittest.TestCase):
    """W6"""

    def setUp(self):
        from fakes import FakeLive
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.game = self.fake.live._game
        self.flag = fnv_game.AUDIO_CACHE_FLAG
        self.fake.link.put(self.flag, b'\x01')

        def stalled(deadline, pid=None, start=None, **kw):
            del self.fake.link.memory[self.flag]        # the main loop is busy: the restore write fails
            raise fnv_game.GameError('NOT_IN_GAME', 'still loading')
        self.game.wait_in_game = stalled

    def fail_start(self):
        with self.assertRaises(fnv_game.GameError) as caught:
            self.game.start_from_menu('coc', 'HelloCourierRoom', deadline=10 ** 9, pid=4242)
        self.fake.link.put(self.flag, b'\x00')          # the load ends: writable again, still off
        return caught.exception

    def test_error_says_the_flag_is_off_and_the_restore_is_kept(self):
        error = self.fail_start()
        self.assertEqual(error.evidence['audioCache']['restored'], False)
        self.assertEqual(self.game.session()['audioCacheRestore'], {'pid': 4242, 'value': '01'})

    def test_status_retries_it(self):
        self.fail_start()
        out = self.game.status()
        self.assertEqual(out['audioCache'], {'restored': True})
        self.assertEqual(self.fake.link.get(self.flag, 1), b'\x01')
        self.assertFalse(self.game.session().get('audioCacheRestore'))

    def test_live_ready_retries_it(self):
        self.fail_start()
        self.fake.live.ready()
        self.assertEqual(self.fake.link.get(self.flag, 1), b'\x01')
        self.assertFalse(self.game.session().get('audioCacheRestore'))

    def test_another_game_drops_it(self):
        self.fail_start()
        self.assertIsNone(self.game.retry_audio_cache(9999))
        self.assertEqual(self.fake.link.get(self.flag, 1), b'\x00')
        self.assertFalse(self.game.session().get('audioCacheRestore'))


if __name__ == '__main__':
    unittest.main()
