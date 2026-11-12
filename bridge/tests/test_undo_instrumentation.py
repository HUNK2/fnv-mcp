"""PLAN-3 S3.6: everything installed in the game is journaled (N8)."""
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from geck_mcp import fnv_time  # noqa: E402
from geck_mcp.fnv_link import hexs, word  # noqa: E402


def install(link, address, label):
    """What every tool sends to put a hook in (trace, tracepoint, profile, debugger hold...)."""
    return link.one('hook', word(address), word(6), '-', '0', '0', '0', '0', '0', '0', '0', '0', '0', hexs(label))


class HookJournalTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live

    def instruments(self):
        return [e for e in self.live.journal.entries() if e['kind'] == 'instrument']

    def test_trace_start_journaled_and_revert_unhooks(self):
        install(self.live.link, 0x004E1090, 'trace:')
        install(self.live.link, 0x004E2000, 'trace:')          # a second hook of the same label: one entry
        [e] = self.instruments()
        self.assertEqual(e['label'], 'trace:')
        self.assertEqual(e['undo'], {'op': 'unhook', 'label': 'trace:'})
        out = self.live.revert_all()
        self.assertEqual(out['reverted'], 1)
        self.assertEqual(self.fake.link.hooks, {})

    def test_hook_removed_in_the_same_call_closes_its_entry(self):
        h = install(self.live.link, 0x004E1090, 'blame.1')['hook']
        self.live.link.one('unhook', word(h['id']))
        [e] = self.instruments()
        self.assertTrue(e['undone'], 'a hook its tool took out again leaves nothing for revert_all')
        self.assertEqual(self.live.revert_all()['reverted'], 0)

    def test_hook_set_and_native_hooks_are_not_journaled_twice(self):
        install(self.live.link, 0x004E1090, 'act:004E1090')
        install(self.live.link, 0x004E2000, 'mod:test:1')
        self.assertEqual(self.instruments(), [])

    def test_session_end_leaves_no_hooks(self):
        install(self.live.link, 0x004E3000, 'before the session')
        mark = self.live.journal.last_seq()                     # fnv_session start keeps this mark
        install(self.live.link, 0x004E1090, 'tp:004E1090')
        install(self.live.link, 0x004E2000, 'dbg 1')
        self.live.revert_all(mark)                              # what fnv_session end runs (fnv_session.py _end)
        self.assertEqual([h['label'] for h in self.fake.link.hooks.values()], ['before the session'])

    def test_hooks_remove_is_journaled(self):
        from geck_mcp.fnv_deep import DeepTools
        install(self.live.link, 0x004E1090, 'warnings_live')
        DeepTools(self.live).hooks_tool('remove', 'all')
        removed = [e for e in self.live.journal.entries() if e['kind'] == 'hook_removed']
        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]['labels'], ['warnings_live'])
        self.assertIsNone(removed[0]['undo'])
        self.assertTrue(self.instruments()[0]['undone'])


class TimeJournalTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live
        link = self.fake.link
        link.put(fnv_time.TIME_GLOBAL, struct.pack('<If', 0, 0.0))
        link.put(fnv_time.MULTIPLIER, struct.pack('<ff', 1.0, 1.0))
        from geck_mcp.fnv_inject import shared_time
        self.control, _ = shared_time(self.live)
        self.control.functions = lambda: {'disable': 0x00AA0000, 'enable': 0x00AA0010, 'clamp': 0x00AA0020}
        real = link._op

        def op(name, args):                  # the clock counter as the game keeps it
            if name == 'call' and int(args[0], 16) in (0x00AA0000, 0x00AA0010):
                counter = struct.unpack('<I', link.get(fnv_time.TIME_GLOBAL, 4))[0]
                counter += 1 if int(args[0], 16) == 0x00AA0000 else -1
                link.put(fnv_time.TIME_GLOBAL, struct.pack('<I', counter))
            return real(name, args)
        link._op = op

    def counter(self):
        return struct.unpack('<I', self.fake.link.get(fnv_time.TIME_GLOBAL, 4))[0]

    def test_time_pause_reverted(self):
        self.control.pause()
        self.assertEqual(self.counter(), 1)
        [e] = [e for e in self.live.journal.entries() if e['kind'] == 'instrument']
        self.assertEqual(e['undo'], {'op': 'time_restore', 'what': 'pause'})
        self.live.revert_all()
        self.assertEqual(self.counter(), 0)
        self.assertFalse(self.control.paused_by_us)

    def test_pause_then_resume_leaves_nothing_to_revert(self):
        self.control.pause()
        self.control.resume()
        self.assertEqual(self.live.revert_all()['reverted'], 0)
        self.assertEqual(self.counter(), 0)

    def test_speed_reverted_to_the_old_multiplier(self):
        self.control.speed(2.0)
        self.live.revert_all()
        self.assertEqual(struct.unpack('<ff', self.fake.link.get(fnv_time.MULTIPLIER, 8)), (1.0, 1.0))


class LeveledRollSeedTests(unittest.TestCase):
    def test_leveled_roll_seed_journaled_and_undone(self):
        from geck_mcp.fnv_agent import AgentTools
        fake = FakeLive()
        self.addCleanup(fake.close)
        live, link = fake.live, fake.link
        rng, buf = 0x00800000, 0x00C00000
        old = bytes(range(256)) * 9 + bytes(2496 - 256 * 9)
        link.put(rng, b'\0' * 8 + old)
        link.put(rng + 0x9C8, struct.pack('<I', 17))
        link.put(1, bytes(0x1C))                      # the leveled list (the fake call returns 1), no entries
        link.put(5, bytes(8))
        link.put(buf, bytes(12))
        live.in_game = lambda: None
        live.resolve = lambda spec: {'ptr': 0x6000, 'formId': '00006000'}
        agent = AgentTools(live, deep=None)
        agent._rng_instance = lambda: rng
        agent._buffer = lambda size=0x1000: buf
        agent.describe_pointers = lambda pointers: {}
        agent.leveled_roll('00006000', level=5, rolls=1, seed=9)
        [e] = [e for e in live.journal.entries() if e['kind'] == 'rng_seed']
        self.assertEqual(e['undo']['op'], 'writes')
        live.undo()
        self.assertEqual(link.get(rng + 8, 2496), old)
        self.assertEqual(link.get(rng + 0x9C8, 4), struct.pack('<I', 17))


if __name__ == '__main__':
    unittest.main()
