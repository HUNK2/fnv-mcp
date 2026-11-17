import json
from pathlib import Path
import sys
import unittest

from mcp import Client
from mcp.client.stdio import StdioServerParameters
from geck_mcp.server import make_server
from geck_mcp.fnv_tools import FNV_TOOLS
from geck_mcp.fnv_live_tools import FNV_LIVE_TOOLS
from geck_mcp.fnv_spec import FNV_SPEC_TOOLS
import project_fixture

ALL = set(FNV_TOOLS) | set(FNV_LIVE_TOOLS) | set(FNV_SPEC_TOOLS)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = project_fixture.ProjectFixture()
        self.fixture.setUp()

    async def asyncTearDown(self):
        self.fixture.tearDown()

    async def test_discovery_schemas_errors_and_resources(self):
        async with Client(make_server(self.fixture.p)) as client:
            listing = await client.list_tools()
            tools = listing.tools if hasattr(listing, 'tools') else listing
            by_name = {tool.name: tool for tool in tools}
            # one family: the GECK editor and legacy tools (geck_*) were removed on 2026-11-18
            self.assertEqual(set(by_name), ALL)
            for name in FNV_TOOLS:
                self.assertTrue(by_name[name].annotations.read_only_hint, name)
            for name, (kind, _) in {**FNV_LIVE_TOOLS, **FNV_SPEC_TOOLS}.items():
                self.assertEqual(by_name[name].annotations.read_only_hint, kind == 'read', name)
                self.assertEqual(by_name[name].annotations.destructive_hint, kind == 'act', name)
            result = await client.call_tool('fnv_func', {'query': 'anything'})
            self.assertTrue(result.is_error)
            self.assertEqual(result.structured_content['error']['code'], 'ENGINE_MAP_UNAVAILABLE')
            self.assertIsNotNone(by_name['fnv_status'].output_schema)
            result = await client.call_tool('fnv_spec', {'action': 'validate'})
            self.assertTrue(result.is_error)
            self.assertEqual(result.structured_content['error']['code'], 'NO_SPEC')
            resource = await client.read_resource('fnv://project/manifest')
            self.assertIn('"project_id": "test"', str(resource).replace('\\"', '"'))
            caps = json.loads((await client.read_resource('fnv://project/capabilities')).contents[0].text)
            self.assertEqual(caps['toolCount'], len(ALL))
            self.assertEqual(set(caps['tools']), ALL)
            prompts = await client.list_prompts()
            self.assertEqual(list(prompts.prompts if hasattr(prompts, 'prompts') else prompts), [])

    async def test_no_tool_takes_an_editor_session(self):
        async with Client(make_server(self.fixture.p)) as client:
            listing = await client.list_tools()
            tools = listing.tools if hasattr(listing, 'tools') else listing
            for tool in tools:
                schema = tool.input_schema if hasattr(tool, 'input_schema') else tool.inputSchema
                for key in ('session_id', 'editor_session'):
                    self.assertNotIn(key, schema.get('properties', {}), tool.name)

    async def test_real_stdio_process(self):
        path = Path(self.fixture.tmp.name) / 'project.json'
        path.write_text(self.fixture.p.model_dump_json())
        root = Path(__file__).resolve().parents[1]
        parameters = StdioServerParameters(command=sys.executable,
                                           args=[str(root / 'run_mcp.py'), '--project', str(path)], cwd=str(root))
        async with Client(parameters) as client:
            listing = await client.list_tools()
            tools = listing.tools if hasattr(listing, 'tools') else listing
            self.assertEqual({t.name for t in tools}, ALL)
            result = await client.call_tool('fnv_guide', {'topic': 'crash'})
            self.assertFalse(result.is_error, result)

    async def test_legacy_initialize_client(self):
        async with Client(make_server(self.fixture.p), mode='legacy') as client:
            listing = await client.list_tools()
            tools = listing.tools if hasattr(listing, 'tools') else listing
            self.assertIn('fnv_spec', {tool.name for tool in tools})
            result = await client.call_tool('fnv_guide', {'topic': 'crash'})
            self.assertFalse(result.is_error, result)


if __name__ == '__main__':
    unittest.main()
