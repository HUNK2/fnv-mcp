"""PLAN-3 S7: fixes for the round-3 audit (BUG-LOG Round 3: U, K, W, X)."""
import ast
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

BRIDGE = Path(__file__).resolve().parents[1]


def tool_source(name):
    source = (BRIDGE / 'geck_mcp' / 'fnv_live_tools.py').read_text(encoding='utf-8')
    fn = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
    return fn


class NameExportPathTests(unittest.TestCase):
    """X1: fnv_name_export wrote to any path the call named, as a read-only tool."""

    def tools(self, tmp):
        from geck_mcp import fnv_naming
        db_path = Path(tmp) / 'atlas.sqlite'
        db = sqlite3.connect(db_path)
        db.executescript('CREATE TABLE runs (id, started, source, probes, notes);'
                         'CREATE TABLE harvests (id, run, at, probes, functions, notes);'
                         'CREATE TABLE harvest_counts (harvest, address, calls);'
                         "INSERT INTO harvests VALUES (1, 1, 0, '', 1, '');"
                         'INSERT INTO harvest_counts VALUES (1, 4198400, 5);')
        db.close()
        state = Path(tmp) / 'state'
        state.mkdir()
        live = SimpleNamespace(engine=lambda: SimpleNamespace(functions={}, identity=lambda: {}),
                               deep=SimpleNamespace(_image=lambda required=False: None),
                               project=SimpleNamespace(state_dir=state))
        tools = fnv_naming.NamingTools.__new__(fnv_naming.NamingTools)
        tools.live = live
        tools.db = lambda: sqlite3.connect(db_path)
        return tools, state

    def test_a_path_outside_the_trace_and_state_folders_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools, _ = self.tools(tmp)
            victim = Path(tmp) / 'Data' / 'MyMod.esp'
            victim.parent.mkdir()
            victim.write_bytes(b'TES4')
            with self.assertRaises(Exception) as caught:
                tools.export(path=str(victim))
            self.assertEqual(getattr(caught.exception, 'code', None), 'BAD_PATH')
            self.assertEqual(victim.read_bytes(), b'TES4')

    def test_dotdot_out_of_the_state_folder_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools, state = self.tools(tmp)
            with self.assertRaises(Exception) as caught:
                tools.export(path=str(state / '..' / 'escape.json'))
            self.assertEqual(getattr(caught.exception, 'code', None), 'BAD_PATH')
            self.assertFalse((Path(tmp) / 'escape.json').exists())

    def test_only_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools, state = self.tools(tmp)
            with self.assertRaises(Exception) as caught:
                tools.export(path=str(state / 'trace.esp'))
            self.assertEqual(getattr(caught.exception, 'code', None), 'BAD_PATH')

    def test_a_json_under_the_state_folder_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools, state = self.tools(tmp)
            want = state / 'traces' / 't.json'
            self.assertEqual(tools._export_path(str(want)), want.resolve())
            self.assertEqual(tools._export_path(None).suffix, '.json', 'the default stays in fnvsym/traces')

    def test_registered_as_control_not_read(self):
        from geck_mcp import fnv_live_tools
        self.assertEqual(fnv_live_tools.FNV_LIVE_TOOLS['fnv_name_export'][0], 'control')
        fn = tool_source('fnv_name_export')
        self.assertEqual([ast.unparse(d) for d in fn.decorator_list], ['server.tool(annotations=control)'])


class BareIdentifierTests(unittest.TestCase):
    """W2: resolve() sent any EditorID-shaped ref to eval, so fnv_ref ref=QuitGame ran QuitGame under read."""

    def live(self, commands):
        from fakes import FakeLive
        fake = FakeLive(engine_control='read')
        self.addCleanup(fake.close)
        live = fake.live

        def engine():
            if commands is None:
                raise RuntimeError('no engine map')
            return SimpleNamespace(commands=commands)
        live.engine = engine
        live.editor_id = lambda edid: None
        return fake, live

    def evals(self, fake):
        return [bytes.fromhex(args[0]).decode('cp1252') for name, args in fake.link.ops if name == 'eval']

    def test_a_command_name_is_never_evaluated(self):
        from geck_mcp.fnv_live import LiveError
        fake, live = self.live([{'name': 'QuitGame', 'short': 'qqq'}, {'name': 'KillAll', 'short': ''}])
        for name in ('QuitGame', 'qqq', 'killall'):
            with self.assertRaises(LiveError) as caught:
                live.resolve(name)
            self.assertEqual(caught.exception.code, 'REF_NOT_FOUND')
        self.assertEqual(self.evals(fake), [])

    def test_without_the_command_list_a_bare_name_is_not_evaluated(self):
        from geck_mcp.fnv_live import LiveError
        fake, live = self.live(None)
        with self.assertRaises(LiveError):
            live.resolve('QuitGame')
        self.assertEqual(self.evals(fake), [])

    def test_an_editor_id_still_resolves(self):
        fake, live = self.live([{'name': 'QuitGame', 'short': 'qqq'}])
        fake.link.expressions['DocMitchellRef'] = {'type': 'form', 'ptr': '0x12345678', 'formId': '00104C0C'}
        self.assertEqual(live.resolve('DocMitchellRef')['formId'], '00104C0C')


class ExpressionToolsAreActTests(unittest.TestCase):
    """W1: fnv_snapshot, fnv_diff and fnv_watch ran caller console lines and expressions as kind read."""

    def test_every_call_is_act(self):
        from geck_mcp.fnv_live_tools import gated_kind
        for name, action in (('fnv_snapshot', None), ('fnv_diff', None), ('fnv_watch', 'sample'),
                             ('fnv_watch', 'frames')):
            self.assertEqual(gated_kind(name, action, 'read'), 'act', name)

    def test_read_mode_refuses_a_snapshot_with_a_console_item(self):
        from fakes import FakeLive
        from geck_mcp.fnv_live_tools import gated_kind
        from geck_mcp.fnv_scenario import snapshot
        with FakeLive(engine_control='read') as fake:
            out = fake.live.run('fnv_snapshot', lambda: snapshot(fake.live, [{'console': 'player.kill'}], 'x'),
                                gated_kind('fnv_snapshot', None, 'read'))
            self.assertFalse(out.get('ok', True))
            self.assertEqual([name for name, _ in fake.link.ops if name in ('con', 'eval')], [])


if __name__ == '__main__':
    unittest.main()
