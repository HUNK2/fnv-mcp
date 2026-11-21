"""Critique fix Task 1: every fnv_status answer says which checkout and commit served it."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402

from fnv_mcp import code_stamp  # noqa: E402
from fnv_mcp.config import Project  # noqa: E402
from fnv_mcp.server import make_server  # noqa: E402

BRIDGE = Path(__file__).resolve().parents[1]


class StampTests(unittest.TestCase):
    def setUp(self):
        code_stamp._cache.clear()

    def test_stamp_names_this_checkout(self):
        first = code_stamp.stamp()
        self.assertEqual(Path(first['path']), BRIDGE)
        self.assertRegex(first['commit'], r'^[0-9a-f]{7,12}$')
        self.assertIsInstance(first['dirty'], bool)
        self.assertEqual(code_stamp.stamp()['startedAt'], first['startedAt'])

    def test_no_git_gives_none(self):
        with mock.patch.object(code_stamp.subprocess, 'run', side_effect=FileNotFoundError):
            out = code_stamp.stamp()
        self.assertIsNone(out['commit'])
        self.assertIsNone(out['dirty'])
        self.assertEqual(Path(out['path']), BRIDGE)


class StatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_carries_the_stamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'game.json'
            path.write_text(json.dumps({'project_id': 'stamp', 'game_root': tmp, 'state_dir': 'state',
                                        'fnv': {'engine_control': 'read'}}), encoding='utf-8')
            with mock.patch('fnv_mcp.fnv_game.game_processes', return_value=[]):
                async with Client(make_server(Project.load(path))) as client:
                    result = await client.call_tool('fnv_status', {})
            data = (result.structured_content or {}).get('data') or {}
            self.assertEqual(Path(data['server']['path']), BRIDGE, result.structured_content)


if __name__ == '__main__':
    unittest.main()
