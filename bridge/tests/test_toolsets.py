"""TOOL-MERGE step 1: toolsets chosen at server start.

The project key `toolsets` (or `run_mcp.py --toolsets game,naming`) picks which families are registered: game
(fnv_*, fnv_spec) and naming (fnv_name_*, fnv_probe, fnv_atlas). Default: both. fnv_guide is always registered, and
its find still names a hidden tool with the toolset that turns it on. (The editor and legacy toolsets, the geck_*
tools, were removed on 2026-11-18; an old project file naming them still loads.)"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from geck_mcp import server as server_module  # noqa: E402
from geck_mcp.config import Project  # noqa: E402
from geck_mcp.fnv_guide import GuideTools, tool_table  # noqa: E402
from geck_mcp.server import make_server  # noqa: E402
from geck_mcp.toolsets import ALWAYS, DEFAULT_TOOLSETS, toolset_of  # noqa: E402
import project_fixture  # noqa: E402


class ToolsetOfTests(unittest.TestCase):
    def test_core_holds_the_five_most_used(self):
        # The five tools that made 66% of 4,228 real calls (critique 2026-11-21).
        from geck_mcp.toolsets import CORE_TOOLS
        for name in ('fnv_native', 'fnv_func', 'fnv_input', 'fnv_console', 'fnv_mem_read'):
            self.assertIn(name, CORE_TOOLS)

    def test_families(self):
        self.assertEqual(toolset_of('fnv_ref'), 'game')
        self.assertEqual(toolset_of('fnv_func'), 'game')
        self.assertEqual(toolset_of('fnv_spec'), 'game')
        self.assertEqual(toolset_of('fnv_name_dossier'), 'naming')
        self.assertEqual(toolset_of('fnv_probe'), 'naming')
        self.assertEqual(toolset_of('fnv_atlas'), 'naming')

    def test_project_key(self):
        p = project_fixture.ProjectFixture()
        p.setUp()
        self.addCleanup(p.tearDown)
        self.assertEqual(p.p.active_toolsets(), set(DEFAULT_TOOLSETS))
        self.assertEqual(p.p.model_copy(update={'toolsets': ['game']}).active_toolsets(), {'game'})
        data = {**p.p.model_dump(mode='json'), 'toolsets': ['game', 'nonsense']}
        with self.assertRaises(ValidationError):
            Project.model_validate(data)

    def test_an_old_project_file_still_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'old.json'
            path.write_text(json.dumps({
                'project_id': 'old', 'launcher': 'native', 'game_root': tmp, 'state_dir': 's', 'helper': 'h.exe',
                'records': [{'editor_id': 'A', 'form_id': '01000800'}], 'helper_timeout_seconds': 30,
                'legacy_tools': True, 'toolsets': ['game', 'editor', 'legacy']}), encoding='utf-8')
            project = Project.load(path)
        self.assertEqual(project.active_toolsets(), {'game'})


class ToolsetServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = project_fixture.ProjectFixture()
        self.fixture.setUp()

    async def asyncTearDown(self):
        self.fixture.tearDown()

    async def names(self, **update):
        project = self.fixture.p.model_copy(update=update)
        async with Client(make_server(project)) as client:
            listing = await client.list_tools()
        return {t.name for t in (listing.tools if hasattr(listing, 'tools') else listing)}

    async def test_default_is_every_family(self):
        names = await self.names()
        self.assertEqual({toolset_of(n) for n in names}, set(DEFAULT_TOOLSETS))
        self.assertFalse(any(n.startswith('geck_') for n in names))

    async def test_game_only(self):
        names = await self.names(toolsets=['game'])
        self.assertEqual({toolset_of(n) for n in names - ALWAYS}, {'game'})
        self.assertIn('fnv_ref', names)
        self.assertIn('fnv_spec', names)
        self.assertNotIn('fnv_probe', names)

    async def test_naming_only_keeps_the_guide(self):
        names = await self.names(toolsets=['naming'])
        self.assertEqual({toolset_of(n) for n in names - ALWAYS}, {'naming'})
        self.assertIn('fnv_guide', names)

    async def test_each_set_adds_its_tools(self):
        every = await self.names(toolsets=['game', 'naming'])
        parts = set()
        for name in ('game', 'naming'):
            parts |= await self.names(toolsets=[name])
        self.assertEqual(parts, every)

    def test_command_line_flag(self):
        seen = {}

        def fake_make(project, **kwargs):
            seen['sets'] = project.active_toolsets()
            return mock.Mock()
        with mock.patch.object(server_module.Project, 'load', return_value=self.fixture.p), \
                mock.patch.object(server_module.fnv_tools, 'warm_up'), \
                mock.patch.object(server_module, 'make_server', side_effect=fake_make):
            server_module.main(['--project', 'x.json', '--toolsets', 'game,naming'])
            self.assertEqual(seen['sets'], {'game', 'naming'})
            server_module.main(['--project', 'x.json', '--toolsets', 'game'])
            self.assertEqual(seen['sets'], {'game'})
            server_module.main(['--project', 'x.json'])
            self.assertEqual(seen['sets'], set(DEFAULT_TOOLSETS))
            with self.assertRaises(SystemExit):
                server_module.main(['--project', 'x.json', '--toolsets', 'game,editor'])


class GuideNamesHiddenToolsTests(unittest.TestCase):
    def test_every_row_has_a_toolset(self):
        table = tool_table()
        self.assertEqual([n for n, t in table.items() if t.get('toolset') != toolset_of(n)], [])
        self.assertIn('fnv_spec', table)
        self.assertFalse([n for n in table if n.startswith('geck_')])

    def test_hidden_tools_say_how_to_turn_them_on(self):
        table = tool_table({'game'})
        self.assertIn('toolsets', table['fnv_probe']['description'])
        self.assertIn('naming', table['fnv_probe']['description'])
        self.assertNotIn('toolsets', table['fnv_ref']['description'])
        rows = GuideTools(toolsets={'game'}).find('probe install functions', limit=50)['rows']
        hit = next(r for r in rows if r['name'] == 'fnv_probe')
        self.assertIn('naming', hit['description'])
        self.assertEqual(hit['toolset'], 'naming')


if __name__ == '__main__':
    unittest.main()
