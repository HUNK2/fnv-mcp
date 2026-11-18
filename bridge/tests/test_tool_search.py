"""Tool quality part 2: an agent that describes a task in plain words gets the right tool in the first three results
of fnv_guide find. This table is the regression guard: a new tool adds its phrases here and its use_when phrases in
fnv_live_tools.USE_WHEN. Rows: (task phrase, expected tool[, expected action of the best row for that tool])."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp.fnv_guide import GuideTools, tool_table, words  # noqa: E402
from geck_mcp.fnv_live_tools import USE_WHEN  # noqa: E402

TABLE = [
    ('put a building on a slope', 'fnv_place'),          # the known failure: it answered fnv_plugin_build
    ('the game crashed', 'fnv_crash'),
    ('why did the game crash', 'fnv_crash'),
    ('my script does not run', 'fnv_script_errors'),
    ('is the game running and ready', 'fnv_status'),
    ('start the game', 'fnv_game', 'launch'),
    ('quit the game', 'fnv_game', 'quit'),
    ('install my plugin into the mod profile', 'fnv_install'),
    ('build the plugin from the project spec', 'fnv_spec', 'build'),
    ('textures and meshes my plugin names are missing', 'fnv_asset_check'),
    ('engine warnings about missing files', 'fnv_warnings'),
    ('where is the player', 'fnv_player'),
    ('what is the player looking at', 'fnv_crosshair'),
    ('find objects near the player', 'fnv_refs_near'),
    ('run a console command', 'fnv_console'),
    ('teleport the player to a cell', 'fnv_move'),
    ('take a picture of an object from several angles', 'fnv_photo', 'ref'),
    ('screenshot of the game', 'fnv_screenshot'),
    ('undo my last change', 'fnv_undo'),
    ('what did I change in the game', 'fnv_journal'),
    ('set a quest stage', 'fnv_script_set'),
    ('quest objectives and current stage', 'fnv_quest'),
    ('why is the quest marker missing', 'fnv_quest', 'targets'),
    ('the npc does not see the player', 'fnv_detection'),
    ('why is this npc hostile to me', 'fnv_reaction'),
    ('which ai package is the npc running', 'fnv_ai_packages'),
    ('the npc cannot find a path', 'fnv_pathing'),
    ('the game is slow, low frame rate', 'fnv_profile'),
    ('the game is frozen and hangs', 'fnv_threads', 'hang'),
    ('memory keeps growing, a leak', 'fnv_memory'),
    ('read a field of an object in memory', 'fnv_mem_read'),
    ('write a value into game memory', 'fnv_mem_write'),
    ('call an engine function with arguments', 'fnv_engine_call'),
    ('how are barter prices computed', 'fnv_systems', 'barter'),
    ('find an engine function by name', 'fnv_func', 'find'),
    ('decompiled source code of a function', 'fnv_func', 'code'),
    ('who calls this function', 'fnv_func', 'callers'),
    ('field offsets of an engine class', 'fnv_class', 'layout'),
    ('change a game setting while playing', 'fnv_setting_set'),
    ('what does this game setting do', 'fnv_setting_info'),
    ('look up a record by its editor id', 'fnv_record', 'find'),
    ('which plugins override this record', 'fnv_conflicts'),
    ('show the load order', 'fnv_loadorder'),
    ('enable a mod in the dev profile', 'fnv_profile_set'),
    ('test my mod with a scenario', 'fnv_test'),
    ('wait until the game is loaded', 'fnv_wait_for'),
    ('set a breakpoint on an engine function', 'fnv_debug', 'break'),
    ('step through a script line by line', 'fnv_script_debug'),
    ('talk to an npc and pick dialogue choices', 'fnv_dialogue_choose'),
    ('every line an npc could say for a topic', 'fnv_dialogue', 'tree'),
    ('items in the player inventory', 'fnv_inventory'),
    ('actor health, skills and special stats', 'fnv_actor_values'),
    ('make an npc attack someone', 'fnv_actor_control'),
    ('press a key in the game', 'fnv_input'),
    ('click a button in a menu', 'fnv_ui_do'),
    ('which menu is open', 'fnv_menu'),
    ('the object is invisible and does not draw', 'fnv_visibility'),
    ('my placed object floats above the ground', 'fnv_place', 'check'),
    ('rotate an object I placed', 'fnv_place', 'rotate'),
    ('delete a reference from the world', 'fnv_world_edit', 'delete'),
    ('change the weather', 'fnv_world_edit', 'weather'),
    ('set the time of day', 'fnv_time'),
    ('pause the game clock', 'fnv_time', 'pause'),
    ('create a new form at run time', 'fnv_form', 'create'),
    ('change the damage of a weapon in memory', 'fnv_form_edit'),
    ('reload a texture without restarting the game', 'fnv_asset', 'reload'),
    ('play a sound', 'fnv_audio', 'play'),
    ('play an animation on an npc', 'fnv_anim', 'play'),
    ('is my machine set up for the tools', 'fnv_setup_check'),
    ('install the engine companion', 'fnv_companion_install'),
    ('check my script source for mistakes', 'fnv_script_lint'),
    ('get the source of a compiled script', 'fnv_script_decompile'),
    ('compare two saves', 'fnv_save_diff'),
    ('read what is in a save file', 'fnv_save_inspect'),
    ('find which mod breaks my test', 'fnv_bisect'),
    ('check every cell my plugin adds', 'fnv_crawl', 'cells'),
    ('chance to hit in vats', 'fnv_vats_odds'),
    ('roll a leveled list', 'fnv_leveled_roll'),
    ('new lines of the nvse log', 'fnv_logs'),
    ('the mouse is stuck in the game window', 'fnv_window'),
    ('walk the player to a door', 'fnv_play', 'goto'),
    ('move the camera to look at an object', 'fnv_camera'),
    ('draw text on the screen', 'fnv_overlay', 'text'),
    ('export the objects I placed into a plugin', 'fnv_author', 'export'),
    ('who changed this value', 'fnv_blame'),
    ('log every call to an engine function', 'fnv_trace'),
    ('ground height under a point', 'fnv_raycast'),
    ('run C code inside the game', 'fnv_native'),
    ('skip an engine function or change its result', 'fnv_hook_set'),
    ('the npc fell through the floor', 'fnv_physics'),
    ('which tool failed most often', 'fnv_journal', 'calls'),
    ('script errors since the last call', 'fnv_script_errors'),
]


def tool_rows(out):
    return [r for r in out['rows'] if r['kind'] == 'tool']


class ToolSearchTableTests(unittest.TestCase):
    def setUp(self):
        self.tools = GuideTools()

    def test_table_is_big_enough(self):
        self.assertGreaterEqual(len(TABLE), 60)
        self.assertEqual(TABLE[0], ('put a building on a slope', 'fnv_place'))

    def test_expected_tool_in_the_first_three(self):
        misses = []
        for row in TABLE:
            phrase, tool = row[0], row[1]
            out = self.tools.run('find', phrase, limit=10)
            top = [r['name'] for r in out['rows'][:3]]
            if tool not in top:
                misses.append('%r: wanted %s, got %s' % (phrase, tool, top))
        self.assertEqual(misses, [])

    def test_instructions_first_tool_lines_are_found_by_search(self):
        """Each "problem: first tool" line of the server instructions works as a search too: the problem's words
        find the line's first tool in the first three tools (a guide may rank above them)."""
        import re
        from geck_mcp.server import INSTRUCTIONS
        lines = INSTRUCTIONS.split('First tool for each problem', 1)[1].split('): ', 1)[1]
        lines = lines.split(' Act tools change', 1)[0].rstrip('.').split('; ')
        self.assertGreaterEqual(len(lines), 12)
        misses, checked = [], 0
        for line in lines:
            problem, answer = line.split(': ', 1)
            named = re.findall(r'fnv_[a-z_]+', answer)
            if not named or named[0] == 'fnv_guide':          # a guide, or "fnv_guide engine": not a tool search
                continue
            checked += 1
            top = [r['name'] for r in tool_rows(self.tools.run('find', problem, limit=10))[:3]]
            if named[0] not in top:
                misses.append('%r: wanted %s, got %s' % (problem, named[0], top))
        self.assertGreaterEqual(checked, 12)
        self.assertEqual(misses, [])

    def test_expected_action(self):
        misses = []
        for row in TABLE:
            if len(row) < 3:
                continue
            phrase, tool, action = row
            hit = next((r for r in tool_rows(self.tools.run('find', phrase, limit=10)) if r['name'] == tool), None)
            if hit is None or hit['action'] != action:
                misses.append('%r: wanted %s %s, got %s' % (phrase, tool, action, hit and hit['action']))
        self.assertEqual(misses, [])


class ToolSearchShapeTests(unittest.TestCase):
    def test_whole_words_not_substrings(self):
        self.assertEqual(words('building on slopes'), ['build', 'slop'])
        out = GuideTools().run('find', 'put a building on a slope')
        self.assertNotEqual(out['rows'][0]['name'], 'fnv_plugin_build')

    def test_row_has_tool_action_and_an_example_call(self):
        row = tool_rows(GuideTools().run('find', 'snap an object to the ground'))[0]
        self.assertEqual((row['tool'], row['action']), ('fnv_place', 'snap'))
        self.assertTrue(row['example'].startswith('fnv_place(action="snap"'), row['example'])
        self.assertNotIn('\n', row['example'])

    def test_example_names_required_parameters(self):
        schemas = {'fnv_ref': {'properties': {'ref': {'type': 'string'}}, 'required': ['ref']}}
        row = next(r for r in tool_rows(GuideTools(schemas=schemas).run('find', 'any reference base form and owner'))
                   if r['name'] == 'fnv_ref')
        self.assertEqual(row['example'], 'fnv_ref(ref=...)')

    def test_only_stop_words_is_no_match(self):
        out = GuideTools().run('find', 'how do I ?')
        self.assertTrue(out['verdict'].startswith('No match'), out['verdict'])

    def test_every_tool_has_use_when_phrases_and_their_actions_exist(self):
        table = tool_table()
        self.assertEqual(sorted(set(table) - set(USE_WHEN)), [])
        self.assertEqual(sorted(set(USE_WHEN) - set(table)), [])
        for name, phrases in USE_WHEN.items():
            self.assertTrue(phrases, name)
            for p in phrases:
                self.assertLessEqual(len(p), 80, p)

    def test_a_match_outside_the_active_toolsets_says_so(self):
        out = GuideTools(toolsets={'game'}).run('find', 'count calls of thousands of functions with a probe')
        row = next(r for r in tool_rows(out) if r['name'] == 'fnv_probe')
        self.assertFalse(row['active'])
        self.assertIn('naming', row['description'])
        active = next(r for r in tool_rows(GuideTools().run('find', 'count calls of functions with a probe'))
                      if r['name'] == 'fnv_probe')
        self.assertTrue(active['active'])


class ToolSearchServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_find_through_the_server_gives_example_calls_with_required_parameters(self):
        import project_fixture
        from mcp import Client
        from geck_mcp.server import make_server
        fixture = project_fixture.ProjectFixture()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        async with Client(make_server(fixture.p)) as client:
            result = await client.call_tool('fnv_guide', {'action': 'find', 'words': 'what is this object'})
        rows = [r for r in result.structured_content['data']['rows'] if r['kind'] == 'tool']
        ref = next(r for r in rows if r['name'] == 'fnv_ref')
        self.assertEqual(ref['example'], 'fnv_ref(ref=...)')


if __name__ == '__main__':
    unittest.main()
