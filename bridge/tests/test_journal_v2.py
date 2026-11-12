"""PLAN-3 S2.3: the append-only journal (seq numbers, undo records, compaction, several processes)."""
import json
import multiprocessing
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp.fnv_journal import Journal  # noqa: E402
from geck_mcp.fnv_state import GameIdentity  # noqa: E402

GAME = GameIdentity(4242, 1000)
OLD = GameIdentity(1111, 500)


def _append(path, n, tag):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from geck_mcp.fnv_journal import Journal as J
    from geck_mcp.fnv_state import GameIdentity as G
    j = J(Path(path))
    for i in range(n):
        j.add({'kind': 'x', 'tag': tag, 'i': i, 'undo': None}, G(4242, 1000))


class JournalV2Tests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / 'journal.jsonl'
        self.j = Journal(self.path)

    def add(self, ident=GAME, undo=True, **kw):
        return self.j.add({'kind': 'memory', 'undo': {'op': 'write'} if undo else None, **kw}, ident)

    def test_two_processes_append_500_each(self):
        ctx = multiprocessing.get_context('spawn')
        procs = [ctx.Process(target=_append, args=(str(self.path), 500, t)) for t in 'ab']
        for p in procs:
            p.start()
        for p in procs:
            p.join(240)
            self.assertEqual(p.exitcode, 0)
        seqs = [e['seq'] for e in Journal(self.path).entries()]
        self.assertEqual(sorted(seqs), list(range(1, 1001)))
        self.assertEqual(Journal(self.path).last_seq(), 1000)

    def test_mark_undone_while_file_open_elsewhere(self):
        e = self.add()
        handle = open(self.path, 'rb')       # an antivirus scan or an editor
        self.addCleanup(handle.close)
        self.j.mark_undone([e['id']])
        self.assertTrue(self.j.entries()[0]['undone'])

    def test_undone_survives_reread_by_new_journal_object(self):
        a, b = self.add(), self.add()
        self.j.mark_undone([a['id']])
        fresh = {e['id']: e['undone'] for e in Journal(self.path).entries()}
        self.assertEqual(fresh, {a['id']: True, b['id']: False})
        lines = self.path.read_text().splitlines()
        self.assertEqual(json.loads(lines[0])['undone'], False, 'nothing is rewritten in place (R8)')

    def test_add_ignores_caller_pid_and_stamps_identity(self):
        e = self.j.add({'kind': 'patch', 'pid': 9, 'started': 'x', 'undo': None}, GAME)
        self.assertEqual((e['pid'], e['started']), (4242, '1000'))
        stored = self.j.entries()[0]
        self.assertEqual((stored['pid'], stored['started']), (4242, '1000'))

    def test_identity_from_game_callable(self):
        j = Journal(self.dir / 'g.jsonl', game=lambda: {'pid': 7, 'started': '70'})
        self.assertEqual(j.add({'kind': 'x', 'pid': 1})['pid'], 7)

    def test_orphan_never_auto_undone(self):
        e = self.j.add({'kind': 'memory', 'undo': {'op': 'write'}}, None)
        self.assertTrue(e['orphan'])
        self.assertNotIn('pid', e)
        self.assertEqual(self.j.entries(process=GAME), [])
        self.assertEqual(self.j.entries(undoable=True, process=GAME), [])

    def test_entries_filters(self):
        a = self.add()
        mark = self.j.last_seq()
        b = self.add()
        c = self.add(undo=False)
        d = self.add(ident=OLD)
        self.assertEqual([e['id'] for e in self.j.entries(since_seq=mark)], [b['id'], c['id'], d['id']])
        self.assertEqual([e['id'] for e in self.j.entries(process=GAME)], [a['id'], b['id'], c['id']])
        self.j.mark_undone([a['id']])
        self.assertEqual([e['id'] for e in self.j.entries(process=GAME, undoable=True)], [b['id']])

    def test_compact_keeps_live_process_and_archives_rest(self):
        old = [self.add(ident=OLD) for _ in range(3)]
        first = self.add()
        mark = self.j.last_seq()
        later = [self.add(), self.add()]
        self.j.mark_undone([old[0]['id'], first['id']])
        out = self.j.compact(GAME)
        self.assertEqual(out['kept'], 3)
        self.assertEqual(out['archived'], 3)
        fresh = Journal(self.path)
        self.assertEqual([e['id'] for e in fresh.entries()], [first['id']] + [e['id'] for e in later])
        self.assertTrue(fresh.entries()[0]['undone'], 'the undo record of a kept entry stays')
        self.assertEqual([e['id'] for e in fresh.entries(since_seq=mark, undoable=True, process=GAME)],
                         [e['id'] for e in later])
        archives = list(self.dir.glob('journal-archive-2*.jsonl'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(fresh.last_seq(), 6, 'seq numbers keep counting after a compaction')
        self.assertEqual(fresh.add({'kind': 'x'}, GAME)['seq'], 7)

    def test_legacy_entries_archived_once(self):
        self.path.write_text(json.dumps({'id': 'leg1', 'time': 1, 'undone': False, 'kind': 'memory'}) + '\n' +
                             json.dumps({'id': 'leg2', 'time': 2, 'undone': True, 'kind': 'memory', 'pid': 4242,
                                         'started': '1000'}) + '\n')
        j = Journal(self.path)
        self.assertEqual([e['id'] for e in j.entries()], ['leg1', 'leg2'])
        self.assertTrue(j.entries()[1]['undone'], 'an old in-place undone flag still counts')
        out = j.compact(GAME)
        self.assertEqual(out, {'kept': 1, 'archived': 1})
        legacy = self.dir / 'journal-archive-legacy.jsonl'
        self.assertEqual([json.loads(l)['id'] for l in legacy.read_text().splitlines()], ['leg1'])
        j.compact(GAME)
        self.assertEqual(len(legacy.read_text().splitlines()), 1)

    def test_incremental_read_sees_appends_from_other_process(self):
        self.add()
        self.assertEqual(len(self.j.entries()), 1)
        other = Journal(self.path)
        other.add({'kind': 'x'}, GAME)
        other.mark_undone([self.j.entries()[0]['id']])
        got = self.j.entries()
        self.assertEqual(len(got), 2)
        self.assertTrue(got[0]['undone'])

    def test_rewritten_file_is_read_again(self):
        self.add(ident=OLD)
        self.add()
        self.assertEqual(len(self.j.entries()), 2)
        Journal(self.path).compact(GAME)
        self.assertEqual(len(self.j.entries()), 1)


class LiveUndoTests(unittest.TestCase):
    """Undo goes through the journal lock: two servers never apply one undo twice."""

    def test_an_entry_undone_elsewhere_is_not_applied_again(self):
        from fakes import FakeLive
        fake = FakeLive()
        self.addCleanup(fake.close)
        live = fake.live
        address = 0x01200000
        fake.link.put(address, b'\x01\x02\x03\x04')
        entry = live.journal.add({'kind': 'memory', 'address': '0x%08X' % address, 'old': '01020304',
                                  'undo': {'op': 'write', 'address': '0x%08X' % address, 'hex': '01020304'}})
        fake.link.put(address, b'\x09\x09\x09\x09')
        real = live.journal.entries

        def listed_then_undone_elsewhere(*a, **k):
            out = real(*a, **k)
            Journal(live.journal.path).mark_undone([entry['id']])   # another server undoes it right after our list
            return out
        live.journal.entries = listed_then_undone_elsewhere
        out = live.undo()
        self.assertEqual([r['id'] for r in out['results']], [entry['id']], 'the entry was listed')
        self.assertFalse(out['changed'])
        self.assertEqual(fake.link.get(address, 4), b'\x09\x09\x09\x09')


if __name__ == '__main__':
    unittest.main()
