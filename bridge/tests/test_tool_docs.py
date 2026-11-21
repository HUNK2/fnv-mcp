"""The generated tool reference (docs/TOOLS.md) and the data it is made from (fnv_mcp/tool_examples.py).

The reference is written by tools/gen_tool_docs.py from the server's own tool list, so it cannot drift from the
code: this test fails when the committed file differs from what the script writes now. Run
`python tools/gen_tool_docs.py` from bridge/ to write it again."""
import asyncio
import sys
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import project_fixture  # noqa: E402
from fnv_mcp.action_params import unused_arguments  # noqa: E402
from fnv_mcp.server import make_server  # noqa: E402
from fnv_mcp import tool_examples  # noqa: E402


def action_enum(schema):
    """The values the tool's `action` parameter allows, or None for a tool with no fixed list of actions."""
    action = (schema.get('properties') or {}).get('action') or {}
    return action.get('enum')


class ToolDocTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = project_fixture.ProjectFixture()
        cls.fixture.setUp()
        server = make_server(cls.fixture.p)
        cls.tools = {t.name: t for t in asyncio.run(server.list_tools())}

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDown()

    def test_every_tool_is_in_exactly_one_group(self):
        seen = [name for _, _, names in tool_examples.GROUPS for name in names]
        self.assertEqual(sorted(n for n in set(seen) if seen.count(n) > 1), [], 'tools in two groups')
        self.assertEqual(sorted(set(self.tools) - set(seen)), [], 'tools in no group')
        self.assertEqual(sorted(set(seen) - set(self.tools)), [], 'grouped names that are not tools')

    def test_every_tool_has_an_example_the_server_accepts(self):
        self.assertEqual(sorted(set(self.tools) ^ set(tool_examples.EXAMPLES)), [])
        for name, args in tool_examples.EXAMPLES.items():
            schema = self.tools[name].input_schema
            with self.subTest(tool=name):
                jsonschema.validate(args, schema)
                action, unused = unused_arguments(name, args, schema)
                self.assertEqual(unused, [], 'the example passes parameters its action does not use')

    def test_every_action_has_one_line(self):
        for name, tool in self.tools.items():
            enum = action_enum(tool.input_schema)
            with self.subTest(tool=name):
                if enum is None:
                    self.assertNotIn(name, tool_examples.ACTIONS)
                    continue
                lines = tool_examples.ACTIONS.get(name, {})
                self.assertEqual(sorted(lines), sorted(enum), 'actions of %s and their lines differ' % name)
                for action, line in lines.items():
                    self.assertTrue(10 <= len(line) <= 200 and '\n' not in line, (name, action))

    def test_renamed_tools_are_gone_and_point_at_tools(self):
        for old, new in tool_examples.RENAMED_TOOLS.items():
            self.assertNotIn(old, self.tools)
            self.assertIn(new.split()[0], self.tools, old)

    def test_committed_reference_is_current(self):
        import gen_tool_docs
        committed = (ROOT / 'docs' / 'TOOLS.md').read_text(encoding='utf-8')
        self.assertEqual(committed, gen_tool_docs.render(),
                         'docs/TOOLS.md is out of date: run python tools/gen_tool_docs.py from bridge/')


if __name__ == '__main__':
    unittest.main()
