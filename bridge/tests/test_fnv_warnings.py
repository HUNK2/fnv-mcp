"""fnv_warnings (log-file version) on temporary folders: no game, no real xNVSE or MO2 files."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp import fnv_warnings  # noqa: E402
from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_warnings import Warnings  # noqa: E402

ENGINE = [
    'MASTERFILE: Form (000A3E7C) in file ModOne.esp has wrong type 12 (expected 13)',
    'MASTERFILE: Form (000A3E7D) in file ModOne.esp has wrong type 12 (expected 13)',
    'SCRIPTS: Script MyQuestScript in ModTwo.esp has unknown variable 5',
    'MODELS: Error loading meshes\\foo.nif',
    'Something without a category',
]
HAVOK = ['Havok: bad constraint 3']


class WarningsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.game = root / 'game'
        self.mo2 = root / 'mo2'
        self.state = root / 'state'
        (self.game / 'Data/NVSE').mkdir(parents=True)
        (self.mo2 / 'profiles/Dev').mkdir(parents=True)
        (self.mo2 / 'profiles/Dev/modlist.txt').write_text('# header\n+High Mod\n-Off Mod\n+Low Mod\n', encoding='utf-8')
        self.w = Warnings(self.game, self.mo2, 'Dev', self.state)

    def tearDown(self):
        self.tmp.cleanup()

    def write_logs(self, engine=ENGINE, havok=HAVOK, partial=''):
        (self.game / 'falloutnv_error.log').write_bytes(('\r\n'.join(engine) + '\r\n' + partial).encode())
        (self.game / 'falloutnv_havok.log').write_text('\n'.join(havok) + '\n' if havok else '', encoding='utf-8')

    # ------------------------------------------------------------ parsing helpers
    def test_category_and_masking(self):
        self.assertEqual(fnv_warnings.category_of('MASTERFILE: x'), 'MASTERFILE')
        self.assertEqual(fnv_warnings.category_of('audio: x'), 'Audio')
        self.assertEqual(fnv_warnings.category_of('Unknown: x'), 'other')
        self.assertEqual(fnv_warnings.category_of('no colon'), 'other')
        a = fnv_warnings.pattern_of('Form (000A3E7C) in file X.esp has wrong type 12')
        b = fnv_warnings.pattern_of('Form (00FF0001) in file X.esp has wrong type 7')
        self.assertEqual(a, b)
        self.assertNotEqual(a, fnv_warnings.pattern_of('Form (000A3E7C) in file Y.esp has wrong type 12'))
        self.assertEqual(fnv_warnings.plugins_in('in file ModOne.esp and DeadMoney.esm'), ['DeadMoney.esm', 'ModOne.esp'])
        self.assertEqual(fnv_warnings.plugins_in('file Fixture.esp x Fixture.ESP'), ['Fixture.ESP', 'Fixture.esp'])

    def test_set_setting_keeps_other_lines(self):
        original = '; comment\r\n[General]\r\nA=1\r\n[Logging]\r\nbAppendErrorLogs=0\r\nenablegameerrorlog = 0\r\n[Other]\r\nB=2\r\n'
        out = fnv_warnings.set_setting(original, 1)
        self.assertEqual(out, original.replace('enablegameerrorlog = 0', 'enablegameerrorlog=1'))
        self.assertEqual(fnv_warnings.read_setting(out), '1')
        added = fnv_warnings.set_setting('[General]\nA=1\n[Logging]\nX=y', 1)
        self.assertEqual(added, '[General]\nA=1\n[Logging]\nEnableGameErrorLog=1\nX=y')
        appended = fnv_warnings.set_setting('[General]\nA=1', 1)
        self.assertEqual(appended, '[General]\nA=1\n[Logging]\nEnableGameErrorLog=1\n')
        self.assertEqual(fnv_warnings.set_setting('', 1), '[Logging]\r\nEnableGameErrorLog=1\r\n')
        self.assertFalse(fnv_warnings.is_on(fnv_warnings.read_setting('[Logging]\nEnableGameErrorLog=0')))
        self.assertIsNone(fnv_warnings.read_setting('[Other]\nEnableGameErrorLog=1'))

    # ------------------------------------------------------------ status and enable
    def test_status_off_then_enable_then_on(self):
        status = self.w.status()
        self.assertEqual(list(status)[:2], ['verdict', 'reason'])
        self.assertFalse(status['enabled'])
        self.assertIn('fnv_warnings enable', status['next'])
        overwrite = self.mo2 / 'overwrite/NVSE/nvse_config.ini'
        self.assertEqual(status['enableWouldWrite'], str(overwrite))
        result = self.w.run('enable')
        self.assertTrue(result['restartNeeded'])
        self.assertIn('next game start', result['verdict'])
        self.assertEqual(result['written'], str(overwrite))
        self.assertIn('EnableGameErrorLog=1', overwrite.read_text())
        self.assertFalse((self.game / 'Data/NVSE/nvse_config.ini').exists())
        self.assertTrue(self.w.status()['enabled'])
        again = self.w.run('enable')
        self.assertIs(again['changed'], False)
        self.assertFalse(again['restartNeeded'])

    def test_enable_seeds_from_real_config_without_touching_it(self):
        real = self.game / 'Data/NVSE/nvse_config.ini'
        real.write_bytes(b'[Memory]\r\nDefaultHeapInitialAllocMB=500\r\n')
        result = self.w.enable()
        written = Path(result['written'])
        self.assertEqual(written, self.mo2 / 'overwrite/NVSE/nvse_config.ini')
        self.assertEqual(written.read_bytes(),
                         b'[Memory]\r\nDefaultHeapInitialAllocMB=500\r\n[Logging]\r\nEnableGameErrorLog=1\r\n')
        self.assertEqual(real.read_bytes(), b'[Memory]\r\nDefaultHeapInitialAllocMB=500\r\n')
        self.assertTrue(result['seededFromOtherFile'])

    def test_enable_edits_winning_mod_file_and_backs_it_up(self):
        files = {self.mo2 / 'mods/High Mod/NVSE/nvse_config.ini': '[Logging]\nEnableGameErrorLog=0\nbAppendErrorLogs=1\n',
                 self.mo2 / 'mods/Low Mod/NVSE/nvse_config.ini': 'low\n',
                 self.mo2 / 'mods/Off Mod/NVSE/nvse_config.ini': 'off\n'}
        for path, text in files.items():
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding='utf-8')
        high, low = self.mo2 / 'mods/High Mod/NVSE/nvse_config.ini', self.mo2 / 'mods/Low Mod/NVSE/nvse_config.ini'
        self.assertEqual(self.w.effective_config(), high)
        result = self.w.enable()
        self.assertEqual(result['written'], str(high))
        self.assertEqual(high.read_text(), '[Logging]\nEnableGameErrorLog=1\nbAppendErrorLogs=1\n')
        self.assertEqual(Path(result['backup']).read_text(), '[Logging]\nEnableGameErrorLog=0\nbAppendErrorLogs=1\n')
        self.assertEqual(low.read_text(), 'low\n')

    # ------------------------------------------------------------ read, summary, clear
    def test_read_filters_and_verdict(self):
        self.write_logs(partial='SCRIPTS: half a li')
        out = self.w.read()
        self.assertEqual(list(out)[:2], ['verdict', 'reason'])
        self.assertEqual(out['verdict'], '5 engine warnings since the mark and 1 Havok messages, 2 of them MASTERFILE')
        self.assertEqual(out['total'], 6)
        self.assertNotIn('half a li', json.dumps(out['rows']))
        self.assertEqual(self.w.read(category='masterfile')['total'], 2)
        self.assertEqual(self.w.read(category='other', source='engine')['total'], 1)
        self.assertEqual(self.w.read(text='unknown variable')['rows'][0]['seq'], 2)
        self.assertEqual(self.w.read(plugin='modone.esp')['total'], 2)
        self.assertEqual(self.w.read(plugin='modone.esp')['rows'][0]['plugins'], ['ModOne.esp'])
        self.assertEqual(self.w.read(source='havok')['rows'][0]['text'], 'Havok: bad constraint 3')
        paged = self.w.read(limit=2, offset=1)
        self.assertEqual((paged['count'], paged['more'], paged['rows'][0]['seq']), (2, True, 1))
        self.assertEqual(self.w.read(since=3, source='engine')['total'], 2)
        self.assertEqual(set(self.w.read(brief=True)['rows'][0]), {'seq', 'source', 'text'})
        with self.assertRaises(ConvError):
            self.w.run('bogus')

    def test_read_without_files_says_what_to_do(self):
        out = self.w.read()
        self.assertEqual(out['total'], 0)
        self.assertIn('fnv_warnings status', out['reason'])

    def test_summary_groups_and_counts(self):
        engine = ENGINE + ['MASTERFILE: Form (000A3E7E) in file ModOne.esp has wrong type 99 (expected 13)']
        self.write_logs(engine=engine)
        out = self.w.summary()
        self.assertEqual(out['byCategory']['MASTERFILE'], 3)
        self.assertEqual(out['byPlugin']['ModOne.esp'], 3)
        top = out['repeated'][0]
        self.assertEqual((top['count'], top['first'], top['last']), (3, 0, 5))
        self.assertIn('most of them MASTERFILE (3)', out['verdict'])

    def test_clear_sets_mark_and_read_starts_there(self):
        self.write_logs()
        cleared = self.w.clear()
        self.assertEqual(cleared['skipped'], {'engine': 5, 'havok': 1})
        self.assertEqual(self.w.read()['total'], 0)
        with (self.game / 'falloutnv_error.log').open('ab') as f:
            f.write(b'AI: new line\r\nSCRIPTS: another\r\n')
        out = self.w.read()
        self.assertEqual([r['text'] for r in out['rows']], ['AI: new line', 'SCRIPTS: another'])
        self.assertEqual(out['rows'][0]['seq'], 5)
        self.assertEqual(self.w.read(since='start')['total'], 8)
        self.assertEqual(self.w.status()['marks'], {'engine': 5, 'havok': 1})

    def test_mark_is_dropped_when_the_game_rewrites_the_file(self):
        self.write_logs()
        self.w.clear()
        self.write_logs(engine=['AI: first line of the new run', 'AI: second', 'AI: third'], havok=[])
        out = self.w.read(source='engine')
        self.assertEqual(out['total'], 3)
        self.assertIn('rewritten', out['notes'][0])
        # a rewritten file that grows past the old mark is detected by its bytes, not by its size
        self.write_logs(engine=['X' * 40 + ' line %d' % i for i in range(20)], havok=[])
        self.assertEqual(self.w.read(source='engine')['total'], 20)


if __name__ == '__main__':
    unittest.main()
