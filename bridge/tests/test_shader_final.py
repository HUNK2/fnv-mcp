"""fnv_shader final-review fixes: the tool from saved state with no game, one lock around the manifest, the manifest
re-checked against the module on every call, restore matching names without regard to case."""
from pathlib import Path
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp.fnv_live import LiveError  # noqa: E402
import test_shader_effects as E  # noqa: E402
import test_shader_replace as R  # noqa: E402


class NoGameNative(E.FakeNative):
    """Any use fails the test: with no game nothing may reach the module."""

    def is_loaded(self, key):
        raise AssertionError('is_loaded with no game')

    def call(self, name, export, args=None):
        raise AssertionError('native call %s with no game' % export)

    def bundled(self, key):
        raise AssertionError('bundled with no game')


def close_game(case, native=None):
    """No game process, the pipe raising, ready() and native untouchable."""
    case.live.game.process.return_value = None
    case.live.game_identity.side_effect = LiveError('NO_GAME', 'The pipe is closed')
    case.live.ready.side_effect = AssertionError('ready() with no game')
    case.tools.native = native or NoGameNative()


class NoGameEffects(E.Harness):
    def test_identity_is_none_without_a_process_and_never_pings(self):
        self.live.game.process.return_value = None
        self.assertIsNone(self.tools._identity())
        self.live.game_identity.assert_not_called()

    def test_identity_is_none_when_the_pipe_raises(self):
        self.live.game_identity.side_effect = LiveError('NO_GAME', 'gone')
        self.assertIsNone(self.tools._identity())

    def test_status_and_list_answer_from_the_manifest(self):
        self.tools.effect_add('x', E.INVERT, photo=False)
        close_game(self)
        out = self.tools.status()
        self.assertEqual((out['module'], out['effects']), (False, 0))
        out = self.tools.effect_list()
        self.assertEqual(out['effects'], [])
        self.assertEqual(out['savedSources'], ['x'])

    def test_remove_of_a_stale_live_entry_works_without_a_game(self):
        self.tools.effect_add('x', E.INVERT, photo=False)
        close_game(self)
        out = self.tools.effect_remove('x', photo=False)
        self.assertEqual(out['removed'], ['x'])
        m = self.manifest()
        self.assertEqual((m['effects'], m['saved']), ({}, {}))
        self.assertEqual(len(m['removed']), 1)

    def test_remove_all_with_stale_and_saved_entries(self):
        self.tools.effect_add('x', E.INVERT, photo=False)
        self.tools.effect_add('y', E.INVERT, photo=False)
        close_game(self)
        out = self.tools.effect_remove('all', photo=False)
        self.assertEqual(sorted(out['removed']), ['x', 'y'])
        m = self.manifest()
        self.assertEqual((m['effects'], m['saved']), ({}, {}))

    def test_undo_of_a_stale_remove_goes_back_to_saved(self):
        self.tools.effect_add('x', E.INVERT, photo=False)
        close_game(self)
        self.tools.effect_remove('x', photo=False)
        rid = next(iter(self.manifest()['removed']))
        self.assertIsNone(self.tools.undo_remove(rid))
        self.assertIn('x', self.manifest()['saved'])


class NoGameReplace(R.Replace):
    def test_restore_of_a_stale_live_replacement_works_without_a_game(self):
        self.tools.replace('SKYTEST.pso', R.OK_SRC, photo=False)
        close_game(self, R.ScanNative())
        self.tools.native = NoGameNative()
        out = self.tools.restore('SKYTEST.pso')
        self.assertEqual(out['restored'], ['SKYTEST.pso'])
        m = self.manifest()
        self.assertEqual((m['replacements'], m['savedReplacements']), ({}, {}))

    def test_restore_all_without_a_game(self):
        self.tools.replace('SKYTEST.pso', R.OK_SRC, photo=False)
        close_game(self)
        out = self.tools.restore('all')
        self.assertEqual(out['restored'], ['SKYTEST.pso'])
        self.assertEqual(self.manifest()['replacements'], {})

    def test_status_counts_no_live_replacement_without_a_game(self):
        self.tools.replace('SKYTEST.pso', R.OK_SRC, photo=False)
        close_game(self)
        self.assertEqual(self.tools.status()['replacements'], 0)

    def test_restore_matches_the_name_without_case(self):
        self.tools.replace('SKYTEST.pso', R.OK_SRC, photo=False)
        out = self.tools.restore('skytest.PSO')
        self.assertEqual(out['restored'], ['SKYTEST.pso'])

    def test_replace_verdict_and_guide_say_only_existing_objects_are_swapped(self):
        out = self.tools.replace('SKYTEST.pso', R.OK_SRC, photo=False)
        self.assertIn('creates later', out['verdict']['note'] if isinstance(out.get('verdict'), dict) else str(out))
        guide = (Path(__file__).resolve().parents[1] / 'fnv_mcp' / 'guides' / 'shaders.md').read_text('utf-8')
        self.assertIn('exist at the time of the call', guide)

    def test_saved_restore_matches_the_name_without_case(self):
        self.tools.replace('SKYTEST.pso', R.OK_SRC, photo=False)
        close_game(self)
        self.tools.state()
        out = self.tools.restore('SkyTest.pso')
        self.assertEqual(out['restored'], ['SKYTEST.pso'])


class Reverified(E.Harness):
    def test_module_unloaded_in_the_same_game_moves_effects_to_saved(self):
        self.tools.effect_add('x', E.INVERT, photo=False)
        self.tools.effect_add('y', E.INVERT, photo=False)
        self.assertEqual(self.tools.status()['effects'], 2)
        self.native.loaded = False
        self.assertEqual(self.tools.status()['effects'], 0)
        out = self.tools.effect_list()
        self.assertEqual(out['effects'], [])
        self.assertEqual(out['savedSources'], ['x', 'y'])
        m = self.manifest()
        self.assertEqual((sorted(m['saved']), m['effects']), (['x', 'y'], {}))

    def test_module_reloaded_without_the_slot_moves_the_effect_to_saved(self):
        self.tools.effect_add('x', E.INVERT, photo=False)
        self.native.slots.clear()                      # unloaded and loaded again: the slot is empty
        self.assertEqual(self.tools.status()['effects'], 0)
        self.assertIn('x', self.manifest()['saved'])


class OneLock(E.Harness):
    def test_parallel_adds_take_different_slots(self):
        real = self.tools._push

        def slow(entry):
            time.sleep(0.15)                           # between choosing the slot and writing the manifest
            return real(entry)
        self.tools._push = slow
        errors, out = [], {}

        def add(name):
            try:
                out[name] = self.tools.effect_add(name, E.INVERT, photo=False)
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=add, args=(n,)) for n in ('a', 'b')]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertNotEqual(out['a']['slot'], out['b']['slot'])
        m = self.manifest()
        self.assertEqual(sorted(m['effects']), ['a', 'b'])
        self.assertEqual(sorted(e['slot'] for e in m['effects'].values()), [0, 1])


if __name__ == '__main__':
    unittest.main()
