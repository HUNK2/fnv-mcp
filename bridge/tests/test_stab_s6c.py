"""PLAN-3 S6 batch C: tools and agents (R6, R12, R17, R18, T3, T7, T8, T9)."""
import json
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


class SubtitleTests(unittest.TestCase):
    """R6: every second subtitle was lost when they came one per poll (`since` is exclusive)."""

    def test_subtitles_one_per_poll_all_caught(self):
        # ported from research/stabilize/repro/subtitle/repro_subtitles_skip.py
        from geck_mcp import fnv_inspect
        from geck_mcp.fnv_deep import RECORD_WORDS, RING, DeepTools
        SEQ_ADDR, RING_ADDR, TEXT = 0x5000, 0x10000000, 0x20000000
        ring, state = {}, {'seq': 0}

        def emit_record():
            state['seq'] += 1
            s = state['seq']
            words = [s] + [0] * (RECORD_WORDS - 1)
            words[12], words[22] = TEXT + s * 0x100, 1000 + s
            ring[s] = words

        class Link:
            def one(self, *op, **kw):
                if op[0] == 'hook':
                    return {'ok': True, 'hook': {'id': 7, 'seqAddress': '0x%X' % SEQ_ADDR, 'records': '0x%X' % RING_ADDR}}
                return {'ok': True}

            def write(self, *a):
                pass

        class Live:
            link = Link()

            def ready(self):
                return {}

            def changing(self, r):
                pass

            def u32s(self, addrs):
                if addrs == [SEQ_ADDR]:          # each poll: one new subtitle since the last
                    emit_record()
                    return [state['seq']]
                return [0] * len(addrs)

            def reads(self, spans):
                out = []
                for addr, size in spans:
                    if addr == fnv_inspect.GENERAL_SUBTITLES + 4:
                        out.append(b'\x01')
                    elif addr == fnv_inspect.SHOW_SUBTITLE:
                        out.append(bytes.fromhex('558bec83ec10535657') + b'\x90' * 15)
                    elif RING_ADDR <= addr < RING_ADDR + RING * RECORD_WORDS * 4:
                        first = (addr - RING_ADDR) // (RECORD_WORDS * 4) + 1
                        blob = b''
                        for k in range(size // (RECORD_WORDS * 4)):
                            blob += struct.pack('<%dI' % RECORD_WORDS, *(ring.get(first + k) or [0] * RECORD_WORDS))
                        out.append(blob)
                    elif addr >= TEXT:
                        out.append(b'line %d\0' % ((addr - TEXT) // 0x100))
                    else:
                        out.append(None)
                return out
        live = Live()
        deep = DeepTools.__new__(DeepTools)
        deep.live = live
        out = fnv_inspect.AudioTools(live, deep).subtitles(seconds=1.0, limit=100)
        got = [r['text'] for r in out['subtitles']]
        self.assertEqual(got, ['line %d' % k for k in range(1, len(got) + 1)], 'no line skipped')
        self.assertGreaterEqual(len(got), state['seq'] - 1)


class SessionRollbackTests(unittest.TestCase):
    """R12: a failed session start left prepare's crash-context hooks on and stopped a capture it had not started."""

    def run_start(self, capture_started):
        from geck_mcp import fnv_session
        calls = []

        class Capture:
            def start(self):
                calls.append('capture.start')
                return {'capturing': True, 'started': capture_started}

            def stop(self):
                calls.append('capture.stop')
        warn = SimpleNamespace(status=lambda: {'enabled': True}, capture=Capture(), clear=lambda: None)

        class Runner:
            def prepare(self, s):
                return {'crashContext': True}

            def mark(self):
                raise RuntimeError('link timeout during mark')

        class Control:
            def fixed(self, fps, journal=True):
                calls.append('fixed %s' % fps)

            def release(self):
                pass

            def snapshot(self, measure=False):
                return {'realFps': 60}
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
        return calls

    def test_session_start_rollback_restores_hooks_and_capture(self):
        calls = self.run_start(capture_started=False)            # the user's capture was already running
        self.assertIn('crash_context disable', calls)
        self.assertNotIn('capture.stop', calls)

    def test_rollback_stops_a_capture_it_started(self):
        self.assertIn('capture.stop', self.run_start(capture_started=True))


class CrawlItemTests(unittest.TestCase):
    """R17: an item that failed mid-test (EquipItem raised) stayed in the player's inventory."""

    def test_crawl_item_failure_removes_test_copy(self):
        from geck_mcp.fnv_crawl import CrawlTools
        lines, counts = [], iter([0, 1])

        class Live:
            def events(self, since):
                return {'seq': 0, 'events': []}

            def changing(self, reason):
                pass

            def values(self, exprs):
                return [next(counts)]

            def console(self, line, ref=0):
                lines.append(line)
                if 'EquipItem' in line and 'Unequip' not in line:
                    raise RuntimeError('the link dropped')
                return {'accepted': True}
        crawl = CrawlTools(Live(), sleep=lambda s: None)
        crawl._worn_items = lambda: []
        rows = []
        crawl._items_rows(rows, [('WEAP', 0x00001234, 'TestGun', None)], 10, False, None)
        self.assertIn('error', rows[0])
        self.assertIn('player.RemoveItem 00001234 1 1', lines, 'the test copy is taken back')


class OutsideNoteTests(unittest.TestCase):
    """R18: "terminated from outside" was said with an old companion and when the bridge itself force-quit."""

    def game(self, companion, quit_rows=()):
        from geck_mcp import fnv_game
        from fakes import project
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        game = fnv_game.Game(project(Path(tmp.name)))
        game._session = {'pid': 100, 'launchedAt': time.time() - 60, 'companion': companion}
        log = Path(game.project.state_dir) / 'fnv' / 'quit-log.jsonl'
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(''.join(json.dumps(r) + '\n' for r in quit_rows), encoding='utf-8')
        game.process = lambda fresh=False: None
        game.crash_since = lambda launched: None
        game.last_exit = lambda launched=None: None
        return game

    def test_outside_note_rules(self):
        self.assertIn('exitNote', self.game(companion=2).status())
        self.assertNotIn('exitNote', self.game(companion=1).status(), 'an old companion writes no exit notes')
        self.assertNotIn('exitNote', self.game(companion=None).status())
        forced = self.game(companion=2, quit_rows=[{'time': time.time() - 5, 'pid': 100, 'step': 'quit'}])
        self.assertNotIn('exitNote', forced.status(), 'the bridge quit it')


class NameExportTests(unittest.TestCase):
    """T3: fnv_name_export wrote an empty trace file."""

    def test_name_export_empty_writes_nothing(self):
        import sqlite3
        from geck_mcp import fnv_naming
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / 'atlas.sqlite'
            db = sqlite3.connect(db_path)
            db.executescript('CREATE TABLE runs (id, started, source, probes, notes);'
                             'CREATE TABLE harvests (id, run, at, probes, functions, notes);'
                             'CREATE TABLE harvest_counts (harvest, address, calls);')
            db.close()
            live = SimpleNamespace(engine=lambda: SimpleNamespace(functions={}, identity=lambda: {}),
                                   deep=SimpleNamespace(_image=lambda required=False: None),
                                   project=SimpleNamespace(state_dir=Path(tmp)))
            tools = fnv_naming.NamingTools.__new__(fnv_naming.NamingTools)
            tools.live = live
            tools.db = lambda: sqlite3.connect(db_path)
            target = Path(tmp) / 'out.json'
            out = tools.export(path=str(target))
            self.assertFalse(target.exists())
            self.assertEqual((out['changed'], out['exported']), (False, 0))


class AssetCheckTests(unittest.TestCase):
    """T7: fnv_asset_check took any path and surfaced raw parse errors."""

    def tools(self, tmp):
        from geck_mcp.fnv_data import DataError  # noqa: F401
        from geck_mcp.fnv_records import RecordsTools
        game_root = Path(tmp) / 'game'
        (game_root / 'Data').mkdir(parents=True)
        fnv = SimpleNamespace(mo2_root=Path(tmp) / 'mo2', mo2_profile='Dev')
        data = SimpleNamespace(project=SimpleNamespace(game_root=game_root, fnv=fnv), _live_plugins=lambda: None)
        tools = RecordsTools(data)
        return tools, game_root

    def test_asset_check_outside_data_refused(self):
        from geck_mcp import fnv_records
        from geck_mcp.fnv_data import DataError
        with tempfile.TemporaryDirectory() as tmp:
            tools, _ = self.tools(tmp)
            elsewhere = Path(tmp) / 'elsewhere.esp'
            elsewhere.write_bytes(b'TES4')
            orig = fnv_records.load_order
            fnv_records.load_order = lambda project, live=None: []
            try:
                with self.assertRaises(DataError) as caught:
                    tools.asset_check(str(elsewhere))
            finally:
                fnv_records.load_order = orig
            self.assertEqual(caught.exception.code, 'BAD_PATH')

    def test_malformed_plugin_is_bad_asset(self):
        from geck_mcp import fnv_records
        from geck_mcp.fnv_data import DataError
        with tempfile.TemporaryDirectory() as tmp:
            tools, game_root = self.tools(tmp)
            bad = game_root / 'Data' / 'Broken.esp'
            bad.write_bytes(b'TES4\xff\xff\xff\xff garbage')
            orig = fnv_records.load_order
            fnv_records.load_order = lambda project, live=None: []
            tools._asset_index = lambda entries: (SimpleNamespace(find=lambda rel: None, archives=[]), [])
            try:
                with self.assertRaises(DataError) as caught:
                    tools.asset_check(str(bad))
            finally:
                fnv_records.load_order = orig
            self.assertEqual(caught.exception.code, 'BAD_ASSET')


class CompileOperationTests(unittest.TestCase):
    """T9: geck_compile_status and geck_compile_resume gave different codes for an unknown operation."""

    def test_compile_status_and_resume_same_code(self):
        from test_compilation import CompileTests
        fixture = CompileTests('setUp')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        status = fixture.service.compile_status('no-such-operation')
        resume = fixture.service.compile_resume('no-such-operation')
        codes = {(status.get('error') or {}).get('code'), (resume.get('error') or {}).get('code')}
        self.assertEqual(codes, {'OPERATION_NOT_FOUND'})


class IgnoredParameterTests(unittest.TestCase):
    """T8: a tool parameter that the tool never reads (an agent cannot tell its argument had no effect)."""

    # Parameters that are read by the schema alone, on purpose: an explicit confirmation the caller must give.
    CONFIRMATIONS = {('geck_plugin_save', 'save_entire_active_plugin')}

    def test_ignored_params_rejected(self):
        import ast
        root = Path(__file__).resolve().parents[1] / 'geck_mcp'
        wrong = []
        for name in ('fnv_live_tools.py', 'fnv_tools.py', 'server.py'):
            source = (root / name).read_text(encoding='utf-8')
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) or not any(
                        isinstance(d, ast.Call) and getattr(d.func, 'attr', None) == 'tool' for d in node.decorator_list):
                    continue
                body = node.body[1:] if node.body and isinstance(node.body[0], ast.Expr) else node.body
                used = {n.id for stmt in body for n in ast.walk(stmt) if isinstance(n, ast.Name)}
                wrong += [(node.name, a.arg) for a in node.args.args + node.args.kwonlyargs
                          if a.arg not in used and (node.name, a.arg) not in self.CONFIRMATIONS]
        self.assertEqual(wrong, [])


if __name__ == '__main__':
    unittest.main()
