"""PLAN-3 S2.1/S2.2: the shared state primitives (locks, atomic writes, json state) and the one game identity."""
import json
import logging
import multiprocessing
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_state  # noqa: E402
from geck_mcp.fnv_state import GameIdentity, StateError, atomic_write, locked, read_json, update_json  # noqa: E402


def _increment(path, n):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from geck_mcp.fnv_state import update_json as up
    for _ in range(n):
        up(Path(path), lambda v: {'n': v['n'] + 1}, {'n': 0})


def _hold(path, ready, release):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from geck_mcp.fnv_state import locked as lk
    with lk(Path(path)):
        ready.set()
        release.wait(30)


class StateStoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_update_json_two_processes(self):
        path = self.dir / 'count.json'
        ctx = multiprocessing.get_context('spawn')
        procs = [ctx.Process(target=_increment, args=(str(path), 200)) for _ in range(2)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(120)
            self.assertEqual(p.exitcode, 0)
        self.assertEqual(json.loads(path.read_text())['n'], 400)

    def test_atomic_write_while_reader_holds_file(self):
        path = self.dir / 'state.json'
        path.write_bytes(b'{"a": 1}')
        handle = open(path, 'rb')
        self.addCleanup(handle.close)
        atomic_write(path, b'{"a": 2}')
        self.assertEqual(json.loads(path.read_bytes()), {'a': 2})
        self.assertEqual(list(self.dir.glob('*.tmp')), [])

    def test_read_json_corrupt_returns_default_and_warns_once(self):
        path = self.dir / 'bad.json'
        path.write_text('{"half": ')
        default = {'items': []}
        with self.assertLogs('geck_mcp.fnv_state', logging.WARNING) as logs:
            first = read_json(path, default)
            second = read_json(path, default)
        self.assertEqual(first, default)
        self.assertEqual(second, default)
        first['items'].append(1)
        self.assertEqual(default, {'items': []}, 'the default must be copied')
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(read_json(self.dir / 'missing.json', 5), 5)
        (self.dir / 'empty.json').write_text('')
        self.assertEqual(read_json(self.dir / 'empty.json', []), [])

    def test_update_json_mutating_fn_returns_new_value(self):
        path = self.dir / 's.json'
        value = update_json(path, lambda v: v.setdefault('k', []).append(1), {})
        self.assertEqual(value, {'k': [1]})
        self.assertEqual(json.loads(path.read_text()), {'k': [1]})

    def test_locked_times_out(self):
        path = self.dir / 'held.json'
        ctx = multiprocessing.get_context('spawn')
        ready, release = ctx.Event(), ctx.Event()
        proc = ctx.Process(target=_hold, args=(str(path), ready, release))
        proc.start()
        try:
            self.assertTrue(ready.wait(60))
            started = time.monotonic()
            with self.assertRaises(StateError) as caught:
                with locked(path, timeout=0.5):
                    pass
            self.assertEqual(caught.exception.code, 'STATE_LOCKED')
            self.assertLess(time.monotonic() - started, 3)
        finally:
            release.set()
            proc.join(30)

    def test_locked_reentrant_in_thread(self):
        path = self.dir / 'r.json'
        with locked(path):
            with locked(path, timeout=0.2):
                update_json(path, lambda v: {'ok': True}, {})
        self.assertEqual(read_json(path, {}), {'ok': True})
        # another thread waits while this one holds it
        order = []
        with locked(path):
            t = threading.Thread(target=lambda: (locked(path).__enter__(), order.append('other')))
            t.start()
            time.sleep(0.2)
            order.append('mine')
        t.join(5)
        self.assertEqual(order, ['mine', 'other'])


class GameIdentityTests(unittest.TestCase):
    def test_same_game_rules(self):
        cases = [
            (GameIdentity(10, 500), GameIdentity(10, 500), True),
            (GameIdentity(10, 500), GameIdentity(11, 500), False),
            (GameIdentity(10, 500), GameIdentity(10, 600), False),
            (GameIdentity(10, 500), GameIdentity(10, None), True),
            (GameIdentity(10, None), GameIdentity(10, None), True),
            (GameIdentity(10, 500), GameIdentity.parse({'pid': 10, 'started': '500'}), True),
        ]
        for a, b, same in cases:
            self.assertEqual(a.same_game(b), same, (a, b))
            self.assertEqual(b.same_game(a), same, (b, a))

    def test_parse_old_forms(self):
        self.assertEqual(GameIdentity.parse({'pid': 4, 'started': '77'}), GameIdentity(4, 77))
        self.assertEqual(GameIdentity.parse({'pid': 4, 'start': 77}), GameIdentity(4, 77))
        self.assertEqual(GameIdentity.parse('4:77'), GameIdentity(4, 77))
        self.assertEqual(GameIdentity.parse('4:?'), GameIdentity(4, None))
        self.assertEqual(GameIdentity.parse(4), GameIdentity(4, None))
        self.assertEqual(GameIdentity.parse([4, 77]), GameIdentity(4, 77))
        self.assertIsNone(GameIdentity.parse(None))
        self.assertIsNone(GameIdentity.parse('junk'))
        self.assertEqual(GameIdentity(4, 77).key(), '4:77')
        self.assertEqual(GameIdentity(4, None).key(), '4:?')

    def test_process_cached_one_second(self):
        from geck_mcp import fnv_game
        calls = []

        def image_pids(name):       # the process list: a snapshot since the speed work, tasklist.exe before
            calls.append(name)
            return [4242]
        game = fnv_game.Game.__new__(fnv_game.Game)
        with mock.patch.object(fnv_game, 'image_pids', image_pids), \
                mock.patch.object(fnv_game, 'process_started', lambda pid: 99):
            first = game.process()
            second = game.process()
        self.assertEqual(first, (4242, 99))
        self.assertEqual(first, second)
        self.assertEqual(calls, ['FalloutNV.exe'])


class ProcStateTests(unittest.TestCase):
    def test_procstate_keeps_sections_when_start_unknown(self):
        from types import SimpleNamespace
        from geck_mcp.fnv_pred import ProcState
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state = ProcState(SimpleNamespace(project=SimpleNamespace(state_dir=tmp.name)))
        state.save('watches', 10, 500, {'a': 1})
        # the same game, but this time its start time could not be read (R20)
        self.assertEqual(state.load('watches', 10, None), {'a': 1})
        state.save('other', 10, None, [1])
        self.assertEqual(state.load('watches', 10, 500), {'a': 1}, 'a save with an unknown start kept the section')
        # a different game process does not see it, and its save drops it
        self.assertIsNone(state.load('watches', 10, 900))
        state.save('other', 11, 900, [2])
        self.assertIsNone(state.load('watches', 10, 500))

if __name__ == '__main__':
    unittest.main()
