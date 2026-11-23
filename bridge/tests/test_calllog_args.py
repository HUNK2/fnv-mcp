"""Tool gaps 2 (2026-11-23): arguments in the call log. fnv.call_log_arguments: "names" (default) writes the names of
the arguments given, "values" also their values (each cut to 200 characters, the line kept under 2 KB, the user's
profile folder written as %USERPROFILE%), "off" neither. fnv_journal action=calls takes tool= and, when values are
logged, counts the first word of each fnv_console line and fnv_run_script source: what agents type for lack of a
tool."""
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402

import project_fixture  # noqa: E402
from fnv_mcp.fnv_calllog import CallLog, LINE_MAX, VALUE_MAX  # noqa: E402
from fnv_mcp.server import make_server  # noqa: E402


class ArgumentsInTheLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)

    def lines(self):
        return [json.loads(t) for t in (self.state / 'fnv' / 'calls.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_names_by_default(self):
        CallLog(self.state).record('fnv_console', None, True, None, 1, 1, False, args={'line': 'tgm', 'ref': 'x'})
        self.assertEqual(self.lines()[0]['args'], ['line', 'ref'])

    def test_off_writes_no_args(self):
        CallLog(self.state, 'off').record('fnv_console', None, True, None, 1, 1, False, args={'line': 'tgm'})
        self.assertNotIn('args', self.lines()[0])

    def test_no_arguments_no_key(self):
        CallLog(self.state, 'values').record('fnv_status', None, True, None, 1, 1, False, args=None)
        self.assertNotIn('args', self.lines()[0])

    def test_values_are_cut_and_scrubbed(self):
        home = str(Path.home())
        log = CallLog(self.state, 'values')
        log.record('fnv_guide', 'find', True, None, 1, 1, False,
                   args={'action': 'find', 'words': 'x' * 500, 'path': home + '\\Documents\\a.txt',
                         'other': home.replace('\\', '/').upper() + '/b', 'n': 5, 'flag': True, 'list': [1, 2]})
        args = self.lines()[0]['args']
        self.assertEqual(len(args['words']), VALUE_MAX)
        self.assertTrue(args['words'].endswith('...'))
        self.assertEqual(args['path'], '%USERPROFILE%\\Documents\\a.txt')
        self.assertEqual(args['other'], '%USERPROFILE%/b')
        self.assertEqual((args['n'], args['flag'], args['list']), (5, True, [1, 2]))
        self.assertNotIn(home.lower(), json.dumps(self.lines()[0]).lower().replace('\\\\', '\\'))

    def test_a_line_stays_under_the_cap(self):
        log = CallLog(self.state, 'values')
        log.record('fnv_spec', 'validate', True, None, 1, 1, False,
                   args={'k%02d' % i: 'v' * 400 for i in range(40)})
        text = (self.state / 'fnv' / 'calls.jsonl').read_text(encoding='utf-8')
        self.assertLessEqual(len(text.strip()), LINE_MAX)
        row = json.loads(text)
        self.assertTrue(row['argsCut'])
        self.assertEqual(set(row['args']), {'k%02d' % i for i in range(40)}, 'every name is kept')

    def test_old_lines_without_args_still_read(self):
        path = self.state / 'fnv' / 'calls.jsonl'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'t': time.time(), 'tool': 'fnv_ref', 'action': None, 'ok': True, 'code': None,
                                    'ms': 1, 'size': 1, 'cut': False}) + '\n', encoding='utf-8')
        self.assertEqual(CallLog(self.state).summary()['total'], 1)

    def test_summary_for_one_tool_and_console_words(self):
        log = CallLog(self.state, 'values')
        for line in ('player.additem f 100', 'tgm', 'TGM', 'coc GoodspringsSource', 'player.GetPos x'):
            log.record('fnv_console', None, True, None, 1, 1, False, args={'line': line})
        log.record('fnv_run_script', None, True, None, 1, 1, False, args={'source': '  GetPlayerGrabbedRef\n'})
        log.record('fnv_status', None, True, None, 1, 1, False)
        one = log.summary(tool='fnv_console')
        self.assertEqual(one['total'], 5)
        self.assertEqual([t['tool'] for t in one['perTool']], ['fnv_console'])
        words = {w['word']: w['count'] for w in log.summary()['consoleWords']}
        self.assertEqual(words, {'additem': 1, 'tgm': 2, 'coc': 1, 'getpos': 1, 'getplayergrabbedref': 1})

    def test_no_console_words_without_values(self):
        log = CallLog(self.state)
        log.record('fnv_console', None, True, None, 1, 1, False, args={'line': 'tgm'})
        out = log.summary()
        self.assertEqual(out['consoleWords'], [])
        self.assertIn('call_log_arguments', out['consoleWordsNote'])


class ArgumentsThroughTheServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = project_fixture.ProjectFixture()
        self.fixture.setUp()
        self.path = Path(self.fixture.p.state_dir) / 'fnv' / 'calls.jsonl'

    async def asyncTearDown(self):
        self.fixture.tearDown()

    def project(self, mode):
        from fnv_mcp.config import FnvSettings
        p = self.fixture.p
        fnv = p.fnv.model_copy(update={'call_log_arguments': mode}) if p.fnv else FnvSettings(call_log_arguments=mode)
        return p.model_copy(update={'fnv': fnv})

    async def test_default_logs_names_not_values(self):
        marker = 'zebracrossing-%d' % time.time_ns()
        async with Client(make_server(self.fixture.p)) as client:
            await client.call_tool('fnv_guide', {'action': 'find', 'words': marker})
        row = json.loads(self.path.read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(row['args'], ['action', 'words'])
        self.assertNotIn(marker, self.path.read_text(encoding='utf-8'))

    async def test_values_when_set_and_journal_filters_by_tool(self):
        marker = 'zebracrossing-%d' % time.time_ns()
        async with Client(make_server(self.project('values'))) as client:
            await client.call_tool('fnv_guide', {'action': 'find', 'words': marker})
            await client.call_tool('fnv_guide', {'action': 'list'})
            await client.call_tool('fnv_status', {})
            result = await client.call_tool('fnv_journal', {'action': 'calls', 'tool': 'fnv_guide'})
        rows = [json.loads(t) for t in self.path.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(rows[0]['args'], {'action': 'find', 'words': marker})
        data = result.structured_content['data']
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['tool'], 'fnv_guide')


if __name__ == '__main__':
    unittest.main()
