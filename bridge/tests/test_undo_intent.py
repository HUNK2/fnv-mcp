"""PLAN-3 S3.2: journal the intent before the game changes, confirm after (D5, N10, G3)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from geck_mcp import fnv_link  # noqa: E402
from geck_mcp.fnv_journal import Journal  # noqa: E402

ADDRESS = 0x00500000


class JournalStateTests(unittest.TestCase):
    def test_pending_until_confirmed_and_after_merges(self):
        with tempfile.TemporaryDirectory() as d:
            j = Journal(Path(d) / 'journal.jsonl', game=lambda: (1, '1'))
            e = j.add({'kind': 'memory', 'state': 'pending', 'old': 'aa'})
            plain = j.add({'kind': 'console', 'undo': None})
            self.assertEqual(j.get(e['id'])['state'], 'pending')
            self.assertEqual(j.get(plain['id'])['state'], 'confirmed')
            j.confirm(e['id'], {'old': 'bb', 'id': 'nope', 'seq': 99})
            got = j.get(e['id'])
            self.assertEqual((got['state'], got['old'], got['id'], got['seq']), ('confirmed', 'bb', e['id'], e['seq']))
            j.confirm(plain['id'])
            self.assertEqual(Journal(Path(d) / 'journal.jsonl').get(plain['id'])['state'], 'confirmed')


class IntentTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live
        self.fake.link.put(ADDRESS, b'\x01\x00\x00\x00')

    def test_mem_write_timeout_leaves_pending_entry(self):
        real = self.fake.link.write

        def timeout(address, data):
            raise fnv_link.LinkError('COMPANION_TIMEOUT', 'no reply')
        self.fake.link.write = timeout
        with self.assertRaises(fnv_link.LinkError):
            self.live.mem_write('0x%08X' % ADDRESS, 7, as_type='u32', cls='Thing')
        [e] = self.live.journal.entries()
        self.assertEqual((e['kind'], e['state']), ('memory', 'pending'))
        self.assertEqual(e['undo'], {'op': 'write', 'address': '0x%08X' % ADDRESS, 'hex': '01000000'})
        self.fake.link.write = real
        self.fake.link.put(ADDRESS, b'\x07\x00\x00\x00')          # the write did land after all
        self.assertTrue(self.live.undo()['results'][0]['undone'])  # a write is repeatable: it ran
        self.assertEqual(self.fake.link.get(ADDRESS, 4), b'\x01\x00\x00\x00')

    def test_mem_write_confirmed_with_the_old_bytes_it_replaced(self):
        out = self.live.mem_write('0x%08X' % ADDRESS, 7, as_type='u32', cls='Thing')
        e = self.live.journal.get(out['journalId'])
        self.assertEqual(e['state'], 'confirmed')
        self.assertEqual(e['undo']['hex'], '01000000')

    def test_pending_non_repeatable_reports_unknown(self):
        e = self.live.journal.add({'kind': 'script_hot_swap', 'state': 'pending',
                                   'undo': {'op': 'swap_script', 'target': '0x1000', 'holder': '0x2000'}})
        r = self.live.undo()['results'][0]
        self.assertFalse(r['undone'])
        self.assertEqual(r['reason'], 'outcome unknown: the change may not have happened')
        self.assertFalse(self.live.journal.get(e['id'])['undone'])

    def test_confirmed_non_repeatable_still_runs(self):
        from geck_mcp import fnv_live
        ran = []
        self.addCleanup(fnv_live.UNDO_OPS.__setitem__, 'swap_script', fnv_live.UNDO_OPS['swap_script'])
        fnv_live.UNDO_OPS['swap_script'] = lambda live, entry, undo: ran.append(undo)
        self.live.journal.add({'kind': 'script_hot_swap',
                               'undo': {'op': 'swap_script', 'target': '0x1000', 'holder': '0x2000'}})
        self.assertTrue(self.live.undo()['results'][0]['undone'])
        self.assertEqual(len(ran), 1)

    def test_repeatable_flags(self):
        from geck_mcp.fnv_live import UNDO_OPS
        self.assertTrue(UNDO_OPS['write'].repeatable)
        self.assertTrue(UNDO_OPS['console_ref'].repeatable)
        self.assertTrue(UNDO_OPS['unhook'].repeatable)
        self.assertFalse(UNDO_OPS['swap_script'].repeatable)


class PlaceIntentTests(unittest.TestCase):
    def test_place_journaled_before_setpos(self):
        from test_fnv_create import FakeLive as CreateLive
        from geck_mcp.fnv_create import PlaceTools
        with tempfile.TemporaryDirectory() as d:
            live = CreateLive(d)
            place = PlaceTools(live)

            def broken(*a, **k):
                raise fnv_link.LinkError('COMPANION_TIMEOUT', 'no reply')
            place._set = broken
            with self.assertRaises(fnv_link.LinkError):
                place.at('0003404A', position=[10, 20, 30])
            [e] = live.journal.entries()
            self.assertEqual((e['kind'], e['state']), ('place', 'pending'))
            self.assertTrue(e['ref'].startswith('FF'))
            self.assertEqual(e['undo'], {'op': 'console_ref', 'ref': e['ref'], 'lines': ['Disable', 'MarkForDelete']})

    def test_place_confirmed_with_its_pose(self):
        from test_fnv_create import FakeLive as CreateLive
        from geck_mcp.fnv_create import PlaceTools
        with tempfile.TemporaryDirectory() as d:
            live = CreateLive(d)
            out = PlaceTools(live).at('0003404A', position=[10, 20, 30])
            e = live.journal.get(out['journalId'])
            self.assertEqual(e['state'], 'confirmed')
            self.assertEqual(e['position'], [10, 20, 30])
            self.assertEqual(e['cell'], '00001234')


class WorldEditIntentTests(unittest.TestCase):
    def _agent(self, d, flags=0):
        from test_bugfix_d import WorldAgent
        agent = WorldAgent(flags=flags)
        agent.live.journal = Journal(Path(d) / 'journal.jsonl', game=lambda: (1, '1'))
        agent.live.u32s = lambda addrs: [0x1] * len(addrs)       # the player pointer is not this ref
        return agent

    def test_world_edit_delete_is_journaled(self):
        from geck_mcp.fnv_world import WorldTools
        with tempfile.TemporaryDirectory() as d:
            agent = self._agent(d)
            out = WorldTools(agent).world_edit('delete', ref='00000ABC')
            [e] = agent.live.journal.entries()
            self.assertEqual((e['kind'], e['action'], e['ref'], e['state']), ('world_edit', 'delete', '00000ABC', 'confirmed'))
            self.assertIsNone(e['undo'])
            self.assertEqual(out['journalId'], e['id'])

    def test_world_edit_move_journaled_before_the_first_axis(self):
        from geck_mcp.fnv_world import WorldTools
        with tempfile.TemporaryDirectory() as d:
            agent = self._agent(d)
            tools = WorldTools(agent)
            seen = []
            real = tools._call

            def call(*args):
                seen.append([e['state'] for e in agent.live.journal.entries()])
                if len(seen) == 2:
                    raise fnv_link.LinkError('COMPANION_TIMEOUT', 'no reply')
                return real(*args)
            tools._call = call
            with self.assertRaises(fnv_link.LinkError):
                tools.world_edit('move', ref='00000ABC', position=[1, 2, 3])
            self.assertEqual(seen[0], ['pending'])
            [e] = agent.live.journal.entries()
            self.assertEqual(e['state'], 'pending')
            self.assertEqual(e['undo']['lines'][:1], ['SetPos X 0.000000'])


if __name__ == '__main__':
    unittest.main()
