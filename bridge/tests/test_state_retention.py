"""PLAN-3 S2.6: retention of the state folder, and revert_all scoped to this game process."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp.fnv_state import GameIdentity, prune  # noqa: E402


def touch(path, age_days=0.0, now=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'x')
    t = (now or time.time()) - age_days * 86400
    os.utime(path, (t, t))


class RetentionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.state = self.root / 'state'
        self.crash = self.root / 'local' / 'fnvmcp'

    def test_prune_keeps_bounds(self):
        now = time.time()
        fnv = self.state / 'fnv'
        fnv.mkdir(parents=True)
        (fnv / 'quit-log.jsonl').write_text(''.join(json.dumps({'n': i}) + '\n' for i in range(700)))
        for i in range(60):
            touch(self.crash / ('crash-context-100-%d.json' % i), age_days=i / 100, now=now)
        touch(self.crash / 'console.log', age_days=30, now=now)            # not a crash context: kept
        for i in range(25):
            touch(fnv / 'install-backups' / ('%d-Mine.esp' % (1000 + i)), age_days=(25 - i) / 100, now=now)
        for i in range(3):
            touch(fnv / 'install-backups' / ('%d-Other.esp' % (1000 + i)), now=now)
        for i in range(22):
            touch(fnv / 'mo2-backups' / ('plugin_loadcheck-%d.tmp' % i), age_days=(22 - i) / 100, now=now)
        for i in range(310):
            touch(fnv / 'screens' / ('s%d.png' % i), age_days=(310 - i) / 1000, now=now)
        touch(fnv / 'screenshots' / 'one.png', now=now)
        touch(fnv / 'modules' / 'old-aaa.dll', age_days=20, now=now)
        touch(fnv / 'modules' / 'old-aaa.pdb', age_days=20, now=now)
        touch(fnv / 'modules' / 'new-bbb.dll', age_days=2, now=now)

        out = prune(self.state, now, crash_dir=self.crash)

        lines = (fnv / 'quit-log.jsonl').read_text().splitlines()
        self.assertEqual(len(lines), 500)
        self.assertEqual(json.loads(lines[-1])['n'], 699)
        self.assertEqual(len(list(self.crash.glob('crash-context-*.json'))), 50)
        self.assertTrue((self.crash / 'crash-context-100-0.json').exists(), 'the newest are kept')
        self.assertTrue((self.crash / 'console.log').exists())
        self.assertEqual(len(list((fnv / 'install-backups').glob('*-Mine.esp'))), 20)
        self.assertTrue((fnv / 'install-backups' / '1024-Mine.esp').exists())
        self.assertEqual(len(list((fnv / 'install-backups').glob('*-Other.esp'))), 3)
        self.assertEqual(len(list((fnv / 'mo2-backups').iterdir())), 20)
        self.assertEqual(len(list((fnv / 'screens').iterdir())), 300)
        self.assertTrue((fnv / 'screens' / 's309.png').exists())
        self.assertEqual(sorted(p.name for p in (fnv / 'modules').iterdir()), ['new-bbb.dll'])
        self.assertEqual(out['quitLog'], 200)
        self.assertEqual(out['crashContexts'], 10)
        self.assertEqual(out['screens'], 10)
        self.assertEqual(out['modules'], 2)

    def test_prune_on_an_empty_state_folder(self):
        self.assertEqual(sum(prune(self.state, time.time(), crash_dir=self.crash).values()), 0)


class RevertScopeTests(unittest.TestCase):
    def test_revert_all_not_undoable_only_this_process(self):
        from fakes import FakeLive
        fake = FakeLive()
        self.addCleanup(fake.close)
        live = fake.live
        old = GameIdentity(1111, 5)
        for i in range(3000):
            live.journal.add({'kind': 'console', 'line': 'x', 'undo': None}, old)
        mark = live.journal.last_seq()
        here = live.journal.add({'kind': 'console', 'line': 'a', 'undo': None})
        live.journal.add({'kind': 'console', 'line': 'b', 'undo': None})
        out = live.revert_all(mark)
        self.assertEqual(out['notUndoable']['count'], 2)
        self.assertIn(here['id'], out['notUndoable']['ids'])
        self.assertLess(len(json.dumps(out)), 5000)
        whole = live.revert_all()
        self.assertEqual(whole['notUndoable']['count'], 2, 'other game processes are never listed')

    def test_not_undoable_ids_are_capped(self):
        from fakes import FakeLive
        fake = FakeLive()
        self.addCleanup(fake.close)
        live = fake.live
        ids = [live.journal.add({'kind': 'console', 'line': 'x', 'undo': None})['id'] for _ in range(80)]
        out = live.revert_all()
        self.assertEqual(out['notUndoable']['count'], 80)
        self.assertEqual(out['notUndoable']['ids'], ids[-50:][::-1], 'the newest 50, newest first')


if __name__ == '__main__':
    unittest.main()
