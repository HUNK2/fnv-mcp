"""fnv_physics set (tool gap 5, 2026-11-24): velocity, angular velocity and impulse on a Havok body, and a push on an
actor's character controller, against a fake game memory (no game needed)."""
import struct
import unittest

from fnv_mcp import fnv_inspect
from fnv_mcp.fnv_conv import ConvError
from fnv_mcp.fnv_inspect import PhysicsTools

REF, LOADED, ROOT, COL, BHK, HKP = 0x1000, 0x2000, 0x3000, 0x4000, 0x5000, 0x6000
PROCESS, CTRL = 0x7000, 0x8000


class Memory:
    def __init__(self):
        self.data = {}

    def put(self, address, raw):
        for i, b in enumerate(raw):
            self.data[address + i] = b

    def get(self, address, size):
        return bytes(self.data.get(address + i, 0) for i in range(size))


class FakeLink:
    def __init__(self, memory):
        self.memory = memory
        self.writes = []

    def write(self, address, data):
        old = self.memory.get(address, len(data))
        self.memory.put(address, data)
        self.writes.append((address, bytes(data)))
        return old


class FakeLive:
    def __init__(self, memory):
        self.memory = memory
        self.link = FakeLink(memory)
        self.calls = []
        self.changes = []
        self.next_scratch = 0x9000

    ACTOR_TYPES = (59, 60)

    def in_game(self):
        return True

    ready = in_game

    def resolve(self, ref):
        return {'ptr': REF, 'formId': '0x00012345'}

    def u32s(self, addresses):
        return [struct.unpack('<I', self.memory.get(a, 4))[0] for a in addresses]

    def reads(self, spans):
        return [self.memory.get(a, n) for a, n in spans]

    def rtti(self, ptrs):
        return {}

    def changing(self, reason):
        self.changes.append(reason)

    def scratch(self, data):
        address, self.next_scratch = self.next_scratch, self.next_scratch + 0x20
        self.memory.put(address, data)
        return address

    def engine_call(self, address, this, args, ret, conv, confirm):
        vec = struct.unpack('<3f', self.memory.get(args[0]['value'], 12)) if args else None
        self.calls.append((int(address, 16), int(this, 16), vec) + tuple(a['value'] for a in args[1:]))
        if int(address, 16) == fnv_inspect.PUSH_IMPULSE:          # velocity = vector / seconds, in Havok units
            seconds = args[1]['value']
            self.memory.put(int(this, 16) + fnv_inspect.PUSH_VELOCITY,
                            struct.pack('<3f', *[v / seconds / fnv_inspect.HAVOK_TO_GAME for v in vec]))
            self.memory.put(int(this, 16) + fnv_inspect.PUSH_TIME, struct.pack('<f', seconds))
        if int(address, 16) == fnv_inspect.SET_LINEAR_VELOCITY:     # the engine stores Havok units
            self.memory.put(HKP + 0x1B0, struct.pack('<3f', *[v / fnv_inspect.HAVOK_TO_GAME for v in vec]))
        if int(address, 16) == fnv_inspect.SET_ANGULAR_VELOCITY:
            self.memory.put(HKP + 0x1C0, struct.pack('<3f', *vec))
        return {'ok': True, 'result': 0}


def u32(value):
    return struct.pack('<I', value)


def with_body(inv_mass=0.5, motion=1, velocity=(0.0, 0.0, 0.0)):
    m = Memory()
    for at, value in ((REF + 0x64, LOADED), (LOADED + 0x14, ROOT), (ROOT + 0x1C, COL), (COL + 0x10, BHK),
                      (BHK + 0x08, HKP)):
        m.put(at, u32(value))
    m.put(HKP + 0x1AC, struct.pack('<f', inv_mass))
    m.put(HKP + 0x1B0, struct.pack('<3f', *[v / fnv_inspect.HAVOK_TO_GAME for v in velocity]))
    m.put(HKP + 0xE8, bytes([motion]))
    return m


def with_controller(controller=True):
    m = Memory()
    m.put(REF + 4, bytes([59]))                 # ACHR
    m.put(REF + 0x68, u32(PROCESS))
    m.put(PROCESS + 0x138, u32(CTRL if controller else 0))
    return m


class SetBodyTests(unittest.TestCase):
    def test_velocity_and_spin_call_the_body_then_wake_it(self):
        live = FakeLive(with_body())
        out = PhysicsTools(live).set_velocity('x', velocity=[100, 0, 50], angular_velocity=[0, 0, 3])
        # woken first: a sleeping body drops what is set
        self.assertEqual([c[0] for c in live.calls], [fnv_inspect.HKP_ACTIVATE, fnv_inspect.SET_LINEAR_VELOCITY,
                                                       fnv_inspect.SET_ANGULAR_VELOCITY])
        self.assertEqual(live.calls[0][1], HKP)          # activate takes the hkpRigidBody
        self.assertEqual(live.calls[1][1], BHK)          # bhkRigidBody methods take the wrapper
        self.assertEqual(live.calls[1][2], (100.0, 0.0, 50.0))
        self.assertEqual(out['velocity'], [100.0, 0.0, 50.0])
        self.assertEqual(out['angularVelocity'], [0.0, 0.0, 3.0])
        self.assertEqual(live.changes, ['physics set'])

    def test_an_impulse_adds_impulse_times_inverse_mass(self):
        live = FakeLive(with_body(inv_mass=0.25, velocity=(10.0, 0.0, 0.0)))
        out = PhysicsTools(live).set_velocity('x', impulse=[400, 0, 0])
        self.assertAlmostEqual(live.calls[1][2][0], 110.0, places=3)
        self.assertEqual(out['impulse'], [400.0, 0.0, 0.0])
        self.assertEqual(out['inverseMass'], 0.25)

    def test_a_fixed_body_is_refused_before_any_call(self):
        live = FakeLive(with_body(motion=5))
        with self.assertRaises(ConvError) as e:
            PhysicsTools(live).set_velocity('x', velocity=[1, 2, 3])
        self.assertEqual(e.exception.code, 'FIXED_BODY')
        self.assertEqual(live.calls, [])

    def test_an_impulse_on_a_body_with_no_inverse_mass_is_refused(self):
        live = FakeLive(with_body(inv_mass=0.0))
        with self.assertRaises(ConvError) as e:
            PhysicsTools(live).set_velocity('x', impulse=[1, 0, 0])
        self.assertEqual(e.exception.code, 'FIXED_BODY')

    def test_bad_vectors_and_nothing_to_set_are_refused(self):
        tools = PhysicsTools(FakeLive(with_body()))
        for kwargs in ({}, {'velocity': [1, 2]}, {'angular_velocity': [1, 'a', 3]},
                       {'impulse': [1, float('nan'), 0]}):
            with self.assertRaises(ConvError) as e:
                tools.set_velocity('x', **kwargs)
            self.assertEqual(e.exception.code, 'BAD_ARGUMENTS', kwargs)


class PushActorTests(unittest.TestCase):
    def test_velocity_on_an_actor_is_the_engines_knockback_call(self):
        live = FakeLive(with_controller())
        out = PhysicsTools(live).set_velocity('x', velocity=[300, 400, 0])
        seconds = (500 / (0.714 * 96)) ** 0.5
        (address, this, vec, duration), = live.calls
        self.assertEqual((address, this), (fnv_inspect.PUSH_IMPULSE, CTRL))
        self.assertAlmostEqual(duration, seconds, places=4)
        self.assertAlmostEqual(vec[0], 300 * seconds, places=2)      # velocity x seconds, over seconds
        self.assertEqual(out['pushVelocity'], [300.0, 400.0, 0.0])
        self.assertEqual(live.link.writes, [])

    def test_spin_or_impulse_on_an_actor_is_refused(self):
        live = FakeLive(with_controller())
        for kwargs in ({'angular_velocity': [0, 0, 1]}, {'impulse': [1, 0, 0]}):
            with self.assertRaises(ConvError) as e:
                PhysicsTools(live).set_velocity('x', **kwargs)
            self.assertEqual(e.exception.code, 'BAD_ARGUMENTS')
        self.assertEqual(live.calls, [])

    def test_no_body_and_no_controller(self):
        for memory in (Memory(), with_controller(controller=False)):
            with self.assertRaises(ConvError) as e:
                PhysicsTools(FakeLive(memory)).set_velocity('x', velocity=[1, 0, 0])
            self.assertEqual(e.exception.code, 'NO_BODY')

    def test_an_actor_with_a_collision_object_on_its_root_is_still_pushed(self):
        memory = with_body()
        memory.put(REF + 4, bytes([59]))
        memory.put(REF + 0x68, u32(PROCESS))
        memory.put(PROCESS + 0x138, u32(CTRL))
        live = FakeLive(memory)
        PhysicsTools(live).set_velocity('x', velocity=[100, 0, 0])
        self.assertEqual([c[0] for c in live.calls], [fnv_inspect.PUSH_IMPULSE])


if __name__ == '__main__':
    unittest.main()
