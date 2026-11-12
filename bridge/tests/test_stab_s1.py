"""PLAN-3 S1: the round-2 high bugs R2 and R3 (bisect)."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp.fnv_conv import ConvError  # noqa: E402
from geck_mcp.fnv_suite import CRASH_CODES, BisectTools, restore_markers  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bugfix_f import make_plugin, stat_ids  # noqa: E402

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



class Passing:
    def run(self, data):
        return {'passed': True, 'failures': [], 'summary': 'x'}


class PendingRestoreTests(unittest.TestCase):                # R3
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / 'A.esp'
        self.ids = ['%08X' % (0x01000800 + i) for i in range(4)]
        self.original = make_plugin(self.path, [int(i, 16) for i in self.ids])
        (self.root / 'modlist.txt').write_text('# h\n+M0\n+M1\n', encoding='utf-8')
        (self.root / 'plugins.txt').write_text('FalloutNV.esm\nA.esp\n', encoding='utf-8')
        (self.root / 'loadorder.txt').write_text('FalloutNV.esm\nA.esp\n', encoding='utf-8')
        self.state = self.root / 'state'
        self.live = SimpleNamespace(game=SimpleNamespace(process=lambda: None, profile_dir=self.root),
                                    project=SimpleNamespace(state_dir=self.state))

    def tools(self):
        t = BisectTools(self.live, None, lambda: Passing(), quit_game=lambda: None)
        t._plugin_file = lambda: self.path
        return t

    def dead_server(self):
        """A server that wrote its backup and a variant, then died (no finally ran)."""
        t1 = self.tools()
        t1.plugin = 'A.esp'
        t1._original = None
        t1._record_candidates(None)
        t1._backup_original()
        t1._write_variant({self.ids[0]})
        self.assertEqual(stat_ids(self.path), {self.ids[0]})
        # the marker belongs to a process that is gone
        marker = self.state / 'fnv/bisect/A.esp.restore.json'
        import json
        data = json.loads(marker.read_text(encoding='utf-8'))
        data['pid'] = 0x7FFFFFF0
        marker.write_text(json.dumps(data), encoding='utf-8')
        return marker

    def test_dead_server_then_new_bisect_restores_original(self):
        self.dead_server()
        seen = []
        t2 = self.tools()
        real = t2._record_candidates

        def candidates(keep):
            seen.append(set(stat_ids(self.path)))
            return real(keep)
        t2._record_candidates = candidates
        t2.bisect('records', [{'wait': 1}], plugin='A.esp')
        self.assertEqual(seen[0], set(self.ids))                 # it started from the 4-record original
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(stat_ids(self.path), set(self.ids))

    def test_marker_removed_after_success(self):
        self.tools().bisect('records', [{'wait': 1}], plugin='A.esp')
        self.assertEqual(restore_markers(self.state), [])
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_backup_missing_refuses(self):
        marker = self.dead_server()
        import json
        Path(json.loads(marker.read_text(encoding='utf-8'))['backup']).unlink()
        variant = self.path.read_bytes()
        with self.assertRaises(ConvError) as cm:
            self.tools().bisect('records', [{'wait': 1}], plugin='A.esp')
        self.assertEqual(cm.exception.code, 'RESTORE_PENDING')
        self.assertEqual(self.path.read_bytes(), variant)
        self.assertTrue(marker.exists())

    def test_profile_marker_restores_lists(self):
        t = self.tools()
        t._backup_profile()
        before = {n: (self.root / n).read_bytes() for n in ('modlist.txt', 'plugins.txt', 'loadorder.txt')}
        for n in before:
            (self.root / n).write_text('# pruned\n', encoding='utf-8')
        import json
        marker = self.state / 'fnv/bisect/profile.restore.json'
        data = json.loads(marker.read_text(encoding='utf-8'))
        data['pid'] = 0x7FFFFFF0
        marker.write_text(json.dumps(data), encoding='utf-8')
        done = self.tools().finish_pending()
        self.assertEqual({n: (self.root / n).read_bytes() for n in before}, before)
        self.assertEqual(restore_markers(self.state), [])
        self.assertTrue(done and done[0].get('restored'))

    def test_marker_of_a_live_server_is_busy(self):
        t1 = self.tools()
        t1.plugin = 'A.esp'
        t1._original = None
        t1._record_candidates(None)
        t1._backup_original()                     # this process: alive, so another server's bisect is running
        t1._write_variant({self.ids[0]})
        other = self.tools()
        other._pid = lambda: 1                    # pretend to be another server
        with self.assertRaises(ConvError) as cm:
            other.finish_pending()
        self.assertEqual(cm.exception.code, 'BUSY')
        self.assertEqual(stat_ids(self.path), {self.ids[0]})

    def test_status_lists_pending_restore(self):
        from geck_mcp import fnv_game
        self.dead_server()
        project = SimpleNamespace(fnv=SimpleNamespace(mo2_root=str(self.root), mo2_profile='Dev', engine_pipe=None),
                                  game_root=str(self.root / 'game'), state_dir=self.state)
        game = fnv_game.Game(project)
        game.process = lambda: None
        out = game.status()
        self.assertEqual([m['plugin'] for m in out['pendingRestore']], [str(self.path)])


if __name__ == '__main__':
    unittest.main()
