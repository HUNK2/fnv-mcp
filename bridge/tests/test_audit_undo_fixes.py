"""Journal and undo fixes from the round-3/4 audits: U4, U5, U8, V5, V6 (GitHub issues 2, 3, 6, 29, 30)."""
import json
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from fnv_mcp import fnv_time  # noqa: E402

ADDRESS = 0x01000000


class Base(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live
        self.journal = self.live.journal

    def memory_entry(self, address=ADDRESS, old='00000000', state='confirmed'):
        return self.journal.add({'kind': 'memory', 'state': state, 'address': '0x%08X' % address,
                                 'undo': {'op': 'write', 'address': '0x%08X' % address, 'hex': old}})


class ConfirmAfterUndoTests(Base):
    """U4: an undo between a change and its confirm."""

    def test_undo_after_the_change_stays_undone_on_confirm(self):
        self.fake.link.put(ADDRESS, b'\x00' * 4)
        e = self.memory_entry(state='pending')
        self.fake.link.put(ADDRESS, b'\x01\x02\x03\x04')        # the change landed
        out = self.live.revert_all()                            # another server reverts before the confirm
        self.assertEqual(out['reverted'], 1)
        self.assertNotIn('changeSeen', out['results'][0])
        self.assertEqual(self.fake.link.get(ADDRESS, 4), b'\x00' * 4)
        self.journal.confirm(e['id'], {'old': '00000000'})
        now = self.journal.get(e['id'])
        self.assertTrue(now['undone'], 'the change is no longer in the game')
        self.assertEqual(now['state'], 'confirmed')
        self.assertEqual(self.journal.entries(undoable=True), [])

    def test_undo_before_the_change_is_reopened_by_confirm(self):
        self.fake.link.put(ADDRESS, b'\x00' * 4)
        e = self.memory_entry(state='pending')
        self.live.revert_all()                                  # the change has not landed yet (S3 review I1)
        self.fake.link.put(ADDRESS, b'\x01\x02\x03\x04')        # now it lands
        self.journal.confirm(e['id'])
        self.assertFalse(self.journal.get(e['id'])['undone'])

    def test_writes_op_reports_a_seen_change(self):
        self.fake.link.put(ADDRESS, b'\x05\x06')
        e = self.journal.add({'kind': 'x', 'state': 'pending',
                              'undo': {'op': 'writes', 'writes': [['0x%08X' % ADDRESS, '0000']]}})
        self.live.revert_all()
        self.journal.confirm(e['id'])
        self.assertTrue(self.journal.get(e['id'])['undone'])


class UndoCountSkipsUnknownTests(Base):
    """U5: undo(count) is not stuck on a newest pending entry the executor refuses."""

    def test_pending_console_entry_is_passed_over_and_listed(self):
        old = self.journal.add({'kind': 'setting', 'undo': {'op': 'console', 'line': 'SetGS fX 1'}})
        stuck = self.journal.add({'kind': 'actor', 'state': 'pending', 'undo': {'op': 'console', 'line': 'tgm'}})
        out = self.live.undo()
        self.assertEqual([r['id'] for r in out['results']], [old['id']])
        self.assertTrue(out['results'][0]['undone'])
        self.assertEqual([s['id'] for s in out['skipped']], [stuck['id']])
        self.assertFalse(self.journal.get(stuck['id'])['undone'])

    def test_no_skipped_key_without_unknown_entries(self):
        self.journal.add({'kind': 'setting', 'undo': {'op': 'console', 'line': 'SetGS fX 1'}})
        self.assertNotIn('skipped', self.live.undo())

    def test_pending_repeatable_entry_is_still_undone(self):
        self.fake.link.put(ADDRESS, b'\x00' * 4)
        e = self.memory_entry(state='pending')
        out = self.live.undo()
        self.assertEqual([r['id'] for r in out['results']], [e['id']])
        self.assertTrue(out['results'][0]['undone'])


class ProbeUndoTests(Base):
    """U8: the probe-remove undo flips the session under one lock, newest probe entry first."""

    def session_path(self):
        return Path(self.fake.project.state_dir) / 'probe_session.json'

    def write_session(self, **fields):
        path = self.session_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'pid': 4242, 'started': '1', 'armed': True, **fields}), encoding='utf-8')

    def probe_entry(self):
        return self.journal.add({'kind': 'probe', 'probes': 1, 'sites': 0, 'undo': {'op': 'probe_remove'}})

    def test_older_probe_entry_waits_for_the_newer_one(self):
        older, newer = self.probe_entry(), self.probe_entry()
        r = self.live.undo(journal_id=older['id'])['results'][0]
        self.assertFalse(r['undone'])
        self.assertEqual(r['later'], [newer['id']])

    def test_revert_all_undoes_both_and_flips_the_session(self):
        self.write_session(installedAt=2)
        self.probe_entry()
        self.probe_entry()
        out = self.live.revert_all()
        self.assertEqual(out['reverted'], 2)
        session = json.loads(self.session_path().read_text(encoding='utf-8'))
        self.assertFalse(session['armed'])
        self.assertEqual(session['installedAt'], 2)

    def test_session_of_another_game_is_left_alone(self):
        self.write_session(pid=99)
        self.probe_entry()
        self.live.revert_all()
        self.assertTrue(json.loads(self.session_path().read_text(encoding='utf-8'))['armed'])

    def test_flip_runs_under_the_session_lock(self):
        from fnv_mcp import fnv_state
        self.write_session()
        self.probe_entry()
        held = []
        real = fnv_state.write_json

        def spy(path, value, indent=None):
            key = str(path) + '.lock'
            locks = getattr(fnv_state._held, 'locks', {}) or {}
            held.append(any(k.endswith('probe_session.json.lock') for k in locks) or key in locks)
            return real(path, value, indent)
        fnv_state.write_json = spy
        self.addCleanup(setattr, fnv_state, 'write_json', real)
        self.live.revert_all()
        self.assertEqual(held, [True])


class PauseUndoTests(Base):
    """V5: the pause undo resumes a journaled pause whose shared flag was lost, and a failed read keeps the flag."""

    def setUp(self):
        super().setUp()
        link = self.fake.link
        link.put(fnv_time.TIME_GLOBAL, struct.pack('<If', 0, 0.0))
        from fnv_mcp.fnv_inject import shared_time
        self.control, _ = shared_time(self.live)
        self.control.functions = lambda: {'disable': 0x00AA0000, 'enable': 0x00AA0010, 'clamp': 0x00AA0020}
        real = link._op

        def op(name, args):
            if name == 'call' and int(args[0], 16) in (0x00AA0000, 0x00AA0010):
                counter = struct.unpack('<I', link.get(fnv_time.TIME_GLOBAL, 4))[0]
                counter += 1 if int(args[0], 16) == 0x00AA0000 else -1
                link.put(fnv_time.TIME_GLOBAL, struct.pack('<I', counter))
            return real(name, args)
        link._op = op

    def counter(self):
        return struct.unpack('<I', self.fake.link.get(fnv_time.TIME_GLOBAL, 4))[0]

    def test_lost_flag_pause_is_resumed_by_revert_all(self):
        self.control.pause()
        self.assertEqual(self.counter(), 1)
        # the save of the flag failed (or another read cleared it): the file says nothing for this game
        self.control.state.save('time_pause', self.control.pid, self.control.start, None)
        self.control.require_game()
        self.assertFalse(self.control.paused_by_us)
        out = self.live.revert_all()
        self.assertEqual(out['reverted'], 1)
        self.assertEqual(self.counter(), 0)

    def test_no_enable_when_the_counter_is_zero(self):
        self.control.pause()
        self.control.state.save('time_pause', self.control.pid, self.control.start, None)
        self.fake.link.put(fnv_time.TIME_GLOBAL, struct.pack('<I', 0))   # released by hand
        self.live.revert_all()
        self.assertEqual(self.counter(), 0)

    def test_unreadable_state_file_keeps_the_flag(self):
        self.control.pause()
        path = self.control.state._path()
        path.write_text('{"time_pause": {"pid": 42', encoding='utf-8')   # met a write in place
        self.control.require_game()
        self.assertTrue(self.control.paused_by_us)
        path.write_text('', encoding='utf-8')
        self.control.require_game()
        self.assertTrue(self.control.paused_by_us)

    def test_file_that_says_not_paused_still_clears_the_flag(self):
        self.control.pause()
        self.control.state.save('time_pause', self.control.pid, self.control.start, None)
        self.control.require_game()
        self.assertFalse(self.control.paused_by_us)


class RevertAllOrderIndexTests(Base):
    """V6: the U1 order check reads the journal once per run, not once per entry."""

    def test_journal_read_once_for_the_order_check(self):
        n = 40
        self.fake.link.put(ADDRESS, b'\x00' * (4 * n))
        for i in range(n):
            self.memory_entry(ADDRESS + 4 * i)
        calls = []
        real = self.journal.entries
        self.journal.entries = lambda *a, **k: calls.append(1) or real(*a, **k)
        self.addCleanup(delattr, self.journal, 'entries')
        out = self.live.revert_all()
        self.assertEqual(out['reverted'], n)
        self.assertLessEqual(len(calls), 2)          # revert_all's own list and its notUndoable list

    def test_overlap_still_refused(self):
        self.fake.link.put(ADDRESS, b'\x00' * 4)
        older = self.memory_entry()
        newer = self.memory_entry()
        r = self.live.undo(journal_id=older['id'])['results'][0]
        self.assertFalse(r['undone'])
        self.assertEqual(r['later'], [newer['id']])

    def test_entry_added_during_a_run_is_seen(self):
        from fnv_mcp.fnv_live import _OpenTargets
        self.fake.link.put(ADDRESS, b'\x00' * 4)
        older = self.memory_entry()
        index = _OpenTargets(self.live)
        newer = self.memory_entry()                  # another server adds an overlapping change meanwhile
        self.assertEqual(self.live._later_open(older, index), [newer['id']])

    def test_entry_undone_elsewhere_is_not_a_blocker(self):
        from fnv_mcp.fnv_live import _OpenTargets
        self.fake.link.put(ADDRESS, b'\x00' * 4)
        older = self.memory_entry()
        newer = self.memory_entry()
        index = _OpenTargets(self.live)
        self.journal.mark_undone([newer['id']])      # another server undid it
        self.assertEqual(self.live._later_open(older, index), [])

    def test_reverse_order_revert_all_of_overlapping_entries(self):
        self.fake.link.put(ADDRESS, b'\x00' * 4)
        self.memory_entry(old='00000000')
        self.fake.link.put(ADDRESS, b'\x01' * 4)
        self.memory_entry(old='01010101')
        self.fake.link.put(ADDRESS, b'\x02' * 4)
        out = self.live.revert_all()
        self.assertEqual(out['reverted'], 2)
        self.assertEqual(self.fake.link.get(ADDRESS, 4), b'\x00' * 4)


if __name__ == '__main__':
    unittest.main()
