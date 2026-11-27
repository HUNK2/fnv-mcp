"""fnv_shader export (SHADER-PLAN Task 6): screen effects into an xNVSE plugin source (SFX_BAKED), engine replacements
into patched packages, sets. Test packages are built with write_sdp from test HLSL (no game content); PluginTools is a
mock."""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import json
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_layout import MO2  # noqa: E402
from fnv_mcp.fnv_link import LinkError, find_zig  # noqa: E402
from fnv_mcp.fnv_native import NativeTools  # noqa: E402
from fnv_mcp.fnv_shader_hlsl import compile_hlsl  # noqa: E402
from fnv_mcp.fnv_shader_pkg import read_sdp  # noqa: E402
from test_shader_replace import (Replace, ORIG_2A, OK_SRC, build)  # noqa: E402

TINT = '''sampler2D scene : register(s0);
uniform float4 tint : register(c2) = float4(1, 0.5, 0.25, 1);
float4 gain : register(c3);
float4 main(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(scene, uv) * tint * (gain + 1); }
'''


class Export(Replace):
    def setUp(self):
        super().setUp()
        self.plugins = mock.MagicMock()
        self.plugins.build.side_effect = self.fake_build
        self.plugins.install.return_value = {'summary': 'installed'}
        self.tools.plugins = self.plugins
        self.live.game.process.return_value = None
        self.root = Path(self.tmp.name)

    def fake_build(self, name, source=None, hooks=None):
        dll = self.state / 'fnv' / 'plugins' / name / (name + '.dll')
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_bytes(b'MZ-test')
        return {'dll': str(dll)}

    def second_package(self, skytest):
        self.write_package(self.game / 'Data' / 'Shaders', [('SKYTEST.pso', skytest)] + self.other, index=4)

    def tree(self, folder):
        return sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob('*') if p.is_file())


class PackageExport(Export):
    def test_export_patches_every_package_with_the_name(self):
        self.second_package(self.code)
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        out = self.tools.export()
        self.assertEqual(sorted(p['file'] for p in out['packages']),
                         ['fnv/shaders/export/live/Shaders/shaderpackage003.sdp',
                          'fnv/shaders/export/live/Shaders/shaderpackage004.sdp'])
        self.assertTrue(all(p['replaced'] == ['SKYTEST.pso'] for p in out['packages']))
        self.assertEqual((out['plugin'], out['skipped'], out['installed']), (None, [], False))
        self.assertEqual(out['folder'], 'fnv/shaders/export/live')
        new = build('ps_3_0', OK_SRC)
        for index in (3, 4):
            src = read_sdp(self.game / 'Data' / 'Shaders' / ('shaderpackage%03d.sdp' % index))
            dst = read_sdp(self.state / 'fnv/shaders/export/live/Shaders' / ('shaderpackage%03d.sdp' % index))
            self.assertEqual([n for n, _ in dst], [n for n, _ in src])
            for (name, a), (_n, b) in zip(src, dst):
                self.assertEqual(b, new if name == 'SKYTEST.pso' else a, name)
        self.assertNotIn('C:', json.dumps(out))

    def test_package_failing_check_is_skipped(self):
        self.second_package(build('ps_3_0', ORIG_2A + '// no tint\n'))     # no c0 there: the replacement needs it
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        out = self.tools.export()
        self.assertEqual([p['file'] for p in out['packages']], ['fnv/shaders/export/live/Shaders/shaderpackage003.sdp'])
        (skip,) = out['skipped']
        self.assertEqual((skip['package'], skip['name']), ('shaderpackage004.sdp', 'SKYTEST.pso'))
        self.assertIn('REGISTER', [p['code'] for p in skip['problems']])
        self.assertFalse((self.state / 'fnv/shaders/export/live/Shaders/shaderpackage004.sdp').exists())

    def test_replacements_survive_a_game_restart_for_export(self):
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        self.live.game_identity.return_value = {'pid': 1, 'started': '7'}
        self.tools.state()                                  # the restart is noticed: the live view is cleared
        self.assertEqual(self.manifest()['replacements'], {})
        out = self.tools.export()
        self.assertEqual(out['packages'][0]['replaced'], ['SKYTEST.pso'])

    def test_direct_install_never_writes_game_folder(self):
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        before = self.tree(self.game)
        out = self.tools.export(install=True)
        self.assertEqual(self.tree(self.game), before)
        self.assertTrue(out['installed'] is False)
        self.assertIn('copy', out['note'].lower())
        self.assertNotIn('C:', json.dumps(out))

    def test_mo2_install_copies_into_the_mod_and_enables_it(self):
        mo2 = self.root / 'mo2'
        (mo2 / 'profiles' / 'dev').mkdir(parents=True)
        (mo2 / 'profiles' / 'dev' / 'modlist.txt').write_text('# header\n+other\n', encoding='utf-8')
        self.live.game.layout.return_value = self.layout(kind=MO2, mo2_root=mo2, profile='dev')
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        out = self.tools.export(set='film', install=True)
        mod = mo2 / 'mods' / 'fnvmcp shaders film' / 'Shaders' / 'shaderpackage003.sdp'
        self.assertTrue(mod.exists())
        self.assertTrue(out['installed'])
        self.assertIn('+fnvmcp shaders film', (mo2 / 'profiles/dev/modlist.txt').read_text('utf-8').splitlines())
        # a second export patches the vanilla package, not the earlier patched copy in the mod
        self.assertEqual(self.tools._holder(self.layout(kind=MO2, mo2_root=mo2, profile='dev'), 3).parent.name,
                         'fnvmcp shaders film')
        again = self.tools.export(set='film')
        self.assertEqual(again['packages'][0]['replaced'], ['SKYTEST.pso'])

    def test_install_refused_while_the_game_runs(self):
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        self.live.game.process.return_value = {'pid': 1}
        with self.assertRaises(ConvError) as cm:
            self.tools.export(install=True)
        self.assertEqual(cm.exception.code, 'GAME_RUNNING')
        self.assertFalse((self.state / 'fnv/shaders/export').exists())


class EffectExport(Export):
    def test_effects_become_plugin_source(self):
        self.tools.effect_add('tint', TINT, order=5, params={'gain': [0.5]}, photo=False)
        self.tools.effect_add('plain', TINT, photo=False)
        out = self.tools.export()
        self.assertEqual(out['plugin']['name'], 'fnvmcp_shaders_live')
        self.assertEqual(out['packages'], [])
        (call,) = self.plugins.build.call_args_list
        self.assertEqual(call[0][0], 'fnvmcp_shaders_live')
        source = call[1].get('source') or call[0][1]
        self.assertTrue(source.startswith('#define SFX_BAKED 1\n'))
        self.assertEqual(call[1].get('hooks'), ['0x0086BA10'])
        self.assertIn('fnvmcp_module_init', source)
        header = (self.state / 'fnv/shaders/export/live/sfx_baked.h').read_text('utf-8')
        self.assertIn(header.strip(), source)
        self.assertEqual(len(re.findall(r'^static const unsigned char sfx_baked_code_\d+\[\]', header, re.M)), 2)
        self.assertIn('#define SFX_BAKED_COUNT 2', header)
        code = compile_hlsl(TINT, 'ps_3_0')['bytecode']
        body = re.search(r'sfx_baked_code_0\[\]\s*=\s*\{(.*?)\};', header, re.S).group(1)
        self.assertEqual(bytes(int(x, 16) for x in re.findall(r'0x([0-9A-Fa-f]{2})', body)), code)
        self.assertIn('{3, {5.000000000e-01f, 0.000000000e+00f, 0.000000000e+00f, 0.000000000e+00f}}', header)
        # the CTAB default of `tint` (c2, never set) is baked
        self.assertIn('{2, {1.000000000e+00f, 5.000000000e-01f, 2.500000000e-01f, 1.000000000e+00f}}', header)
        self.assertEqual(out['plugin']['dll'], 'fnv/plugins/fnvmcp_shaders_live/fnvmcp_shaders_live.dll')
        self.assertNotIn('C:', json.dumps(out))

    def test_disabled_effects_are_not_baked(self):
        self.tools.effect_add('tint', TINT, photo=False)
        self.tools.effect_add('off', TINT, photo=False)
        self.tools.effect_set('off', enabled=False, photo=False)
        self.tools.export()
        header = (self.state / 'fnv/shaders/export/live/sfx_baked.h').read_text('utf-8')
        self.assertIn('#define SFX_BAKED_COUNT 1', header)

    def test_saved_sources_still_export_after_a_restart(self):
        self.tools.effect_add('tint', TINT, photo=False)
        self.live.game_identity.return_value = {'pid': 1, 'started': '7'}
        self.tools.state()
        self.assertEqual(self.manifest()['effects'], {})
        self.tools.export()
        header = (self.state / 'fnv/shaders/export/live/sfx_baked.h').read_text('utf-8')
        self.assertIn('#define SFX_BAKED_COUNT 1', header)

    def test_plugin_install(self):
        self.tools.effect_add('tint', TINT, photo=False)
        out = self.tools.export(set='film', install=True)
        self.plugins.install.assert_called_once_with('fnvmcp_shaders_film')
        self.assertTrue(out['installed'])

    def test_baked_source_builds_with_zig(self):
        try:
            find_zig(None)
        except LinkError:
            self.skipTest('no zig found')
        self.tools.effect_add('tint', TINT, order=2, params={'gain': [0.5]}, photo=False)
        self.tools.export()
        source = self.plugins.build.call_args[1].get('source') or self.plugins.build.call_args[0][1]
        with tempfile.TemporaryDirectory() as tmp:
            result = NativeTools(SimpleNamespace(project=SimpleNamespace(state_dir=tmp))).build(source, 'baked_check')
        self.assertTrue(result['dll'].endswith('.dll'))


class PluginBuildErrors(Export):
    def test_build_failure_leaks_no_user_path(self):
        home = 'C:' + chr(92) + 'Users' + chr(92) + 'Some Name'            # built here: no user path in the file
        text = 'zig cc failed:\n' + home + chr(92) + 'state' + chr(92) + 'x.c:3:1: error: bad'
        self.plugins.build.side_effect = ConvError('BUILD_FAILED', text, {'stderr': text})
        self.tools.effect_add('tint', TINT, photo=False)
        with self.assertRaises(ConvError) as cm:
            self.tools.export()
        self.assertEqual(cm.exception.code, 'BUILD_FAILED')
        self.assertNotIn('Users' + chr(92), str(cm.exception))
        self.assertNotIn('Some Name', json.dumps(cm.exception.evidence) + str(cm.exception))
        self.assertIn('error: bad', str(cm.exception))

    def test_install_failure_leaks_no_user_path(self):
        self.plugins.install.side_effect = ConvError('NOT_BUILT', 'missing C:/' + 'Users/Some Name/x.dll')
        self.tools.effect_add('tint', TINT, photo=False)
        with self.assertRaises(ConvError) as cm:
            self.tools.export(install=True)
        self.assertNotIn('Some Name', str(cm.exception))

    def test_real_plugin_build_of_the_generated_source(self):
        """PluginTools.build for real (includes check, plugin runtime, plan header, zig); only the prologue plan of
        the device-lost hook, which needs the game image, is stubbed."""
        try:
            find_zig(None)
        except LinkError:
            self.skipTest('no zig found')
        from fnv_mcp.fnv_plugin_build import PluginTools
        self.tools.effect_add('tint', TINT, order=2, params={'gain': [0.5]}, photo=False)
        self.tools.plugins = PluginTools(self.live)
        row = '    {0x0086BA10u, 5u, {0}, 0u},'
        with mock.patch.object(PluginTools, '_plans', return_value=([row], [])):
            out = self.tools.export()
        self.assertEqual(out['plugin']['name'], 'fnvmcp_shaders_live')
        self.assertTrue((self.state / out['plugin']['dll']).is_file())
        self.assertTrue((self.state / out['folder'] / 'fnvmcp_shaders_live.dll').is_file())


class SetsAndEmpty(Export):
    def test_set_saves_then_exports(self):
        self.tools.effect_add('tint', TINT, photo=False)
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        out = self.tools.export(set='film')
        sets = self.state / 'fnv/shaders/sets/film'
        saved = json.loads((sets / 'manifest.json').read_text('utf-8'))
        (effect,) = saved['effects'].values()
        self.assertEqual((sets / effect['source_file']).read_text('utf-8'), TINT)
        self.assertTrue((sets / effect['bytecode_file']).exists())
        self.assertEqual(out['folder'], 'fnv/shaders/export/film')
        self.assertEqual(out['plugin']['name'], 'fnvmcp_shaders_film')
        self.assertEqual(out['packages'][0]['replaced'], ['SKYTEST.pso'])     # both kinds from one export
        self.assertTrue((self.state / 'fnv/shaders/export/film/Shaders/shaderpackage003.sdp').exists())

    def test_saved_set_exports_without_the_live_one(self):
        self.tools.effect_add('tint', TINT, photo=False)
        self.tools.export(set='film')
        self.tools.effect_remove('all', photo=False)
        shutil.rmtree(self.state / 'fnv/shaders/live')
        out = self.tools.export(set='film')
        self.assertEqual(out['plugin']['name'], 'fnvmcp_shaders_film')

    def test_bad_set_name(self):
        with self.assertRaises(ConvError) as cm:
            self.tools.export(set='../x')
        self.assertEqual(cm.exception.code, 'BAD_NAME')

    def test_nothing_to_export(self):
        with self.assertRaises(ConvError) as cm:
            self.tools.export()
        self.assertEqual(cm.exception.code, 'NOTHING_TO_EXPORT')
        self.assertFalse((self.state / 'fnv/shaders/sets').exists())


if __name__ == '__main__':
    unittest.main()
