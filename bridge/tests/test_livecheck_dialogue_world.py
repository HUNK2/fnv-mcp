"""Live-check fixes (2026-11-18): dialogue choice numbers, goodbye flag, negated speaker conditions, scene graph depth cut
and rotation, brief scene/threads/hang, flee pathing goal, quest targets reason, globals did-you-mean."""
from pathlib import Path
from types import SimpleNamespace
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_brief  # noqa: E402
from geck_mcp.fnv_social import SocialTools  # noqa: E402
from geck_mcp.fnv_world import (DATA_HANDLER, DIALOG_INFO, DIALOG_MENU, TOPIC_MANAGER, WorldTools,  # noqa: E402
                                _transform)


class MemLive:
    """A tiny live: u32 cells and byte blobs by address."""

    def __init__(self):
        self.mem = {}
        self.lists = {}

    def ready(self):
        pass

    def in_game(self):
        pass

    def u32s(self, addrs):
        return [self.mem.get(a, 0) for a in addrs]

    def reads(self, spec):
        return [self.mem.get(a) if isinstance(self.mem.get(a), (bytes, type(None))) else None for a, _ in spec]

    def walk_list(self, head, limit=500):
        return self.lists.get(head, [])


class DialogueNowTests(unittest.TestCase):
    def test_choice_index_counts_from_zero_like_dialogue_choose_and_new_replaces_alreadyheard(self):
        live = MemLive()
        menu = 0x1000
        live.mem[DIALOG_MENU] = menu
        live.mem[DIALOG_INFO] = 0
        live.mem[TOPIC_MANAGER] = 0x2000
        live.mem[menu] = bytes(0x13C)
        live.mem[0x2000] = struct.pack('<II', 0, 0x3000) + bytes(0x10)
        live.lists[0x3000] = [0x4000, 0x4100, 0x4200]
        spoken, c1, c2 = (bytearray(0x2C) for _ in range(3))
        c1[0x25] = 1       # the byte fnv_dialogue_choose calls "new"
        for addr, raw in ((0x4000, spoken), (0x4100, c1), (0x4200, c2)):
            live.mem[addr] = bytes(raw)
        w = object.__new__(WorldTools)
        w.agent = SimpleNamespace(live=live, _tile_strings=lambda ptrs: {})
        w._describe = lambda ptrs: {p: None for p in ptrs}
        w._texts = lambda ptrs, limit=400: ['spoken', 'first', 'second'][:len(ptrs)]
        out = w.dialogue()
        self.assertEqual([c['index'] for c in out['choices']], [0, 1])
        self.assertEqual([c['prompt'] for c in out['choices']], ['first', 'second'])
        self.assertEqual(out['spokenTopic']['prompt'], 'spoken')
        self.assertIsNone(out['spokenTopic']['index'])
        self.assertTrue(out['choices'][0]['new'])
        self.assertFalse(out['choices'][1]['new'])
        self.assertNotIn('alreadyHeard', out['choices'][0])


class SceneTests(unittest.TestCase):
    def node_raw(self, children_ptr=0, size=0):
        raw = bytearray(0xC0)
        struct.pack_into('<13f', raw, 0x34, 1, 0, 0, 0, 1, 0, 0, 0, 1, 5, 6, 7, 2)
        struct.pack_into('<13f', raw, 0x68, 0, -1, 0, 1, 0, 0, 0, 0, 1, 8, 9, 10, 1)
        struct.pack_into('<IHH', raw, 0xA0, children_ptr, size, size)
        return bytes(raw)

    def test_transform_has_a_rotation_matrix(self):
        t = _transform((1, 0, 0, 0, 1, 0, 0, 0, 1, 5, 6, 7, 2))
        self.assertEqual(t['rotationMatrix3x3'], [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
        self.assertEqual(t['translate'], [5, 6, 7])
        self.assertEqual(t['scale'], 2)

    def run_graph(self, depth):
        live = MemLive()
        root = 0x5000
        live.mem[0x5000] = self.node_raw(0x6000, 2)
        live.mem[0x6000] = struct.pack('<II', 0x5100, 0x5200)
        live.mem[0x5100] = self.node_raw(0x6100, 3)       # a node with 3 children
        live.mem[0x5200] = self.node_raw()
        live.mem[0x6100] = struct.pack('<III', 0x5300, 0x5400, 0x5500)
        live.reads = lambda spec: [live.mem.get(a) if a in live.mem and a in (0x5000, 0x5100, 0x5200)
                                   else (live.mem.get(a)[:n] if live.mem.get(a) else None) for a, n in spec]
        live.resolve = lambda ref: {'ptr': 0x100, 'formId': '00000014'}
        live.mem[0x100 + 0x64] = 0x200
        live.mem[0x200 + 0x14] = root
        w = object.__new__(WorldTools)
        w.agent = SimpleNamespace(live=live)
        w._eax = lambda *a: 0
        w._texts = lambda ptrs, limit=400: ['n%d' % i for i, _ in enumerate(ptrs)] if ptrs and ptrs[0] != 0 else \
            [None for _ in ptrs]
        w._describe = lambda ptrs: {p: None for p in ptrs}
        w._classes = lambda ptrs: {p: 'NiNode' for p in ptrs}
        w._geometry_details = w._controller_details = w._extra_details = lambda nodes: None
        return w.scene_graph(depth=depth)

    def test_children_past_128_are_reported(self):
        # bug hunt 2026-11-18: only 128 children of a node are read, and the answer did not say so
        live = MemLive()
        live.mem[0x5000] = self.node_raw(0x6000, 200)
        live.mem[0x6000] = b'\0' * 800
        live.reads = lambda spec: [(live.mem.get(a) or b'')[:n] or None for a, n in spec]
        live.resolve = lambda ref: {'ptr': 0x100, 'formId': '00000014'}
        live.mem[0x100 + 0x64] = 0x200
        live.mem[0x200 + 0x14] = 0x5000
        w = object.__new__(WorldTools)
        w.agent = SimpleNamespace(live=live)
        w._eax = lambda *a: 0
        w._texts = lambda ptrs, limit=400: [None for _ in ptrs]
        w._describe = lambda ptrs: {p: None for p in ptrs}
        w._classes = lambda ptrs: {p: 'NiNode' for p in ptrs}
        w._geometry_details = w._controller_details = w._extra_details = lambda nodes: None
        out = w.scene_graph(depth=3)
        self.assertTrue(out['capped'])
        self.assertEqual(out['wideCut'], 72)
        self.assertIn('past the first 128', out['note'])

    def test_depth_cut_is_reported(self):
        out = self.run_graph(depth=1)
        self.assertTrue(out['capped'])
        self.assertEqual(out['depthCut'], 3)
        self.assertIn('depth', out['note'])
        self.assertIn('rotationMatrix3x3', out['nodes'][0]['local'])
        self.assertIn('rotationMatrix3x3', out['nodes'][0]['world'])

    def test_no_cut_when_depth_is_enough(self):
        out = self.run_graph(depth=3)
        self.assertEqual(out['depthCut'], 0)
        self.assertFalse(out['capped'])


class BriefTests(unittest.TestCase):
    def test_scene_brief_counts_sequences_and_collapses_whitespace(self):
        seqs = [{'name': 'a%d.kf' % i, 'active': i == 2} for i in range(32)]
        answer = {'nodes': [{'node': '0x1', 'class': 'NiNode', 'name': 'r', 'parent': None,
                             'controllers': [{'sequences': seqs}],
                             'extraData': [{'name': 'UPB', 'value': 'a=1\r\nb=2\r\n  c=3'}]}]}
        line = fnv_brief.scene(answer)['tree'][0]
        self.assertIn('32 sequences, active: a2.kf', line)
        self.assertNotIn('a5.kf', line)
        self.assertIn('UPB=a=1 b=2 c=3', line)
        self.assertNotIn('\r', line)

    def test_clean_name(self):
        self.assertEqual(fnv_brief.clean_name('E7CCD0Renderer::PresentFrame'), 'Renderer::PresentFrame')
        self.assertEqual(fnv_brief.clean_name('Renderer::PresentFrame'), 'Renderer::PresentFrame')
        self.assertEqual(fnv_brief.clean_name('0x00E7CCD0Renderer::PresentFrame', None), '0x00E7CCD0Renderer::PresentFrame')
        self.assertEqual(fnv_brief.clean_name('00E7CCD0Renderer::PresentFrame', '0x00E7CCD0'), 'Renderer::PresentFrame')
        self.assertEqual(fnv_brief.clean_name('ABCDEFoo::Bar'), 'ABCDEFoo::Bar')

    def test_threads_brief_groups_driver_threads_and_fixes_locks(self):
        rows = [{'threadId': 1, 'main': True, 'start': 'WinMain', 'gameFunction': 'E7CCD0Renderer::PresentFrame',
                 'now': 'ntdll.dll!0x10'}]
        rows += [{'threadId': 10 + i, 'main': False, 'start': 'nvd3dum.dll!0x%X' % i, 'gameFunction': None}
                 for i in range(5)]
        rows += [{'threadId': 50, 'main': False, 'start': 'nvwgf2um.dll!0x9', 'gameFunction': None}]
        locks = [{'lock': 'script delayed actions', 'ownerThread': 0xFFFFFFFF, 'count': 0xFFFFFFFF},
                 {'lock': 'ok', 'ownerThread': 7, 'count': 1}]
        out = fnv_brief.threads({'threads': rows, 'locks': locks})
        self.assertEqual(len(out['threads']), 1)
        self.assertEqual(out['threads'][0]['gameFunction'], 'Renderer::PresentFrame')
        groups = {g['startModule']: g for g in out['threadsWithoutGameFunction']}
        self.assertEqual(groups['nvd3dum.dll']['count'], 5)
        self.assertEqual(groups['nvd3dum.dll']['threadIds'], [10, 11, 12, 13, 14])
        self.assertEqual(groups['nvwgf2um.dll']['count'], 1)
        self.assertIsNone(out['locks'][0]['ownerThread'])
        self.assertIsNone(out['locks'][0]['count'])
        self.assertIn('not a valid lock state', out['locks'][0]['note'])
        self.assertEqual(out['locks'][1]['ownerThread'], 7)

    def test_hang_brief_starts_at_the_first_game_frame_and_cleans_the_verdict(self):
        frames = [{'module': 'ntdll.dll', 'offset': '0x%X' % i} for i in range(10)]
        frames += [{'function': 'E7CCD0Renderer::PresentFrame', 'address': '0xE7CCD5'},
                   {'function': 'Main::Loop', 'address': '0x1'}]
        answer = {'verdict': 'Main thread running; in 4 of 5 samples it was in E7CCD0Renderer::PresentFrame',
                  'threads': [{'threadId': 1, 'main': True, 'topGameFunction': 'E7CCD0Renderer::PresentFrame',
                               'samples': [{'frames': frames}]}]}
        out = fnv_brief.hang(answer)
        self.assertEqual(out['threads'][0]['stack'][0], 'Renderer::PresentFrame')
        self.assertEqual(out['threads'][0]['framesAboveGameFunction'], 10)
        self.assertEqual(out['threads'][0]['topGameFunction'], 'Renderer::PresentFrame')
        self.assertIn('it was in Renderer::PresentFrame', out['verdict'])
        self.assertNotIn('E7CCD0', out['verdict'])


class GlobalsTests(unittest.TestCase):
    def test_empty_query_says_what_is_near(self):
        live = MemLive()
        live.mem[DATA_HANDLER] = 0x7000
        live.lists[0x7000 + 0xE8] = [0x7100, 0x7200]
        for addr, fid in ((0x7100, 0x38), (0x7200, 0x39)):
            raw = bytearray(0x28)
            struct.pack_into('<I', raw, 0x0C, fid)
            struct.pack_into('<I', raw, 0x18, addr + 1)
            raw[0x20] = ord('f')
            live.mem[addr] = bytes(raw)
        w = object.__new__(WorldTools)
        w.agent = SimpleNamespace(live=live)
        w._texts = lambda ptrs, limit=400: ['GameHour' if p == 0x7101 else 'TimeScale' for p in ptrs]
        out = w.globals(query='GameHourr')
        self.assertEqual(out['count'], 0)
        self.assertEqual(out['didYouMean'], ['GameHour'])
        self.assertIn('GameHour', out['note'])
        hit = w.globals(query='GameHour')
        self.assertEqual(hit['count'], 1)
        self.assertNotIn('didYouMean', hit)


if __name__ == '__main__':
    unittest.main()
