"""PLAN-3 S3.4: records derived from a change follow its undo (G1, N4, N5, N6, N7, swap order)."""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402

RNG = 0x00800000


class PoseTests(unittest.TestCase):
    def test_nudge_undo_then_list_shows_game_pose(self):
        from test_fnv_create import FakeLive as CreateLive
        from fnv_mcp.fnv_create import PlaceTools
        with tempfile.TemporaryDirectory() as d:
            live = CreateLive(d)
            place = PlaceTools(live)
            ref = place.at('0003404A', position=[10, 20, 30])['ref']
            adjust = place.adjust(ref, move=[5, 0, 0])
            self.assertEqual(place.list()['placed'][0]['position'], [15, 20, 30])
            live.journal.mark_undone([adjust['journalId']])          # fnv_undo ran its console lines
            self.assertEqual(place.list()['placed'][0]['position'], [10, 20, 30], 'G1: the pose the game shows')

    def test_pose_stored_unrounded_list_rounds(self):
        from test_fnv_create import FakeLive as CreateLive
        from fnv_mcp.fnv_create import PlaceTools
        with tempfile.TemporaryDirectory() as d:
            live = CreateLive(d)
            place = PlaceTools(live)
            out = place.at('0003404A', position=[10.123, 20, 30])
            self.assertEqual(live.journal.get(out['journalId'])['position'][0], 10.123)
            self.assertEqual(place.list()['placed'][0]['position'][0], 10.12)


class FormDeleteTests(unittest.TestCase):
    def test_form_delete_hides_create_from_export(self):
        from test_fnv_create import FakeLive as CreateLive
        from fnv_mcp.fnv_create import AuthorTools, FormTools
        with tempfile.TemporaryDirectory() as d:
            live = CreateLive(d)
            create = live.journal.add({'kind': 'form_create', 'formId': 'FF000801', 'type': 'MISC', 'undo': None})
            forms = FormTools(live)
            forms.created.append({'formId': 'FF000801'})
            live.resolve = lambda ref: {'ptr': 0x6000, 'formId': 'FF000801'}
            live.mem[0x6008] = 0
            live.link.write = lambda address, data: b'\0\0\0\0'
            forms.delete('FF000801')
            deleted = [e for e in live.journal.entries() if e['kind'] == 'form_delete'][0]
            self.assertEqual(deleted['supersedes'], create['id'])
            author = AuthorTools(live, place=None)
            spec = json.loads(Path(author.export(name='t')['path']).read_text())
            self.assertEqual(spec['records'], {})


class ExecutorDerivedTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live
        self.sent = []
        real = self.fake.link._op

        def op(name, args):
            if name in ('probe', 'dbg'):
                self.sent.append([name] + args)
                if args[:1] == ['info'] and name == 'probe':
                    return {'ok': True, 'armed': 3}
                if args[:1] == ['info'] and name == 'dbg':
                    return {'ok': True, 'active': 0b0101, 'bpSlots': 0,
                            'slots': [{'address': '0x00001000', 'on': True}, {'address': '0x0', 'on': False},
                                      {'address': '0x00002000', 'on': True}, {'address': '0x0', 'on': False}]}
                return {'ok': True}
            return real(name, args)
        self.fake.link._op = op

    def test_probe_undo_removes_stubs(self):
        from fnv_mcp.fnv_probe import ProbeTools
        probes = ProbeTools(self.live)
        probes._save('probe_session.json', {'armed': True, 'pid': 4242})
        e = self.live.journal.add({'kind': 'probe', 'undo': {'op': 'probe_remove'}})
        self.assertTrue(self.live.undo(journal_id=e['id'])['results'][0]['undone'])
        self.assertIn(['probe', 'disarm'], self.sent)
        self.assertFalse(probes._load('probe_session.json', None)['armed'])

    def test_dbg_clear_leaves_other_slots(self):
        self.live.journal.add({'kind': 'watchpoint', 'undo': {'op': 'dbg_clear', 'slot': 2, 'address': '0x00002000'}})
        self.live.undo()
        clears = [s for s in self.sent if s[1:2] == ['clear']]
        self.assertEqual(clears, [['dbg', 'clear', '2']])

    def test_dbg_clear_skips_a_reused_slot(self):
        self.live.journal.add({'kind': 'watchpoint', 'undo': {'op': 'dbg_clear', 'slot': 2, 'address': '0x00003000'}})
        r = self.live.undo()['results'][0]
        self.assertTrue(r['undone'])
        self.assertEqual([s for s in self.sent if s[1:2] == ['clear']], [], 'another watch owns slot 2 now')

    def test_legacy_dbg_clear_never_clears_every_register(self):
        self.live.journal.add({'kind': 'watchpoint', 'undo': {'op': 'dbg_clear'}})
        self.live.undo()
        self.assertNotIn(['dbg', 'clear', 'f'], self.sent)

    def test_rng_undo_restores_index(self):
        from fnv_mcp.fnv_agent import AgentTools
        link = self.fake.link
        old_state = bytes(range(256)) * 9 + bytes(2496 - 256 * 9)
        link.put(RNG, b'\0' * 8 + old_state)
        link.put(RNG + 0x9C8, struct.pack('<I', 17))
        agent = AgentTools(self.live, deep=None)
        agent._rng_instance = lambda: RNG
        agent.rng(seed=5)
        self.assertEqual(link.get(RNG + 0x9C8, 4), struct.pack('<I', 0x270))
        self.live.undo()
        self.assertEqual(link.get(RNG + 8, 2496), old_state)
        self.assertEqual(link.get(RNG + 0x9C8, 4), struct.pack('<I', 17))

    def test_swap_undo_out_of_order_refused(self):
        from fnv_mcp import fnv_data
        swaps = []
        self.addCleanup(setattr, fnv_data, 'swap_script_code', fnv_data.swap_script_code)
        fnv_data.swap_script_code = lambda live, target, holder: swaps.append((target, holder))
        first = self.live.journal.add({'kind': 'script_hot_swap', 'target': '0x1000', 'holder': '0x2000',
                                       'undo': {'op': 'swap_script', 'target': '0x1000', 'holder': '0x2000'}})
        self.live.journal.add({'kind': 'script_hot_swap', 'target': '0x1000', 'holder': '0x3000',
                               'undo': {'op': 'swap_script', 'target': '0x1000', 'holder': '0x3000'}})
        r = self.live.undo(journal_id=first['id'])['results'][0]
        self.assertFalse(r['undone'])
        self.assertEqual(r['reason'], 'undo the later swap first')
        self.assertEqual(swaps, [])
        out = self.live.revert_all()                  # newest first: both run, in order
        self.assertEqual(out['reverted'], 2)
        self.assertEqual(swaps, [(0x1000, 0x3000), (0x1000, 0x2000)])


if __name__ == '__main__':
    unittest.main()
