"""Gemini audit area 4 (BUG-053 .. BUG-064): debugger, flow, coverage, blame and engine AI fixes."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp import fnv_blame, fnv_conv, fnv_coverage, fnv_deep, fnv_engine_ai, fnv_scriptcov  # noqa: E402
from fnv_mcp.fnv_link import word  # noqa: E402
from test_fnv_blame import make_live  # noqa: E402
from test_fnv_debug import DebugTestBase  # noqa: E402


class BlamePresetStringTests(unittest.TestCase):
    """BUG-054: what is a string on the wire."""

    def run_ref(self, what):
        tool = fnv_blame.BlameTools(make_live(), sleep=lambda s: None, clock=lambda: 0.0)
        tool.live.engine = lambda: None
        seen = []

        def target(engine, candidates):
            seen.append(candidates)
            return None, None
        tool._preset_target = target
        out = tool.ref('player', what=what)
        return out, seen

    def test_a_single_preset_name_is_accepted(self):
        name = fnv_blame.PRESET_NAMES[0]
        _, seen = self.run_ref(name)
        self.assertEqual(len(seen), 1)

    def test_comma_separated_names_are_accepted(self):
        a, b = fnv_blame.PRESET_NAMES[:2]
        _, seen = self.run_ref('%s, %s' % (a, b))
        self.assertEqual(len(seen), 2)

    def test_all_means_every_preset(self):
        _, seen = self.run_ref('all')
        self.assertEqual(len(seen), len(fnv_blame.PRESET_NAMES))

    def test_a_list_still_works(self):
        _, seen = self.run_ref(list(fnv_blame.PRESET_NAMES[:2]))
        self.assertEqual(len(seen), 2)

    def test_an_unknown_name_in_a_string_is_refused(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.run_ref('%s,nope' % fnv_blame.PRESET_NAMES[0])
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')
        self.assertIn('nope', str(cm.exception))


class ClearSweepTests(DebugTestBase):
    """BUG-055: the clear-all sweep leaves a stuck break's hook alone and lists only what it removed."""

    def test_sweep_skips_the_stuck_hook_and_reports_only_removed_ones(self):
        hold = self.run_tool('break', op='set', location='Actor::ApplyDamage', when='frame > 5')['break']['number']
        stuck_id = self.dbg.breaks[hold]['id']
        self.link.hooks.append({'id': 77, 'label': 'dbg 5 Orphan::Fn', 'target': '%x' % 0x500000, 'stolen': 6})
        original = self.link.one

        def one(*op, timeout=None):
            if op[0] == 'unhook' and int(op[1], 16) == stuck_id:
                self.link.ops.append(op)
                return {'ok': False, 'error': 'busy'}
            return original(*op, timeout=timeout)
        self.link.one = one
        out = self.run_tool('break', op='clear')
        self.assertEqual(out['notCleared'][0]['break'], hold)
        self.assertEqual(out['swept']['hooks'], [77])
        retries = [op for op in self.link.sent('unhook') if int(op[1], 16) == stuck_id]
        self.assertEqual(len(retries), 1)            # tried once as the break, not again by the sweep


class CoverageFakeLink:
    def __init__(self, armed=False):
        self.ops, self.armed, self.added = [], armed, 0

    def one(self, *op, timeout=None):
        self.ops.append(op)
        if op[1] == 'disarm':
            self.armed = False
            return {'ok': True, 'hits': 0}
        if op[1] == 'reset':
            return {'ok': False, 'error': 'armed'} if self.armed else {'ok': True}
        if op[1] == 'add':
            if self.armed:
                return {'ok': False, 'error': 'armed'}
            return {'ok': True, 'added': len(op[2]) // 8, 'skipped': 0}
        if op[1] == 'arm':
            self.armed = True
            return {'ok': True, 'armed': 1}
        if op[1] == 'read':
            return {'addresses': '', 'next': 0, 'hits': 0}
        return {'ok': True}


class CoverageTests(unittest.TestCase):
    def tool(self, link, queries=None):
        db = SimpleNamespace(_all=lambda sql, params=(): (queries.append(sql) if queries is not None else None) or [
            {'a': 0x401000, 'line': 3, 'path': 'src/a.cpp', 'func_addr': 0x401000}])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        engine = SimpleNamespace(symdb=db, resolve=lambda name: 0x401000, summary=lambda a, with_location=True: {})
        live = SimpleNamespace(link=link, ready=lambda: None, engine=lambda: engine, changing=lambda *a: None,
                               project=SimpleNamespace(state_dir=tmp.name))
        return fnv_coverage.CoverageTools(live)

    def test_functions_query_filters_on_verified(self):
        queries = []
        self.tool(CoverageFakeLink(), queries).start(functions=['f'])
        self.assertTrue(all('verified = 1' in q for q in queries))

    def test_start_adopts_a_run_left_armed_by_an_earlier_server(self):
        link = CoverageFakeLink(armed=True)
        out = self.tool(link).start(functions=['f'])
        self.assertTrue(link.armed)
        self.assertEqual(out['data']['armed'] if 'data' in out else out['armed'], 1)

    def test_stop_without_a_run_disarms_an_orphan(self):
        link = CoverageFakeLink(armed=True)
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.tool(link).stop()
        self.assertEqual(cm.exception.code, 'NOT_STARTED')
        self.assertFalse(link.armed)


class PrologueGuardTests(unittest.TestCase):
    """BUG-057: cheap decoder guards."""

    def decode(self, raw):
        return fnv_deep.decode_prologue(bytes(raw) + b'\x90' * 24)

    def test_int3_in_a_prologue_is_refused(self):
        with self.assertRaises(fnv_deep.HookError):
            self.decode(b'\xcc\x8b\xff\x55\x8b\xec')

    def test_a_16_bit_jump_or_call_is_refused(self):
        for raw in (b'\x66\xe8\x00\x00\x00\x00', b'\x66\xe9\x00\x00\x00\x00', b'\x66\x0f\x84\x00\x00\x00\x00'):
            with self.assertRaises(fnv_deep.HookError):
                self.decode(raw + b'\x90' * 4)

    def test_a_normal_prologue_still_decodes(self):
        self.assertEqual(self.decode(b'\x55\x8b\xec\x83\xec\x20')[0], 6)


class ScriptCoverageTests(unittest.TestCase):
    def cov(self, suite):
        runner = SimpleNamespace(run_all=suite)
        return fnv_scriptcov.ScriptCoverage(SimpleNamespace(ready=lambda: None), None, None, runner=lambda: runner)

    def test_a_suite_error_is_not_replaced_by_not_recording(self):
        cov = self.cov(lambda folder: (_ for _ in ()).throw(RuntimeError('suite broke')))
        cov.start = lambda plugin=None: None
        with self.assertRaises(RuntimeError):
            cov.run('tests')

    def test_suite_summaries_are_reported(self):
        cov = self.cov(lambda folder: {'passed': 1, 'summaries': ['x']})
        cov.start = lambda plugin=None: None
        cov.stop = lambda: {}
        self.assertEqual(cov.run('tests')['suite']['summaries'], ['x'])

    def test_report_survives_the_poll_thread_adding(self):
        live = SimpleNamespace(ready=lambda: None, form_ids=lambda ptrs: [{} for _ in ptrs])
        cov = fnv_scriptcov.ScriptCoverage(live, None, None)
        cov.seen = {1: {1, 2}}
        self.assertEqual(cov.report()['data']['scripts'] if 'data' in cov.report() else cov.report()['scripts'], [])


class PerkStopTests(unittest.TestCase):
    """BUG-056: stop counts only hooks that came out."""

    def test_unhook_reports_failure(self):
        def remove(hook_id):
            raise RuntimeError('refused')
        agent = SimpleNamespace(deep=SimpleNamespace(remove=remove))
        tool = fnv_engine_ai.EngineAITools.__new__(fnv_engine_ai.EngineAITools)
        tool.agent = agent
        self.assertFalse(tool._unhook({'id': 1}))
        agent.deep.remove = lambda hook_id: {'ok': True}
        self.assertTrue(tool._unhook({'id': 1}))


if __name__ == '__main__':
    unittest.main()
