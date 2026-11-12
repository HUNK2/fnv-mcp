"""PLAN-3 S1: the round-2 high bugs R2 and R3 (bisect)."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp.fnv_suite import CRASH_CODES, BisectTools  # noqa: E402

CRASH = {'passed': False, 'crash': None, 'summary': 'FAILED',
         'failures': [{'phase': 'prepare', 'reason': 'The game exited while loading', 'code': 'GAME_EXITED',
                       'evidence': {'crash': {'module': 'FalloutNV.exe'}}}]}
BLOCKED = {'passed': False, 'crash': None, 'summary': 'FAILED',
           'failures': [{'phase': 'prepare', 'reason': 'launch blocked', 'code': 'LAUNCH_BLOCKED', 'evidence': None}]}
PASS = {'passed': True, 'failures': [], 'summary': 'PASSED'}
FAIL = {'passed': False, 'failures': [{'phase': 'steps', 'reason': 'expectation failed'}], 'summary': 'FAILED'}


class Fixture:
    """A profile with four candidates; `rule(state)` gives the scenario report for the current on/off state."""

    def __init__(self, test, rule, level='mods', names=('M0', 'M1', 'M2', 'M3'), masters=None):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        profile = Path(tmp.name)
        if level == 'mods':
            (profile / 'modlist.txt').write_text('# h\n' + '\n'.join('+' + n for n in names) + '\n')
        else:
            (profile / 'plugins.txt').write_text('# h\nFalloutNV.esm\n' + '\n'.join(names) + '\n')
        self.state = {n: True for n in names}
        self.seen = []
        fixture = self

        class Records:
            def profile_set(self, mods, plugins, game, dry):
                fixture.state.update(mods or plugins)

        class Runner:
            def run(self, data):
                on = frozenset(n for n, v in fixture.state.items() if v)
                fixture.seen.append(on)
                return rule(on)
        game = SimpleNamespace(profile_dir=profile, process=lambda: None, quit=lambda: None)
        self.tools = BisectTools(SimpleNamespace(game=game), Records(), lambda: Runner())
        if masters is not None:
            self.tools._plugin_masters = lambda names: {n.lower(): [m for m in masters.get(n, [])] for n in names}


class ClassifyTests(unittest.TestCase):
    def test_crash_codes(self):
        self.assertEqual(set(CRASH_CODES), {'GAME_EXITED', 'GAME_CRASHED', 'NOT_IN_GAME'})

    def test_classify(self):
        classify = BisectTools._classify
        self.assertEqual(classify(PASS), 'pass')
        self.assertEqual(classify(FAIL), 'fail')
        self.assertEqual(classify(CRASH), 'fail')
        self.assertEqual(classify(BLOCKED), 'inconclusive')
        no_code_crash = {'passed': False, 'failures': [{'phase': 'prepare', 'reason': 'x', 'code': None,
                                                        'evidence': {'crash': {'module': 'x.dll'}}}]}
        self.assertEqual(classify(no_code_crash), 'fail')
        self.assertEqual(classify({'passed': False, 'crash': {'x': 1}, 'failures': [{'phase': 'prepare'}]}), 'fail')


class BisectCrashTests(unittest.TestCase):                   # R2
    def test_load_crash_counts_as_fail(self):
        fx = Fixture(self, lambda on: CRASH if 'M2' in on else PASS)
        out = fx.tools.bisect('mods', [{'wait': 1}])
        self.assertEqual(out['culprit'], 'M2', out['verdict'])
        self.assertTrue(out['confirmed'])

    def test_launch_blocked_is_inconclusive(self):
        fx = Fixture(self, lambda on: BLOCKED)
        out = fx.tools.bisect('mods', [{'wait': 1}])
        self.assertTrue(out['verdict'].startswith('Inconclusive'), out['verdict'])
        self.assertIsNone(out.get('culprit'))

    def test_every_half_crashes(self):
        fx = Fixture(self, lambda on: CRASH)
        out = fx.tools.bisect('mods', [{'wait': 1}])
        self.assertIsNone(out.get('culprit'))
        self.assertIn('every tested set crashed', out['verdict'].lower())

    def test_half_keeps_its_masters(self):
        names = ('A.esp', 'B.esp', 'C.esp', 'D.esp')
        fx = Fixture(self, lambda on: FAIL if 'C.esp' in on else PASS, level='plugins', names=names,
                     masters={'A.esp': ['FalloutNV.esm', 'B.esp']})
        out = fx.tools.bisect('plugins', [{'wait': 1}])
        for on in fx.seen:
            self.assertFalse('A.esp' in on and 'B.esp' not in on, sorted(on))
        self.assertEqual(out['culprit'], 'C.esp', out['verdict'])

    def test_suspects_that_cannot_be_split_stop(self):
        # A needs B and both are suspects: any half with A also has B, so halving makes no progress.
        fx = Fixture(self, lambda on: FAIL if 'A.esp' in on else PASS, level='plugins', names=('A.esp', 'B.esp'),
                     masters={'A.esp': ['B.esp']})
        out = fx.tools.bisect('plugins', [{'wait': 1}])
        self.assertLessEqual(len(fx.seen), 4, 'it kept relaunching without progress')
        self.assertIsNone(out.get('culprit'))
        self.assertEqual(sorted(out['suspects']), ['A.esp', 'B.esp'])

    def test_close_under_masters_is_transitive(self):
        fx = Fixture(self, lambda on: PASS, level='plugins', names=('A.esp', 'B.esp', 'C.esp'),
                     masters={'A.esp': ['B.esp'], 'B.esp': ['C.esp']})
        fx.tools._masters = fx.tools._plugin_masters(['A.esp', 'B.esp', 'C.esp'])
        self.assertEqual(fx.tools._close_under_masters(['A.esp'], ['A.esp', 'B.esp', 'C.esp']),
                         ['A.esp', 'B.esp', 'C.esp'])


if __name__ == '__main__':
    unittest.main()
