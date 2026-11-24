"""Live proof of tool gap 4 (2026-11-24): fnv_anim play kf plays a KF made by fnv_asset make_kf on the player and on an
NPC, and fnv_anim reload rebuilds an actor's 3D (the player too) with the game going on.

setUpClass: quit the game, make a one-second SpecialIdle KF (the right arm raised) into the mod "fnvmcp anim live",
launch (MO2 maps a file new to a mod folder only at launch). tearDownClass turns the mod off and relaunches.
Run alone: tools/fnv_live_regress.py --suite anim (about 3 minutes: two game starts)."""
import json
import os
from pathlib import Path

from .base import LiveCase, Session

START = os.environ.get('FNV_LIVE_START', 'coc:GoodspringsSource')
MOD = 'fnvmcp anim live'
KF = 'meshes\\fnvmcp_live\\wave.kf'
KEYS = {'name': 'SpecialIdle', 'loop': False, 'length': 1.0,
        'nodes': {'Bip01 R UpperArm': {'rotation': [[0, [1, 0, 0, 0]], [0.5, [0.7071, 0, 0, 0.7071]],
                                                    [1.0, [1, 0, 0, 0]]]}}}


def must(envelope, what):
    if not envelope.get('ok'):
        raise AssertionError('%s failed: %s' % (what, json.dumps(envelope.get('error'))[:500]))
    return envelope.get('data')


class AnimLive(LiveCase):
    made = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            s = cls.session
            work = Path(s.project.state_dir) / 'fnv' / 'anim-live'
            work.mkdir(parents=True, exist_ok=True)
            (work / 'wave.json').write_text(json.dumps(KEYS), encoding='utf-8')
            if (s.call('fnv_status').get('data') or {}).get('running'):
                must(s.call('fnv_game', {'action': 'quit'}), 'quit')
            cls.made = must(s.call('fnv_asset', {'action': 'make_kf', 'path': str(work / 'wave.json'), 'out': KF,
                                                 'options': {'mod': MOD}}), 'make_kf')
            must(s.call('fnv_game', {'action': 'launch', 'start': START}), 'launch with the mod')
            must(s.call('fnv_status'), 'fnv_status')
        except BaseException:
            cls._clean_up()
            raise

    @classmethod
    def tearDownClass(cls):
        cls._clean_up()

    @classmethod
    def _clean_up(cls):
        s = cls.session or Session.get()
        from fnv_mcp import fnv_link
        s.call('fnv_game', {'action': 'quit'})
        fnv_link.disable_mod(s.project.fnv.mo2_root, s.project.fnv.mo2_profile, MOD)
        s.call('fnv_game', {'action': 'launch', 'start': START})
        s.call('fnv_status')

    def run_world(self):
        state = self.ok('fnv_window', action='unpause')['state']
        self.assertFalse(state['pauseMenuOpen'], state)

    def npc(self):
        """The nearest NPC with 3D (the nearest one may be in an unloaded cell or disabled)."""
        refs = self.ok('fnv_refs_near', radius=8000, form_type=42, limit=30)['refs']
        for r in refs:
            if str(r.get('formId', '')).upper().endswith('00000014'):
                continue
            body = self.call('fnv_physics', action='body', ref=r['formId'])
            if body.get('ok') and body['data']['chain'][0]['ptr']:
                return r['formId']
        self.skipTest('no NPC with 3D within 8000 units of %s' % START)

    def sequence(self, address):
        """(state, last time) of an NiControllerSequence: state +0x44 (1 animating), m_fLastTime +0x34."""
        base = int(address, 16)
        state = self.ok('fnv_mem_read', target='0x%08X' % (base + 0x44), as_type='u32')
        time = self.ok('fnv_mem_read', target='0x%08X' % (base + 0x34), as_type='f32')
        return state.get('value'), time.get('value')

    def assert_plays(self, ref):
        # live run 2026-11-24: fnv_anim state lists the sequences of the body sections, not one the controller
        # manager runs, so the sequence itself is read: animating, and its clock moving
        self.run_world()
        out = self.ok('fnv_anim', action='play', ref=ref, kf=KF)
        self.assertEqual(out['kf'], KF[len('meshes\\'):], out)
        self.assertEqual((out['group'] or {}).get('type'), 2, out)          # SpecialIdle
        state, first = self.sequence(out['sequence'])
        self.ok('fnv_wait_for', frames=10)
        state_after, later = self.sequence(out['sequence'])
        self.assertEqual(state, 1, 'not animating: %s (play gave %s)' % (state, out))
        self.assertNotEqual(first, later, 'the sequence clock did not move: %s -> %s' % (first, later))
        again = self.ok('fnv_anim', action='play', ref=ref, kf=KF)
        self.assertFalse(again['loaded'], again)                # the second play reuses the sequence
        self.assertEqual(again['sequence'], out['sequence'])

    def test_10_a_made_kf_plays_on_the_player(self):
        self.assertEqual(self.made['sequence'], 'SpecialIdle', self.made)
        self.assert_plays('player')

    def test_20_a_made_kf_plays_on_an_npc(self):
        self.assert_plays(self.npc())

    def assert_rebuilt(self, ref):
        self.run_world()
        out = self.ok('fnv_anim', action='reload', ref=ref)
        self.assertTrue(out['queued'], out)
        self.ok('fnv_wait_for', frames=30)
        status = self.ok('fnv_status')
        self.assertTrue(status['session']['ready'] and status['phase'] in ('in_game', 'in_menu', 'paused_menu'),
                        status.get('phase'))
        body = self.ok('fnv_physics', action='body', ref=ref)
        self.assertTrue(body['chain'][0]['ptr'], 'no 3D after the rebuild: %s' % body)
        self.assertTrue(self.ok('fnv_anim', action='state', ref=ref).get('animation'), 'no animation after rebuild')

    def test_30_the_player_3d_is_rebuilt(self):
        self.assert_rebuilt('player')

    def test_40_an_npc_3d_is_rebuilt(self):
        self.assert_rebuilt(self.npc())
