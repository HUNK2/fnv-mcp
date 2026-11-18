"""Bug hunt B (2026-11-18): fnv_create (place remove / adjust / export), fnv_hands click, fnv_eyes camera set,
fnv_world (world_edit, window, heap, menu names). Fakes only; no game."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_fnv_create import FakeLive  # noqa: E402
from geck_mcp import fnv_hands, fnv_world  # noqa: E402
from geck_mcp.fnv_create import AuthorTools, PlaceTools  # noqa: E402
from geck_mcp.fnv_eyes import EyesTools  # noqa: E402
from geck_mcp.fnv_hands import HandsTools, menu_id_of  # noqa: E402


class PlaceRemoveTests(unittest.TestCase):
    def test_partial_failure_still_marks_the_removed_ones_undone(self):
        with tempfile.TemporaryDirectory() as d:
            live = FakeLive(d)
            place = PlaceTools(live)
            first = place.at('0003404A', position=[0, 0, 0])['ref']
            second = place.at('0003404A', position=[5, 0, 0])['ref']
            real = live.resolve

            def resolve(ref):
                if ref == second:
                    raise RuntimeError('game went away')
                return real(ref)
            live.resolve = resolve
            with self.assertRaises(RuntimeError):
                place.remove()
            live.resolve = real
            # the first is deleted in the game: it must not stay listed (and exported)
            self.assertEqual([p['ref'] for p in place.list()['placed']], [second])
            self.assertIn(first, [e['ref'] for e in live.journal.entries() if e['kind'] == 'place' and e['undone']])

    def test_remove_accepts_a_0x_ref(self):
        with tempfile.TemporaryDirectory() as d:
            live = FakeLive(d)
            place = PlaceTools(live)
            ref = place.at('0003404A', position=[0, 0, 0])['ref']
            self.assertEqual(place.remove('0x' + ref.lower())['removed'], [ref])


class PlaceInputTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.live = FakeLive(self.dir.name)
        self.place = PlaceTools(self.live)

    def tearDown(self):
        self.dir.cleanup()

    def test_adjust_without_a_value_raises(self):
        ref = self.place.at('0003404A', position=[0, 0, 0])['ref']
        before = len(self.live.journal.entries())
        with self.assertRaises(ValueError):
            self.place.adjust(ref)
        with self.assertRaises(ValueError):
            self.place.adjust(ref, local=True)
        self.assertEqual(len(self.live.journal.entries()), before)    # nothing journaled for a no-op

    def test_adjust_with_a_short_vector_raises(self):
        ref = self.place.at('0003404A', position=[0, 0, 0])['ref']
        with self.assertRaises(ValueError):
            self.place.adjust(ref, move=[1, 2])

    def test_at_with_a_zero_scale_raises(self):
        with self.assertRaises(ValueError):
            self.place.at('0003404A', position=[0, 0, 0], scale=0)
        with self.assertRaises(ValueError):
            self.place.at('0003404A', position=[0, 0], rotation=[0, 0, 0])
        self.assertEqual(self.live.refs, {})       # nothing was placed


class ExportTests(unittest.TestCase):
    def test_typed_record_keeps_its_name(self):
        with tempfile.TemporaryDirectory() as d:
            live = FakeLive(d)
            live.journal.add({'kind': 'form_create', 'type': 'misc', 'name': 'Lucky Coin', 'formId': 'FF000042',
                              'undo': None})
            author = AuthorTools(live, place=PlaceTools(live))
            author._editor_id = lambda form_id: None
            spec = json.loads(Path(author.export(name='t')['path']).read_text())
            rec = next(iter(spec['records'].values()))
            self.assertEqual(rec['type'], 'MISC')
            self.assertEqual(rec['fields'], [{'tag': 'FULL', 'text': 'Lucky Coin'}])

    def test_placement_without_a_pose_is_left_out_with_a_note(self):
        with tempfile.TemporaryDirectory() as d:
            live = FakeLive(d)
            live.journal.add({'kind': 'place', 'state': 'pending', 'ref': 'FF000099', 'base': '0003404A',
                              'undo': None})

            def resolve(ref):
                raise RuntimeError('ref is gone')
            live.resolve = resolve
            author = AuthorTools(live, place=PlaceTools(live))
            author._editor_id = lambda form_id: None
            out = author.export(name='t')
            spec = json.loads(Path(out['path']).read_text())
            self.assertEqual(spec['cells'], {})
            self.assertTrue(any('no pose' in p for p in out['problems']))


class HandsClickTests(unittest.TestCase):
    def test_menu_names_with_or_without_menu(self):
        self.assertEqual(menu_id_of('InventoryMenu'), 1002)
        self.assertEqual(menu_id_of('inventory'), 1002)
        self.assertEqual(menu_id_of(1002), 1002)

    def test_unknown_menu_name_is_an_error_not_the_top_menu(self):
        hands = HandsTools(SimpleNamespace(ready=lambda: None))
        hands._top_menu = lambda: 1002       # the old code fell back to this one
        with self.assertRaises(ValueError):
            hands.click(target='x', menu='NoSuchMenu')


class CameraSetTests(unittest.TestCase):
    def test_bad_fov_raises_before_the_camera_is_touched(self):
        calls = []

        class Cam:
            position = (0.0, 0.0, 0.0)

            def angles(self):
                return 0.0, 0.0

        live = SimpleNamespace(in_game=lambda: None, console=lambda *a, **k: calls.append(a),
                               link=SimpleNamespace(one=lambda *a, **k: calls.append(a)))
        eyes = EyesTools(live)
        eyes.camera = lambda: Cam()
        eyes._remember = lambda: calls.append('remember')
        eyes._fly = lambda *a, **k: calls.append('fly')
        with self.assertRaises(ValueError):
            eyes.set(position=[1, 2, 3], fov=500)
        self.assertEqual(calls, [])


class WorldTests(unittest.TestCase):
    def _world(self):
        live = SimpleNamespace(in_game=lambda: None, ready=lambda: None,
                               resolve=lambda r: (_ for _ in ()).throw(AssertionError('resolve must not run')))
        return fnv_world.WorldTools(SimpleNamespace(live=live))

    def test_world_edit_move_without_a_position_raises(self):
        world = self._world()
        for action in ('move', 'rotate', 'scale', 'transform'):
            with self.assertRaises(ValueError, msg=action):
                world.world_edit(action, ref='FF000001')
        with self.assertRaises(ValueError):
            world.world_edit('move', ref='FF000001', position=[1, 2])

    def test_window_unknown_action_raises_before_persist(self):
        world = self._world()
        world._persist_background_mouse = lambda on: self.fail('persist must not run for a bad action')
        with self.assertRaises(ValueError):
            world.window('release', persist=True)

    def test_hud_main_menu_names_resolve(self):
        self.assertEqual(fnv_world.MENU_IDS['hudmain'], 1004)
        self.assertIn('credits', fnv_world.MENU_IDS)

    def test_heap_growth_without_a_process_does_not_crash(self):
        world = self._world()
        sample = lambda: {'addressSpaceMB': None, 'counts': {'forms': 1}, 'pools': []}      # noqa: E731
        world._heap_sample = sample
        # scoped: fnv_world.time is the shared time module, so a bare assignment broke every later test that sleeps
        with mock.patch.object(fnv_world.time, 'sleep', lambda s: None):
            out = world.heap(seconds=1)
        self.assertIsNone(out['growth']['addressSpaceMB'])

    def test_ini_with_non_utf8_bytes_is_not_corrupted(self):
        with tempfile.TemporaryDirectory() as d:
            ini = Path(d) / 'Fallout.ini'
            ini.write_bytes(b'[General]\nsLanguage=ENGLISH\nsPath=C:\\Jos\xe9\n[Controls]\nbBackground Mouse=0\n')
            world = self._world()
            world._ini_path = lambda: ini
            world._layout = lambda: SimpleNamespace(ini=ini)
            real = fnv_world.fnv_layout if hasattr(fnv_world, 'fnv_layout') else None
            from geck_mcp import fnv_layout
            old = fnv_layout.guard
            fnv_layout.guard = lambda layout, what: None
            try:
                world._persist_background_mouse(True)
            finally:
                fnv_layout.guard = old
            data = ini.read_bytes()
            self.assertIn(b'Jos\xe9', data)
            self.assertIn(b'bBackground Mouse=1', data)
            del real


if __name__ == '__main__':
    unittest.main()
