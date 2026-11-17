"""fnv_place snap rests an object on the bottom of its bounds, not on its origin (seen live 2026-11-18).

WoodShack01's bounds run from z -279 to +279 (its origin is at its centre), so placing its origin on the ground
buried half of it, and check did not notice because it also judged the origin. Now at/array/snap lift the
object by the bottom of its base form's bounds (TESBoundObject boundMin/boundMax, six int16 at +0x24) times
its scale, tilted by its X/Y angles, and check judges floating and sinking by that bottom. Fakes only; no game."""
import math
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp import fnv_create  # noqa: E402
from test_p1_place_surface import PlaneRays  # noqa: E402

GROUND = 4314.0
BASE, REF = 0x5000, 0x99
SHACK = (-316, -397, -279, 316, 397, 279)   # WoodShack01 OBND
FENCE = (-9, -12, -3, 256, 12, 143)         # PBFence01 OBND


def fake_live(bounds, pose=None):
    """A live session whose base form at BASE has `bounds` and whose reference REF has BASE as its base."""
    mem = {REF + fnv_create.REF_BASE_OFFSET: BASE}

    def reads(spans):
        return [struct.pack('<6h', *bounds) if a == BASE + fnv_create.BOUND_OFFSET and n == 12 else bytes(n)
                for a, n in spans]

    def resolve(ref):
        return {'ptr': BASE, 'formId': '00014F02'} if ref == 'WoodShack01' else {'ptr': REF, 'formId': 'FF0014B9'}

    return SimpleNamespace(in_game=lambda: None, changing=lambda reason: None, resolve=resolve, reads=reads,
                           u32s=lambda addrs: [mem.get(a, 0) for a in addrs])


def tools(bounds, planes=((GROUND, 0x30),)):
    t = fnv_create.PlaceTools(live=fake_live(bounds), hands=SimpleNamespace(rays=PlaneRays(list(planes))))
    placed = []
    t._place_one = lambda base, position, rotation=None, scale=None: placed.append(
        {'position': position, 'rotation': rotation, 'scale': scale}) or {'position': position}
    return t, placed


class AtTests(unittest.TestCase):
    def test_snap_rests_the_bottom_of_the_bounds_on_the_ground(self):
        t, placed = tools(SHACK)
        t.at('WoodShack01', position=[-8150, 44725, 4700], snap=True)
        self.assertAlmostEqual(placed[0]['position'][2], GROUND + 279)

    def test_the_lift_scales_with_the_object(self):
        t, placed = tools(SHACK)
        t.at('WoodShack01', position=[0, 0, 4700], scale=2.0, snap=True)
        self.assertAlmostEqual(placed[0]['position'][2], GROUND + 558)

    def test_an_object_with_its_origin_at_its_base_barely_moves(self):
        t, placed = tools(FENCE)
        t.at('WoodShack01', position=[0, 0, 4700], snap=True)
        self.assertAlmostEqual(placed[0]['position'][2], GROUND + 3)

    def test_without_snap_the_position_is_kept(self):
        t, placed = tools(SHACK)
        t.at('WoodShack01', position=[0, 0, 4700])
        self.assertEqual(placed[0]['position'][2], 4700)

    def test_array_snap_lifts_every_object(self):
        t, placed = tools(SHACK)
        t.array('WoodShack01', origin=[0, 0, 4700], count=3, spacing=800, snap=True)
        self.assertEqual([round(p['position'][2], 3) for p in placed], [GROUND + 279] * 3)

    def test_unreadable_bounds_keep_the_old_origin_snap(self):
        t, placed = tools(SHACK)
        t.live.reads = lambda spans: [b'' for _ in spans]
        t.at('WoodShack01', position=[0, 0, 4700], snap=True)
        self.assertAlmostEqual(placed[0]['position'][2], GROUND)

    def test_nonsense_bounds_are_ignored(self):
        t, placed = tools((10, 10, 300, -10, -10, -300))       # min over max: not a bound
        t.at('WoodShack01', position=[0, 0, 4700], snap=True)
        self.assertAlmostEqual(placed[0]['position'][2], GROUND)


class SnapRefTests(unittest.TestCase):
    def snap(self, bounds, z, rotation=(0, 0, 0), scale=1.0, align=False, planes=((GROUND, 0x30),)):
        t, _ = tools(bounds, planes)
        t._pose = lambda ptr: {'position': [0.0, 0.0, z], 'rotation': list(rotation), 'scale': scale}
        moves = []
        t.adjust = lambda ref, move=None, rotate=None: moves.append((move, rotate)) or {'ref': ref}
        return t.snap('FF0014B9', align=align), moves

    def test_a_half_buried_shack_is_lifted_onto_its_bottom(self):
        out, moves = self.snap(SHACK, GROUND)                   # the live repro: origin on the ground
        self.assertTrue(out['snapped'])
        self.assertAlmostEqual(moves[0][0][2], 279)
        self.assertEqual(out['lift'], 279)

    def test_a_shack_already_resting_on_its_bottom_does_not_move(self):
        out, moves = self.snap(SHACK, GROUND + 279)
        self.assertAlmostEqual(moves[0][0][2], 0)

    def test_a_tilted_object_lifts_by_its_tilted_bottom(self):
        out, moves = self.snap(SHACK, GROUND, rotation=(20, 0, 0))
        self.assertAlmostEqual(moves[0][0][2], 279 * math.cos(math.radians(20)), places=3)


class CheckTests(unittest.TestCase):
    def check(self, bounds, z, scale=1.0):
        t, _ = tools(bounds)
        t._pose = lambda ptr: {'position': [0.0, 0.0, z], 'rotation': [0, 0, 0], 'scale': scale}
        return t.check('FF0014B9')

    def test_a_shack_with_its_origin_on_the_ground_is_reported_sunk(self):
        out = self.check(SHACK, GROUND)
        self.assertIn('below the surface under it by 279 units', out['issues'])

    def test_a_shack_resting_on_its_bottom_is_fine(self):
        out = self.check(SHACK, GROUND + 279)
        self.assertEqual(out['issues'], [])

    def test_a_shack_floating_over_its_bottom_is_reported(self):
        out = self.check(SHACK, GROUND + 279 + 60)
        self.assertIn('floats 60 units above the surface under it', out['issues'])


if __name__ == '__main__':
    unittest.main()
