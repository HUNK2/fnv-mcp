"""Live proof of tool gap 5 (2026-11-24): fnv_physics set moves a Havok body (velocity, spin, impulse) and pushes an
actor through its character controller.

A stack of caps is placed in front of the player (tearDown's undo removes it) and given a velocity up; it must rise.
The player is pushed sideways; it must move. Run alone: tools/fnv_live_regress.py --suite physics."""
import math

from .base import CAPS, LiveCase


def position(data):
    p = data.get('position') or {}
    return [p['x'], p['y'], p['z']] if isinstance(p, dict) else list(p)


class PhysicsLive(LiveCase):
    def unpause(self):
        """The world must run: with the window out of focus the pause menu opens and Havok stops (live run
        2026-11-24: a set velocity stayed at 600 and the caps hung in the air until fnv_window unpause)."""
        state = self.ok('fnv_window', action='unpause')['state']
        self.assertFalse(state['pauseMenuOpen'], state)

    def place_caps(self):
        self.unpause()
        placed = self.ok('fnv_place', action='at', base=CAPS, near='player', offset=[0, 150, 20])
        ref = placed['ref']
        self.ok('fnv_wait_for', seconds=2, clock='real')          # let it fall and settle
        body = self.ok('fnv_physics', action='body', ref=ref)
        self.assertIn('velocity', body, 'the placed caps have no Havok body: %s' % body)
        return ref

    def test_10_velocity_up_lifts_a_body(self):
        ref = self.place_caps()
        before = position(self.ok('fnv_ref', ref=ref))
        self.unpause()
        out = self.ok('fnv_physics', action='set', ref=ref, velocity=[0, 0, 600])
        self.assertIn('bhkRigidBody::SetLinearVelocity 0x005615D0', out['calls'], out)
        self.ok('fnv_wait_for', frames=5)
        after = position(self.ok('fnv_ref', ref=ref))
        self.assertGreater(after[2] - before[2], 10, 'the caps did not rise: %s -> %s (set gave %s)'
                           % (before, after, out))

    def test_20_spin_reads_back(self):
        # in the air: on the ground a cap's spin is gone by the read (live run 2026-11-24, 0.025 rad/s read back)
        ref = self.place_caps()
        self.unpause()
        out = self.ok('fnv_physics', action='set', ref=ref, velocity=[0, 0, 400], angular_velocity=[0, 0, 20])
        spin = out.get('angularVelocity') or [0, 0, 0]
        self.assertGreater(abs(spin[2]), 1, 'no spin read back: %s' % out)

    def test_30_an_impulse_lifts_a_body(self):
        ref = self.place_caps()
        before = position(self.ok('fnv_ref', ref=ref))
        self.unpause()
        out = self.ok('fnv_physics', action='set', ref=ref, impulse=[0, 0, 800])
        self.assertGreater(out['inverseMass'], 0, out)
        self.ok('fnv_wait_for', frames=5)
        after = position(self.ok('fnv_ref', ref=ref))
        self.assertGreater(after[2] - before[2], 10, 'the impulse did not lift the caps: %s -> %s (set gave %s)'
                           % (before, after, out))

    def test_40_a_push_moves_the_player(self):
        self.unpause()
        before = position(self.ok('fnv_player'))
        out = self.ok('fnv_physics', action='set', ref='player', velocity=[400, 0, 0])
        self.assertGreater(out['seconds'], 0, out)
        self.unpause()
        self.ok('fnv_wait_for', seconds=2, clock='real')
        after = position(self.ok('fnv_player'))
        moved = math.hypot(after[0] - before[0], after[1] - before[1])
        self.assertGreater(moved, 50, 'the player moved %.1f units: %s -> %s (push %s)' % (moved, before, after, out))
