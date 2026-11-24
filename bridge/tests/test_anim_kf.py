"""fnv_anim play kf and reload (tool gap 4, 2026-11-24) against a fake game memory: the path rules, the engine calls in
order (kNVSE's PlayAnimationPath route and JIP's Update3D), and that the actor's own group map is put back."""
import struct
import unittest

from fnv_mcp import fnv_inspect
from fnv_mcp.fnv_conv import ConvError
from fnv_mcp.fnv_inspect import AnimTools, kf_path

ACTOR, ANIM, MGR, OWN_MAP, LOADER, MODEL, GROUP, SEQ, BASE, VTABLE, GET_SEQ = (
    0x1000, 0x2000, 0x3000, 0x4000, 0x5000, 0x6000, 0x7000, 0x8000, 0x9000, 0xA000, 0x00ABCDEF)
LOADED, ROOT = 0xB000, 0xC000


class Memory(dict):
    def put(self, address, raw):
        for i, b in enumerate(raw):
            self[address + i] = b

    def get_bytes(self, address, size):
        return bytes(self.get(address + i, 0) for i in range(size))


class FakeControl:
    def __init__(self):
        self.events = []

    def hold(self, seconds):
        self.events.append('hold')
        return {'changed': True}

    def release(self):
        self.events.append('release')


class FakeLink:
    def __init__(self, memory):
        self.memory, self.writes = memory, []

    def write(self, address, data):
        self.memory.put(address, data)
        self.writes.append((address, bytes(data)))


class FakeLive:
    ACTOR_TYPES = (59, 60)

    def __init__(self, memory, known=False, group_id=0x0B, adds=True, player=False):
        self.memory, self.link = memory, FakeLink(memory)
        self.calls, self.next = [], 0x20000
        self.known, self.group_id, self.adds, self.player = known, group_id, adds, player
        self.time_control, self.wait_for = FakeControl(), object()
        self.map_at_add = None

    def in_game(self):
        return True

    def changing(self, reason):
        pass

    def resolve_actor(self, ref):
        return {'ptr': ACTOR, 'formId': '00000014' if self.player else '0012ABCD'}

    def u32s(self, addresses):
        return [struct.unpack('<I', self.memory.get_bytes(a, 4))[0] for a in addresses]

    def reads(self, spans):
        return [self.memory.get_bytes(a, n) for a, n in spans]

    def scratch(self, data):
        address, self.next = self.next, self.next + 0x100
        self.memory.put(address, data)
        return address

    def _game_string(self, text):
        self.text = text
        return self.scratch(text.encode() + b'\0')

    def engine_call(self, address, this, args, ret, conv, confirm):
        a, t = int(address, 16), int(this, 16)
        values = [x['value'] for x in args]
        self.calls.append((a, t, values))
        result = None
        if a == fnv_inspect.GET_ANIMATION:
            result = '0x%08X' % ANIM
        elif a == fnv_inspect.LOOKUP_SEQUENCE:
            result = '0x%08X' % (SEQ if self.known else 0)
        elif a == fnv_inspect.LOAD_KF_MODEL:
            result = '0x%08X' % MODEL
        elif a == fnv_inspect.ADD_ANIMATION:
            self.map_at_add = self.u32s([ANIM + 0xDC])[0]
            self.memory.put(ANIM + 0x9E, struct.pack('<H', 0x1234))       # AddAnimation's movement "fix"
            result = self.adds
        elif a == fnv_inspect.GROUP_MAP_LOOKUP:
            self.memory.put(values[1], struct.pack('<I', BASE if self.adds else 0))
            result = True
        elif a == GET_SEQ:
            result = '0x%08X' % SEQ
        elif a == fnv_inspect.ACTIVATE_SEQUENCE:
            self.memory.put(SEQ + 0x44, struct.pack('<I', 0))
            result = True
        return {'result': result}


def u32(v):
    return struct.pack('<I', v)


def memory():
    m = Memory()
    m.put(ANIM + 0xD8, u32(MGR))
    m.put(ANIM + 0xDC, u32(OWN_MAP))
    m.put(ANIM + 0x9E, struct.pack('<H', 0x0101))
    m.put(fnv_inspect.MODEL_LOADER, u32(LOADER))
    m.put(MODEL + 8, u32(GROUP))
    m.put(BASE, u32(VTABLE))
    m.put(VTABLE + 0x10, u32(GET_SEQ))
    m.put(SEQ + 0x1C, struct.pack('<f', 1.0))
    m.put(SEQ + 0x44, u32(2))                       # was playing: deactivated first
    m.put(ACTOR + 0x64, u32(LOADED))
    m.put(LOADED + 0x14, u32(ROOT))
    return m


class KfPathTests(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(kf_path('Meshes/Characters/_Male/IdleAnims/Wave.KF'), 'characters\\_male\\idleanims\\wave.kf')
        self.assertEqual(kf_path('\\mymod\\a.kf'), 'mymod\\a.kf')
        for bad in ('x.nif', '', 'mymod/../../x.kf', 'C:/x.kf', 'a' * 260 + '.kf'):
            with self.assertRaises(ConvError, msg=bad):
                kf_path(bad)


class PlayKfTests(unittest.TestCase):
    def addresses(self, live):
        return [c[0] for c in live.calls]

    def test_a_new_kf_is_loaded_through_a_one_bucket_map_and_activated(self):
        m = memory()
        m.put(GROUP + 0x10, struct.pack('<H', 0x0B))
        live = FakeLive(m)
        out = AnimTools(live).play('npc', kf='meshes/mymod/wave.kf')
        self.assertEqual(live.text, 'mymod\\wave.kf')
        self.assertEqual(self.addresses(live), [
            fnv_inspect.GET_ANIMATION, fnv_inspect.LOOKUP_SEQUENCE, fnv_inspect.LOAD_KF_MODEL,
            fnv_inspect.GROUP_MAP_INIT, fnv_inspect.ADD_ANIMATION, fnv_inspect.GROUP_MAP_LOOKUP, GET_SEQ,
            fnv_inspect.DEACTIVATE_SEQUENCE, fnv_inspect.ACTIVATE_SEQUENCE])
        self.assertNotEqual(live.map_at_add, OWN_MAP)                       # added into the temporary map
        self.assertEqual(live.u32s([ANIM + 0xDC])[0], OWN_MAP)              # and the actor's own map put back
        self.assertEqual(m.get_bytes(ANIM + 0x9E, 2), struct.pack('<H', 0x0101))
        activate = live.calls[-1]
        self.assertEqual((activate[1], activate[2][:3]), (MGR, [SEQ, 0, 1]))
        self.assertEqual(live.time_control.events, ['hold', 'release'])
        self.assertTrue(out['loaded'])
        self.assertEqual(out['sequence'], '0x%08X' % SEQ)

    def test_a_kf_already_on_the_actor_is_only_started_again(self):
        live = FakeLive(memory(), known=True)
        out = AnimTools(live).play('npc', kf='mymod/wave.kf')
        self.assertNotIn(fnv_inspect.LOAD_KF_MODEL, self.addresses(live))
        self.assertIn(fnv_inspect.ACTIVATE_SEQUENCE, self.addresses(live))
        self.assertFalse(out['loaded'])

    def test_a_kf_with_no_known_group_is_refused_before_adding(self):
        m = memory()
        m.put(GROUP + 0x10, struct.pack('<H', 0x00FF))
        live = FakeLive(m)
        with self.assertRaises(ConvError) as e:
            AnimTools(live).play('npc', kf='mymod/wave.kf')
        self.assertEqual(e.exception.code, 'KF_NO_GROUP')
        self.assertNotIn(fnv_inspect.ADD_ANIMATION, self.addresses(live))
        self.assertEqual(live.time_control.events, ['hold', 'release'])

    def test_a_refused_add_puts_the_map_back_and_says_so(self):
        m = memory()
        m.put(GROUP + 0x10, struct.pack('<H', 0x0B))
        live = FakeLive(m, adds=False)
        with self.assertRaises(ConvError) as e:
            AnimTools(live).play('npc', kf='mymod/wave.kf')
        self.assertEqual(e.exception.code, 'KF_NOT_ADDED')
        self.assertEqual(live.u32s([ANIM + 0xDC])[0], OWN_MAP)

    def test_one_of_group_idle_or_kf(self):
        with self.assertRaises(ConvError):
            AnimTools(FakeLive(memory())).play('npc', group='Idle', kf='a.kf')


class ReloadTests(unittest.TestCase):
    def test_an_npc_is_dropped_marked_and_queued(self):
        live = FakeLive(memory())
        out = AnimTools(live).reload('npc')
        self.assertEqual([c[:2] for c in live.calls], [(fnv_inspect.SET_3D, ACTOR),
                                                       (fnv_inspect.QUEUE_REFERENCE, LOADER)])
        self.assertEqual(live.calls[0][2], [0, 1])
        self.assertEqual(live.calls[1][2], [ACTOR, 1, 0])
        self.assertEqual(live.memory.get_bytes(ACTOR + 0x5F, 1)[0] & 1, 1)
        self.assertTrue(out['queued'])

    def test_the_player_is_not_dropped(self):
        live = FakeLive(memory(), player=True)
        AnimTools(live).reload('player')
        self.assertEqual([c[0] for c in live.calls], [fnv_inspect.QUEUE_REFERENCE])

    def test_no_3d(self):
        m = memory()
        m.put(ACTOR + 0x64, u32(0))
        with self.assertRaises(ConvError) as e:
            AnimTools(FakeLive(m)).reload('npc')
        self.assertEqual(e.exception.code, 'NO_3D')


if __name__ == '__main__':
    unittest.main()
