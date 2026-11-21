"""PLAN-3 S6 batch C review fixes (fresh Opus review of 6acce6d..c85f854)."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


class OutsideNoteTests(unittest.TestCase):
    def game(self, companion, quit_rows=()):
        from fnv_mcp import fnv_game
        from fakes import project
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        game = fnv_game.Game(project(Path(tmp.name)))
        launched = time.time() - 60
        # what a real bridge quit leaves: _quit_result clears the session pid
        game._session = {'pid': None, 'launchedAt': launched, 'companion': companion}
        log = Path(game.project.state_dir) / 'fnv' / 'quit-log.jsonl'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(''.join(json.dumps(r) + '\n' for r in quit_rows), encoding='utf-8')
        game.process = lambda fresh=False: None
        game.crash_since = lambda launched: None
        game.last_exit = lambda launched=None: None
        return game, launched

    def test_i1_a_bridge_quit_after_the_launch_is_not_outside(self):
        game, launched = self.game(companion=3)
        log = Path(game.project.state_dir) / 'fnv' / 'quit-log.jsonl'
        log.write_text(json.dumps({'time': launched + 30, 'pid': 4242, 'step': 'quit'}) + '\n', encoding='utf-8')
        self.assertNotIn('exitNote', game.status())

    def test_i1_an_older_bridge_quit_does_not_hide_a_kill(self):
        game, launched = self.game(companion=3)
        log = Path(game.project.state_dir) / 'fnv' / 'quit-log.jsonl'
        log.write_text(json.dumps({'time': launched - 300, 'pid': 1, 'step': 'quit'}) + '\n', encoding='utf-8')
        self.assertIn('exitNote', game.status())

    def test_i2_exit_notes_need_companion_3(self):
        self.assertNotIn('exitNote', self.game(companion=2)[0].status(), 'build 2 may predate exit notes')
        source = (Path(__file__).resolve().parents[1] / 'native' / 'engine' / 'fnvmcp_engine.c').read_text(
            encoding='utf-8')
        self.assertIn('#define COMPANION_VERSION 3', source)


class AssetBuildDirTests(unittest.TestCase):
    def test_i3_a_just_built_plugin_can_be_checked(self):
        from fnv_mcp import fnv_records
        from fnv_mcp.fnv_records import RecordsTools
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_root, state = root / 'game', root / 'proj' / 'state'
            (game_root / 'Data').mkdir(parents=True)
            build = root / 'proj' / 'build'
            build.mkdir(parents=True)
            built = build / 'My.esp'
            built.write_bytes(b'x')
            fnv = SimpleNamespace(mo2_root=root / 'mo2', mo2_profile='Dev')
            data = SimpleNamespace(project=SimpleNamespace(game_root=game_root, fnv=fnv, state_dir=state),
                                   _live_plugins=lambda: None)
            tools = RecordsTools(data)
            tools._asset_index = lambda entries: (SimpleNamespace(find=lambda rel: None, archives=[]), [])
            orig, orig_assets = fnv_records.load_order, fnv_records._plugin_assets
            fnv_records.load_order = lambda project, live=None: []
            fnv_records._plugin_assets = lambda path: {}
            try:
                out = tools.asset_check(str(built))
            finally:
                fnv_records.load_order, fnv_records._plugin_assets = orig, orig_assets
            self.assertEqual(out['plugin'], 'My.esp')


class ProbeDryRunTests(unittest.TestCase):
    def test_i4_dry_run_on_a_changing_action_is_refused(self):
        import ast
        source = (Path(__file__).resolve().parents[1] / 'fnv_mcp' / 'fnv_live_tools.py').read_text(encoding='utf-8')
        fn = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef) and n.name == 'fnv_probe')
        text = ast.get_source_segment(source, fn)
        self.assertIn("dry_run and action != 'install'", text)


class SessionTests(unittest.TestCase):
    def test_m_rollback_only_disables_crash_context_prepare_turned_on(self):
        from fnv_mcp import fnv_session
        calls = []
        warn = SimpleNamespace(status=lambda: {'enabled': True},
                               capture=SimpleNamespace(start=lambda: {'capturing': False, 'started': False},
                                                       stop=lambda: calls.append('capture.stop')),
                               clear=lambda: None)

        class Runner:
            def prepare(self, s):
                return {'crashContext': 'off (HOOKS_UNAVAILABLE)'}

            def mark(self):
                raise RuntimeError('mark failed')

        class Control:
            def fixed(self, fps, journal=True):
                pass

            def release(self):
                pass

            def snapshot(self, measure=False):
                return {}
        patches = {'setup_check': lambda p: {'ready': True}, '_runner': lambda live: Runner(),
                   'warnings_for_live': lambda live: warn, 'shared_time': lambda live: (Control(), None),
                   '_deep': lambda live: SimpleNamespace(crash_context=lambda a: calls.append('crash_context %s' % a))}
        saved = {k: getattr(fnv_session, k) for k in patches}
        for k, v in patches.items():
            setattr(fnv_session, k, v)
        try:
            with self.assertRaises(RuntimeError):
                fnv_session.SessionTools(SimpleNamespace(project=None, session_state=None)).start('continue', 60, False)
        finally:
            for k, v in saved.items():
                setattr(fnv_session, k, v)
        self.assertNotIn('crash_context disable', calls, 'enable had failed: nothing to disable')


class CrawlRewearTests(unittest.TestCase):
    def test_m_a_failed_unequip_still_rewears_and_counts_the_cleanup(self):
        from fnv_mcp.fnv_crawl import CrawlTools
        lines, counts, rewore = [], iter([0, 1]), []

        class Live:
            def events(self, since):
                return {'seq': 0, 'events': []}

            def changing(self, reason):
                pass

            def values(self, exprs):
                return [next(counts)]

            def console(self, line, ref=0):
                lines.append(line)
                if 'UnequipItem' in line:
                    raise RuntimeError('the link dropped')
                return {'accepted': True}
        crawl = CrawlTools(Live(), sleep=lambda s: None)
        crawl._worn_items = lambda: ['00012345']
        crawl._rewear = lambda worn: rewore.append(worn)
        crawl._settle = lambda frames: None
        rows = []
        crawl._items_rows(rows, [('ARMO', 0x00001234, 'TestHat', None)], 10, False, None)
        self.assertEqual(rewore, [['00012345']], "the player's own armor goes back on")


class SubtitleLostTests(unittest.TestCase):
    def test_m_lost_lines_are_reported(self):
        import inspect
        from fnv_mcp import fnv_inspect
        self.assertIn('lost', inspect.getsource(fnv_inspect.AudioTools.subtitles).split('out = dict(')[1])


if __name__ == '__main__':
    unittest.main()
