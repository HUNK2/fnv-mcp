"""Open audit bugs W5 (#17: fnv_status 'terminated from outside' after a launch where no game started) and Y4 (#34:
fnv_perk_entries evaluate ends a recording the caller started)."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp import fnv_engine_ai  # noqa: E402


class FailedLaunchNoteTests(unittest.TestCase):
    def test_w5_a_launch_where_no_game_started_has_no_outside_note(self):
        from fnv_mcp import fnv_game, fnv_layout, fnv_display
        from fakes import project
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        game = fnv_game.Game(project(Path(tmp.name)))
        # the previous game ran with companion build 3 and ended normally (its exit note is older than this launch)
        game.save_session(pid=None, launchedAt=time.time() - 600, companion=3)
        game.forget_process = lambda: None

        def no_game(*a, **kw):
            raise fnv_game.GameError('GAME_NOT_STARTED', 'no game process appeared')
        game._spawn = no_game
        with mock.patch.object(fnv_display, 'hold', lambda: None), \
                mock.patch.object(fnv_display, 'release', lambda: None):
            with self.assertRaises(fnv_game.GameError):
                game._launch('menu', 5, 'menu', None, SimpleNamespace(kind=fnv_layout.DIRECT))
            game.process = lambda fresh=False: None
            game.crash_since = lambda launched: None
            game.last_exit = lambda launched=None: {'forLastLaunch': False}
            game._bridge_quit = lambda launched: False
            status = game.status()
        self.assertIsNone(game.session().get('companion'))
        self.assertNotIn('exitNote', status)


class PerkEvaluateTests(unittest.TestCase):
    def tools(self, hooks):
        removed, started = [], []
        deep = SimpleNamespace(hooks=lambda: list(hooks), remove=lambda hid: removed.append(hid))
        live = SimpleNamespace(in_game=lambda: None, resolve=lambda name: {'ptr': 0x1000})
        ai = fnv_engine_ai.EngineAITools(SimpleNamespace(live=live, link=SimpleNamespace(one=lambda *a: {}), deep=deep,
                                                         _buffer=lambda: 0x2000))
        ai._call = lambda *a: None
        ai._floats = lambda addrs: [42.0]
        real = ai.perk_entries

        def perk_entries(action='start', *a, **kw):
            if action == 'start':
                started.append(True)
                return {'started': True}
            if action == 'read':
                ai._perk_since = (99, 99)       # what a real read does: moves the read position
                return {'calls': [], 'uses': []}
            return real(action, *a, **kw)
        ai.perk_entries = perk_entries
        return ai, removed, started

    def test_y4_evaluate_keeps_a_running_recording(self):
        labels = (fnv_engine_ai.PERK_PREFIX + 'apm', fnv_engine_ai.PERK_PREFIX + 'fn')
        ai, removed, started = self.tools([{'id': 1, 'label': labels[0]}, {'id': 2, 'label': labels[1]}])
        ai._perk_since = (5, 7)
        out = ai._perk_evaluate(9, 100, 'player', [])
        self.assertEqual(out['valueAfter'], 42.0)
        self.assertEqual(started, [], 'a running recording is not restarted')
        self.assertEqual(removed, [], 'nor are its hooks removed')
        self.assertEqual(ai._perk_since, (5, 7), "the caller's next read still sees its checks")

    def test_y4_evaluate_without_a_recording_cleans_up(self):
        ai, removed, started = self.tools([])
        ai._perk_evaluate(9, 100, 'player', [])
        self.assertEqual(started, [True])


if __name__ == '__main__':
    unittest.main()
