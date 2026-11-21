"""fnv_script_log (W2): the statement ring (commands), history of a reference, block events from the flag setters,
raise and the script-error passthrough, against the fake companion of test_fnv_script_debug."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp import fnv_conv, fnv_debugger, fnv_scriptdbg  # noqa: E402
from test_fnv_script_debug import ACTION, EVAL, LOCALS_ACTION, REF, SCRIPT, FakeClock, ScriptLive  # noqa: E402


def record(seq, **fields):
    """A 48-word hook record: r[0] seq, r[2] thread, r[5] caller, r[12..19] arguments, r[22] frame."""
    r = [0] * 48
    r[0] = seq
    r[2] = 1000
    r[22] = 7
    for index, value in fields.items():
        r[int(index)] = value
    return r


def statement_record(seq, opcode, line=12, ref=REF):
    return record(seq, **{'12': SCRIPT, '13': opcode, '14': ref, '16': 0x20, '18': line})


class LogBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.live = ScriptLive(self.tmp.name)
        self.link = self.live.link
        self.dbg = fnv_debugger.Debugger(self.live, sleep=lambda s: None, clock=FakeClock())
        self.dbg.pid = 1234
        self.eval = fnv_scriptdbg.EvalHook(self.live)
        self.log = fnv_scriptdbg.ScriptLog(self.live, eval_hook=self.eval, debugger=self.dbg)

    def run_tool(self, action, **args):
        return self.log.run(action, **args)

    def labels(self):
        return [h['label'] for h in self.link.hooks]


class CommandTests(LogBase):
    def test_unknown_action(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('jump')
        self.assertEqual(cm.exception.code, 'UNKNOWN_ACTION')

    def test_read_before_start_is_refused(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('commands', mode='read')
        self.assertEqual(cm.exception.code, 'NOT_ARMED')

    def test_start_installs_the_statement_hook_as_record_only(self):
        out = self.run_tool('commands', mode='start')
        self.assertTrue(out['armed'])
        self.assertEqual(self.labels(), [fnv_scriptdbg.EVAL_LABEL])
        self.assertFalse(self.link.sent('hookact'))

    def test_read_names_commands_and_filters_by_script(self):
        self.run_tool('commands', mode='start')
        self.live.deep.rings[fnv_scriptdbg.EVAL_LABEL] = [statement_record(1, 0x1234),
                                                          statement_record(2, 0x0042, line=13),
                                                          record(3, **{'12': 0x30999999, '13': 0x1234})]
        out = self.run_tool('commands', mode='read', script='QuestScript')
        self.assertEqual([r['command'] for r in out['rows']], ['Disable', 'Print'])
        self.assertEqual(out['rows'][0]['line'], 12)
        self.assertEqual(out['next'], 3)

    def test_read_filters_by_opcode(self):
        self.run_tool('commands', mode='start')
        self.live.deep.rings[fnv_scriptdbg.EVAL_LABEL] = [statement_record(1, 0x1234), statement_record(2, 0x0042)]
        out = self.run_tool('commands', mode='read', opcode=0x0042)
        self.assertEqual([r['seq'] for r in out['rows']], [2])

    def test_stop_while_breaks_hold_keeps_the_hook(self):
        self.run_tool('commands', mode='start')
        self.eval.holding = True
        out = self.run_tool('commands', mode='stop')
        self.assertTrue(out['armed'])
        self.assertEqual(self.labels(), [fnv_scriptdbg.EVAL_LABEL])

    def test_stop_removes_the_hook(self):
        self.run_tool('commands', mode='start')
        out = self.run_tool('commands', mode='stop')
        self.assertFalse(out['armed'])
        self.assertTrue(self.link.sent('unhook'))


class HistoryTests(LogBase):
    def test_history_lists_only_world_changing_commands(self):
        self.run_tool('commands', mode='start')
        self.live.deep.rings[fnv_scriptdbg.EVAL_LABEL] = [statement_record(1, 0x1234), statement_record(2, 0x0042)]
        out = self.run_tool('history', ref='player')
        self.assertEqual([r['command'] for r in out['rows']], ['Disable'])
        self.assertEqual(out['rows'][0]['reference'], '0x%08X' % REF)

    def test_history_needs_a_reference(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('history')
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')


class EventTests(LogBase):
    def test_start_hooks_both_flag_setters_under_flag_labels(self):
        out = self.run_tool('events', mode='start')
        self.assertTrue(out['armed'])
        self.assertEqual(sorted(self.labels()), ['locals_flag', 'script_flag'])
        targets = sorted(int(str(h['target']), 16) for h in self.link.hooks)
        self.assertEqual(targets, sorted([ACTION, LOCALS_ACTION]))

    def test_read_names_the_flags_and_setter(self):
        self.run_tool('events', mode='start')
        self.live.deep.rings['script_flag'] = [record(1, **{'12': REF, '14': 0x1})]
        self.live.deep.rings['locals_flag'] = [record(2, **{'3': 0, '12': 0x40})]
        out = self.run_tool('events', mode='read')
        flags = {r['source']: r['flag'] for r in out['rows']}
        self.assertEqual(flags['Script_SetActionFlag'], 'OnAdd')
        self.assertEqual(flags['ScriptLocals::SetActionFlag'], 'OnCombatEnd')
        self.assertEqual(out['rows'][0]['form'], '00000042')

    def test_stop_removes_flag_hooks(self):
        self.run_tool('events', mode='start')
        out = self.run_tool('events', mode='stop')
        self.assertFalse(out['armed'])
        self.assertEqual(len(self.link.sent('unhook')), 2)


class RaiseTests(LogBase):
    def test_unknown_flag_refused_with_the_valid_list(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('raise', ref='player', flag='OnActivate')
        self.assertEqual(cm.exception.code, 'BAD_FLAG')
        self.assertIn('OnAdd', str(cm.exception))

    def test_raise_calls_script_set_action_flag_with_the_mask(self):
        self.run_tool('raise', ref='player', flag='OnAdd')
        function, this, args = self.live.calls[-1]
        self.assertEqual(function, 'Script_SetActionFlag')
        self.assertEqual(args[0]['value'], '0x%08X' % REF)
        self.assertEqual(args[2], {'type': 'int', 'value': 0x1})

    def test_raise_needs_a_reference(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('raise', flag='OnAdd')
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')


class ErrorTests(LogBase):
    def test_errors_wraps_the_script_error_tool(self):
        self.live.script_errors = lambda since='last': {'errors': [{'script': 'ABCD'}], 'since': since}
        out = self.run_tool('errors')
        self.assertEqual(out['since'], 'last')
        self.assertEqual(out['errors'][0]['script'], 'ABCD')


if __name__ == '__main__':
    unittest.main()
