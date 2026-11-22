"""The companion's fast exit (2026-11-22). Every quit of the game under MO2 left a Crash Logger log: xNVSE's static
destructor faulted in LdrShutdownProcess after the game's own exit. After the exit message the companion now ends
the process when Windows detaches it, before xNVSE's destructors run, and says so in its exit note.

The fake xNVSE host returns a chosen code from main; a process the companion ended exits with 0 instead."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import engine_host  # noqa: E402


@engine_host.needs_zig
class FastExitHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.receipt = engine_host.build(cls.tmp.name)
        cls.folder = Path(cls.receipt['dll']).parent

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_host(self, *extra):
        local = Path(self.tmp.name) / ('local-' + uuid.uuid4().hex[:8])
        local.mkdir()       # the companion makes only its own folder in LOCALAPPDATA
        env = dict(os.environ, FNVMCP_PIPE='fnvmcp-fastexit-' + uuid.uuid4().hex[:8], LOCALAPPDATA=str(local),
                   FNVMCP_CONSOLE_LOG=str(local / 'console.log'))
        process = subprocess.run([str(self.folder / 'fnvmcp_fake_host.exe'), self.receipt['dll'], '1', *extra],
                                 env=env, capture_output=True, text=True, timeout=60)
        notes = sorted((local / 'fnvmcp').glob('exit-*.json'))
        return process, [json.loads(p.read_text(encoding='utf-8')) for p in notes]

    def test_after_the_exit_message_the_companion_ends_the_process(self):
        process, notes = self.run_host('7')
        self.assertEqual(process.returncode, 0, 'the fast exit ends the process before main\'s code is used')
        self.assertIn('exitHookThreadDone', process.stdout)     # the host ran to its end first
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0]['fastExit'])
        self.assertEqual(notes[0]['how'], 'window_or_menu')

    def test_without_the_exit_message_the_process_exits_normally(self):
        process, notes = self.run_host('7', 'noexit')
        self.assertEqual(process.returncode, 7)
        self.assertEqual(notes, [])


class LastExitTests(unittest.TestCase):
    def last_exit(self, note):
        from fnv_mcp.fnv_game import Game
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'fnvmcp'
            folder.mkdir()
            (folder / 'exit-42.json').write_text(json.dumps(note), encoding='utf-8')
            game = Game.__new__(Game)
            game.project = mock.Mock(state_dir=str(Path(tmp) / 'state'))
            with mock.patch.dict(os.environ, {'LOCALAPPDATA': tmp}):
                return game.last_exit(time.time() - 60)

    def test_last_exit_says_fast_exit(self):
        note = {'schema': 1, 'pid': 42, 'how': 'console', 'frame': 9}
        self.assertFalse(self.last_exit(note)['fastExit'])
        self.assertTrue(self.last_exit({**note, 'fastExit': True})['fastExit'])


if __name__ == '__main__':
    unittest.main()
