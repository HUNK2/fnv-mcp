"""PLAN-3 S2.4: every hand-rolled state file goes through the shared lock (session.json, swaps.json, the MO2
profile lists, crash signatures, warnings marks, log cursors, the probe files)."""
import json
import multiprocessing
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp import fnv_link  # noqa: E402
from fnv_mcp.fnv_game import Game  # noqa: E402


def bare_game(state):
    game = Game.__new__(Game)
    game.state = Path(state)
    game._session = None
    return game


def _sessions(state, tag, n):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fnv_mcp.fnv_game import Game as G
    game = G.__new__(G)
    game.state, game._session = Path(state), None
    for i in range(n):
        game.save_session(**{'%s%d' % (tag, i): i})


def _enable(root, tag, n):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from fnv_mcp import fnv_link as fl
    for i in range(n):
        fl.enable_mod(root, 'Dev', '%s%d' % (tag, i))


class StateFileTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_save_session_parallel_threads_and_processes_keep_all_keys(self):
        ctx = multiprocessing.get_context('spawn')
        for run in range(3):
            state = self.dir / ('run%d' % run)
            state.mkdir()
            procs = [ctx.Process(target=_sessions, args=(str(state), t, 25)) for t in 'ab']
            for p in procs:
                p.start()
            threads = [threading.Thread(target=_sessions, args=(str(state), t, 25)) for t in 'cd']
            for t in threads:
                t.start()
            for t in threads:
                t.join(120)
            for p in procs:
                p.join(120)
                self.assertEqual(p.exitcode, 0)
            stored = json.loads((state / 'session.json').read_text())
            missing = ['%s%d' % (t, i) for t in 'abcd' for i in range(25) if '%s%d' % (t, i) not in stored]
            self.assertEqual(missing, [], 'run %d lost keys' % run)

    def test_corrupt_session_json_does_not_break_status(self):
        (self.dir / 'session.json').write_text('{"pid": 12, "ready')
        game = bare_game(self.dir)
        self.assertEqual(game.session(), {})
        self.assertEqual(game.save_session(ready=True), {'ready': True})
        self.assertEqual(json.loads((self.dir / 'session.json').read_text()), {'ready': True})

    def test_session_journal_field_is_gone(self):
        game = bare_game(self.dir)
        game.save_session(pid=1, journal=None)
        self.assertNotIn('journal', json.loads((self.dir / 'session.json').read_text()))

    def test_swaps_two_servers(self):
        from fnv_mcp.fnv_code import CodeTools
        live = SimpleNamespace(project=SimpleNamespace(state_dir=str(self.dir)),
                               game=SimpleNamespace(process=lambda: (4242, 1)))
        a, b = CodeTools(live), CodeTools(live)
        self.assertEqual(a.swaps, {})
        self.assertEqual(b.swaps, {})           # both servers read the empty list
        a.swaps[0x401000] = {'function': 'A', 'old': '00'}
        a._save_swaps(0x401000)
        b.swaps[0x402000] = {'function': 'B', 'old': '11'}
        b._save_swaps(0x402000)
        stored = json.loads((self.dir / 'fnv/code/swaps.json').read_text())
        self.assertEqual(sorted(stored['swaps']), ['0x00401000', '0x00402000'])
        b.swaps.pop(0x402000)
        b._save_swaps(0x402000)
        stored = json.loads((self.dir / 'fnv/code/swaps.json').read_text())
        self.assertEqual(sorted(stored['swaps']), ['0x00401000'])

    def test_profile_edit_two_processes_keep_both_changes(self):
        profile = self.dir / 'profiles' / 'Dev'
        profile.mkdir(parents=True)
        (profile / 'modlist.txt').write_text('# header\n+Base\n')
        ctx = multiprocessing.get_context('spawn')
        procs = [ctx.Process(target=_enable, args=(str(self.dir), t, 30)) for t in 'ab']
        for p in procs:
            p.start()
        for p in procs:
            p.join(120)
            self.assertEqual(p.exitcode, 0)
        lines = (profile / 'modlist.txt').read_text().splitlines()
        missing = ['+%s%d' % (t, i) for t in 'ab' for i in range(30) if '+%s%d' % (t, i) not in lines]
        self.assertEqual(missing, [])
        self.assertEqual(lines[0], '# header')
        self.assertIn('+Base', lines)
        self.assertTrue(fnv_link.disable_mod(str(self.dir), 'Dev', 'a0'))
        self.assertIn('-a0', (profile / 'modlist.txt').read_text().splitlines())


if __name__ == '__main__':
    unittest.main()
