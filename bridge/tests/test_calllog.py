"""Tool quality part 1: the call log. One JSON line per tool call in <state_dir>/fnv/calls.jsonl with time, tool,
action, ok, error code, elapsed ms, answer size and whether the answer was cut. No argument values and no answer
content. Pruned to 30 days by the state maintenance; fnv_journal action=calls summarises it."""
import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402

import project_fixture  # noqa: E402
from geck_mcp import fnv_calllog  # noqa: E402
from geck_mcp.fnv_calllog import CallLog  # noqa: E402
from geck_mcp.fnv_state import prune  # noqa: E402
from geck_mcp.server import make_server  # noqa: E402

KEYS = {'t', 'tool', 'action', 'ok', 'code', 'ms', 'size', 'cut'}


class CallLogUnitTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.log = CallLog(self.state)

    def lines(self):
        return [json.loads(l) for l in (self.state / 'fnv' / 'calls.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_one_line_per_record_with_the_fixed_keys(self):
        self.log.record('fnv_ref', None, True, None, 12, 345, False)
        self.log.record('fnv_place', 'snap', False, 'TOO_STEEP', 7, 99, True)
        rows = self.lines()
        self.assertEqual(len(rows), 2)
        self.assertEqual(set(rows[0]), KEYS)
        self.assertEqual(rows[1]['action'], 'snap')
        self.assertEqual(rows[1]['code'], 'TOO_STEEP')
        self.assertTrue(rows[1]['cut'])

    def test_an_action_that_is_not_a_plain_word_is_not_recorded(self):
        self.log.record('fnv_guide', 'C:\\Users\\me\\secret.txt', True, None, 1, 1, False)
        self.assertIsNone(self.lines()[0]['action'])

    def test_record_never_raises(self):
        broken = CallLog(self.state / 'file.txt')
        (self.state / 'file.txt').write_text('a file where the folder should be')
        broken.record('fnv_ref', None, True, None, 1, 1, False)      # no exception

    def test_prune_keeps_30_days(self):
        now = time.time()
        path = self.state / 'fnv' / 'calls.jsonl'
        path.parent.mkdir(parents=True)
        old = {'t': now - 31 * 86400, 'tool': 'fnv_ref', 'action': None, 'ok': True, 'code': None, 'ms': 1,
               'size': 1, 'cut': False}
        new = dict(old, t=now - 29 * 86400)
        path.write_text(json.dumps(old) + '\n' + 'not json\n' + json.dumps(new) + '\n', encoding='utf-8')
        self.assertEqual(fnv_calllog.prune(self.state, now), 2)
        self.assertEqual([r['t'] for r in self.lines()], [new['t']])
        self.assertEqual(prune(self.state, now)['calls'], 0)       # part of the state maintenance

    def test_summary_lists_tools_never_called(self):
        self.log.record('a', None, True, None, 1, 1, False)
        out = self.log.summary(known=['a', 'b', 'c'])
        self.assertEqual(out['neverCalled'], {'count': 2, 'tools': ['b', 'c']})
        self.assertNotIn('neverCalled', self.log.summary())

    def test_summary(self):
        for ms, ok, code in ((10, True, None), (500, False, 'GAME_NOT_RUNNING'), (20, False, 'GAME_NOT_RUNNING')):
            self.log.record('fnv_ref', 'x', ok, code, ms, ms * 10, False)
        self.log.record('fnv_status', None, True, None, 3, 30000, True)
        out = self.log.summary()
        self.assertEqual(out['total'], 4)
        self.assertEqual(out['failed'], 2)
        self.assertEqual(out['perCode'], [{'code': 'GAME_NOT_RUNNING', 'count': 2}])
        ref = next(r for r in out['perTool'] if r['tool'] == 'fnv_ref')
        self.assertEqual((ref['calls'], ref['failed'], ref['maxMs']), (3, 2, 500))
        self.assertEqual(out['slowest'][0]['ms'], 500)
        self.assertEqual(out['largest'][0]['tool'], 'fnv_status')
        self.assertEqual(out['cut'], 1)
        self.assertEqual(self.log.summary(since_seconds=0)['total'], 0)


class CallLogServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = project_fixture.ProjectFixture()
        self.fixture.setUp()
        self.path = Path(self.fixture.p.state_dir) / 'fnv' / 'calls.jsonl'

    async def asyncTearDown(self):
        self.fixture.tearDown()

    def rows(self):
        return [json.loads(l) for l in self.path.read_text(encoding='utf-8').splitlines()]

    async def test_each_call_is_one_line_and_no_argument_value_is_written(self):
        marker = 'zebracrossing-%d' % time.time_ns()
        async with Client(make_server(self.fixture.p)) as client:
            await client.call_tool('fnv_guide', {'action': 'find', 'words': marker})
            await client.call_tool('fnv_func', {'query': marker})                # no engine map: fails with a code
            await client.call_tool('fnv_guide', {'action': 'find', 'limit': 'many'})    # fails the input schema
        rows = self.rows()
        self.assertEqual([r['tool'] for r in rows], ['fnv_guide', 'fnv_func', 'fnv_guide'])
        self.assertEqual(rows[0]['action'], 'find')
        self.assertTrue(rows[0]['ok'])
        self.assertGreater(rows[0]['size'], 0)
        self.assertEqual((rows[1]['ok'], rows[1]['code']), (False, 'ENGINE_MAP_UNAVAILABLE'))
        self.assertFalse(rows[2]['ok'])
        self.assertTrue(rows[2]['code'])
        self.assertNotIn(marker, self.path.read_text(encoding='utf-8'))

    async def test_fnv_journal_calls(self):
        async with Client(make_server(self.fixture.p)) as client:
            await client.call_tool('fnv_guide', {'action': 'list'})
            result = await client.call_tool('fnv_journal', {'action': 'calls'})
            old = await client.call_tool('fnv_journal', {})                     # the old call still lists changes
        self.assertFalse(result.is_error, result)
        data = result.structured_content['data']
        self.assertEqual(data['total'], 1)
        self.assertEqual(data['perTool'][0]['tool'], 'fnv_guide')
        self.assertIn('entries', old.structured_content['data'])


if __name__ == '__main__':
    unittest.main()
