"""Tool quality part 5: a smaller tool list.

- The size budget: the whole listing (every tool as it goes on the wire) must not grow back. Measured on
  2026-11-18: 231,716 characters at dc21098 (138 tools); 197,668 after dropping the output schema stub and the
  default annotation values; 198,281 with the brief parameters; 217,593 on the merged tree, where every parameter
  has a description (187 had none, about 19,000 characters). A new tool or a longer description spends from the
  headroom; raise BUDGET only with a reason in the commit.
- The opt-in `core` toolset: 29 everyday tools (55,248 characters on 2026-11-21; 23 tools and 42,260 before, about 13,900
  tokens); the default list stays every tool; fnv_guide find marks a
  match outside the active toolsets."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402

import project_fixture  # noqa: E402
from fnv_mcp.fnv_guide import GuideTools  # noqa: E402
from fnv_mcp.server import make_server, toolset_list  # noqa: E402
from fnv_mcp.toolsets import ALWAYS, CORE_TOOLS, registered  # noqa: E402

BEFORE = 231716          # characters at dc21098, the start of the tool quality pass
BUDGET = 222800          # 222,636 with fnv_shader (12 actions, 11 parameters, all described; 139 tools), plus a little headroom
CORE_BUDGET = 55800      # the core list of 29: 55,248 measured 2026-11-21 (six engine-loop tools added), about 13,900 tokens


def listing_size(tools):
    return sum(len(json.dumps(t.model_dump(by_alias=True, exclude_none=True, mode='json'))) for t in tools)


class ListBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = project_fixture.ProjectFixture()
        self.fixture.setUp()

    async def asyncTearDown(self):
        self.fixture.tearDown()

    async def tools(self, **update):
        async with Client(make_server(self.fixture.p.model_copy(update=update))) as client:
            return (await client.list_tools()).tools

    async def test_the_whole_list_stays_within_budget(self):
        tools = await self.tools()
        size = listing_size(tools)
        self.assertLess(BUDGET, BEFORE * 0.97)                     # the list stays at least 3% under the start (was 5% until fnv_shader, 2026-11-28)
        self.assertLessEqual(size, BUDGET, 'the tool list grew to %d characters' % size)

    async def test_no_annotation_value_equals_the_mcp_default(self):
        defaults = {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': False, 'openWorldHint': True}
        for tool in await self.tools():
            dumped = tool.annotations.model_dump(by_alias=True, exclude_none=True) if tool.annotations else {}
            for key, value in dumped.items():
                self.assertNotEqual(defaults.get(key, object()), value, '%s.%s' % (tool.name, key))

    async def test_core_is_opt_in_and_small(self):
        everything = {t.name for t in await self.tools()}
        core = await self.tools(toolsets=['core'])
        self.assertEqual({t.name for t in core}, set(CORE_TOOLS) | ALWAYS)
        self.assertEqual(len(core), 29)
        self.assertLessEqual(listing_size(core), CORE_BUDGET)
        self.assertTrue(set(CORE_TOOLS) <= everything)
        self.assertGreater(len(everything), 130)                   # the default is still every tool
        both = {t.name for t in await self.tools(toolsets=['core', 'naming'])}
        self.assertIn('fnv_probe', both)
        self.assertTrue(set(CORE_TOOLS) <= both)
        self.assertEqual({t.name for t in await self.tools(toolsets=['core', 'game'])} - both,
                         everything - both)

    def test_core_tools(self):
        for name in ('fnv_status', 'fnv_place', 'fnv_spec', 'fnv_install', 'fnv_guide', 'fnv_undo'):
            self.assertIn(name, CORE_TOOLS)
        self.assertTrue(registered('fnv_place', {'core'}))
        self.assertFalse(registered('fnv_debug', {'core'}))
        self.assertTrue(registered('fnv_debug', {'core', 'game'}))
        self.assertTrue(registered('fnv_guide', set()))
        self.assertEqual(toolset_list('core,naming'), ['core', 'naming'])

    def test_find_says_when_a_match_is_outside_core(self):
        rows = [r for r in GuideTools(toolsets={'core'}).run('find', 'set a breakpoint on an engine function')['rows']
                if r['kind'] == 'tool']
        debug = next(r for r in rows if r['name'] == 'fnv_debug')
        self.assertFalse(debug['active'])
        self.assertIn('"game"', debug['description'])
        place = next(r for r in GuideTools(toolsets={'core'}).run('find', 'put a building on a slope')['rows']
                     if r['name'] == 'fnv_place')
        self.assertTrue(place['active'])


if __name__ == '__main__':
    unittest.main()
