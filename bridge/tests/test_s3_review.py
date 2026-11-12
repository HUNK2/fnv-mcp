"""PLAN-3 S3 review fixes (batch S3.1-S3.3: I1-I4 and the unreadable-flag minor)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from geck_mcp import fnv_eyes, fnv_link  # noqa: E402
from geck_mcp.fnv_journal import Journal  # noqa: E402
from test_undo_stateful import OSG, Cam  # noqa: E402


class JournalFoldTests(unittest.TestCase):
    def test_i1_confirm_after_an_undo_reopens_the_entry(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / 'journal.jsonl', game=lambda: (1, '1'))
            e = j.add({'kind': 'memory', 'state': 'pending', 'undo': {'op': 'write', 'address': '0x1', 'hex': '00'}})
            j.mark_undone([e['id']])          # another server's revert ran before this change landed
            j.confirm(e['id'])                # ... then the change landed
            got = j.get(e['id'])
            self.assertFalse(got['undone'], 'the game holds the change: it is still to undo')
            self.assertEqual(got['state'], 'confirmed')

    def test_confirm_of_an_already_confirmed_entry_does_not_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / 'journal.jsonl', game=lambda: (1, '1'))
            e = j.add({'kind': 'memory', 'undo': None})
            j.mark_undone([e['id']])
            j.confirm(e['id'])
            self.assertTrue(j.get(e['id'])['undone'])


class FailedIntentTests(unittest.TestCase):
    def test_i2_unwritable_memory_closes_its_intent(self):
        with FakeLive() as fake:
            fake.link.put(0x00500000, b'\x01\x00\x00\x00')

            def refuse(address, data):
                raise fnv_link.LinkError('MEMORY_UNWRITABLE', 'read-only page')
            fake.link.write = refuse
            with self.assertRaises(fnv_link.LinkError):
                fake.live.mem_write('0x00500000', 7, as_type='u32', cls='Thing')
            [e] = fake.live.journal.entries()
            self.assertTrue(e['undone'], 'nothing landed: nothing is left to undo')
            out = fake.live.revert_all()
            self.assertEqual((out['reverted'], out['results']), (0, []))

    def test_i2_timeout_keeps_its_intent_open(self):
        with FakeLive() as fake:
            fake.link.put(0x00500000, b'\x01\x00\x00\x00')

            def timeout(address, data):
                raise fnv_link.LinkError('COMPANION_TIMEOUT', 'no reply')
            fake.link.write = timeout
            with self.assertRaises(fnv_link.LinkError):
                fake.live.mem_write('0x00500000', 7, as_type='u32', cls='Thing')
            self.assertFalse(fake.live.journal.entries()[0]['undone'])


class CameraHudTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live
        link = self.fake.link
        link.put(fnv_eyes.OS_GLOBALS, OSG.to_bytes(4, 'little'))
        link.put(OSG + fnv_eyes.FLY_FLAG, b'\x00')
        link.put(fnv_eyes.MENUS_SHOWN_FLAG, b'\x01')
        self.lines = []
        real = self.live.console

        def console(line, ref=0):
            self.lines.append(line)
            if line == 'ToggleMenus':
                link.put(fnv_eyes.MENUS_SHOWN_FLAG, b'\x00' if link.get(fnv_eyes.MENUS_SHOWN_FLAG, 1)[0] else b'\x01')
            return real(line, ref)
        self.live.console = console

    def test_i3_camera_undo_puts_the_fov_back(self):
        eyes = fnv_eyes.EyesTools(self.live, sleep=lambda s: None)
        eyes.camera = lambda: Cam()
        eyes._remember()
        self.live.revert_all()
        self.assertIn('fov 75.0', self.lines)

    def test_i3_menus_toggle_undo_shows_the_hud_again(self):
        from geck_mcp.fnv_render import RenderTools
        self.live.in_game = lambda: None
        out = RenderTools(self.live).toggle('menus')
        self.assertEqual(self.fake.link.get(fnv_eyes.MENUS_SHOWN_FLAG, 1), b'\x00')
        self.assertFalse(out['on'], 'N20: the menus are off now ("Menus -> Off" in the game)')
        [e] = [e for e in self.live.journal.entries() if e['kind'] == 'render_toggle']
        self.assertEqual(e['undo'], {'op': 'hud_set', 'on': True})

    def test_unreadable_fly_flag_is_not_an_undo(self):
        from geck_mcp.fnv_live import UNDO_OPS
        self.fake.link.put(fnv_eyes.OS_GLOBALS, b'\x00\x00\x00\x00')     # OSGlobals not there (yet)
        r = UNDO_OPS['fly_set'](self.live, {}, {'op': 'fly_set', 'on': False})
        self.assertFalse(r['undone'])


class PendingPlaceTests(unittest.TestCase):
    def test_i4_pending_place_listed_with_the_live_pose(self):
        from test_fnv_create import FakeLive as CreateLive
        from geck_mcp.fnv_create import PlaceTools
        with tempfile.TemporaryDirectory() as d:
            live = CreateLive(d)
            place = PlaceTools(live)
            real = place._set
            calls = []

            def flaky(*a, **k):
                calls.append(1)
                real(*a, **k)
                raise fnv_link.LinkError('COMPANION_TIMEOUT', 'no reply after the lines ran')
            place._set = flaky
            with self.assertRaises(fnv_link.LinkError):
                place.at('0003404A', position=[10, 20, 30])
            [row] = place.list()['placed']
            self.assertEqual(row['position'], [10, 20, 30])


if __name__ == '__main__':
    unittest.main()
