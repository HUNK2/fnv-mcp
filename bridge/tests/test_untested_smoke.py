"""PLAN-3 S4.5: smoke tests for modules that had none (fnv_oracle, fnv_replay, fnv_scriptcov, fnv_render,
wincapture) and the scenario runner's prepare failure, crash path and run_all.

The contract checked: every public entry point, called with one valid and one invalid input over the shared fake
companion (fakes.FakeLive, no game), returns a dict (or the value its docstring promises) or raises an error that
carries a code. A bare Python exception (TypeError, KeyError, AttributeError...) reaches the agent as FNV_ERROR with
no recovery step, so it counts as a bug."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fakes  # noqa: E402


def outcome(fn):
    """'value' when fn returns, 'coded:<CODE>' when it raises a coded error, else 'UNCODED <type>: <message>'."""
    try:
        fn()
        return 'value'
    except Exception as e:  # noqa: BLE001 - the classification is the test
        code = getattr(e, 'code', None)
        if isinstance(code, str) and code:
            return 'coded:' + code
        return 'UNCODED %s: %s' % (type(e).__name__, str(e)[:160])


class ContractCase(unittest.TestCase):
    def setUp(self):
        self.fake = fakes.FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live

    def check(self, cases):
        bad = {name: result for name, fn in cases.items() if (result := outcome(fn)).startswith('UNCODED')}
        self.assertEqual(bad, {})


class ReplaySmoke(ContractCase):
    def test_entry_points(self):
        from fnv_mcp.fnv_replay import ReplayTools
        t = ReplayTools(self.live)
        self.check({
            'list': lambda: t.run('list'),
            'unknown action': lambda: t.run('nope'),
            'play without name': lambda: t.run('play'),
            'play missing': lambda: t.run('play', name='missing'),
            'stop without record': lambda: t.run('stop'),
            'record': lambda: t.run('record', name='smoke'),
            'record bad name': lambda: t.run('record', name='../x'),
        })


class ScriptCoverageSmoke(ContractCase):
    def test_entry_points(self):
        from fnv_mcp.fnv_scriptcov import ScriptCoverage
        from fnv_mcp.fnv_scriptdbg import EvalHook
        t = ScriptCoverage(self.live, statement_hook=EvalHook(self.live), script_debug=None)
        self.check({
            'report': lambda: t.dispatch('report'),
            'stop without start': lambda: t.dispatch('stop'),
            'run without folder': lambda: t.dispatch('run'),
            'unknown action': lambda: t.dispatch('nope'),
            'start': lambda: t.dispatch('start'),
            'decompile bad form': lambda: t.decompile('NoSuchForm'),
        })


class RenderSmoke(ContractCase):
    def test_entry_points(self):
        from fnv_mcp.fnv_render import OverlayTools, RenderTools
        r, o = RenderTools(self.live), OverlayTools(self.live)
        self.check({
            'shaders': lambda: r.run('shaders'),
            'lights': lambda: r.run('lights'),
            'toggle unknown': lambda: r.run('toggle', name='nope'),
            'imagespace without name': lambda: r.run('imagespace'),
            'render unknown action': lambda: r.run('nope'),
            'overlay text': lambda: o.run('text', text='hi'),
            'overlay text missing': lambda: o.run('text'),
            'overlay marker without target': lambda: o.run('marker'),
            'overlay clear': lambda: o.run('clear'),
            'overlay unknown action': lambda: o.run('nope'),
        })


class OracleSmoke(ContractCase):
    def test_entry_points(self):
        from fnv_mcp.fnv_oracle import OracleTools
        t = OracleTools(self.live, naming=None, agent=None)
        self.check({
            'purity': lambda: t.purity(),
            'run unknown function': lambda: t.run('NoSuchFunction'),
            'sweep': lambda: t.sweep(limit=1),
            'calibrate': lambda: t.calibrate(limit=1),
            'forms': lambda: t.forms(per_type=1),
        })


@unittest.skipUnless(sys.platform == 'win32', 'Windows capture')
class WinCaptureSmoke(unittest.TestCase):
    def test_entry_points(self):
        from fnv_mcp import wincapture
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'x.png'
            bad = {name: result for name, fn in {
                'capture no window': lambda: wincapture.capture({'hwnd': 0}, out),   # callers pass {'hwnd': ...}
            }.items() if (result := outcome(fn)).startswith('UNCODED')}
        self.assertEqual(bad, {})


class ScenarioRunnerSmoke(ContractCase):
    def test_prepare_failure_is_reported(self):
        # The game runs; a start that names no valid cell is refused before any console line is sent (N15).
        from fnv_mcp.fnv_scenario import ScenarioRunner
        report = ScenarioRunner(self.live).run(data={'name': 'x', 'start': {'coc': 'bad;cell'}, 'steps': [{'wait': 1}]})
        self.assertFalse(report['passed'])
        self.assertEqual([(f['phase'], f.get('code')) for f in report['failures']], [('prepare', 'BAD_CELL')])
        sent = [bytes.fromhex(a[-1]).decode('cp1252', 'replace') for name, a in self.fake.link.ops if name == 'sched']
        self.assertFalse([line for line in sent if 'bad;cell' in line], sent)

    def test_crash_path_is_reported(self):
        # The game dies during a step: the report marks the crash (run phase), not a prepare failure.
        from unittest import mock
        from fnv_mcp import fnv_game, fnv_link, fnv_scenario
        from fnv_mcp.fnv_scenario import ScenarioRunner
        link, gone = self.fake.link, []
        real = link._op

        def op(name, args):
            if name in ('run', 'con') and 'GetAV Luck' in bytes.fromhex(args[0]).decode('cp1252', 'replace'):
                gone.append(1)
                self.live._game.process = lambda: None
                raise fnv_link.LinkError('COMPANION_DISCONNECTED', 'the game went away')
            return real(name, args)
        link._op = op
        procs = lambda: [] if gone else [(4242, 1)]  # noqa: E731
        with mock.patch.object(fnv_scenario, 'game_processes', procs), mock.patch.object(fnv_game, 'game_processes', procs):
            report = ScenarioRunner(self.live).run(data={'name': 'x', 'relaunch_on_crash': False,
                                                         'steps': [{'run': 'player.GetAV Luck'}]})
        self.assertTrue(gone, 'the step never ran: %s' % report['failures'])
        self.assertFalse(report['passed'])
        self.assertTrue(report.get('crash'), report['failures'])

    def test_run_all(self):
        from fnv_mcp.fnv_scenario import ScenarioRunner
        with tempfile.TemporaryDirectory() as tmp:
            self.check({
                'empty folder': lambda: ScenarioRunner(self.live).run_all(tmp),
                'missing folder': lambda: ScenarioRunner(self.live).run_all(str(Path(tmp) / 'none')),
            })


if __name__ == '__main__':
    unittest.main()
