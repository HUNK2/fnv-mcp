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


if __name__ == '__main__':
    unittest.main()
