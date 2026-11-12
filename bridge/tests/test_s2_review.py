"""PLAN-3 S2 review fixes (I1-I4, M1-M3, M7-M9, M11, M13)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp import fnv_journal, fnv_state  # noqa: E402
from geck_mcp.fnv_journal import Journal  # noqa: E402
from geck_mcp.fnv_state import GameIdentity  # noqa: E402

GAME = GameIdentity(4242, 1000)


class JournalReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / 'journal.jsonl'

    def test_i1_torn_last_line_does_not_swallow_the_next_record(self):
        j = Journal(self.path)
        a = j.add({'kind': 'x'}, GAME)
        with open(self.path, 'ab') as f:
            f.write(b'{"id": "torn", "kind": "x", "big": "aaaa')      # a server killed mid-append
        b = j.add({'kind': 'x'}, GAME)
        j.mark_undone([a['id']])
        got = {e['id']: e['undone'] for e in Journal(self.path).entries()}
        self.assertEqual(got, {a['id']: True, b['id']: False})

    def test_i2_empty_seq_sidecar_never_reuses_a_seq(self):
        j = Journal(self.path)
        for _ in range(5):
            j.add({'kind': 'x'}, GAME)
        j.seq_path.write_text('')                    # truncated by an in-place write that crashed
        self.assertEqual(j.add({'kind': 'x'}, GAME)['seq'], 6)
        j.seq_path.write_text('2')                   # behind the file for any reason
        self.assertEqual(Journal(self.path).add({'kind': 'x'}, GAME)['seq'], 7)

    def test_i3_add_outwaits_an_undo_holding_the_lock(self):
        from geck_mcp import fnv_link, fnv_live
        self.assertGreater(fnv_journal.ADD_TIMEOUT,
                           fnv_live.UNDO_LOCK_TIMEOUT + fnv_link.EngineLink.DEFAULT_TIMEOUT)

    def test_m1_entry_id_never_comes_from_the_caller(self):
        j = Journal(self.path)
        ids = [j.add({'kind': 'native_load', 'id': 1}, GAME)['id'] for _ in range(3)]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(len(j.entries()), 3)
        zero = j.add({'kind': 'native_load', 'id': 0}, GAME)
        self.assertIn(zero['id'], [e['id'] for e in j.entries()])


class StateReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_m2_unreadable_file_is_not_overwritten(self):
        path = self.dir / 'session.json'
        path.write_text(json.dumps({'pid': 1, 'ready': True}))
        real_text, real_bytes = Path.read_text, Path.read_bytes

        def deny(real):
            def read(self, *a, **k):
                if self == path:
                    raise PermissionError(32, 'sharing violation')
                return real(self, *a, **k)
            return read
        with mock.patch.object(Path, 'read_text', deny(real_text)),                 mock.patch.object(Path, 'read_bytes', deny(real_bytes)),                 mock.patch.object(fnv_state, 'READ_RETRY_SECONDS', 0.05):
            with self.assertRaises(fnv_state.StateError) as caught:
                fnv_state.update_json(path, lambda v: v.update({'x': 1}), {})
        self.assertEqual(caught.exception.code, 'STATE_UNREADABLE')
        self.assertEqual(json.loads(path.read_text()), {'pid': 1, 'ready': True})

    def test_m8_swaps_not_saved_without_a_game(self):
        from geck_mcp.fnv_code import CodeTools
        live = SimpleNamespace(project=SimpleNamespace(state_dir=str(self.dir)),
                               game=SimpleNamespace(process=lambda: (4242, 1)))
        a = CodeTools(live)
        self.assertEqual(a.swaps, {})
        a.swaps[0x401000] = {'function': 'A'}
        a._save_swaps(0x401000)
        b = CodeTools(SimpleNamespace(project=live.project, game=SimpleNamespace(process=lambda: None)))
        b._swaps_game = None
        b._save_swaps()
        stored = json.loads((self.dir / 'fnv/code/swaps.json').read_text())
        self.assertEqual(list(stored['swaps']), ['0x00401000'])

    def test_m11_procstate_start_as_text_matches_int(self):
        from geck_mcp.fnv_pred import ProcState
        state = ProcState(SimpleNamespace(project=SimpleNamespace(state_dir=str(self.dir))))
        state.save('s', 10, 500, [1])
        self.assertEqual(state.load('s', 10, '500'), [1])

    def test_m9_running_guards_ask_fresh(self):
        import inspect
        from geck_mcp import fnv_game, fnv_records
        self.assertIn('process(fresh=True)', inspect.getsource(fnv_game.Game.install_plugin))
        self.assertIn('process(fresh=True)', inspect.getsource(fnv_records.RecordsTools._profile_set))


class MaintainTests(unittest.TestCase):
    def test_i4_register_without_maintenance_starts_no_thread(self):
        import threading
        from geck_mcp import fnv_live_tools
        self.assertIn('maintain', fnv_live_tools.register.__code__.co_varnames)
        before = {t.name for t in threading.enumerate()}
        with mock.patch.object(fnv_live_tools.LiveService, 'maintain') as m:
            try:
                fnv_live_tools.register(SimpleNamespace(tool=lambda *a, **k: (lambda f: f)), _project(),
                                        ('read', 'act', 'control'), dict, maintain=False)
            except Exception:  # noqa: BLE001 - registration details do not matter here
                pass
        self.assertFalse(m.called)
        self.assertNotIn('fnv-state-maintenance', {t.name for t in threading.enumerate()} - before)


def _project():
    tmp = tempfile.mkdtemp()
    return SimpleNamespace(state_dir=tmp, project_id='t', game_root=tmp,
                           fnv=SimpleNamespace(mo2_root=tmp, mo2_profile='Dev', engine_control='full'))


class CreateIdentityTests(unittest.TestCase):
    def test_m3_place_list_uses_the_pinging_identity(self):
        import inspect
        from geck_mcp import fnv_create
        src = inspect.getsource(fnv_create)
        self.assertNotIn('entries(process=self.live.identity())', src)


if __name__ == '__main__':
    unittest.main()
