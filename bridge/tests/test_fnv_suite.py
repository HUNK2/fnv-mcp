"""fnv_bisect against a fake MO2 profile and a fake scenario runner (the plan's check: among eight mods, the one that
breaks the scenario is named in three rounds), and fnv_crawl's cell scoring and voice naming rule."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp.fnv_crawl import CrawlTools  # noqa: E402
from fnv_mcp.fnv_suite import BisectTools  # noqa: E402

MODS = ['Mod A', 'Mod B', 'Mod C', 'Mod D', 'Quest Breaker', 'Mod F', 'Mod G', 'Mod H']


class FakeRecords:
    def __init__(self, state):
        self.state = state
        self.calls = 0

    def profile_set(self, mods, plugins, game, dry_run):
        self.calls += 1
        for k, v in (mods or plugins).items():
            self.state[k] = v


def bisect_with(culprit):
    tmp = tempfile.TemporaryDirectory()
    profile = Path(tmp.name)
    (profile / 'modlist.txt').write_text('# header\n' + '\n'.join(
        ['+fnvmcp engine companion'] + ['+' + m for m in MODS] + ['+JIP LN NVSE Plugin']) + '\n')
    state = {m: True for m in MODS}
    game = SimpleNamespace(profile_dir=profile, process=lambda: None, quit=lambda: None)
    live = SimpleNamespace(game=game)
    runs = []

    class Runner:
        def run(self, data):
            passed = not state.get(culprit, False)
            runs.append(dict(state))
            return {'passed': passed, 'summary': 'ok' if passed else 'quest stuck'}
    tools = BisectTools(live, FakeRecords(state), lambda: Runner())
    return tools, state, runs, tmp


class BisectTests(unittest.TestCase):
    def test_names_the_culprit_of_eight_in_three_rounds(self):
        tools, state, runs, tmp = bisect_with('Quest Breaker')
        self.addCleanup(tmp.cleanup)
        out = tools.bisect('mods', [{'expect': {'stage': 'MyQuest', 'equals': 10}}])
        self.assertEqual(out['culprit'], 'Quest Breaker')
        self.assertEqual(len([r for r in out['rounds'] if r['round'] > 0]), 3)
        self.assertNotIn('fnvmcp engine companion', out['candidates'])         # protected mods stay on
        self.assertTrue(all(state.values()))                                      # the profile is put back

    def test_a_scenario_that_passes_is_not_bisected(self):
        tools, state, runs, tmp = bisect_with('Not Installed')
        self.addCleanup(tmp.cleanup)
        out = tools.bisect('mods', [{'wait': 1}])
        self.assertIsNone(out.get('culprit'))
        self.assertIn('passes with every', out['verdict'])


class CrawlRuleTests(unittest.TestCase):
    def test_voice_file_name_follows_the_archive_rule(self):
        class Data:
            def _active(self):
                return [{'name': 'FalloutNV.esm', 'index': 0, 'masters': []}], None

            def _resolve(self, entries, ref):
                return 'falloutnv.esm', int(ref, 16) & 0xFFFFFF

            def record_show(self, ref, all_versions=False):
                return {'fields': [{'tag': 'TRDT'}, {'tag': 'TRDT'}]}
        crawl = CrawlTools(None, data=Data())
        crawl._edid = lambda form: 'VFreeformGoodsprings'
        files = crawl._voice_files('MaleOld02', '00104C66', 'PLAYERTHROWGRENADE', '001254A9')
        self.assertEqual(files, ['sound\\voice\\falloutnv.esm\\maleold02\\vfreeformg_playerthrowgren_001254a9_1',
                                 'sound\\voice\\falloutnv.esm\\maleold02\\vfreeformg_playerthrowgren_001254a9_2'])


if __name__ == '__main__':
    unittest.main()
