"""fnv_place snap tests the whole footprint, not one ray under the centre (design agreed 2026-11-18).

A building snapped by the ground under its centre floated on the downhill side of a slope, and one with a boulder
under its middle sat on the boulder. Now at/array/snap cast rays over the bounds' footprint (turned to the heading)
and, with rest='low' (the default when the bounds are known), rest the bottom on the lowest ground under it: nothing
floats, the uphill side sinks, and a single sample far under the rest (a cliff edge, a gully) is an overhang that is
reported instead of burying the object. A steep spread warns and suggests a heading; a spread deeper than the object
is refused. check reports a floating corner. Fakes only; no game."""
import math
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_create  # noqa: E402

BASE, REF = 0x5000, 0x99
SHACK = (-316, -397, -279, 316, 397, 279)     # WoodShack01 OBND: origin at the centre
CRATE = (-16, -16, 0, 16, 16, 30)             # small: one ray
TERRAIN = 0x30


class TerrainRays:
    """Vertical rays over terrain z = c + a*x + b*y, plus boxes (x0, x1, y0, y1, top, ref) standing on it. Going
    down, a ray hits the highest surface between its start and end; terrain hits name TERRAIN."""

    def __init__(self, a=0.0, b=0.0, c=0.0, boxes=(), drop=None):
        self.a, self.b, self.c = a, b, c
        self.boxes = list(boxes)
        self.drop = drop          # (x_min, depth): terrain drops by depth where x >= x_min (a cliff step)
        self.calls = []

    def terrain(self, x, y):
        z = self.c + self.a * x + self.b * y
        if self.drop and x >= self.drop[0]:
            z -= self.drop[1]
        return z

    def cast(self, segments, layer=0x26, refs=False):
        self.calls.append(len(segments))
        out = []
        for a, b in segments:
            x, y = a[0], a[1]
            hits = []
            t = self.terrain(x, y)
            if b[2] <= t <= a[2]:
                hits.append((t, TERRAIN))
            for x0, x1, y0, y1, top, ref in self.boxes:
                if x0 <= x <= x1 and y0 <= y <= y1 and b[2] <= top <= a[2]:
                    hits.append((top, ref))
            if not hits:
                out.append({'hit': False})
                continue
            z, ref = max(hits)
            f = (a[2] - z) / (a[2] - b[2])
            out.append({'hit': True, 'fraction': f, 'point': (x, y, z), 'ref': ref})
        return out


def fake_live(bounds, form_type=0):
    mem = {REF + fnv_create.REF_BASE_OFFSET: BASE}

    def reads(spans):
        out = []
        for a, n in spans:
            if a == BASE + fnv_create.BOUND_OFFSET and n == 12:
                out.append(struct.pack('<6h', *bounds))
            elif a in (BASE + 4, REF + 4) and n == 1:
                out.append(bytes([form_type if a == BASE + 4 else 0]))
            else:
                out.append(bytes(n))
        return out

    return SimpleNamespace(in_game=lambda: None, changing=lambda reason: None, reads=reads,
                           resolve=lambda ref: {'ptr': BASE, 'formId': '00014F02'} if ref == 'BASE'
                           else {'ptr': REF, 'formId': 'FF0014B9'},
                           u32s=lambda addrs: [mem.get(a, 0) for a in addrs])


def tools(bounds, rays, form_type=0):
    t = fnv_create.PlaceTools(live=fake_live(bounds, form_type), hands=SimpleNamespace(rays=rays))
    placed = []

    def place_one(base, position, rotation=None, scale=None):
        placed.append({'position': list(position), 'rotation': rotation, 'scale': scale})
        return {'position': list(position)}
    t._place_one = place_one
    return t, placed


SLOPE = math.tan(math.radians(10))     # 10 degrees rising toward +x


class ColumnRays:
    """Vertical rays over a world given as hit lists: column(x, y) -> [(z, ref), ...] in any order. A ray going down
    hits the highest listed surface between its start and end. ref None is the landscape (the pick names no
    reference for it); every other surface is a reference. The world's lists are copied from rays seen live
    2026-11-18 at a boulder in Goodsprings."""

    def __init__(self, column):
        self.column = column
        self.calls = []

    def cast(self, segments, layer=0x26, refs=False):
        self.calls.append(len(segments))
        out = []
        for a, b in segments:
            hits = [(z, ref) for z, ref in self.column(a[0], a[1]) if b[2] <= z <= a[2]]
            if not hits:
                out.append({'hit': False})
                continue
            z, ref = max(hits, key=lambda h: h[0])
            out.append({'hit': True, 'fraction': (a[2] - z) / (a[2] - b[2]), 'point': (a[0], a[1], z),
                        'ref': ref if refs else None})
        return out


ROCK = 0x41196680
STEEP25 = math.tan(math.radians(25))   # its high side rises over the origin: live it rose 517 of 558


class LiveRockTests(unittest.TestCase):
    """Two faults the first live run found (2026-11-18, the boulder by Goodsprings Source)."""

    def test_nothing_under_the_landscape_is_ground(self):
        # placing from z 400: under the boulder the ray met its top (700), the landscape inside it (577, more
        # than 64 over the placement height, so "overhead" by the curb rule) and then the boulder's buried
        # underside (50): the snap rested on that and sank the shack under the ground around it
        def column(x, y):
            if x < -300:
                return [(700.0, ROCK), (577.0, None), (50.0, ROCK)]
            return [(100.0, None)]
        t, placed = tools(SHACK, ColumnRays(column))
        out = t.at('BASE', position=[0, 0, 400], snap=True)
        self.assertAlmostEqual(placed[0]['position'][2], 100 + 279)
        self.assertEqual(out['footprint']['groundMin'], 100)

    def test_placement_reports_a_boulder_inside_it_as_check_will(self):
        # a boulder top 400 over the ground, inside the shack once it rests on the ground (top 658): the curb rule
        # (from z 400, overhead past 464) passed over it, and the placement said "sunk 0" where check then said 400
        # (seen live: 336 at placement, 530 at check)
        def column(x, y):
            return [(500.0, ROCK), (100.0, None)] if x > 200 else [(100.0, None)]
        t, placed = tools(SHACK, ColumnRays(column))
        out = t.at('BASE', position=[0, 0, 400], snap=True)
        self.assertAlmostEqual(placed[0]['position'][2], 100 + 279)
        self.assertEqual(out['footprint']['groundMax'], 500)
        self.assertAlmostEqual(out['footprint']['sunk'], 400)
        self.assertIn('sunk', out['footprint']['warning'])

    def sunk_on_a_slope(self, x, y, bottom):
        """The landscape rising along x, the shack's own collision over the whole footprint: a roof low at the eaves
        (60 over the floor, as live: a sloped roof met at 6479..6600 under the landscape at 6546..6683) and the
        floor. On the high side a ray meets the landscape, then its roof, then its floor, then nothing (inside the
        hill)."""
        hits = [(100.0 + STEEP25 * x, None)]
        if -316 <= x <= 316 and -397 <= y <= 397:
            hits += [(bottom + 60.0, REF), (bottom + 1.6, REF)]
        return hits

    def test_check_finds_the_ground_inside_a_sunk_side(self):
        low = 100 - STEEP25 * (316 - 4)
        t, _ = tools(SHACK, ColumnRays(lambda x, y: self.sunk_on_a_slope(x, y, low)))
        t._pose = lambda ptr: {'position': [0.0, 0.0, low + 279], 'rotation': [0, 0, 0], 'scale': 1.0}
        out = t.check('FF0014B9')
        self.assertEqual(out['issues'], [])
        self.assertNotIn('missed', out['footprint'])
        self.assertAlmostEqual(out['footprint']['sunk'], 2 * STEEP25 * (316 - 4), places=1)

    def test_a_shack_resting_low_does_not_move_when_snapped_again(self):
        low = 100 - STEEP25 * (316 - 4)
        t, _ = tools(SHACK, ColumnRays(lambda x, y: self.sunk_on_a_slope(x, y, low)))
        t._pose = lambda ptr: {'position': [0.0, 0.0, low + 279], 'rotation': [0, 0, 0], 'scale': 1.0}
        moves = []
        t.adjust = lambda ref, move=None, rotate=None: moves.append(move) or {'ref': ref}
        t.snap('FF0014B9')
        self.assertAlmostEqual(moves[0][2], 0, places=1)

    def test_a_tree_over_the_roof_is_not_ground(self):
        # with the object present, a surface over its top at a sample is overhead (a canopy, an overhang)
        def column(x, y):
            hits = [(100.0, None), (379.0 + 279, REF), (101.6, REF)] if abs(x) <= 316 and abs(y) <= 397 else \
                [(100.0, None)]
            if x > 200:
                hits.append((900.0, 0x7EE))
            return hits
        t, _ = tools(SHACK, ColumnRays(column))
        t._pose = lambda ptr: {'position': [0.0, 0.0, 379.0], 'rotation': [0, 0, 0], 'scale': 1.0}
        out = t.check('FF0014B9')
        self.assertEqual(out['issues'], [])
        self.assertEqual(out['footprint']['groundMax'], 100)


class AtTests(unittest.TestCase):
    def test_flat_ground_is_the_same_as_before(self):
        t, placed = tools(SHACK, TerrainRays(c=100.0))
        out = t.at('BASE', position=[0, 0, 400], snap=True)
        self.assertAlmostEqual(placed[0]['position'][2], 100 + 279)
        self.assertEqual(out['footprint']['spread'], 0)

    def test_on_a_slope_the_lowest_corner_carries_it(self):
        rays = TerrainRays(a=SLOPE, c=100.0)
        t, placed = tools(SHACK, rays)
        out = t.at('BASE', position=[0, 0, 400], snap=True)
        low = 100 - SLOPE * (316 - 4)                 # the downhill edge, pulled in 4 units
        self.assertAlmostEqual(placed[0]['position'][2], low + 279, places=2)
        fp = out['footprint']
        self.assertAlmostEqual(fp['groundMin'], low, places=1)
        self.assertAlmostEqual(fp['sunk'], 2 * SLOPE * (316 - 4), places=1)
        self.assertEqual(fp['floats'], 0)
        self.assertEqual(fp['rest'], 'low')

    def test_the_footprint_is_one_batched_cast_per_layer_pass(self):
        rays = TerrainRays(a=SLOPE, c=100.0)
        t, _ = tools(SHACK, rays)
        out = t.at('BASE', position=[0, 0, 400], snap=True)
        self.assertGreater(out['footprint']['samples'], 9)
        self.assertLessEqual(out['footprint']['samples'], 64)
        self.assertEqual(rays.calls[0], out['footprint']['samples'])     # every sample in the first call

    def test_a_boulder_under_the_centre_is_not_the_seat(self):
        rays = TerrainRays(c=100.0, boxes=[(-60, 60, -60, 60, 240.0, 0x77)])
        t, placed = tools(SHACK, rays)
        t.at('BASE', position=[0, 0, 400], snap=True)
        self.assertAlmostEqual(placed[0]['position'][2], 100 + 279)

    def test_a_cliff_under_one_edge_is_an_overhang_not_a_burial(self):
        rays = TerrainRays(c=100.0, drop=(300, 600.0))   # past x=300 the ground falls 600
        t, placed = tools(SHACK, rays)
        out = t.at('BASE', position=[0, 0, 400], snap=True)
        self.assertAlmostEqual(placed[0]['position'][2], 100 + 279)
        self.assertTrue(out['footprint']['overhangs'])
        self.assertIn('overhang', out['footprint']['warning'])

    def test_a_low_step_under_one_side_is_rested_on_not_overhung(self):
        # a 100-unit terrace step under a third of it: sinking the high side 100 (18% of its height) beats leaving
        # that third floating 100 over the step
        rays = TerrainRays(c=100.0, drop=(100, 100.0))
        t, placed = tools(SHACK, rays)
        out = t.at('BASE', position=[0, 0, 400], snap=True)
        self.assertAlmostEqual(placed[0]['position'][2], 0 + 279)
        self.assertEqual(out['footprint']['overhangs'], [])

    def test_rest_centre_keeps_the_old_seat_and_reports_the_float(self):
        t, placed = tools(SHACK, TerrainRays(a=SLOPE, c=100.0))
        out = t.at('BASE', position=[0, 0, 400], snap=True, rest='centre')
        self.assertAlmostEqual(placed[0]['position'][2], 100 + 279, places=2)
        self.assertAlmostEqual(out['footprint']['floats'], SLOPE * (316 - 4), places=1)

    def test_rest_high_sinks_nothing(self):
        t, placed = tools(SHACK, TerrainRays(a=SLOPE, c=100.0))
        out = t.at('BASE', position=[0, 0, 400], snap=True, rest='high')
        self.assertAlmostEqual(placed[0]['position'][2], 100 + SLOPE * (316 - 4) + 279, places=2)
        self.assertEqual(out['footprint']['sunk'], 0)

    def test_the_heading_turns_the_footprint(self):
        # turned 90 degrees, the long side (794) runs along x, across the slope
        t, _ = tools(SHACK, TerrainRays(a=SLOPE, c=100.0))
        out = t.at('BASE', position=[0, 0, 400], rotation=[0, 0, 90], snap=True)
        self.assertAlmostEqual(out['footprint']['sunk'], 2 * SLOPE * (397 - 4), places=1)

    def test_scale_grows_the_footprint(self):
        t, _ = tools(SHACK, TerrainRays(a=SLOPE, c=100.0))
        out = t.at('BASE', position=[0, 0, 400], scale=2.0, snap=True)
        self.assertAlmostEqual(out['footprint']['sunk'], 2 * SLOPE * (2 * 316 - 4), places=1)

    def test_a_steep_slope_warns_and_suggests_a_heading(self):
        steep = math.tan(math.radians(20))
        t, placed = tools(SHACK, TerrainRays(a=steep, c=100.0))
        out = t.at('BASE', position=[0, 0, 400], snap=True)
        self.assertTrue(placed)                                     # placed anyway: it warns, not refuses
        fp = out['footprint']
        self.assertIn('sunk', fp['warning'])
        self.assertIn(fp['suggestHeading']['heading'] % 180, (0, 90))
        # along the slope the narrow side (632) faces downhill: less spread than heading 90
        self.assertEqual(fp['suggestHeading']['heading'] % 180, 0)
        self.assertLess(fp['suggestHeading']['spread'], fp['spread'] + 1e-6)

    def test_a_slope_deeper_than_the_object_is_refused(self):
        cliff = math.tan(math.radians(50))
        t, placed = tools(SHACK, TerrainRays(a=cliff, c=100.0))
        with self.assertRaises(fnv_create.LiveError) as cm:
            t.at('BASE', position=[0, 0, 400], snap=True)
        self.assertEqual(cm.exception.code, 'TOO_STEEP')
        self.assertEqual(placed, [])

    def test_a_small_object_keeps_one_ray(self):
        rays = TerrainRays(a=SLOPE, c=100.0)
        t, placed = tools(CRATE, rays)
        out = t.at('BASE', position=[0, 0, 400], snap=True)
        self.assertNotIn('footprint', out)
        self.assertEqual(max(rays.calls), 1)
        self.assertAlmostEqual(placed[0]['position'][2], 100.0)

    def test_an_actor_keeps_one_ray(self):
        rays = TerrainRays(a=SLOPE, c=100.0)
        t, placed = tools(SHACK, rays, form_type=fnv_create.ACTOR_BASE_TYPES[0])
        out = t.at('BASE', position=[0, 0, 400], snap=True)
        self.assertNotIn('footprint', out)
        self.assertEqual(max(rays.calls), 1)

    def test_array_reports_each_footprint_and_skips_too_steep_ones(self):
        cliff = math.tan(math.radians(50))
        t, placed = tools(SHACK, TerrainRays(a=cliff, c=100.0))
        out = t.array('BASE', origin=[0, 0, 400], count=2, spacing=1000, snap=True)
        self.assertEqual(placed, [])
        self.assertEqual(len(out['skipped']), 2)
        self.assertEqual(out['skipped'][0]['code'], 'TOO_STEEP')


class SnapRefTests(unittest.TestCase):
    def snap(self, rays, z, rotation=(0, 0, 0), align=False, rest=None):
        t, _ = tools(SHACK, rays)
        t._pose = lambda ptr: {'position': [0.0, 0.0, z], 'rotation': list(rotation), 'scale': 1.0}
        moves = []
        t.adjust = lambda ref, move=None, rotate=None: moves.append((move, rotate)) or {'ref': ref}
        return t.snap('FF0014B9', align=align, rest=rest), moves

    def test_snap_on_a_slope_rests_on_the_lowest_ground(self):
        out, moves = self.snap(TerrainRays(a=SLOPE, c=100.0), 100.0)
        low = 100 - SLOPE * (316 - 4)
        self.assertAlmostEqual(moves[0][0][2], low + 279 - 100, places=2)
        self.assertEqual(out['footprint']['rest'], 'low')

    def test_a_snap_too_steep_does_not_move(self):
        out, moves = self.snap(TerrainRays(a=math.tan(math.radians(50)), c=100.0), 100.0)
        self.assertFalse(out['snapped'])
        self.assertEqual(moves, [])
        self.assertIn('steep', out['note'])

    def test_an_actor_ref_keeps_one_ray(self):
        rays = TerrainRays(a=SLOPE, c=100.0)
        t, _ = tools(SHACK, rays, form_type=fnv_create.ACTOR_BASE_TYPES[0])
        t._pose = lambda ptr: {'position': [0.0, 0.0, 100.0], 'rotation': [0, 0, 0], 'scale': 1.0}
        t.adjust = lambda ref, move=None, rotate=None: {'ref': ref}
        out = t.snap('FF0014B9')
        self.assertNotIn('footprint', out)
        self.assertEqual(max(rays.calls), 1)

    def test_align_fits_a_plane_through_the_footprint(self):
        out, moves = self.snap(TerrainRays(a=SLOPE, c=100.0), 100.0, align=True)
        self.assertAlmostEqual(out['slopeDegrees'], 10.0, places=1)
        self.assertIn('footprint', out['how'])
        # laid on the plane its bottom is 279 under the origin along the normal: 279 / cos 10 straight up (the old
        # align used 279 * cos 10, the height of the bottom's centre, and sank it ~8 units on this slope)
        lift = 279 / math.cos(math.radians(10))
        self.assertAlmostEqual(moves[0][0][2], 100 + lift - 100, places=1)


class CheckTests(unittest.TestCase):
    def check(self, rays, z, rotation=(0, 0, 0)):
        t, _ = tools(SHACK, rays)
        t._pose = lambda ptr: {'position': [0.0, 0.0, z], 'rotation': list(rotation), 'scale': 1.0}
        return t.check('FF0014B9')

    def test_a_shack_seated_by_its_centre_on_a_slope_floats_at_a_corner(self):
        out = self.check(TerrainRays(a=SLOPE, c=100.0), 100.0 + 279)       # today's farm shack
        floats = [i for i in out['issues'] if i.startswith('floats')]
        self.assertTrue(floats, out['issues'])
        self.assertIn('%.0f units' % (SLOPE * (316 - 4)), floats[0])

    def test_a_shack_laid_on_the_slope_is_judged_by_its_tilted_bottom(self):
        # align leaves it turned 10 degrees about Y (up axis (-sin 10, 0, cos 10) = the slope's normal), its origin
        # 279 / cos 10 over the plane: its bottom lies on the ground everywhere (seen live: check judged the flat
        # bottom height and called an aligned shack floating 133 units)
        out = self.check(TerrainRays(a=SLOPE, c=100.0), 100.0 + 279 / math.cos(math.radians(10)),
                         rotation=(0, 10, 0))
        self.assertEqual(out['issues'], [])
        self.assertLess(out['footprint']['floats'], 1)
        self.assertLess(out['footprint']['sunk'], 1)

    def test_an_edge_past_a_cliff_is_reported_as_an_overhang(self):
        out = self.check(TerrainRays(c=100.0, drop=(300, 600.0)), 100.0 + 279)
        self.assertTrue([i for i in out['issues'] if 'overhang' in i], out['issues'])
        self.assertFalse([i for i in out['issues'] if i.startswith('floats')], out['issues'])

    def test_a_shack_seated_low_has_no_issues_and_reports_the_sinking(self):
        low = 100 - SLOPE * (316 - 4)
        out = self.check(TerrainRays(a=SLOPE, c=100.0), low + 279)
        self.assertEqual(out['issues'], [])
        self.assertAlmostEqual(out['footprint']['sunk'], 2 * SLOPE * (316 - 4), places=1)


if __name__ == '__main__':
    unittest.main()
