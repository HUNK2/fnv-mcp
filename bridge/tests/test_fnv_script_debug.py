"""fnv_script_debug (W2): breaks as holds on ScriptRunner::EvaluateLine, status and variables from the stopped
statement, set, eval, freeze and thaw, and the save/load refusal, against a fake companion and fake memory. The
helpers ScriptLink, ScriptEngine and ScriptLive are shared with test_fnv_script_log."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp import fnv_conv, fnv_debugger, fnv_engine, fnv_scriptdbg  # noqa: E402
from geck_mcp.fnv_deep import SELECTORS  # noqa: E402
from geck_mcp.fnv_pred import compile_predicate  # noqa: E402
from test_fnv_debug import FakeLink  # noqa: E402

EVAL = 0x005E1550
ACTION = 0x005AC750
LOCALS_ACTION = 0x005A8E20
PROCESS = 0x0118C000
PROCESS_BYTE = 0x0118C685
SCRIPT = 0x30000000          # Script object: form id +0xC, variable list +0x4C
RUNNER = 0x30010000          # ScriptRunner: locals +8, script +0x14
LOCALS = 0x30020000          # ScriptLocals: variable list +0x0C
NODE_V = 0x30030000          # BSSimpleList node of a local variable
VAR = 0x30040000             # ScriptLocalVariable: id +0, fValue +8, string flag +0x10
NODE_S = 0x30050000          # BSSimpleList node of the script's variable info
INFO = 0x30060000            # ScriptVariableInfo: id +0, name pointer +0x18, length +0x1C
NAME = 0x30070000
BLOCK = 0x30080000           # the hook's register block at the stop
FORM_ID = 0x0000ABCD
REF = 0x30090000
LINE = 12


class ScriptLink(FakeLink):
    """FakeLink plus the statement-hook ops: hookfilter, hookact (recorded), hook with its label, eval."""

    def __init__(self, regions):
        super().__init__(regions)
        self.filters, self.evals = [], []

    def eval(self, expression, ref=0, timeout=None):
        return self.one('eval', expression, ref)

    def one(self, *op, timeout=None):
        if op[0] == 'hook':
            self.ops.append(op)
            hook = {'id': 3 + len(self.hooks), 'label': bytes.fromhex(op[13]).decode('latin-1'), 'target': op[1],
                    'stolen': int(op[2], 16), 'calls': 0}
            self.hooks.append(hook)
            return {'ok': True, 'hook': hook}
        if op[0] == 'pred':
            self.ops.append(op)
            return {'ok': True, 'id': int(op[2], 16), 'ops': 1}
        if op[0] == 'hookfilter':
            self.ops.append(op)
            self.filters.append(op[1:])
            return {'ok': True}
        if op[0] == 'eval':
            self.ops.append(op)
            self.evals.append(op)
            return {'ok': True, 'result': {'type': 'number', 'value': 2}}
        return super().one(*op, timeout=timeout)


class ScriptEngine:
    """Names and addresses of the engine map the script tools resolve; symbolize names every frame frame_name."""

    def __init__(self):
        self.names = {'ScriptRunner::EvaluateLine': EVAL, 'Script_SetActionFlag': ACTION,
                      'ScriptLocals::SetActionFlag': LOCALS_ACTION}
        self.frame_name = 'ScriptRunner::EvaluateLine'
        self.commands = [{'name': 'Disable', 'opcode': '0x1234'}, {'name': 'Print', 'opcode': '0x0042'}]

    def resolve(self, name):
        if name in self.names:
            return self.names[name]
        raise fnv_engine.EngineMapError('NOT_FOUND', 'No function named %s' % name)

    def summary(self, address, with_location=True):
        return {'name': self.frame_name, 'status': 'proven'}

    def symbolize(self, address, modules=None, return_address=False):
        return {'kind': 'function', 'name': self.frame_name, 'offset': '0x0'}

    def containing(self, address):
        return None

    def _lines(self, rel):
        return []


class ScriptDeep:
    def __init__(self, live):
        self.live = live
        self.rings = {}

    def hooks(self):
        return [dict(h) for h in self.live.link.hooks]

    def records(self, h, since=0, limit=4096):
        recs = [r for r in self.rings.get(h['label'], []) if r[0] > since][-limit:]
        return len(self.rings.get(h['label'], [])), recs, 0

    def script_form(self, spec):
        if spec != 'QuestScript':
            raise fnv_conv.ConvError('NOT_FOUND', 'No script %s' % spec)
        return SCRIPT, {'formId': '%08X' % FORM_ID, 'formType': 17}, None

    def script_source(self, script_ptr):
        return None


class ScriptLive:
    def __init__(self, tmp):
        self.link = ScriptLink([
            (EVAL, bytearray(b'\x55\x8b\xec\x83\xec\x20' + b'\x90' * 26)),
            (ACTION, bytearray(b'\x55\x8b\xec\x83\xec\x20' + b'\x90' * 26)),
            (LOCALS_ACTION, bytearray(b'\x55\x8b\xec\x83\xec\x20' + b'\x90' * 26)),
            (PROCESS, bytearray(0x1000)),
            (SCRIPT, self._script()), (RUNNER, self._words({0x08: LOCALS})), (LOCALS, self._words({0x0C: NODE_V})),
            (NODE_V, self._words({0: VAR, 4: 0})), (VAR, self._var()), (NODE_S, self._words({0: INFO, 4: 0})),
            (INFO, self._info()), (NAME, bytearray(b'bossHP')), (BLOCK, self._block()), (REF, bytearray(0x100)),
        ])
        self.link.regions[3][1][0x685] = 1      # the process-scripts byte: scripts run
        self.engine_obj = ScriptEngine()
        self.deep = ScriptDeep(self)
        self.game = SimpleNamespace(process=lambda: (1234, 0))
        self.project = SimpleNamespace(state_dir=Path(tmp))
        self.forms = {SCRIPT: {'formId': '%08X' % FORM_ID, 'formType': 17},
                      REF: {'formId': '00000042', 'formType': 0x40}}
        self.calls = []

    @staticmethod
    def _words(values, size=0x40):
        raw = bytearray(size)
        for off, value in values.items():
            struct.pack_into('<I', raw, off, value)
        return raw

    def _script(self):
        raw = self._words({0x0C: FORM_ID, 0x4C: NODE_S}, 0x100)
        return raw

    def _var(self):
        raw = bytearray(0x20)
        struct.pack_into('<I', raw, 0, 5)
        struct.pack_into('<d', raw, 8, 2.0)
        return raw

    def _info(self):
        raw = bytearray(0x40)
        struct.pack_into('<I', raw, 0, 5)
        struct.pack_into('<I', raw, 0x18, NAME)
        struct.pack_into('<H', raw, 0x1C, 6)
        return raw

    def _block(self):
        raw = bytearray(0x100)
        struct.pack_into('<I', raw, fnv_debugger.HOOK_BLOCK['ecx'], RUNNER)
        args = [SCRIPT, 0x1234, REF, 0, 0x20, 0, LINE, 0]          # arg0 script, arg1 opcode, arg2 ref, arg6 line
        for i, value in enumerate(args):
            struct.pack_into('<I', raw, fnv_debugger.HOOK_ARGS + 4 * i, value)
        return raw

    def ready(self):
        return None

    def engine(self):
        return self.engine_obj

    def reads(self, spans):
        out = []
        for address, length in spans:
            data, at = self.link.region(address, length)
            out.append(bytes(data[at:at + length]) if data is not None else None)
        return out

    def u32s(self, addresses):
        return [struct.unpack('<I', raw)[0] if raw else None for raw in self.reads([(a, 4) for a in addresses])]

    def form_ids(self, pointers):
        return [self.forms.get(p) if p else None for p in pointers]

    def resolve(self, ref):
        if ref == 'player':
            return {'ptr': REF, 'formId': '00000042', 'expr': 'player'}
        raise fnv_conv.ConvError('REF_NOT_FOUND', 'No reference %s' % ref)

    def script_errors(self, since='last'):
        return {'errors': []}

    def engine_call(self, function, this=None, args=None, returns='int', **kw):
        self.calls.append((function, this, args))
        return {'value': 0}


def statement_stop(seq, hook_id, line=LINE, block=BLOCK):
    return {'seq': seq, 'kind': 'hook', 'id': hook_id, 'thread': '1000', 'main': True, 'eip': '%x' % EVAL,
            'frame': None, 'regs': '%x' % block, 'context': None, 'exception': None}


class FakeClock:
    """Advances one second per call, so a wait for a stop that never comes ends."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 1.0
        return self.now


class ScriptDebugTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.live = ScriptLive(self.tmp.name)
        self.link = self.live.link
        self.dbg = fnv_debugger.Debugger(self.live, sleep=lambda s: None, clock=FakeClock())
        self.dbg.pid = 1234
        self.tool = fnv_scriptdbg.ScriptDebugger(self.live, dbg=self.dbg)

    def run_tool(self, action, **args):
        return self.tool.run(action, **args)

    def hook_id(self):
        return self.link.hooks[0]['id']

    def stop_statement(self):
        self.link.stops = [statement_stop(1, self.hook_id())]


class BreakTests(ScriptDebugTestBase):
    def test_unknown_action(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('jump')
        self.assertEqual(cm.exception.code, 'UNKNOWN_ACTION')

    def test_break_needs_a_target(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('break', op='set')
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')

    def test_break_line_and_opcode_together(self):
        self.run_tool('break', op='set', script='QuestScript', line=LINE, opcode=0x1234)
        hold = self.link.sent('hookact')[-1]
        expected = compile_predicate('(arg0.[12]:u32 == %d and arg6:u32 == %d and arg1:u16 == %d)'
                                     % (FORM_ID, LINE, 0x1234))['hex']
        self.assertEqual(hold[3], '38')                       # the tool's predicate slot (56)
        self.assertEqual(self.link.sent('pred')[-1][1:], ('set', '38', expected))

    def test_break_is_a_hold_with_the_script_predicate_and_line_filter(self):
        out = self.run_tool('break', op='set', script='QuestScript', line=LINE)
        self.assertEqual(out['break']['script'], FORM_ID)
        self.assertEqual(out['break']['line'], LINE)
        self.assertEqual(self.link.sent('hook')[0][13], fnv_scriptdbg.EVAL_LABEL.encode().hex())
        hold = self.link.sent('hookact')[-1]
        self.assertEqual(hold[2], 'hold')
        expected = compile_predicate('(arg0.[12]:u32 == %d and arg6:u32 == %d)' % (FORM_ID, LINE))['hex']
        self.assertEqual(hold[3], '38')                       # the tool's predicate slot (56)
        self.assertEqual(self.link.sent('pred')[-1][1:], ('set', '38', expected))
        self.assertEqual((SELECTORS['none'], 0), [(int(f[1], 16), int(f[2], 16)) for f in self.link.filters][-1])
        self.assertTrue(self.tool.eval.holding)

    def test_second_break_with_other_filter_is_joined_with_or(self):
        self.run_tool('break', op='set', script='QuestScript', line=LINE)
        self.run_tool('break', op='set', script='QuestScript', opcode=0x1234)
        self.assertEqual(len(self.tool.breaks), 2)
        expected = compile_predicate('(arg0.[12]:u32 == %d and arg6:u32 == %d) or (arg0.[12]:u32 == %d and '
                                     'arg1:u16 == %d)' % (FORM_ID, LINE, FORM_ID, 0x1234))['hex']
        self.assertEqual(self.link.sent('pred')[-1][1:], ('set', '38', expected))

    def test_other_label_on_the_statement_hook_is_refused(self):
        self.link.hooks.append({'id': 9, 'label': 'script_line', 'target': '%x' % EVAL, 'stolen': 6})
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('break', op='set', script='QuestScript', line=LINE)
        self.assertEqual(cm.exception.code, 'HOOKED_ELSEWHERE')

    def test_clear_drops_the_hold_by_rehooking(self):
        self.run_tool('break', op='set', script='QuestScript', line=LINE)
        out = self.run_tool('break', op='clear')
        self.assertEqual(out['cleared'], [1])
        self.assertFalse(self.tool.eval.holding)
        self.assertTrue(self.link.sent('unhook'))


class StopTests(ScriptDebugTestBase):
    def test_status_reports_script_line_and_runner(self):
        self.run_tool('break', op='set', script='QuestScript', line=LINE)
        self.stop_statement()
        stop = self.run_tool('status')['stop']
        self.assertEqual(stop['script'], '%08X' % FORM_ID)
        self.assertEqual(stop['line'], LINE)
        self.assertEqual(stop['opcode'], 0x1234)

    def test_vars_lists_names_and_values(self):
        self.run_tool('break', op='set', script='QuestScript', line=LINE)
        self.stop_statement()
        out = self.run_tool('vars')
        self.assertEqual(out['total'], 1)
        self.assertEqual(out['variables'][0]['name'], 'bossHP')
        self.assertEqual(out['variables'][0]['value'], 2.0)
        self.assertEqual(out['variables'][0]['type'], 'number')

    def test_set_writes_the_variable_value(self):
        self.run_tool('break', op='set', script='QuestScript', line=LINE)
        self.stop_statement()
        self.run_tool('set', name='bossHP', value=9)
        data, at = self.link.region(VAR + 8, 8)
        self.assertEqual(struct.unpack_from('<d', data, at)[0], 9.0)

    def test_set_unknown_variable_refused(self):
        self.run_tool('break', op='set', script='QuestScript', line=LINE)
        self.stop_statement()
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('set', name='nope', value=1)
        self.assertEqual(cm.exception.code, 'NO_SUCH_VARIABLE')

    def test_status_and_vars_need_a_stop(self):
        self.link.stops = []
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('vars')
        self.assertEqual(cm.exception.code, 'NOT_STOPPED')

    def test_eval_runs_on_the_stopped_reference(self):
        self.run_tool('break', op='set', script='QuestScript', line=LINE)
        self.stop_statement()
        out = self.run_tool('eval', expression='GetStage QuestName')
        self.assertEqual(out['result'], {'type': 'number', 'value': 2})
        self.assertEqual(self.link.evals[-1][2], REF)

    def test_continue_refuses_a_save_load_stop(self):
        self.run_tool('break', op='set', script='QuestScript', line=LINE)
        self.stop_statement()
        self.live.engine_obj.frame_name = 'SaveGame'
        out = self.run_tool('continue')
        self.assertEqual(out['state'], 'running')
        self.assertEqual(self.tool.skipped, 1)
        self.assertTrue(any(op[2] == 'run' for op in self.link.sent('resume')))

    def test_continue_passes_a_normal_stop_on(self):
        self.run_tool('break', op='set', script='QuestScript', line=LINE)
        self.stop_statement()
        self.live.engine_obj.frame_name = 'ScriptRunner::EvaluateLine'
        self.link.on_resume = lambda op: setattr(self.link, 'stops', [statement_stop(2, self.hook_id())])
        out = self.run_tool('continue')
        self.assertEqual(out['stop']['seq'], 2)
        self.assertEqual(self.tool.skipped, 0)
        # next would ignore the break condition and stop at the very next statement
        self.assertEqual([op[2] for op in self.link.sent('resume')], ['run'])


class ProcessTests(ScriptDebugTestBase):
    def test_freeze_and_thaw_the_process_scripts_byte(self):
        self.assertEqual(self.run_tool('freeze')['state'], 'frozen')
        self.assertEqual(self.live.link.region(PROCESS_BYTE, 1)[0][PROCESS_BYTE - PROCESS], 0)
        out = self.run_tool('thaw')
        self.assertEqual(out['restored'], 1)
        self.assertEqual(self.live.link.region(PROCESS_BYTE, 1)[0][PROCESS_BYTE - PROCESS], 1)

    def test_freeze_refuses_an_unexpected_byte(self):
        self.live.link.regions[3][1][0x685] = 7
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_tool('freeze')
        self.assertEqual(cm.exception.code, 'UNEXPECTED_VALUE')

    def test_status_is_frozen_after_freeze(self):
        self.run_tool('freeze')
        self.assertEqual(self.run_tool('status')['state'], 'frozen')


if __name__ == '__main__':
    unittest.main()
