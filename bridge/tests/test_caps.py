"""PLAN-3 S5.3: replies that could run to thousands of rows are capped and say so (G4 cap, G10)."""
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive, wire  # noqa: E402


class CappedTests(unittest.TestCase):
    def test_capped(self):
        from geck_mcp.fnv_args import capped
        self.assertEqual(capped(range(5000)), {'items': list(range(200)), 'total': 5000, 'truncated': True})
        self.assertEqual(capped(range(5000), newest_first=True)['items'], list(range(4800, 5000)))
        self.assertEqual(capped([1, 2]), {'items': [1, 2], 'total': 2, 'truncated': False})


class ToolCapTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live

    def test_revert_all_results_capped(self):
        # 250 journal entries (each add is a locked file append): enough to pass the cap of 200
        self.fake.link.put(0x01000000, b'\x00')
        for _ in range(250):
            self.live.journal.add({'kind': 'memory', 'undo': {'op': 'write', 'address': '0x01000000', 'hex': '00'}})
        r = self.live.revert_all()
        self.assertEqual(r['reverted'], 250)
        self.assertEqual(len(r['results']), 200)
        self.assertEqual(r['total'], 250)
        self.assertTrue(r['truncated'])

    def test_hooks_list_capped(self):
        from geck_mcp.fnv_deep import DeepTools
        for k in range(5000):
            hook = wire('hook')['hook']
            hook.update(id=k + 1, target='0x%08x' % (0x00400000 + 16 * k), label='trace:%d' % k)
            self.fake.link.hooks[k + 1] = hook
        self.live._engine = SimpleNamespace(summary=lambda a: {'name': 'F'})
        r = DeepTools(self.live).hooks_tool('list')
        self.assertEqual(len(r['hooks']), 200)
        self.assertEqual(r['total'], 5000)
        self.assertTrue(r['truncated'])

    def test_events_capped_oldest_first(self):
        self.fake.link.events = lambda since=0: {'ok': True, 'seq': 5000, 'lost': False, 'events': [
            {'seq': k + 1, 'tickMs': k, 'type': 'SaveGame', 'text': ''} for k in range(5000)]}
        r = self.live.events(0)
        self.assertEqual(len(r['events']), 200)
        self.assertEqual(r['events'][-1]['seq'], 200)
        self.assertEqual(r['total'], 5000)
        self.assertTrue(r['truncated'])
        self.assertEqual(r['next'], 200, 'call again with since=next for the rest')

    def test_place_list_capped(self):
        from geck_mcp.fnv_create import PlaceTools
        for k in range(250):
            self.live.journal.add({'kind': 'place', 'ref': 'FF%06X' % k, 'base': '00000014', 'position': [0, 0, 0],
                                   'rotation': [0, 0, 0], 'scale': 1.0, 'undo': None})
        tools = PlaceTools.__new__(PlaceTools)
        tools.live = self.live
        r = tools.run('list')
        self.assertEqual(len(r['placed']), 200)
        self.assertEqual(r['total'], 250)
        self.assertTrue(r['truncated'])
        self.assertEqual(len(tools.list(exact=True)['placed']), 250, 'export still sees every placed ref')

    def test_console_output_capped(self):
        self.live.console = lambda line, ref=0: {'line': line, 'accepted': True, 'compileError': False,
                                                 'output': ['row %d' % k for k in range(5000)], 'scriptErrors': 0,
                                                 'outputCaptured': True, 'frame': 1}
        r = self.live.run_console('help')
        self.assertEqual(len(r['output']), 400)
        self.assertEqual(r['outputTotal'], 5000)
        self.assertTrue(r['outputTruncated'])

    def test_watch_timeline_capped(self):
        from geck_mcp.fnv_agent import AgentTools
        agent = AgentTools(self.live, deep=None)
        agent._watch_items = lambda items: [{'kind': 'expr', 'label': 'x', 'op': ('eval', '00', '0')}]
        ticks = iter(range(10 ** 6))
        agent.link.batch = lambda ops, timeout=None: [{'ok': True, 'frame': 1, 'result': {'type': 'number',
                                                                                        'value': next(ticks)}}]
        self.live.game.keep_unpaused = lambda s: None
        self.live.game.stop_task = lambda slot: None
        r = agent.watch(['x'], seconds=60, interval_ms=0, max_samples=600)
        item = r['items'][0]
        self.assertEqual(len(item['timeline']), 500)
        self.assertEqual(item['timelineTotal'], 600)
        self.assertTrue(item['timelineTruncated'])


class GlobalsTests(unittest.TestCase):
    def test_globals_set_lists_only_changed_with_new_value(self):
        from geck_mcp import fnv_world
        from geck_mcp.fnv_world import WorldTools
        with FakeLive() as fake:
            live = fake.live
            dh, node, names = 0x02000000, 0x02100000, ['GameHour', 'GameDay', 'TimeScale']
            fake.link.put(fnv_world.DATA_HANDLER, struct.pack('<I', dh))
            ptrs = [0x02200000 + 0x100 * k for k in range(3)]
            live.walk_list = lambda head, limit=500: ptrs
            for k, ptr in enumerate(ptrs):
                raw = bytearray(0x28)
                struct.pack_into('<I', raw, 0x0C, 0x38 + k)
                raw[0x20] = ord('s')
                struct.pack_into('<f', raw, 0x24, 13.0)
                fake.link.put(ptr, bytes(raw))
            world = WorldTools(SimpleNamespace(live=live, link=live.link))
            world._texts = lambda pointers, n: names
            r = world.globals(set_values={'GameDay': 9})
            self.assertEqual([g['editorId'] for g in r['globals']], ['GameDay'])
            self.assertEqual(r['globals'][0]['value'], 9, 'the value read after the write')
            self.assertEqual(r['count'], 1)
            listed = world.globals(query='Game', set_values={'GameHour': 7})
            self.assertEqual({g['editorId']: g['value'] for g in listed['globals']}, {'GameHour': 7, 'GameDay': 9})


if __name__ == '__main__':
    unittest.main()
