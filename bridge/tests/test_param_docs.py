"""Every tool parameter is described in the schema a client sees (tool quality pass, part 4).

UNDESCRIBED_ALLOWED is the allow-list of parameters that may still lack a description. It only shrinks: an entry
that has gained a description must be removed, and a new parameter must come with one."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402

import project_fixture  # noqa: E402
from fnv_mcp.param_docs import PARAM_DOCS  # noqa: E402
from fnv_mcp.server import make_server  # noqa: E402


def own_description(schema):
    return bool(schema.get('description')) or any(s.get('description') for s in schema.get('anyOf', []))


# {tool: {parameter, ...}} that may still have no description. Only ever remove entries.
UNDESCRIBED_ALLOWED: dict[str, set[str]] = {}


class ParamDocTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = project_fixture.ProjectFixture()
        self.fixture.setUp()

    async def asyncTearDown(self):
        self.fixture.tearDown()

    async def listing(self):
        async with Client(make_server(self.fixture.p)) as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    async def test_tools_with_six_or_more_parameters_are_described(self):
        missing = {}
        for name, tool in (await self.listing()).items():
            props = tool.input_schema.get('properties', {})
            if len(props) >= 6:
                gaps = [p for p, schema in props.items() if not own_description(schema)]
                if gaps:
                    missing[name] = gaps
        self.assertEqual(missing, {})

    async def test_every_parameter_is_described(self):
        missing = set()
        for name, tool in (await self.listing()).items():
            for param, schema in tool.input_schema.get('properties', {}).items():
                if not own_description(schema):
                    missing.add((name, param))
        allowed = {(tool, param) for tool, params in UNDESCRIBED_ALLOWED.items() for param in params}
        self.assertEqual(sorted(missing - allowed), [], 'parameters with no description')
        self.assertEqual(sorted(allowed - missing), [], 'described now: remove them from UNDESCRIBED_ALLOWED')

    async def test_place_rotation_is_in_degrees(self):
        rotation = (await self.listing())['fnv_place'].input_schema['properties']['rotation']
        self.assertIn('degrees', rotation['description'])
        self.assertNotIn('game units', rotation['description'])

    async def test_every_fix_names_a_real_parameter(self):
        from fnv_mcp.param_docs import PARAM_FIXES
        server = make_server(self.fixture.p)
        raw = {t.name: t.parameters.get('properties', {}) for t in server._tool_manager.list_tools()}
        for tool, params in PARAM_FIXES.items():
            for param, (old, new) in params.items():
                self.assertIn(param, raw.get(tool, {}), '%s.%s' % (tool, param))
                self.assertTrue(10 <= len(new) <= 300 and old != new, '%s.%s' % (tool, param))

    async def test_every_entry_is_real_and_needed(self):
        from fnv_mcp.fnv_live_tools import register  # noqa: F401 - the listing below is the truth
        server = make_server(self.fixture.p)
        raw = {t.name: t.parameters.get('properties', {}) for t in server._tool_manager.list_tools()}
        problems = []
        for tool, params in PARAM_DOCS.items():
            if tool not in raw:
                problems.append('%s: no such tool' % tool)
                continue
            for param, text in params.items():
                if param not in raw[tool]:
                    problems.append('%s.%s: no such parameter' % (tool, param))
                elif own_description(raw[tool][param]):
                    problems.append('%s.%s: already described in its signature' % (tool, param))
                elif not (isinstance(text, str) and 10 <= len(text) <= 300):
                    problems.append('%s.%s: description must be 10 to 300 characters' % (tool, param))
        self.assertEqual(problems, [])


if __name__ == '__main__':
    unittest.main()
