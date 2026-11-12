"""Plan 2 W3 hands: key names, the route planner (around a wall, through a door, around a spot marked blocked),
activation refusals, menu clicks by label. Fakes only; no game."""
import math
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_hands  # noqa: E402
from geck_mcp.fnv_hands import HandsTools, scan_code  # noqa: E402
from geck_mcp.fnv_live import LiveError  # noqa: E402


def segments_cross(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (orient(a, b, c) * orient(a, b, d) < 0) and (orient(c, d, a) * orient(c, d, b) < 0)


class WallRays:
    """A flat floor with no pickable surface (as in Prospector Saloon) and wall segments; a door segment hits its
    door reference."""

    def __init__(self, walls, doors=()):
        self.walls = walls
        self.doors = doors     # [(segment, door ptr)]
        self.casts = 0

    def cast(self, segments, layer=0x26, refs=False):
        out = []
        for a, b in segments:
            self.casts += 1
            if a[0] == b[0] and a[1] == b[1]:
                out.append({'hit': False})            # floor rays: nothing (the planner keeps the height)
                continue
            hit = None
            for w in self.walls:
                if segments_cross(a[:2], b[:2], w[0], w[1]):
                    hit = {'hit': True, 'ref': 0x99, 'point': a}
            for w, ptr in self.doors:
                if segments_cross(a[:2], b[:2], w[0], w[1]):
                    hit = hit or {'hit': True, 'ref': ptr, 'point': a}
            out.append(hit or {'hit': False})
        return out


def hands_with(rays):
    h = HandsTools(live=None, sleep=lambda s: None)
    h.rays = rays
    return h


class KeyTests(unittest.TestCase):
    def test_names(self):
        self.assertEqual(scan_code('E'), 0x12)
        self.assertEqual(scan_code('space'), 57)
        self.assertEqual(scan_code('F1'), 59)
        self.assertEqual(scan_code('1'), 2)
        self.assertEqual(scan_code('0'), 11)
        self.assertEqual(scan_code('Escape'), 1)
        with self.assertRaises(ValueError):
            scan_code('NoSuchKey')


class PlannerTests(unittest.TestCase):
    def test_open_floor_goes_straight(self):
        h = hands_with(WallRays([]))
        path, stats = h.plan((0, 0, 0), (0, 640, 0), [])
        self.assertTrue(stats['reachedGoal'])
        route = h._simplify(path)
        self.assertEqual(len(route), 2)                      # start and goal, nothing in between

    def test_routes_around_a_wall(self):
        wall = [((-200, 300), (200, 300))]                   # a wall across the way, open past x = 200
        h = hands_with(WallRays(wall))
        path, stats = h.plan((0, 0, 0), (0, 640, 0), [])
        self.assertTrue(stats['reachedGoal'])
        for a, b in zip(path, path[1:]):
            self.assertFalse(segments_cross(a[:2], b[:2], *wall[0]))
        self.assertTrue(any(abs(p[0]) >= 200 for p in path))

    def test_a_door_in_the_wall_is_passable(self):
        wall = [((-400, 300), (-32, 300)), ((32, 300), (400, 300))]
        door = [(((-32, 300), (32, 300)), 0x55)]
        h = hands_with(WallRays(wall, door))
        doors = [{'ptr': 0x55, 'formId': '00000055', 'position': (0, 320, 0), 'loadDoor': False}]
        path, stats = h.plan((0, 0, 0), (0, 640, 0), doors)
        self.assertTrue(stats['reachedGoal'])
        self.assertIn(0x55, [p[3] for p in path])

    def test_avoid_points_close_the_way(self):
        h = hands_with(WallRays([]))
        straight, _ = h.plan((0, 0, 0), (0, 640, 0), [])
        avoided, _ = h.plan((0, 0, 0), (0, 640, 0), [], avoid=[(0, 320)])
        self.assertTrue(any(abs(p[0]) < 1 and abs(p[1] - 320) < 1 for p in straight))
        self.assertFalse(any(math.dist(p[:2], (0, 320)) < fnv_hands.STEP * 0.75 for p in avoided))

    def test_wide_check_sees_a_post_the_centre_ray_misses(self):
        post = [((10, 200), (30, 200))]                      # a post 10 to 30 units to the side of the line
        h = hands_with(WallRays(post))
        clear, used = h._wide([((0, 0, 45), (0, 400, 45))])
        self.assertEqual(used, 3)
        self.assertEqual(clear, [False])


class DoorAheadTests(unittest.TestCase):
    def test_the_door_in_front_is_picked_not_the_one_behind_or_far(self):
        doors = [{'ptr': 1, 'position': (0, 100, 0), 'loadDoor': False},     # ahead, close
                 {'ptr': 2, 'position': (0, -60, 0), 'loadDoor': False},     # behind
                 {'ptr': 3, 'position': (0, 400, 0), 'loadDoor': False},     # ahead, too far
                 {'ptr': 4, 'position': (0, 50, 0), 'loadDoor': True}]       # a load door
        self.assertEqual(HandsTools._door_ahead((0, 0, 0), (0, 600, 0), doors, set())['ptr'], 1)
        self.assertIsNone(HandsTools._door_ahead((0, 0, 0), (0, 600, 0), doors, {1}))


class FakeAgent:
    def __init__(self, menus=None, tiles=None):
        self._menus = menus or {'inMenuMode': False, 'visible': [], 'menuStack': [], 'activeMenu': None}
        self.tiles = tiles or {}

    def menus(self):
        return self._menus

    def _tile_name(self, ptrs):
        return {p: self.tiles[p]['name'] for p in ptrs}

    def _children(self, t):
        return self.tiles[t].get('children', [])

    def _tile_strings(self, ptrs, trait=0xFC4):
        return {p: self.tiles[p].get('text') for p in ptrs}

    def _tile_values(self, ptrs):
        return {p: ({fnv_hands.TRAIT_ID: (self.tiles[p]['id'], 0)} if 'id' in self.tiles[p] else {}) for p in ptrs}


class FakeLink:
    def __init__(self):
        self.sent = []

    def one(self, *op, **kw):
        self.sent.append(op)
        return {'ok': True}


class FakeLive:
    def __init__(self, mem=None):
        self.mem = mem or {}
        self.link = FakeLink()
        self.journal = type('J', (), {'add': lambda s, e: e})()

    def u32s(self, addresses):
        return [self.mem.get(a, 0) for a in addresses]

    def reads(self, spans):
        out = []
        for a, n in spans:
            if a == fnv_hands.MENU_ARRAY + 4:
                out.append(struct.pack('<IHH', 0x5000, 0, 200))
            else:
                out.append(bytes(n))
        return out

    def ready(self):
        return {}

    def changing(self, reason):
        return None


class ClickTests(unittest.TestCase):
    def test_button_by_label_clicks_the_nearest_tile_with_an_id(self):
        tiles = {0x100: {'name': 'ContainerMenu', 'children': [0x101]},
                 0x101: {'name': 'CM_TakeAllButton', 'id': 10, 'children': [0x102]},
                 0x102: {'name': 'text', 'text': 'Take All'}}
        idx = 1008 - 0x3E9
        mem = {0x5000 + 4 * idx: 0x100, 0x100 + fnv_hands.TILEMENU_MENU: 0x200, 0x200: 0x300, 0x300 + 0x0C: 0x400}
        menus = {'inMenuMode': True, 'visible': [{'id': 1008, 'name': 'ContainerMenu'}], 'menuStack': [],
                 'activeMenu': {'id': 1008}}
        h = HandsTools(FakeLive(mem), agent=FakeAgent(menus, tiles), sleep=lambda s: None)
        out = h.click(label='take all')
        self.assertEqual(out['clicked'], 'ContainerMenu/CM_TakeAllButton')
        call = [s for s in h.live.link.sent if s[0] == 'call'][-1]
        self.assertEqual(int(call[1], 16), 0x400)          # the menu's vtable slot 3
        self.assertEqual(int(call[3], 16), 0x200)          # this = the menu
        self.assertEqual(int(call[5], 16), 10)             # the tile id
        self.assertEqual(int(call[6], 16), 0x101)          # the tile

    def test_click_without_a_menu_refused(self):
        h = HandsTools(FakeLive(), agent=FakeAgent(), sleep=lambda s: None)
        with self.assertRaises(LiveError) as cm:
            h.click(label='Accept')
        self.assertEqual(cm.exception.code, 'NO_MENU')

    def test_type_needs_text(self):
        h = HandsTools(FakeLive(), agent=FakeAgent(), sleep=lambda s: None)
        with self.assertRaises(LiveError) as cm:
            h.ui_do('type')
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')


if __name__ == '__main__':
    unittest.main()


class GotoArrivalTests(unittest.TestCase):
    """N16: goto to a reference arrives within the activation reach in 3D, not just within 135 units across."""

    def test_a_raised_target_needs_a_closer_stand(self):
        from geck_mcp.fnv_hands import ref_arrive_radius, ref_arrived
        player, token = (0.0, 0.0, 0.0), (135.0, 0.0, 115.0)        # the s1-play token on a raised spot
        self.assertFalse(ref_arrived(player, token), 'horizontal 135 but 177 in 3D: activate cannot reach it')
        radius = ref_arrive_radius(player, token)
        self.assertLess(radius, 90.0)
        stand = (token[0] - radius, 0.0, 0.0)
        self.assertTrue(ref_arrived(stand, token))

    def test_a_target_on_the_floor_keeps_the_usual_radius(self):
        from geck_mcp.fnv_hands import ARRIVE, ref_arrive_radius, ref_arrived
        self.assertEqual(ref_arrive_radius((0.0, 0.0, 0.0), (500.0, 0.0, 10.0)), ARRIVE)
        self.assertTrue(ref_arrived((0.0, 0.0, 0.0), (90.0, 0.0, 10.0)))

    def test_an_unreachable_height_still_walks_close(self):
        from geck_mcp.fnv_hands import ref_arrive_radius
        self.assertGreaterEqual(ref_arrive_radius((0.0, 0.0, 0.0), (300.0, 0.0, 400.0)), 30.0)


class GotoSettleTests(unittest.TestCase):
    """goto must not return while its queued forward holds still move the player (s1-play: the player slid past
    the token while activate aimed at it)."""

    def test_settle_clears_queued_input_and_waits_for_the_player_to_stop(self):
        from geck_mcp import fnv_hands
        h = HandsTools(live=None, sleep=lambda s: None)
        positions = iter([(0, 0, 0), (10, 0, 0), (18, 0, 0), (20, 0, 0), (20.2, 0, 0), (20.2, 0, 0)] + [(20.2, 0, 0)] * 20)
        cleared, waited = [], []
        h._player = lambda: 1
        h._pos = lambda ptr: next(positions)
        h._frames = lambda n: waited.append(n)
        h._clear_input = lambda: cleared.append(1)
        final = h._settle()
        self.assertEqual(cleared, [1])
        self.assertEqual(final, (20.2, 0, 0))
        self.assertLess(len(waited), 12)

    def test_settle_gives_up_on_a_player_that_keeps_moving(self):
        h = HandsTools(live=None, sleep=lambda s: None)
        n = iter(range(1000))
        h._player = lambda: 1
        h._pos = lambda ptr: (next(n) * 5.0, 0, 0)
        h._frames = lambda k: None
        h._clear_input = lambda: None
        h._settle(max_rounds=10)       # returns, never loops forever


class PickupNoteTests(unittest.TestCase):
    """A taken item's reference is marked deleted (form flag 0x20), not disabled: activate must say it was taken."""

    def test_taken_item_is_reported(self):
        from geck_mcp.fnv_hands import pickup_note
        self.assertIsNone(pickup_note(0x00000400, 0x00000400))
        self.assertIn('taken', pickup_note(0x00000400, 0x00000420))
        self.assertIsNone(pickup_note(0x00000420, 0x00000420), 'already deleted before: not this press')
        self.assertIsNone(pickup_note(None, 0x20))
