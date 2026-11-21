"""Tool quality part 4: one shape per name, every old shape still accepted.

`rotation` is [x, y, z] in degrees (a Rotation type, split from the position types); the five `value` parameters
that took anything are typed; `since` is a cursor and `since_seconds` a time; `start` keeps its meaning per tool and
says it. The compatibility table: each old shape must still pass the tool's own argument model (what a call is
checked against), so no call that works today starts failing."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402
from pydantic import ValidationError  # noqa: E402

import project_fixture  # noqa: E402
from fnv_mcp.server import make_server  # noqa: E402

# (tool, arguments in a shape that works today)
OLD_SHAPES = [
    ('fnv_place', {'action': 'at', 'base': 'X', 'position': [1, 2, 3], 'rotation': [0, 0, 90]}),
    ('fnv_place', {'action': 'nudge', 'ref': 'X', 'offset': [0, 0, 10]}),
    ('fnv_place', {'action': 'rotate', 'ref': 'X', 'rotation': [0.0, 0.0, -45.5]}),
    ('fnv_move', {'position': [100, None, 5]}),
    ('fnv_move', {'position': {'x': 100}}),
    ('fnv_move', {'position': [1, 2, 3], 'rotation': [None, None, 90]}),
    ('fnv_move', {'rotation': {'z': 90}}),
    ('fnv_world_edit', {'action': 'move', 'ref': 'X', 'position': [1, None, 3]}),
    ('fnv_world_edit', {'action': 'rotate', 'ref': 'X', 'rotation': [None, None, 90]}),
    ('fnv_world_edit', {'action': 'move', 'ref': 'X', 'position': [1, 2]}),
    ('fnv_raycast', {'start': [0, 0, 0], 'end': {'x': 1, 'y': 0, 'z': 0}}),
    ('fnv_camera', {'action': 'set', 'position': [1, 2, 3]}),
    ('fnv_play', {'action': 'goto', 'position': [1, 2, 3]}),
    ('fnv_overlay', {'action': 'marker', 'position': [1, 2, 3]}),
    ('fnv_audio', {'action': 'hear', 'position': [1, 2, 3]}),
    ('fnv_physics', {'action': 'query', 'position': [1, 2, 3]}),
    # value: numbers, booleans, text, and actor_control's [x, y, z]
    ('fnv_mem_write', {'target': '0x01000000', 'value': 5}),
    ('fnv_mem_write', {'target': '0x01000000', 'value': 1.5}),
    ('fnv_mem_write', {'target': '0x01000000', 'value': True}),
    ('fnv_mem_write', {'target': '0x01000000', 'value': '0A FF', 'as_type': 'bytes'}),
    ('fnv_form_edit', {'form': 'X', 'field': 'value', 'value': 100}),
    ('fnv_form_edit', {'form': 'X', 'field': 'name', 'value': 'text'}),
    ('fnv_actor_control', {'ref': 'X', 'action': 'set_av', 'target': 'Health', 'value': 50}),
    ('fnv_actor_control', {'ref': 'X', 'action': 'set_position', 'value': [1, 2, 3]}),
    ('fnv_actor_control', {'ref': 'X', 'action': 'kill'}),
    ('fnv_script_set', {'target': 'Q', 'kind': 'stage', 'value': 10}),
    ('fnv_script_set', {'target': 'Q', 'kind': 'objective_completed', 'value': True, 'index': 1}),
    ('fnv_script_set', {'target': 'Q', 'kind': 'variable', 'value': 2.5, 'name': 'v'}),
    ('fnv_script_set', {'target': 'G', 'kind': 'global', 'value': '3'}),
    ('fnv_hook_set', {'function': 'F', 'action': 'skip', 'value': 0}),
    ('fnv_hook_set', {'function': 'F', 'action': 'result', 'value': 1.0, 'value_type': 'float'}),
    ('fnv_hook_set', {'function': 'F', 'action': 'arg', 'arg': 1, 'value': '0x01000000', 'value_type': 'ptr'}),
    ('fnv_hook_set', {'function': 'F', 'action': 'list'}),
    # since and start: every shape of today
    ('fnv_form', {'action': 'changes', 'since': 1763000000.5}),
    ('fnv_events', {'since': 12}),
    ('fnv_logs', {'since': 'start'}),
    ('fnv_script_errors', {'since': 'last'}),
    ('fnv_warnings', {'action': 'read', 'since': 'mark'}),
    ('fnv_warnings', {'action': 'read', 'since': 40}),
    ('fnv_script_log', {'action': 'errors', 'since': 'start'}),
    ('fnv_script_log', {'action': 'errors', 'since': 3}),
    ('fnv_native', {'action': 'log', 'since': 5}),
    ('fnv_game', {'action': 'launch', 'start': 'coc:GSDocMitchellHouse'}),
    ('fnv_dialogue_choose', {'start': False}),
    ('fnv_systems', {'action': 'sweep', 'start': 1.0}),
    ('fnv_probe', {'action': 'plan', 'start': '0x00401000'}),
    # address: another name for target (part 3)
    ('fnv_mem_read', {'target': 'player'}),
    ('fnv_mem_read', {'address': '0x011DEA0C'}),
    ('fnv_mem_write', {'address': '0x011DEA0C', 'value': 1}),
]

# shapes that never worked and the typed schema now refuses before the tool runs
REFUSED = [
    ('fnv_mem_write', {'target': '0x01000000', 'value': {'a': 1}}),
    ('fnv_script_set', {'target': 'Q', 'kind': 'stage', 'value': [10]}),
    ('fnv_place', {'action': 'rotate', 'ref': 'X', 'rotation': [0, 90]}),
]


class ParamShapeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = project_fixture.ProjectFixture()
        self.fixture.setUp()
        self.server = make_server(self.fixture.p)

    async def asyncTearDown(self):
        self.fixture.tearDown()

    def model(self, tool):
        return self.server._tool_manager.get_tool(tool).fn_metadata.arg_model

    def test_every_old_shape_still_validates(self):
        bad = []
        for tool, args in OLD_SHAPES:
            try:
                self.model(tool).model_validate(args)
            except ValidationError as e:
                bad.append('%s %s: %s' % (tool, args, e.errors()[0]['msg']))
        self.assertEqual(bad, [])

    def test_shapes_that_never_worked_are_refused(self):
        for tool, args in REFUSED:
            with self.assertRaises(ValidationError, msg='%s %s' % (tool, args)):
                self.model(tool).model_validate(args)

    async def test_emitted_schema(self):
        async with Client(self.server) as client:
            tools = {t.name: t.input_schema['properties'] for t in (await client.list_tools()).tools}
        for tool in ('fnv_place', 'fnv_move', 'fnv_world_edit'):
            self.assertIn('degrees', str(tools[tool]['rotation']), tool)
            self.assertNotIn('game units', str(tools[tool]['rotation']), tool)
        for tool in ('fnv_mem_write', 'fnv_form_edit', 'fnv_actor_control', 'fnv_script_set', 'fnv_hook_set'):
            schema = tools[tool]['value']
            self.assertTrue(schema.get('anyOf') or schema.get('type'), '%s.value is untyped' % tool)
        self.assertIn('since_seconds', tools['fnv_form'])
        for tool in ('fnv_dialogue_choose', 'fnv_systems', 'fnv_probe'):
            self.assertTrue(tools[tool]['start'].get('description'), '%s.start says what it means' % tool)


if __name__ == '__main__':
    unittest.main()
