"""PLAN-3 S3.5: lossless global values (G9) and spawn from a reference (G2)."""
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402
from fnv_mcp import fnv_world  # noqa: E402
from fnv_mcp.fnv_live import LiveError  # noqa: E402

DH, NODE2 = 0x00900000, 0x00900400
GLOBALS = {0x00910000: ('GameHour', 0x00000038, 's', 9.12),
           0x00910100: ('MyShort', 0x00001234, 's', 2.0),
           0x00910200: ('MyFloat', 0x00001235, 'f', 0.25)}


class GlobalsTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live
        link = self.fake.link
        ptrs = list(GLOBALS)
        link.put(fnv_world.DATA_HANDLER, struct.pack('<I', DH))
        link.put(DH + 0xE8, struct.pack('<II', ptrs[0], NODE2))
        link.put(NODE2, struct.pack('<II', ptrs[1], NODE2 + 8))
        link.put(NODE2 + 8, struct.pack('<II', ptrs[2], 0))
        names = {}
        for k, (g, (name, fid, kind, value)) in enumerate(GLOBALS.items()):
            raw = bytearray(0x28)
            struct.pack_into('<I', raw, 0x0C, fid)
            struct.pack_into('<I', raw, 0x18, 0x00A00000 + k)
            raw[0x20] = ord(kind)
            struct.pack_into('<f', raw, 0x24, value)
            link.put(g, bytes(raw))
            names[0x00A00000 + k] = name
        agent = SimpleNamespace(live=self.live, link=link,
                                social=SimpleNamespace(_texts=lambda pointers, limit=400: [names.get(p) for p in pointers]))
        self.world = fnv_world.WorldTools(agent)

    def value(self, name):
        g = next(p for p, v in GLOBALS.items() if v[0] == name)
        return struct.unpack('<f', self.fake.link.get(g + 0x24, 4))[0]

    def test_gamehour_fraction_kept_and_undo_exact(self):
        listed = {g['editorId']: g for g in self.world.globals('GameHour')['globals']}
        self.assertAlmostEqual(listed['GameHour']['value'], 9.12, places=5)
        out = self.world.globals(set_values={'GameHour': 9.5})
        [c] = out['changed']
        self.assertAlmostEqual(c['old'], 9.12, places=5)
        self.assertEqual(c['new'], 9.5)
        self.assertFalse(c.get('rounded'))
        self.assertEqual(self.value('GameHour'), 9.5)
        self.live.undo()
        self.assertEqual(self.value('GameHour'), struct.unpack('<f', struct.pack('<f', 9.12))[0])

    def test_short_global_integer_written(self):
        out = self.world.globals(set_values={'MyShort': 3.7})
        [c] = out['changed']
        self.assertEqual((c['new'], c['rounded']), (4, True))
        self.assertEqual(self.value('MyShort'), 4.0)
        out = self.world.globals(set_values={'MyShort': 5})
        self.assertFalse(out['changed'][0].get('rounded'))
        self.assertEqual(self.value('MyShort'), 5.0)

    def test_float_global_untouched(self):
        self.world.globals(set_values={'MyFloat': 0.75})
        self.assertEqual(self.value('MyFloat'), 0.75)


class SpawnRefTests(unittest.TestCase):
    def setUp(self):
        from fnv_mcp.fnv_agent import AgentTools
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live
        self.live.in_game = lambda: None
        self.agent = AgentTools(self.live, deep=None)
        self.placed = []

    def test_spawn_from_ref_uses_base(self):
        link = self.fake.link
        link.put(0x5000 + 4, struct.pack('<4sII', bytes([59, 0, 0, 0]), 0, 0x0002F00D))   # an ACHR
        link.put(0x5000 + 0x20, struct.pack('<I', 0x6000))                              # its base NPC_
        link.put(0x6000 + 4, struct.pack('<4sII', bytes([42, 0, 0, 0]), 0, 0x0001ABCD))
        forms = {'0001ABCD': 0x6000}

        def resolve(spec):
            if spec == '0002F00D':
                return {'ptr': 0x5000, 'formId': '0002F00D', 'expr': '(BuildRef 0 192525)'}
            return {'ptr': 0x14, 'formId': '00000014', 'expr': 'player'}
        self.live.resolve = resolve
        seen = []

        def evaluate(text, ref=0):
            seen.append(text)
            raise LiveError('STOP', 'stop after the PlaceAtMe line')
        link.eval = evaluate
        with self.assertRaises(LiveError):
            self.agent.spawn('0002F00D')
        self.assertTrue(seen[0].startswith('PlaceAtMe 0001ABCD 1'), seen)
        self.assertTrue(forms)

    def test_spawn_player_refused_clearly(self):
        self.fake.link.put(0x14 + 4, struct.pack('<4sII', bytes([59, 0, 0, 0]), 0, 0x14))
        self.live.resolve = lambda spec: {'ptr': 0x14, 'formId': '00000014', 'expr': 'player'}
        with self.assertRaises(LiveError) as caught:
            self.agent.spawn('player')
        self.assertEqual(caught.exception.code, 'BAD_FORM')
        self.assertIn('00000007', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
