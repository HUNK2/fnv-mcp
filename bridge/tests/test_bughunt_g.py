"""Bug hunt 2026-11-18, hunter G: the MCP tool layer (fnv_live_tools, param_docs, action_params)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402

from fnv_mcp.param_docs import PARAM_DOCS  # noqa: E402
from fnv_mcp.server import make_server  # noqa: E402
from test_stab_gate import GateFixture  # noqa: E402


class HookActionsNeedFull(GateFixture, unittest.IsolatedAsyncioTestCase):
    """These actions install a code hook in the engine (record_hooks, or the subtitle hook), as fnv_damage_log and
    fnv_script_profile do, which MUTATING_ACTIONS gates. They were registered as read, so engine_control = read
    ran them."""

    CASES = {
        'anim events': ('fnv_anim', {'action': 'events', 'ref': 'player'}),
        'audio log': ('fnv_audio', {'action': 'log'}),
        'audio subtitles': ('fnv_audio', {'action': 'subtitles'}),
        'physics triggers': ('fnv_physics', {'action': 'triggers'}),
    }

    async def test_hooking_actions_are_refused_when_read(self):
        wrong = []
        async with Client(make_server(self.project)) as client:
            for label, (tool, args) in sorted(self.CASES.items()):
                self.pipe_ops.clear()
                code, _ = await self.call(client, tool, args)
                if code != 'CONTROL_DISABLED' or self.pipe_ops:
                    wrong.append((label, code, len(self.pipe_ops)))
        self.assertEqual(wrong, [])

    async def test_plain_reads_stay_allowed_when_read(self):
        wrong = []
        async with Client(make_server(self.project)) as client:
            for tool, args in (('fnv_audio', {'action': 'playing'}), ('fnv_physics', {'action': 'layers'})):
                code, _ = await self.call(client, tool, args)
                if code == 'CONTROL_DISABLED':
                    wrong.append((tool, args['action']))
        self.assertEqual(wrong, [])


class ParamDocTruth(unittest.TestCase):
    def test_game_force_is_for_quit_only(self):
        # restart takes no force (action_params) and fnv_game ignores it there
        text = PARAM_DOCS['fnv_game']['force']
        self.assertNotIn('restart', text.lower())


if __name__ == '__main__':
    unittest.main()
