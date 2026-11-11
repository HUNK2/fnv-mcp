"""Plan 2 W3 eyes: camera math (projection, rectangles, angles), HUD reads, visibility verdicts, the camera save and
restore. Fake live service; no game."""
import math
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_eyes  # noqa: E402
from geck_mcp.fnv_eyes import Camera, EyesTools, direction, heading_to  # noqa: E402
from geck_mcp.fnv_live import LiveError  # noqa: E402


def camera_looking(yaw, pitch, position=(0.0, 0.0, 0.0), fov=75.0, width=1280, height=720):
    """A Camera whose matrices are built the way the engine's are (column 0 forward, column 2 right)."""
    f = direction(yaw, pitch)
    right = (math.cos(math.radians(yaw)), -math.sin(math.radians(yaw)), 0.0)
    up = (right[1] * f[2] - right[2] * f[1], right[2] * f[0] - right[0] * f[2], right[0] * f[1] - right[1] * f[0])
    rotate = (f[0], up[0], right[0], f[1], up[1], right[1], f[2], up[2], right[2])
    r = math.tan(math.radians(fov) / 2)
    t = r * height / width
    px, py, pz = position

    def row(v, scale):
        return [v[0] / scale, v[1] / scale, v[2] / scale, -(v[0] * px + v[1] * py + v[2] * pz) / scale]

    clip = row(right, r) + row(up, t) + row(f, 1.0) + row(f, 1.0)
    frustum = (-r, r, t, -t, 5.0, 10000.0)
    return Camera(position, rotate, tuple(clip), frustum, fov, width, height)


class MathTests(unittest.TestCase):
    def test_heading_to_north_east_and_down(self):
        self.assertAlmostEqual(heading_to((0, 0, 0), (0, 10, 0))[0], 0.0)
        self.assertAlmostEqual(heading_to((0, 0, 0), (10, 0, 0))[0], 90.0)
        self.assertAlmostEqual(heading_to((0, 0, 0), (0, -10, 0))[0], 180.0)
        self.assertAlmostEqual(heading_to((0, 0, 10), (10, 0, 0))[1], 45.0)   # looking down is positive

    def test_direction_matches_heading(self):
        d = direction(90.0, 0.0)
        self.assertAlmostEqual(d[0], 1.0)
        self.assertAlmostEqual(d[2], 0.0)
        yaw, pitch = heading_to((0, 0, 0), direction(33.0, -20.0))
        self.assertAlmostEqual(yaw, 33.0, places=4)
        self.assertAlmostEqual(pitch, -20.0, places=4)

    def test_point_ahead_projects_to_the_centre(self):
        cam = camera_looking(0.0, 0.0)
        u, v, depth = cam.project((0.0, 500.0, 0.0))
        self.assertAlmostEqual(u, 0.5)
        self.assertAlmostEqual(v, 0.5)
        self.assertAlmostEqual(depth, 500.0)
        self.assertIsNone(cam.project((0.0, -500.0, 0.0)))     # behind

    def test_right_and_up_land_on_the_right_and_top(self):
        cam = camera_looking(0.0, 0.0)
        u, _, _ = cam.project((100.0, 500.0, 0.0))
        _, v, _ = cam.project((0.0, 500.0, 100.0))
        self.assertGreater(u, 0.5)
        self.assertLess(v, 0.5)

    def test_angles_come_back(self):
        cam = camera_looking(250.0, 12.0)
        yaw, pitch = cam.angles()
        self.assertAlmostEqual(yaw, 250.0, places=3)
        self.assertAlmostEqual(pitch, 12.0, places=3)

    def test_rect_of_a_sphere_in_view_and_out_of_view(self):
        cam = camera_looking(0.0, 0.0)
        rect = cam.rect((0.0, 400.0, 0.0), 50.0)
        self.assertTrue(rect['onScreen'])
        x0, y0, x1, y1 = rect['pixels']
        self.assertTrue(x0 < 640 < x1 and y0 < 360 < y1)
        self.assertFalse(rect['clipped'])
        self.assertIsNone(cam.rect((0.0, -400.0, 0.0), 50.0))
        self.assertFalse(cam.rect((4000.0, 400.0, 0.0), 50.0)['onScreen'])


class FakeLink:
    def __init__(self, mem):
        self.mem = mem
        self.sent = []
        self.frame = 100

    def one(self, op, *args, **kw):
        self.sent.append((op,) + args)
        if op == 'wr':
            addr, data = int(args[0], 16), bytes.fromhex(args[1])
            for k, b in enumerate(data):
                self.mem[addr + k] = b
            return {'ok': True}
        if op == 'rs':
            addr = int(args[0], 16)
            out = bytearray()
            while self.mem.get(addr, 0):
                out.append(self.mem[addr])
                addr += 1
            return {'ok': True, 'text': out.decode()}
        if op == 'call':
            return {'ok': True, 'eax': '0x0'}
        return {'ok': True}

    def ping(self):
        self.frame += 5
        return {'frame': self.frame}


class FakeLive:
    def __init__(self):
        self.mem = {}
        self.link = FakeLink(self.mem)
        self.lines = []
        self.values_map = {}
        self.journal = SimpleJournal()

    def put(self, addr, data):
        for k, b in enumerate(data):
            self.mem[addr + k] = b

    def put32(self, addr, value):
        self.put(addr, struct.pack('<I', value))

    def u32s(self, addresses):
        return [struct.unpack('<I', bytes(self.mem.get(a + k, 0) for k in range(4)))[0] for a in addresses]

    def reads(self, spans):
        return [bytes(self.mem.get(a + k, 0) for k in range(n)) if a else None for a, n in spans]

    def ready(self):
        return {}

    def in_game(self):
        return {'loaded': True}

    def identity(self):
        return {'pid': 7}

    def changing(self, reason):
        return None

    def console(self, line, ref=0):
        self.lines.append(line)
        if line.startswith('tfc'):
            osg = self.u32s([fnv_eyes.OS_GLOBALS])[0]
            self.mem[osg + fnv_eyes.FLY_FLAG] = 0 if self.mem.get(osg + fnv_eyes.FLY_FLAG) else 1
        return {'accepted': True, 'output': []}

    def values(self, exprs, ref=0):
        return [self.values_map.get(e) for e in exprs]

    def form_ids(self, ptrs):
        return [{'formId': '%08X' % p, 'formType': 0, 'flags': self.u32s([p + 8])[0]} if p else None for p in ptrs]

    def resolve(self, ref):
        return {'ptr': int(ref, 16), 'formId': ref.upper().zfill(8), 'expr': ref}

    def walk_list(self, head, limit=500):
        return []


class SimpleJournal:
    def __init__(self):
        self.entries_ = []

    def add(self, entry):
        self.entries_.append(entry)
        return dict(entry, id=str(len(self.entries_)))

    def entries(self):
        return list(self.entries_)


SG, CAM, OSG, PLAYER, HUD = 0x1000, 0x2000, 0x3000, 0x4000, 0x6000


def world():
    live = FakeLive()
    live.put32(fnv_eyes.SCENE_GRAPH, SG)
    live.put32(SG + fnv_eyes.SG_CAMERA, CAM)
    live.put(SG + fnv_eyes.SG_FOV, struct.pack('<f', 75.0))
    cam = camera_looking(0.0, 0.0, (10.0, 20.0, 30.0))
    live.put(CAM + fnv_eyes.CAM_ROT, struct.pack('<9f', *cam.rotate))
    live.put(CAM + fnv_eyes.CAM_POS, struct.pack('<3f', *cam.position))
    live.put(CAM + fnv_eyes.CAM_CLIP, struct.pack('<16f', *cam.clip))
    live.put(CAM + fnv_eyes.CAM_FRUSTUM, struct.pack('<6f', *cam.frustum))
    live.put32(fnv_eyes.OS_GLOBALS, OSG)
    live.put32(fnv_eyes.PLAYER, PLAYER)
    live.mem[PLAYER + fnv_eyes.THIRD_PERSON] = 1
    live.values_map.update({'GetNumericIniSetting "iSize W:Display"': 1280, 'GetNumericIniSetting "iSize H:Display"': 720})
    return live


class CameraToolTests(unittest.TestCase):
    def test_get_reads_position_angles_and_view(self):
        eyes = EyesTools(world(), sleep=lambda s: None)
        out = eyes.get()
        self.assertEqual(out['position'], [10.0, 20.0, 30.0])
        self.assertAlmostEqual(out['yaw'], 0.0, places=1)
        self.assertEqual(out['fov'], 75.0)
        self.assertFalse(out['firstPerson'])
        self.assertFalse(out['fly'])

    def test_set_turns_fly_on_writes_the_pose_and_restore_turns_it_off(self):
        live = world()
        eyes = EyesTools(live, sleep=lambda s: None)
        eyes.set(position=[1, 2, 3], yaw=90, pitch=10)
        self.assertEqual(live.mem[OSG + fnv_eyes.FLY_FLAG], 1)
        self.assertIn('tfc 1', live.lines)                       # world frozen while the camera is free
        pose = struct.unpack('<5f', live.reads([(PLAYER + fnv_eyes.FLY_POSE, 20)])[0])
        self.assertAlmostEqual(pose[0], math.radians(90))
        self.assertAlmostEqual(pose[1], math.radians(10))
        self.assertEqual(pose[2:], (1.0, 2.0, 3.0))
        out = eyes.restore()
        self.assertTrue(out['restored'])
        self.assertEqual(live.mem[OSG + fnv_eyes.FLY_FLAG], 0)
        self.assertIsNone(eyes.saved)

    def test_pitch_out_of_range_refused(self):
        with self.assertRaises(ValueError):
            EyesTools(world(), sleep=lambda s: None).set(pitch=95)

    def test_restore_without_a_change_says_so(self):
        out = EyesTools(world(), sleep=lambda s: None).restore()
        self.assertFalse(out['restored'])


class HudTests(unittest.TestCase):
    def test_hud_read_mode_flags_crosshair_and_queue(self):
        live = world()
        live.put32(fnv_eyes.HUD, HUD)
        live.put32(HUD + fnv_eyes.HUD_FLAGS, 0x1 | 0x2 | 0x400)
        live.put32(HUD + fnv_eyes.HUD_MODE, 2)
        eyes = EyesTools(live, agent=None, sleep=lambda s: None)
        out = eyes.hud_read()
        self.assertEqual(out['mode'], {'value': 2, 'name': 'normal'})
        self.assertEqual(out['visible'], ['ap', 'hp', 'subtitles'])
        self.assertIsNone(out['crosshair'])

    def test_post_calls_queue_ui_message(self):
        live = world()
        live._game_string = lambda text: 0x9000
        eyes = EyesTools(live, sleep=lambda s: None)
        eyes.hud_post('hello', 2.0)
        call = [s for s in live.link.sent if s[0] == 'call'][-1]
        self.assertEqual(int(call[1], 16), fnv_eyes.QUEUE_UI_MESSAGE)
        self.assertEqual(int(call[5], 16), 0x9000)
        self.assertEqual(int(call[9], 16), struct.unpack('<I', struct.pack('<f', 2.0))[0])

    def test_post_needs_text(self):
        with self.assertRaises(ValueError):
            EyesTools(world(), sleep=lambda s: None).hud_post('')


class VisibilityTests(unittest.TestCase):
    def test_disabled_reference_without_3d(self):
        live = world()
        ref = 0x7000
        live.put32(ref + 8, 0x800)          # disabled
        live.values_map.update({'GetScale': 1.0, 'GetParentCell': '00000001', 'GetInSameCell player': 1})
        out = EyesTools(live, sleep=lambda s: None).visibility('7000')
        self.assertEqual(out['cause']['cause'], 'no_3d')
        self.assertIn('disabled', [a['cause'] for a in out['also']])

    def test_zero_scale_root(self):
        live = world()
        ref, render, root, bound = 0x7000, 0x7100, 0x7200, 0x7300
        live.put32(ref + fnv_eyes.REF_RENDER, render)
        live.put32(render + fnv_eyes.RENDER_ROOT, root)
        live.put32(root + fnv_eyes.NODE_BOUND, bound)
        live.put(bound, struct.pack('<4f', 0.0, 400.0, 0.0, 40.0))
        live.put(root + fnv_eyes.NODE_SCALE, struct.pack('<f', 0.0))
        live.values_map.update({'GetScale': 1.0, 'GetParentCell': '00000001', 'GetInSameCell player': 1})
        out = EyesTools(live, sleep=lambda s: None).visibility('7000')
        self.assertEqual(out['cause']['cause'], 'zero_scale')


class PhotoMenuTests(unittest.TestCase):
    def test_photo_refused_while_a_menu_is_open(self):
        class Agent:
            def menus(self):
                return {'inMenuMode': True, 'menuStack': [{'id': 1013, 'name': 'StartMenu'}], 'visible': []}

            def screenshot(self, **kw):
                raise AssertionError('no capture under a menu')
        live = world()
        live.in_game = lambda: {}
        eyes = EyesTools(live, agent=Agent(), sleep=lambda s: None)
        with self.assertRaises(LiveError) as cm:
            eyes.photo('view')
        self.assertEqual(cm.exception.code, 'MENU_OPEN')
        self.assertIn('StartMenu', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
