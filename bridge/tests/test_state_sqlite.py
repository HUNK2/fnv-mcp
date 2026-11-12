"""PLAN-3 S2.5: SQLite files shared by server processes (R14, R15)."""
import sqlite3
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_data, fnv_records  # noqa: E402
from geck_mcp.fnv_data import RecordIndex  # noqa: E402


def plugin(path, edid):
    sub = edid.encode() + b'\0'
    rec = b'EDID' + struct.pack('<H', len(sub)) + sub
    path.write_bytes(b'TES4' + struct.pack('<IIII', 0, 0, 0, 0) + struct.pack('<I', 0)
                     + b'STAT' + struct.pack('<III', len(rec), 0, 0x800) + b'\0' * 8 + rec)


class SqliteTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)   # per-thread connections close on GC
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_two_processes_first_index_build(self):
        p = self.dir / 'Mine.esp'
        plugin(p, 'Rock')
        RecordIndex(self.dir).ensure(p)
        time.sleep(0.05)
        plugin(p, 'RockTwo')                     # the plugin was rebuilt
        real_scan = fnv_data.scan_plugin

        def slow_scan(path):
            time.sleep(0.3)
            return real_scan(path)
        out = []

        def go():
            try:
                out.append(RecordIndex(self.dir).ensure(p))
            except Exception as e:  # noqa: BLE001
                out.append('%s: %s' % (type(e).__name__, e))
        # each "process" has its own lock table: only the database can keep them apart
        with mock.patch.object(fnv_data, '_ensure_lock', lambda path: threading.Lock()), \
                mock.patch.object(fnv_data, 'scan_plugin', slow_scan):
            ts = [threading.Thread(target=go) for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(60)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(isinstance(x, int) for x in out), out)
        self.assertEqual(out[0], out[1], 'both callers get the one rebuilt file')
        db = sqlite3.connect(str(self.dir / 'fnv' / 'records.sqlite'))
        self.addCleanup(db.close)
        self.assertEqual(db.execute('select count(*) from files where path = ?', (str(p),)).fetchone()[0], 1)
        self.assertEqual(db.execute("select count(*) from records where edid = 'RockTwo'").fetchone()[0], 1)

    def test_failed_refs_build_releases_lock(self):
        p = self.dir / 'Mine.esp'
        plugin(p, 'Rock')
        index = RecordIndex(self.dir)
        fid = index.ensure(p)

        class Data:
            pass
        data = Data()
        data.index, data.project = index, None
        tools = fnv_records.RecordsTools(data)
        tools._cond = {}
        with mock.patch.object(fnv_records, 'scan_references', lambda path, exists, cond=None: [(1, 2)]):
            with self.assertRaises(Exception):
                tools._ensure_refs([{'fileId': fid, 'path': str(p), 'name': 'Mine.esp'}])
        self.assertFalse(index.db.in_transaction)
        other = sqlite3.connect(str(self.dir / 'fnv' / 'records.sqlite'), timeout=2)
        self.addCleanup(other.close)
        other.execute("insert into files(path) values ('x')")
        other.commit()

    def test_atlas_connect_twice_no_schema_rerun(self):
        from geck_mcp import fnv_atlas
        path = self.dir / 'atlas.sqlite'
        db = fnv_atlas._connect(path)
        db.execute('DROP INDEX counts_address')
        db.commit()
        db.close()
        db = fnv_atlas._connect(path)
        self.addCleanup(db.close)
        found = db.execute("select 1 from sqlite_master where name = 'counts_address'").fetchone()
        self.assertIsNone(found, 'the schema script ran again on a file that already had it')
        self.assertGreater(db.execute('PRAGMA user_version').fetchone()[0], 0)

    def test_write_txn_rolls_back(self):
        from geck_mcp.fnv_state import sqlite_connect, write_txn
        db = sqlite_connect(self.dir / 't.sqlite')
        self.addCleanup(db.close)
        db.execute('create table t(x)')
        with self.assertRaises(ZeroDivisionError):
            with write_txn(db):
                db.execute('insert into t values (1)')
                1 / 0
        self.assertFalse(db.in_transaction)
        self.assertEqual(db.execute('select count(*) from t').fetchone()[0], 0)
        self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')


if __name__ == '__main__':
    unittest.main()
