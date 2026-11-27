"""Gemini audit area 7 (journal, undo, state storage): BUG-102, 104, 105, 107, 108."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from fnv_mcp import fnv_state, fnv_journal  # noqa: E402
from fnv_mcp.fnv_live import LiveError  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live
        self.journal = self.live.journal


class OutcomeUnknownOverlapTests(Base):
    """BUG-105: a pending outcome-unknown entry sharing a target must not block an older entry (nor deadlock)."""

    def entries(self):
        old = self.journal.add({'kind': 'setting', 'undo': {'op': 'console', 'line': 'SetGS fX 1'}})
        stuck = self.journal.add({'kind': 'setting', 'state': 'pending',
                                  'undo': {'op': 'console', 'line': 'SetGS fX 2'}})
        return old, stuck

    def test_undo_count_undoes_the_older_overlapping_entry(self):
        old, stuck = self.entries()
        out = self.live.undo()
        self.assertEqual([r['id'] for r in out['results']], [old['id']])
        self.assertTrue(out['results'][0]['undone'], out)
        self.assertEqual([s['id'] for s in out['skipped']], [stuck['id']])
        self.assertFalse(self.journal.get(stuck['id'])['undone'])

    def test_revert_all_undoes_the_older_and_reports_the_unknown_one(self):
        old, stuck = self.entries()
        out = self.live.revert_all()
        self.assertEqual(out['reverted'], 1)
        self.assertEqual(out['failed'], [stuck['id']])
        self.assertTrue(self.journal.get(old['id'])['undone'])

    def test_a_confirmed_newer_entry_still_blocks(self):
        old = self.journal.add({'kind': 'setting', 'undo': {'op': 'console', 'line': 'SetGS fX 1'}})
        new = self.journal.add({'kind': 'setting', 'undo': {'op': 'console', 'line': 'SetGS fX 2'}})
        out = self.live.undo(journal_id=old['id'])
        self.assertFalse(out['results'][0]['undone'])
        self.assertEqual(out['results'][0]['later'], [new['id']])


class SetPositionUnreadableTests(Base):
    def test_unreadable_position_refuses_before_journaling(self):
        before = len(self.journal.entries())
        with mock.patch.object(type(self.live), 'values', lambda self, expr, ref=0: [1.0, None, 3.0]), \
                mock.patch.object(type(self.live), 'resolve', lambda self, t: {'ptr': 1, 'formId': '00000014'}):
            with self.assertRaises(LiveError) as cm:
                self.live.actor_control('player', 'set_position', value=[1, 2, 3])
        self.assertEqual(cm.exception.code, 'POSITION_UNREADABLE')
        self.assertEqual(len(self.journal.entries()), before)


class StateFileTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_update_json_parses_the_bytes_it_checked(self):
        path = self.dir / 's.json'
        path.write_text('{"a": 1}', encoding='utf-8')

        def deny(self_, *a, **k):
            raise OSError('simulated deny-read on a second read')
        with mock.patch.object(Path, 'read_text', deny):
            out = fnv_state.update_json(path, lambda v: v.update(b=2), {})
        self.assertEqual(out, {'a': 1, 'b': 2})

    def test_no_tmp_left_after_a_write_error(self):
        path = self.dir / 's.json'
        with mock.patch.object(os, 'fsync', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                fnv_state.atomic_write(path, 'x')
        self.assertEqual([p.name for p in self.dir.iterdir()], [])

    def test_no_tmp_left_after_a_non_permission_replace_error(self):
        path = self.dir / 's.json'
        with mock.patch.object(os, 'replace', side_effect=OSError('boom')):
            with self.assertRaises(OSError):
                fnv_state.atomic_write(path, 'x')
        self.assertEqual([p.name for p in self.dir.iterdir()], [])


class CompactFailureTests(unittest.TestCase):
    def test_failed_rewrite_does_not_duplicate_the_archive(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        j = fnv_journal.Journal(d / 'journal.jsonl')
        j.add({'kind': 'x', 'pid': 111, 'started': '1', 'undo': None})
        j.add({'kind': 'y', 'undo': None})     # legacy
        live = '222:2'
        real = fnv_journal.atomic_write

        def fail(path, *a, **k):
            if Path(path) == j.path:
                raise PermissionError('held')
            return real(path, *a, **k)
        with mock.patch.object(fnv_journal, 'atomic_write', fail):
            with self.assertRaises(PermissionError):
                j.compact(live)
        self.assertEqual(sum(p.stat().st_size for p in d.glob('journal-archive-*')), 0)
        out = j.compact(live)
        self.assertEqual(out['archived'], 2)
        lines = sum(len(p.read_text().splitlines()) for p in d.glob('journal-archive-*'))
        self.assertEqual(lines, 2)


if __name__ == '__main__':
    unittest.main()
