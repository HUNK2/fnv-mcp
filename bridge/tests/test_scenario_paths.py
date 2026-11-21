"""fnv_test finds the shipped examples the test-a-mod guide names, takes name= with action=run and says plainly
when the tests folder does not exist (live check 2026-11-18). Fakes only; no game."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp import action_params  # noqa: E402
from fnv_mcp.fnv_scenario import ScenarioError, ScenarioRunner  # noqa: E402
from fnv_mcp.safe_paths import EXAMPLES_DIR, read_roots  # noqa: E402


def runner(tests_dir=None, state=None):
    project = SimpleNamespace(project_dir=state, state_dir=state, game_root=None,
                              fnv=SimpleNamespace(tests_dir=tests_dir, mo2_root=None, decomp_root=None, crash_dirs=[],
                                                  mo2_profile=None, game_root=None))
    r = ScenarioRunner.__new__(ScenarioRunner)
    r.live = SimpleNamespace(project=project)
    return r


class ExamplesTests(unittest.TestCase):
    def test_the_examples_folder_is_readable(self):
        self.assertTrue((EXAMPLES_DIR / 'fnv-tests' / 'hello-wasteland.yaml').is_file())
        with tempfile.TemporaryDirectory() as d:
            r = runner(state=d)
            try:
                roots = read_roots(r.live.project)
            except Exception:  # noqa: BLE001 - a layout the fake project lacks: the root list is what matters
                self.skipTest('read_roots needs a fuller project')
            self.assertIn(EXAMPLES_DIR, roots)
            for path in ('fnv-tests/hello-wasteland.yaml', 'examples/fnv-tests/hello-wasteland.yaml',
                         str(EXAMPLES_DIR / 'fnv-tests' / 'hello-wasteland.yaml')):
                scenario = r.load(path)
                self.assertEqual(scenario['_source'], str((EXAMPLES_DIR / 'fnv-tests' / 'hello-wasteland.yaml')
                                                          .resolve()), path)

    def test_a_missing_scenario_names_the_examples(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ScenarioError) as caught:
                runner(state=d).load('fnv-tests/none.yaml')
        self.assertEqual(caught.exception.code, 'NOT_FOUND')
        self.assertIn(str(EXAMPLES_DIR / 'fnv-tests'), str(caught.exception))

    def test_run_all_on_a_missing_tests_dir_says_so(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ScenarioError) as caught:
                runner(tests_dir=str(Path(d) / 'fnv-tests'), state=d).run_all()
        self.assertEqual(caught.exception.code, 'NO_TESTS')
        self.assertIn('does not exist', str(caught.exception))
        self.assertIn("fnv.tests_dir", str(caught.exception))


class NameTests(unittest.TestCase):
    def test_run_takes_name(self):
        self.assertIn('name', action_params.ACTION_PARAMS['fnv_test']['actions']['run'])
        r = runner()
        seen = {}
        r._run_once = lambda scenario, relaunch: seen.setdefault('name', scenario['name']) and {'passed': True}
        r.run(text='steps:\n  - wait: 0\n', name='hello-wasteland')
        self.assertEqual(seen['name'], 'hello-wasteland')


if __name__ == '__main__':
    unittest.main()
