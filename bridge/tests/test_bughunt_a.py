"""Bug hunt A (2026-11-18): core plumbing (fnv_live, fnv_link, fnv_journal, fnv_args, safe_paths, ...).
Each test failed on the code before its fix."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from geck_mcp import fnv_link, safe_paths  # noqa: E402
from geck_mcp.fnv_live import LiveError  # noqa: E402


class LiveBase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live
        self.link = self.fake.link
        self.link.expressions['player'] = {'type': 'form', 'formId': '00000014', 'ptr': '0x00200000'}


class MemWriteTests(LiveBase):
    def test_raw_address_without_type_is_invalid_argument(self):
        self.link.put(0x00100000, bytes(8))
        r = self.live.run('fnv_mem_write', lambda: self.live.mem_write('0x00100000', 5), 'act')
        self.assertFalse(r['ok'])
        self.assertEqual(r['error']['code'], 'INVALID_ARGUMENT')       # was INTERNAL_ERROR (AttributeError)
        self.assertEqual(r['outcome'], 'failed_before_change')
        self.assertIn('as_type', r['error']['message'])


class ScriptSetTests(LiveBase):
    def test_missing_global_is_refused_and_not_journaled(self):
        before = len(self.live.journal.entries())
        with self.assertRaises(LiveError) as ctx:
            self.live.script_set('NoSuchGlobal', 'global', 5)
        self.assertEqual(ctx.exception.code, 'NO_GLOBAL')
        self.assertEqual(len(self.live.journal.entries()), before)
        self.assertFalse(self.link.console_log)

    def test_global_undo_line_is_a_number(self):
        self.link.expressions['GetGlobalValue TimeScale'] = {'type': 'number', 'value': 30.0}
        r = self.live.script_set('TimeScale', 'global', '5')
        self.assertEqual(r['line'], 'set TimeScale to 5')
        entry = self.live.journal.get(r['journalId'])
        self.assertEqual(entry['undo']['line'], 'set TimeScale to 30')

    def test_global_text_value_is_refused(self):
        self.link.expressions['GetGlobalValue TimeScale'] = {'type': 'number', 'value': 30.0}
        with self.assertRaises(ValueError):
            self.live.script_set('TimeScale', 'global', '1\nQuitGame')
        self.assertFalse(self.link.console_log)

    def test_objective_text_false_sets_zero(self):
        self.link.expressions['(BuildRef 0 100)'] = {'type': 'form', 'formId': '00000064', 'ptr': '0x00100000'}
        self.link.expressions['GetObjectiveCompleted (BuildRef 0 100) 10'] = {'type': 'number', 'value': 1}
        r = self.live.script_set('0x64', 'objective_completed', 'false', index=10)
        self.assertTrue(r['line'].endswith(' 10 0'), r['line'])      # was ' 10 1': "false" is truthy


class ActorControlTests(LiveBase):
    def test_set_av_without_value_is_a_clear_error(self):
        self.link.put(0x00200000 + 4, bytes([59]))
        with self.assertRaises(ValueError):
            self.live.actor_control('player', 'set_av', 'Health', None)     # was TypeError in _num: INTERNAL_ERROR

    def test_add_item_zero_is_refused(self):
        self.link.put(0x00200000 + 4, bytes([59]))
        self.link.expressions['(BuildRef 0 15)'] = {'type': 'form', 'formId': '0000000F', 'ptr': '0x00300000'}
        with self.assertRaises(ValueError):
            self.live.actor_control('player', 'add_item', '0x0000000F', 0)  # was: added one item
        self.assertFalse(self.link.console_log)


class UndoCountTests(LiveBase):
    def test_count_zero_undoes_nothing(self):
        self.link.put(0x00100000, bytes([1, 2]))
        for k in range(2):
            self.live.journal.add({'kind': 'memory', 'undo': {'op': 'write', 'address': '0x%08X' % (0x00100000 + k),
                                                              'hex': '09'}})
        with self.assertRaises(ValueError):          # was: [-0:] is every entry
            self.live.undo(0)
        self.assertEqual(self.link.get(0x00100000, 2), bytes([1, 2]))
        self.assertEqual(len(self.live.journal.entries(undoable=True)), 2)


class LinkProtocolTests(unittest.TestCase):
    def link(self, reply):
        link = fnv_link.EngineLink('fnvmcp-bughunt-a')
        link.idle_release_seconds = 0
        link.handle = 1                      # connected: _exchange does not open the pipe
        link._io = lambda data, timeout: reply
        link.close = lambda: setattr(link, 'handle', None)
        return link

    def test_reply_that_is_not_json_is_a_protocol_error(self):
        link = self.link(b'{"id": "x", "results": [\n')
        with self.assertRaises(fnv_link.LinkError) as ctx:
            link.batch([('info',)])
        self.assertEqual(ctx.exception.code, 'COMPANION_PROTOCOL')     # was a bare ValueError (INVALID_ARGUMENT)
        self.assertIsNone(link.handle)

    def test_non_ascii_op_text_is_a_clear_error(self):
        link = self.link(b'{}\n')
        with self.assertRaises(ValueError) as ctx:
            link.batch([('rs', 'caf\u00e9')])
        self.assertIn('ASCII', str(ctx.exception))


class IncludeTests(unittest.TestCase):
    def test_digraph_hash_include_is_checked(self):
        with self.assertRaises(ValueError):
            safe_paths.check_includes('%:include "..\\outside.h"\nint x;\n')
        with self.assertRaises(ValueError):
            safe_paths.check_includes('%: include <C:/x/y.h>\n')
        safe_paths.check_includes('%:include "ok.h"\n')


if __name__ == '__main__':
    unittest.main()
