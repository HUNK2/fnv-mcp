"""Gemini audit area 9 (BUG-126 .. BUG-140): scenarios, bisect, experiments, atlas and the CLI helpers."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BRIDGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BRIDGE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp import fnv_scenario  # noqa: E402
from fnv_mcp.fnv_scenario import ScenarioRunner, compare, parse_yaml, _Crash  # noqa: E402
from fnv_mcp.fnv_suite import BisectTools  # noqa: E402
from test_bugfix_f import make_plugin, stat_ids  # noqa: E402
from w1_support import W1Live  # noqa: E402


class Passing:
    def run(self, data):
        return {'passed': True, 'failures': [], 'summary': 'x'}


class BisectKeepTests(unittest.TestCase):                      # BUG-126
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / 'A.esp'
        self.ids = ['%08X' % (0x01000800 + i) for i in range(4)]
        make_plugin(self.path, [int(i, 16) for i in self.ids])
        (self.root / 'modlist.txt').write_text('# h\n+M0\n+M1\n', encoding='utf-8')
        # the original order: a candidate before a base plugin; a re-enable would append it last
        self.plugins = 'ZFirst.esp\nFalloutNV.esm\nB.esp\n'
        (self.root / 'plugins.txt').write_text(self.plugins, encoding='utf-8')
        self.state = self.root / 'state'
        self.live = SimpleNamespace(game=SimpleNamespace(process=lambda: None, profile_dir=self.root),
                                    project=SimpleNamespace(state_dir=self.state))

    def tools(self):
        t = BisectTools(self.live, None, lambda: Passing(), quit_game=lambda: None)
        t._plugin_file = lambda: self.path
        return t

    def test_keep_records_stay_in_every_variant(self):
        t = self.tools()
        t.plugin = 'A.esp'
        t._original = None
        candidates = t._record_candidates([self.ids[3]])
        t._variant_candidates = set(candidates)
        self.assertNotIn(self.ids[3], candidates)
        t._write_variant({self.ids[0]})
        self.assertEqual(stat_ids(self.path), {self.ids[0], self.ids[3]})

    def test_profile_restore_writes_back_the_exact_backup(self):
        t = self.tools()
        t._backup_profile()
        (self.root / 'plugins.txt').write_text('FalloutNV.esm\n', encoding='utf-8')     # a bisect step disabled two
        self.assertTrue(t._restore_profile_backup())
        self.assertEqual((self.root / 'plugins.txt').read_text(encoding='utf-8'), self.plugins)

    def test_no_backup_means_no_restore(self):
        self.assertFalse(self.tools()._restore_profile_backup())


class SubstTests(unittest.TestCase):                           # BUG-127
    def setUp(self):
        self.runner = ScenarioRunner(W1Live())
        self.runner.vars = {'hp0': 100.0, 'name': 'Joe'}

    def test_whole_string_keeps_its_type(self):
        self.assertEqual(self.runner._subst('$hp0'), 100.0)
        self.assertIsInstance(self.runner._subst('$hp0'), float)
        self.assertEqual(self.runner._subst({'gt': '$hp0'}), {'gt': 100.0})
        self.assertEqual(compare(100.0, {'equals': self.runner._subst('$hp0')})[0], True)

    def test_partial_substitution_is_text(self):
        self.assertEqual(self.runner._subst('hp=$hp0 of $name'), 'hp=100.0 of Joe')
        self.assertEqual(self.runner._subst('$unknown'), '$unknown')


class CalledCheckTests(unittest.TestCase):                     # BUG-129
    def check(self, spec, count):
        runner = ScenarioRunner(W1Live())
        runner.live.agent = SimpleNamespace(trace=lambda *a, **k: {'recorded': count})
        actual, _what, out = runner._agent_expect(spec)
        return compare(actual, out)[0]

    def test_at_most_zero_is_satisfiable(self):
        self.assertTrue(self.check({'called': 'f', 'at_most': 0}, 0))
        self.assertFalse(self.check({'called': 'f', 'at_most': 0}, 1))

    def test_defaults_still_hold(self):
        self.assertTrue(self.check({'called': 'f'}, 1))
        self.assertFalse(self.check({'called': 'f'}, 0))
        self.assertTrue(self.check({'called': 'f', 'at_least': 2}, 2))
        self.assertTrue(self.check({'called': 'f', 'at_least': 1, 'at_most': 3}, 2))


class ScenarioRunTests(unittest.TestCase):
    def setUp(self):
        self.live = W1Live()
        self.runner = ScenarioRunner(self.live)
        for name, value in (('prepare', {'launched': False}), ('save_report', None), ('screenshot', None),
                            ('debug_bundle', {})):
            p = mock.patch.object(ScenarioRunner, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def test_crash_in_teardown_fails_and_relaunches(self):          # BUG-128
        launched = []
        self.live.game.link = SimpleNamespace(close=lambda: None)
        self.live.game.launch = lambda start, timeout=0: launched.append(start) or {'ok': True}
        real = ScenarioRunner.step

        def step(self_, spec, marker):
            if 'revert_all' in spec:
                raise _Crash('gone', {'why': 'x'})
            return real(self_, spec, marker)
        with mock.patch.object(ScenarioRunner, 'step', step):
            report = self.runner.run(data={'name': 't', 'allow_menus': True, 'steps': [],
                                           'teardown': [{'revert_all': True}]})
        self.assertFalse(report['passed'])
        self.assertEqual(report['failures'][-1]['phase'], 'teardown')
        self.assertTrue(launched)
        self.assertEqual(report['relaunched'], {'ok': True})

    def test_trace_is_stopped_when_the_run_errors_early(self):      # BUG-134
        calls = []
        self.live.deep = SimpleNamespace(script_trace=lambda script, action, **k: calls.append(action) or {})
        self.live.script_errors = mock.Mock(side_effect=RuntimeError('boom'))
        self.runner.run(data={'name': 't', 'allow_menus': True, 'trace_script': 'S', 'steps': []})
        self.assertEqual(calls, ['start', 'stop'])

    def test_crash_context_is_an_accepted_key(self):                # BUG-132
        self.assertIn('crash_context', fnv_scenario.SCENARIO_KEYS)


class ParserTests(unittest.TestCase):                          # BUG-133
    def test_escaped_quote_before_hash(self):
        self.assertEqual(parse_yaml('a: "x \\" # y" # c'), {'a': 'x " # y'})

    def test_doubled_apostrophe_before_hash(self):
        self.assertEqual(parse_yaml("a: 'it''s # x' # c"), {'a': "it's # x"})

    def test_flow_doubled_apostrophe(self):
        self.assertEqual(parse_yaml("a: {k: 'it''s'}"), {'a': {'k': "it's"}})

    def test_list_item_with_extra_spaces(self):
        self.assertEqual(parse_yaml('- run: x\n  b: 1\n-   run: y\n    b: 2'),
                         [{'run': 'x', 'b': 1}, {'run': 'y', 'b': 2}])


def load_tool(name, folder='tools'):
    root = BRIDGE.parent
    spec = importlib.util.spec_from_file_location(name, root / folder / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseTests(unittest.TestCase):                         # BUG-138
    def test_source_suffix_is_case_insensitive_and_heading_is_a_token(self):
        module = load_tool('package_release', 'tools') if (BRIDGE.parent / 'tools' / 'package_release.py').is_file() \
            else None
        self.assertIsNotNone(module)
        self.assertTrue(module.included('bridge/fnv_mcp/FOO.PY'))
        text = (BRIDGE.parent / 'tools' / 'package_release.py').read_text(encoding='utf-8')
        self.assertIn("re.split(", text)


class ExperimentTests(unittest.TestCase):                      # BUG-140
    def test_zero_reps_is_refused(self):
        from fnv_mcp.fnv_experiment import ExperimentTools
        e = object.__new__(ExperimentTools)
        e.live = SimpleNamespace(in_game=lambda: None, engine=lambda: None)
        with self.assertRaises(ValueError):
            e.blind_battery(reps=0)


if __name__ == '__main__':
    unittest.main()
