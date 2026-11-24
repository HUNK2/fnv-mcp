"""Tool quality part 3: errors say what to do. No Python exception text reaches the agent (INTERNAL_ERROR with a
plain sentence, the traceback in the bridge log); "did you mean" for names; address as another name for target;
GAME_NOT_RUNNING in plain words; COMPANION_NOT_READY names its cause; BAD_PREDICATE shows valid forms."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402

import project_fixture  # noqa: E402
from fakes import FakeLive  # noqa: E402
from fnv_mcp import fnv_game  # noqa: E402
from fnv_mcp.fnv_conv import ConvError, nearest  # noqa: E402
from fnv_mcp.fnv_link import LinkError  # noqa: E402
from fnv_mcp.fnv_live import LiveError  # noqa: E402
from fnv_mcp.fnv_pred import PREDICATE_EXAMPLES, compile_predicate  # noqa: E402
from fnv_mcp.server import make_server  # noqa: E402

# The exceptions the transcripts showed reaching the agent as FNV_ERROR with a Python message.
SEEN = [Exception('SQLite objects created in a thread can only be used in that same thread'),
        NameError("name 'mo2' is not defined"), KeyError('pose'),
        __import__('struct').error("'B' format requires 0 <= number <= 255")]


def raising(error):
    def action():
        raise error
    return action


class NearestTests(unittest.TestCase):
    def test_close_names_first(self):
        self.assertEqual(nearest('flags', ['flags1', 'baseForm', 'flagsEx', 'scale'])[:2], ['flags1', 'flagsEx'])
        self.assertEqual(nearest('helth', ['Health', 'Strength', 'Luck']), ['Health'])
        self.assertEqual(nearest('HEALTH', ['Health']), ['Health'])
        self.assertEqual(nearest('zzzz', ['Health', 'Luck']), [])
        self.assertEqual(nearest('', ['a']), [])
        self.assertLessEqual(len(nearest('a', ['ab', 'ac', 'ad', 'ae'])), 3)


class InternalErrorTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live

    def test_no_exception_text_reaches_the_agent(self):
        for error in SEEN:
            with self.assertLogs('fnv_mcp', level='ERROR'):
                r = self.live.run('fnv_record', raising(error))
            self.assertEqual(r['error']['code'], 'INTERNAL_ERROR', error)
            message = r['error']['message']
            self.assertTrue(message.startswith('The tool hit a bug: %s in ' % type(error).__name__), message)
            self.assertNotIn(str(error).strip("'"), message)
            self.assertIn('changed nothing', r['error']['recovery'])

    def test_an_act_bug_points_at_the_journal(self):
        with self.assertLogs('fnv_mcp', level='ERROR'):
            r = self.live.run('fnv_place', raising(KeyError('pose')), kind='act')
        self.assertEqual(r['error']['code'], 'INTERNAL_ERROR')
        self.assertEqual(r['outcome'], 'outcome_unknown')
        self.assertIn('fnv_journal', r['error']['recovery'])

    def test_value_error_is_invalid_argument(self):
        r = self.live.run('fnv_ref', raising(ValueError('radius must be 1 to 10000')))
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')
        self.assertIn('radius must be 1 to 10000', r['error']['message'])

    def test_offline_and_spec_envelopes(self):
        from fnv_mcp.fnv_tools import FnvService
        fixture = project_fixture.ProjectFixture()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        with self.assertLogs('fnv_mcp', level='ERROR'):
            r = FnvService(fixture.p).run('fnv_func', raising(KeyError('x')))
        self.assertEqual(r['error']['code'], 'INTERNAL_ERROR')
        from fnv_mcp import fnv_spec
        service = fnv_spec.SpecTools(fixture.p)
        with mock.patch.object(service, 'validate', raising(AttributeError('nope'))), \
                self.assertLogs('fnv_mcp', level='ERROR'):
            r = service.run('validate')
        self.assertEqual(r['error']['code'], 'INTERNAL_ERROR')
        self.assertNotIn('nope', r['error']['message'])


class ServerInternalErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_exception_that_escapes_a_tool_is_internal_error(self):
        fixture = project_fixture.ProjectFixture()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        server = make_server(fixture.p)

        @server.tool()
        async def fnv_boom() -> str:
            raise KeyError('secret detail')
        with self.assertLogs('fnv_mcp', level='ERROR'):
            async with Client(server) as client:
                result = await client.call_tool('fnv_boom', {})
        self.assertTrue(result.is_error)
        error = result.structured_content['error']
        self.assertEqual(error['code'], 'INTERNAL_ERROR')
        self.assertNotIn('secret detail', error['message'])


class DidYouMeanTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live

    def test_no_field_names_the_closest_fields(self):
        layout = {'size': 16, 'bases': [], 'fields': [{'name': 'flags1', 'offset': 8, 'type': 'UInt32'},
                                                      {'name': 'baseForm', 'offset': 0x20, 'type': 'TESForm*'}]}
        with mock.patch.object(self.live, '_layout', lambda cls: layout):
            with self.assertRaises(LiveError) as caught:
                self.live.locate('0x01000000', 'flags', cls='TESObjectREFR')
        self.assertEqual(caught.exception.code, 'NO_FIELD')
        self.assertIn('Did you mean flags1', str(caught.exception))
        self.assertIn('2 fields', str(caught.exception))
        self.assertEqual(caught.exception.evidence['didYouMean'], ['flags1'])

    def test_unknown_actor_value(self):
        with self.assertRaises(ValueError) as caught:
            self.live.actor_values('player', ['Helth'])
        self.assertIn('Helth (did you mean Health', str(caught.exception))

    def test_every_unknown_actor_value_gets_a_suggestion(self):
        # live check 2026-11-18: only the first of two misspelt names had a suggestion
        with self.assertRaises(ValueError) as caught:
            self.live.actor_values('player', ['Helth', 'Strenght'])
        self.assertIn('Helth (did you mean Health', str(caught.exception))
        self.assertIn('Strenght (did you mean Strength', str(caught.exception))

    def test_no_field_evidence_has_no_full_field_list(self):
        layout = {'size': 16, 'bases': [], 'fields': [{'name': 'flags1', 'offset': 8, 'type': 'UInt32'}]}
        with mock.patch.object(self.live, '_layout', lambda cls: layout):
            with self.assertRaises(LiveError) as caught:
                self.live.locate('0x01000000', 'flags', cls='TESObjectREFR')
        self.assertNotIn('fields', caught.exception.evidence)
        self.assertEqual(caught.exception.evidence['fieldCount'], 1)

    def test_at_editor_id_is_an_editor_id(self):
        # fnv_spec lookup answers "@Barrel03static"; fnv_place refused it (live check 2026-11-18)
        self.live._indexes = {'FalloutNV.esm': {'DocMitchell': '00104C0C'}}
        with self.assertRaises(LiveError) as caught:
            self.live.resolve('@DocMitchel')
        self.assertEqual(caught.exception.code, 'REF_NOT_FOUND')
        self.assertIn('Did you mean DocMitchell', str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            self.live.resolve('not a ref!')
        self.assertIn('@EditorID', str(caught.exception))

    def test_set_position_needs_three_numbers(self):
        # live check 2026-11-18: value=[1, 2] answered "not enough values to unpack (expected 3, got 2)"
        with mock.patch.object(self.live, 'in_game', lambda: None), \
                mock.patch.object(self.live, 'resolve', lambda ref: {'ptr': 1, 'formId': '00000014'}):
            for value in ([1, 2], None, 'here', [1, 2, 'x'], '123'):
                with self.assertRaises(ValueError) as caught:
                    self.live.actor_control('player', 'set_position', value=value)
                self.assertIn('set_position needs value=[x, y, z]', str(caught.exception))

    def test_unknown_editor_id(self):
        self.live._indexes = {'FalloutNV.esm': {'DocMitchell': '00104C0C', 'DocMitchellRef': '00104C0D'}}
        with self.assertRaises(LiveError) as caught:
            self.live.resolve('DocMitchel')
        self.assertEqual(caught.exception.code, 'REF_NOT_FOUND')
        self.assertIn('Did you mean DocMitchell', str(caught.exception))

    def test_unknown_function_name(self):
        from fnv_mcp.fnv_engine import EngineMap, EngineMapError
        engine = EngineMap.__new__(EngineMap)
        engine.functions = {0x401000: {'name': 'Actor::GetLevel', 'status': 'proven'}}
        engine.by_name = {'actor::getlevel': {0x401000}, 'getlevel': {0x401000}}
        with self.assertRaises(EngineMapError) as caught:
            engine.resolve('Actor::GetLevl')
        self.assertIn('Did you mean Actor::GetLevel', str(caught.exception))

    def test_unknown_global(self):
        from fnv_mcp.fnv_world import did_you_mean_global
        self.assertIn('Did you mean GameHour', did_you_mean_global('GameHur', ['GameHour', 'GameDay']))


class GameStateWordsTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live

    def test_game_not_running_is_plain(self):
        self.live._game.process = lambda fresh=False: None
        r = self.live.run('fnv_ref', raising(LinkError('NOT_RUNNING', '[WinError 2] The system cannot find the file')))
        self.assertEqual(r['error']['code'], 'GAME_NOT_RUNNING')
        self.assertEqual(r['error']['message'], 'The game is not running.')
        self.assertIn('WinError 2', r['data']['detail'])


class NotReadyCauseTests(unittest.TestCase):
    def base(self, **over):
        checks = {'crash_log': None, 'companion_present': True, 'pipe_exists': False, 'window_hung': False,
                  'idle_seconds': 5}
        checks.update(over)
        return fnv_game.not_ready_cause(checks)

    def test_crash_log_wins(self):
        cause, text = self.base(crash_log='C:/x/CrashLogger.log', companion_present=False)
        self.assertEqual(cause, 'crashed')
        self.assertIn('fnv_crash', text)

    def test_companion_missing(self):
        cause, text = self.base(companion_present=False)
        self.assertEqual(cause, 'companion_missing')
        self.assertIn('fnv_companion_install', text)

    def test_not_drawing_frames(self):
        for over in ({'pipe_exists': True}, {'window_hung': True}):
            cause, text = self.base(**over)
            self.assertEqual(cause, 'not_drawing', over)
            self.assertIn('display', text)

    def test_unknown(self):
        cause, text = self.base()
        self.assertEqual(cause, 'unknown')
        self.assertTrue(text)


class PredicateTests(unittest.TestCase):
    def test_examples_compile(self):
        self.assertEqual(len(PREDICATE_EXAMPLES), 3)
        for text in PREDICATE_EXAMPLES:
            compile_predicate(text)

    def test_bad_predicate_offers_the_nearest_valid_form(self):
        with self.assertRaises(ConvError) as caught:
            compile_predicate('frme >= 500')
        e = caught.exception
        self.assertEqual(e.code, 'BAD_PREDICATE')
        self.assertIn("Did you mean 'frame'", str(e))
        self.assertEqual(e.evidence['examples'], list(PREDICATE_EXAMPLES))
        with self.assertRaises(ConvError) as caught:
            compile_predicate('[0x011DEA0C]:u9 == 1')
        self.assertIn("Did you mean 'u8'", str(caught.exception))


class AddressAliasTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = project_fixture.ProjectFixture()
        self.fixture.setUp()
        # a pipe name of its own: with a real game running, its companion answered the default pipe (PIPE_NOT_GAME,
        # full suite 2026-11-24)
        from fnv_mcp.config import FnvSettings
        self.fixture.p = self.fixture.p.model_copy(update={'fnv': FnvSettings(engine_pipe='fnvmcp_test_no_game')})
        patch = mock.patch.object(fnv_game, 'game_processes', lambda: [])
        patch.start()
        self.addCleanup(patch.stop)

    async def asyncTearDown(self):
        self.fixture.tearDown()

    async def test_address_is_another_name_for_target(self):
        async with Client(make_server(self.fixture.p)) as client:
            one = await client.call_tool('fnv_mem_read', {'address': '0x011DEA0C'})
            both = await client.call_tool('fnv_mem_read', {'address': '0x011DEA0C', 'target': 'player'})
            none = await client.call_tool('fnv_mem_read', {})
            write = await client.call_tool('fnv_mem_write', {'address': '0x011DEA0C', 'value': 1})
        self.assertEqual(one.structured_content['error']['code'], 'GAME_NOT_RUNNING')      # past argument checks
        self.assertEqual(both.structured_content['error']['code'], 'INVALID_ARGUMENT')
        self.assertEqual(none.structured_content['error']['code'], 'INVALID_ARGUMENT')
        self.assertFalse((write.structured_content.get('data') or {}).get('schema'))      # the schema took it


class SchemaRefusalTests(unittest.IsolatedAsyncioTestCase):
    """Live check 2026-11-18: an argument outside the schema's bounds came back as pydantic's own text (a type code
    and a link); now INVALID_ARGUMENT names the parameter, the bound and the value in plain words."""

    async def test_out_of_range_is_plain_invalid_argument(self):
        from fnv_mcp.server import schema_problems
        fixture = project_fixture.ProjectFixture()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        async with Client(make_server(fixture.p)) as client:
            low = await client.call_tool('fnv_wait_for', {'frames': 0})
            high = await client.call_tool('fnv_time', {'action': 'step', 'frames': 20000})
            missing = await client.call_tool('fnv_ref', {})
        for result, parameter in ((low, 'frames'), (high, 'frames'), (missing, 'ref')):
            error = result.structured_content['error']
            self.assertEqual(error['code'], 'INVALID_ARGUMENT')
            self.assertTrue(error['message'].startswith('Invalid argument: %s ' % parameter), error['message'])
            text = ' '.join(getattr(c, 'text', '') for c in result.content)
            for library in ('pydantic', 'type=', 'validation error', 'Arguments'):
                self.assertNotIn(library, text)
            self.assertTrue(result.structured_content['data']['schema'])
        self.assertIn('frames should be greater than or equal to 1 (got 0)', low.structured_content['error']['message'])
        self.assertIn('ref is required', missing.structured_content['error']['message'])
        self.assertEqual(schema_problems('Error executing tool x: 1 validation error for xArguments\nlimit\n'
                                         '  Input should be less than or equal to 5 [type=less_than_equal, '
                                         'input_value=9, input_type=int]\n    For further information visit u'),
                         [{'parameter': 'limit', 'text': 'limit should be less than or equal to 5 (got 9)'}])


class PredicateDescriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_predicate_parameters_show_three_examples_that_compile(self):
        import re
        fixture = project_fixture.ProjectFixture()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        async with Client(make_server(fixture.p)) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
        for tool, param in (('fnv_debug', 'when'), ('fnv_wait_for', 'predicate')):
            text = tools[tool].input_schema['properties'][param]['description']
            examples = re.search(r'Examples: (.*)$', text).group(1).split('; ')
            self.assertEqual(len(examples), 3, text)
            for example in examples:
                compile_predicate(example)


if __name__ == '__main__':
    unittest.main()
