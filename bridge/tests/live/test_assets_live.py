"""Live proof of tool gaps 3 (2026-11-24): a mesh made by fnv_asset make_nif is drawn by the game, and a texture
remade by make_dds shows after fnv_asset reload.

setUpClass: quit the game, make a 1 m cube (.glb with an embedded red texture) into the mod "fnvmcp asset live"
with make_nif, build a plugin (FnvmcpAssetLive.esp) with one STAT naming it, install it into the same mod, launch.
Each test places the cube in front of the player with fnv_place and photographs it with fnv_photo (tearDown's undo
removes it). tearDownClass turns the mod off and relaunches.

Run alone: tools/fnv_live_regress.py --suite assets (about 3 minutes: two game starts)."""
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image

from .base import LiveCase, Session

START = os.environ.get('FNV_LIVE_START', 'coc:GoodspringsSource')
PLUGIN = 'FnvmcpAssetLive.esp'
MOD = 'fnvmcp asset live'
MASTER = 'FalloutNV.esm'
MESH = 'meshes\\fnvmcp_live\\cube.nif'
TEXTURE = 'textures\\fnvmcp_live\\crate_d.dds'        # write_glb names its embedded image crate_d


def must(envelope, what):
    if not envelope.get('ok'):
        raise AssertionError('%s failed: %s' % (what, json.dumps(envelope.get('error'))[:500]))
    return envelope.get('data')


def colour_pixels(path):
    """(strongly red pixels, strongly blue pixels) of a photo: the cube is the only saturated red or blue thing in
    the Mojave scene (the pale sky is not twice as blue as it is red)."""
    img = np.asarray(Image.open(path).convert('RGB'), np.float64)
    r, g, b = img[:, :, 0], img[:, :, 1], img[:, :, 2]
    red = (r > 80) & (r > 2 * g) & (r > 2 * b)
    blue = (b > 80) & (b > 2 * g) & (b > 2 * r)
    return int(red.sum()), int(blue.sum())


class AssetsLive(LiveCase):
    built = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        try:
            cls._set_up()
        except BaseException:
            cls._clean_up()
            raise

    @classmethod
    def _set_up(cls):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from test_assets_make import cube, write_glb
        from fnv_mcp.authoring import Authoring
        s = cls.session
        work = Path(s.project.state_dir) / 'fnv' / 'asset-live'
        src = work / 'src'
        src.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (64, 64), (220, 30, 30)).save(src / 'fnvlive_cube_d.png')
        Image.new('RGB', (64, 64), (30, 30, 220)).save(src / 'blue.png')
        pos, nrm, uv, tri = cube()
        write_glb(src / 'cube.glb', pos - 0.5 * np.array([1, 0, 1]), tri, nrm, uv,
                  image_png=src / 'fnvlive_cube_d.png')
        if (s.call('fnv_status').get('data') or {}).get('running'):
            must(s.call('fnv_game', {'action': 'quit'}), 'quit')
        made = must(s.call('fnv_asset', {'action': 'make_nif', 'path': str(src / 'cube.glb'), 'out': MESH,
                                         'options': {'mod': MOD}}), 'make_nif')
        spec = {'schema': 1, 'project_id': 'fnvmcp-asset-live', 'plugin': PLUGIN, 'masters': [MASTER],
                'records': {'LiveCube': {'type': 'STAT', 'model': 'fnvmcp_live\\cube.nif', 'bounds': made['obnd']}}}
        authoring = Authoring(s.project.model_copy(update={'plugin': PLUGIN}), spec_path=work / 'spec.json',
                              build_dir=work / 'build')
        authoring.spec_path.write_text(json.dumps(spec, indent=1), encoding='utf-8')
        receipt, _data = authoring.build(write=True)
        assert receipt['written'], receipt['validation']
        cls.built = {'made': made, 'receipt': receipt, 'src': src}
        cls.built['install'] = must(s.call('fnv_install', {'plugin_path': str(authoring.built_path),
                                                           'mod_name': MOD}), 'fnv_install')
        must(s.call('fnv_game', {'action': 'launch', 'start': START}), 'launch with the mod')
        must(s.call('fnv_status'), 'fnv_status')

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

    def cube_form(self):
        return '%s:%06X' % (PLUGIN, int(self.built['receipt']['formIds']['LiveCube'], 16) & 0xFFFFFF)

    def place_and_photograph(self, name):
        player = self.ok('fnv_player')['position']
        placed = self.ok('fnv_place', action='at', base=self.cube_form(),
                         position=[player['x'] + 200, player['y'], player['z']], snap=True)
        ref = placed.get('ref') or placed.get('formId')
        self.assertTrue(ref, placed)
        # the pause menu (StartMenu) can be open after a launch: live run 2026-11-24 refused the photo (MENU_OPEN)
        self.ok('fnv_ui_do', action='close_all')
        shot =self.ok('fnv_photo', action='ref', ref=ref, angles=['front'], name=name)
        return ref, shot['views'][0]['path']

    def test_10_the_made_files_are_loose_in_their_mod(self):
        self.assertEqual(self.built['made']['textures'], [TEXTURE, TEXTURE[:-4] + '_n.dds'])
        where = self.ok('fnv_asset', action='where', path=MESH)
        self.assertIn(MOD, json.dumps(where), where)
        check = self.ok('fnv_asset', action='nif', path=MESH)
        self.assertTrue(check['ok'], check)
        self.assertTrue(all(t['found'] for t in check['textures']), check['textures'])

    def test_20_the_game_draws_the_cube(self):
        ref, path = self.place_and_photograph('asset-live-red')
        info = self.ok('fnv_place', action='check', ref=ref)
        self.assertTrue(info, info)
        red, blue = colour_pixels(path)
        self.assertGreater(red, 2000, 'no red cube in the photo (%d red, %d blue pixels): %s' % (red, blue, path))

    def test_30_a_remade_texture_shows_after_reload(self):
        made = self.ok('fnv_asset', action='make_dds', path=str(self.built['src'] / 'blue.png'), out=TEXTURE,
                       options={'mod': MOD})
        self.assertEqual(made['format'], 'DXT1')
        _ref, before = self.place_and_photograph('asset-live-before-reload')
        self.ok('fnv_asset', action='file_log', mode='start')
        try:
            reload = self.ok('fnv_asset', action='reload', path=TEXTURE)
            _ref, after = self.place_and_photograph('asset-live-blue')
            opened = self.ok('fnv_asset', action='file_log', mode='read', folder='fnvmcp_live')
        finally:
            self.call('fnv_asset', action='file_log', mode='stop')
        self.assertGreaterEqual(reload['refs'], 1, reload)
        red, blue = colour_pixels(after)
        self.assertGreater(blue, 2000, 'the cube did not turn blue: %d red, %d blue pixels after (before %s); %s; '
                           'files opened: %s' % (red, blue, colour_pixels(before), after,
                                                 json.dumps(opened)[:1500]))
