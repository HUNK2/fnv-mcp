"""PLAN-3 S3 review B fixes (batch S3.4-S3.6: I1-I5, M1, M8)."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from geck_mcp import fnv_time  # noqa: E402
from geck_mcp.fnv_live import LiveError  # noqa: E402
from test_undo_instrumentation import install  # noqa: E402


class HookOwnerTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live

    def test_i1_journal_add_runs_outside_the_hook_lock(self):
        held = []
        real = self.live.journal.add

        def add(entry, *a):
            held.append(self.live._hook_lock.locked())
            return real(entry, *a)
        self.live.journal.add = add
        install(self.live.link, 0x004E1090, 'trace:')
        self.assertEqual(held, [False], 'an undo holding the journal lock would wait on the hook lock (I1)')

    def test_i2_i3_owner_managed_hooks_are_left_to_their_tools(self):
        install(self.live.link, 0x004E1090, 'dbg 1')            # a debugger hold: a thread may be parked in it
        install(self.live.link, 0x004E2000, 'warnings_live')    # the session's warnings capture
        self.assertEqual([e for e in self.live.journal.entries() if e['kind'] == 'instrument'], [])
        self.live.revert_all()
        self.assertEqual(len(self.fake.link.hooks), 2)

    def test_i3_profile_and_crash_context_forget_hooks_revert_removed(self):
        from geck_mcp.fnv_deep import PROFILE, DeepTools
        deep = DeepTools(self.live)
        deep._baseline.update(profile={'frame': 1, 'time': 0}, crash_context=True)
        for k, label in enumerate(PROFILE):
            install(self.live.link, 0x005AC1E0 + 0x40 * k, label)
        self.live.revert_all()
        self.assertNotIn('profile', deep._baseline)
        self.assertFalse(deep._crash_context_on())


class OrderTests(unittest.TestCase):
    def test_i4_older_fixed_step_undo_refused(self):
        with FakeLive() as fake:
            live = fake.live
            fake.link.put(fnv_time.TIME_GLOBAL, struct.pack('<If', 0, 0.0))
            from geck_mcp.fnv_inject import shared_time
            control, _ = shared_time(live)
            control.functions = lambda: {'disable': 0x00AA0000, 'enable': 0x00AA0010, 'clamp': 0x00AA0020}
            control.fixed(30)
            control.fixed(60)
            first = [e for e in live.journal.entries() if e['kind'] == 'instrument'][0]
            r = live.undo(journal_id=first['id'])['results'][0]
            self.assertFalse(r['undone'])
            self.assertEqual(r['reason'], 'undo the later change first')

    def test_i5_older_adjust_undo_refused(self):
        with FakeLive() as fake:
            live = fake.live
            undo = {'op': 'console_ref', 'ref': 'FF000900', 'lines': ['SetPos X 0']}
            a1 = live.journal.add({'kind': 'place_adjust', 'ref': 'FF000900', 'undo': undo})
            live.journal.add({'kind': 'place_adjust', 'ref': 'FF000900', 'undo': undo})
            live.journal.add({'kind': 'place_adjust', 'ref': 'FF000901', 'undo': dict(undo, ref='FF000901')})
            r = live.undo(journal_id=a1['id'])['results'][0]
            self.assertFalse(r['undone'])
            self.assertEqual(r['reason'], 'undo the later change first')


class SessionEndTests(unittest.TestCase):
    def test_m1_session_end_turns_the_fixed_step_off_unjournaled(self):
        import inspect
        from geck_mcp import fnv_session
        src = inspect.getsource(fnv_session)
        self.assertNotIn('control.fixed(0)', src, 'session start/end turn the step off without a new entry')


class SpawnProjectileTests(unittest.TestCase):
    def test_m8_projectile_ref_refused(self):
        from geck_mcp.fnv_agent import AgentTools
        with FakeLive() as fake:
            live = fake.live
            live.in_game = lambda: None
            fake.link.put(0x5000 + 4, struct.pack('<4sII', bytes([62, 0, 0, 0]), 0, 0xFF000A00))   # a projectile ref
            fake.link.put(0x5000 + 0x20, struct.pack('<I', 0x6000))                               # its PROJ base
            fake.link.put(0x6000 + 4, struct.pack('<4sII', bytes([51, 0, 0, 0]), 0, 0x00001234))
            live.resolve = lambda spec: {'ptr': 0x5000, 'formId': 'FF000A00', 'expr': 'GetCrosshairRef'}
            with self.assertRaises(LiveError) as caught:
                AgentTools(live, deep=None).spawn('crosshair')
            self.assertEqual(caught.exception.code, 'BAD_FORM')


if __name__ == '__main__':
    unittest.main()
