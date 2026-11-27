"""fnv_shader engine shader listing and replacement (SHADER-PLAN Task 5): a mock native module, a test package built
with write_sdp from test HLSL (no game content), the manifest, the journal and the two undo ops."""
from pathlib import Path
from unittest import mock
import json
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_layout import DIRECT, MO2, Layout  # noqa: E402
from fnv_mcp.fnv_live import UNDO_OPS  # noqa: E402
from fnv_mcp.fnv_shader import ShaderTools  # noqa: E402
from fnv_mcp.fnv_shader_hlsl import compile_hlsl  # noqa: E402
from fnv_mcp.fnv_shader_pkg import Package, fnv1a32, write_sdp  # noqa: E402
from test_shader_effects import FakeNative, GAME, OTHER  # noqa: E402

ORIG = '''sampler2D tex : register(s0);
float4 tint : register(c0);
float4 main(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(tex, uv) * tint; }
'''
OK_SRC = '''sampler2D tex : register(s0);
float4 tint : register(c0);
float4 main(float2 uv : TEXCOORD0) : COLOR0 { return 1 - tex2D(tex, uv) * tint; }
'''
NEW_REG = '''sampler2D tex : register(s0);
float4 tint : register(c0);
float4 extra : register(c5);
float4 main(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(tex, uv) * tint + extra; }
'''
ORIG_2A = '''sampler2D tex : register(s0);
float4 main(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(tex, uv); }
'''
TABLE = 0x6000


def build(profile, source):
    out = compile_hlsl(source, profile)
    assert out['ok'], out['errors']
    return out['bytecode']


class ScanNative(FakeNative):
    """FakeNative plus the shaderfx engine-swap exports. `objects` is a list of code hashes (kind 0)."""

    def __init__(self, objects=()):
        super().__init__(loaded=True)
        self.objects = list(objects)
        self.swapped = {}

    def call(self, name, export, args=None):
        args = list(args or [])
        if export == 'sfx_scan':
            self.calls.append((export, args))
            return {'value': len(self.objects)}
        if export == 'sfx_table':
            self.calls.append((export, args))
            return {'value': TABLE}
        if export == 'sfx_swap':
            self.calls.append((export, args))
            n = self.objects.count(args[0])
            if n:
                self.swapped[args[0]] = args[1:]
            return {'value': n}
        if export == 'sfx_restore':
            self.calls.append((export, args))
            self.swapped.pop(args[0], None)
            return {'value': self.objects.count(args[0])}
        if export == 'sfx_restore_all':
            self.calls.append((export, args))
            n, self.swapped = len(self.swapped), {}
            return {'value': n}
        return super().call(name, export, args)

    def table(self):
        return b''.join(struct.pack('<5I', 0x1000 + i, 0, 0x2000 + i, 100, h) for i, h in enumerate(self.objects))


class Replace(unittest.TestCase):
    PACKAGE_INDEX = 3

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state = root / 'state'
        self.game = root / 'game'
        self.code = build('ps_3_0', ORIG)
        self.h = fnv1a32(self.code)
        self.other = [('FILL%02d.pso' % i, build('ps_3_0', ORIG_2A + '// %d\n' % i)) for i in range(5)]
        self.write_package(self.game / 'Data' / 'Shaders', [('SKYTEST.pso', self.code)] + self.other)
        self.live = mock.MagicMock()
        self.live.project.state_dir = str(self.state)
        self.live.game_identity.return_value = dict(GAME)
        self.live.u32s.return_value = [5, 0, 0, 0]
        self.live.journal.add.side_effect = lambda e: dict(e, id='j%d' % self.live.journal.add.call_count)
        self.live.eyes.photo.side_effect = lambda action, **kw: (
            {'path': 'C:/pics/view-1.png'} if action == 'view' else {'now': 'C:/pics/view-2.png',
                                                                   'changedPercent': 12.5})
        self.live.world.frame_profile.return_value = {'msPerFrame': 16.7}
        self.live.game.layout.return_value = self.layout()
        self.set_objects([self.h] * 3 + [fnv1a32(c) for _, c in self.other])

    def layout(self, kind=DIRECT, **kw):
        base = dict(kind=kind, game_root=self.game, mo2_root=None, profile=None, user_dir=Path(self.tmp.name) / 'user',
                    appdata_dir=Path(self.tmp.name) / 'appdata', detected_by='test', changes_allowed=True)
        base.update(kw)
        return Layout(**base)

    def write_package(self, folder, entries, index=PACKAGE_INDEX):
        folder.mkdir(parents=True, exist_ok=True)
        write_sdp(folder / ('shaderpackage%03d.sdp' % index), entries)

    def set_objects(self, hashes):
        self.native = ScanNative(hashes)
        self.reads = []

        def read(addr, n):
            self.reads.append((addr, n))
            if n > 65536:                                     # the companion's READ_MAX
                raise AssertionError('read of %d bytes' % n)
            return self.native.table()[addr - TABLE:addr - TABLE + n]
        self.live.link.read.side_effect = read
        self.tools = ShaderTools(self.live, self.native)
        self.live.shader_tools = self.tools

    def manifest(self):
        return json.loads((self.state / 'fnv/shaders/live/manifest.json').read_text('utf-8'))

    def exports(self):
        return self.native.exports()

    def run_undo(self, index):
        entry = self.live.journal.add.call_args_list[index][0][0]
        return UNDO_OPS[entry['undo']['op']](self.live, entry, entry['undo'])


class ReplaceTests(Replace):
    def test_replace_checks_then_swaps_all_objects_with_that_code(self):
        out = self.tools.replace('SKYTEST.pso', OK_SRC)
        (swap,) = [a for e, a in self.native.calls if e == 'sfx_swap']
        written = self.live.link.write.call_args[0]
        self.assertEqual(swap, [self.h, 0x5000, len(written[1])])
        self.assertEqual(written[0], 0x5000)
        self.assertEqual(out['objects'], 3)
        self.assertEqual(out['names'], ['SKYTEST.pso'])
        self.assertEqual(out['changedPercent'], 12.5)
        self.assertEqual(out['before'], 'view-1.png')
        self.assertEqual(out['frameMs'], {'before': 16.7, 'after': 16.7})
        (entry,) = [c[0][0] for c in self.live.journal.add.call_args_list]
        self.assertEqual(entry['undo']['op'], 'shader_restore')
        rep = self.manifest()['replacements']
        self.assertEqual(len(rep), 1)
        (saved,) = rep.values()
        folder = self.state / 'fnv/shaders/live'
        self.assertEqual((folder / saved['source_file']).read_text('utf-8'), OK_SRC)
        self.assertTrue((folder / saved['bytecode_file']).exists())
        self.assertNotIn('C:', json.dumps(out))

    def test_shared_code_is_named(self):
        self.write_package(self.game / 'Data' / 'Shaders', [('SKYTEST.pso', self.code), ('SKYTEST2.pso', self.code)]
                           + self.other)
        out = self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        self.assertEqual(sorted(out['names']), ['SKYTEST.pso', 'SKYTEST2.pso'])
        self.assertTrue(any('SKYTEST2.pso' in w and 'also change' in w for w in out['warnings']), out['warnings'])

    def test_incompatible_sends_nothing(self):
        with self.assertRaises(ConvError) as cm:
            self.tools.replace('SKYTEST.pso', NEW_REG)
        self.assertEqual(cm.exception.code, 'INCOMPATIBLE')
        self.assertEqual(cm.exception.evidence['problems'][0]['code'], 'REGISTER')
        self.assertNotIn('sfx_buffer', self.exports())
        self.assertNotIn('sfx_swap', self.exports())
        self.live.link.write.assert_not_called()
        self.live.journal.add.assert_not_called()

    def test_compile_error_sends_nothing(self):
        with self.assertRaises(ConvError) as cm:
            self.tools.replace('SKYTEST.pso', 'float4 main( : COLOR0 { return 1 }')
        self.assertEqual(cm.exception.code, 'COMPILE_FAILED')
        self.assertNotIn('sfx_swap', self.exports())

    def test_unknown_name(self):
        with self.assertRaises(ConvError) as cm:
            self.tools.replace('SKYTEST.ps', OK_SRC)
        self.assertEqual(cm.exception.code, 'NO_SUCH_SHADER')
        self.assertIn('SKYTEST.pso', cm.exception.evidence['suggestions'])
        self.assertIn('SKYTEST.pso', str(cm.exception))

    def test_no_live_objects(self):
        self.set_objects([fnv1a32(c) for _, c in self.other])
        with self.assertRaises(ConvError) as cm:
            self.tools.replace('SKYTEST.pso', OK_SRC)
        self.assertEqual(cm.exception.code, 'NO_LIVE_OBJECTS')
        self.assertNotIn('sfx_swap', self.exports())

    def test_unknown_objects_never_touched(self):
        unknown = 0xDEADBEEF
        self.set_objects([self.h] * 3 + [fnv1a32(c) for _, c in self.other] * 8 + [unknown])
        out = self.tools.engine_list()
        self.assertEqual(out['unknownObjects'], 1)
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        swapped = [a[0] for e, a in self.native.calls if e == 'sfx_swap']
        self.assertEqual(swapped, [self.h])
        self.assertNotIn(unknown, swapped)

    def test_package_mismatch(self):
        self.set_objects([0x11111111 + i for i in range(30)] + [self.h])
        with self.assertRaises(ConvError) as cm:
            self.tools.replace('SKYTEST.pso', OK_SRC)
        self.assertEqual(cm.exception.code, 'PACKAGE_MISMATCH')
        self.assertEqual(cm.exception.evidence['objects'], 31)
        self.assertEqual(cm.exception.evidence['found'], 1)
        self.assertNotIn('sfx_swap', self.exports())

    def test_profile_follows_original(self):
        old = build('ps_2_a', ORIG_2A)
        self.write_package(self.game / 'Data' / 'Shaders', [('OLD.pso', old)] + self.other)
        self.set_objects([fnv1a32(old)])
        same = ORIG_2A.replace('tex2D(tex, uv)', '1 - tex2D(tex, uv)')
        self.tools.replace('OLD.pso', same, photo=False)
        sent = self.live.link.write.call_args[0][1]
        self.assertEqual(sent[:4], struct.pack('<I', 0xFFFF0201))        # ps_2_a, not the ps_3_0 default
        self.assertEqual(self.tools.template('OLD.pso')['profile'], 'ps_2_a')

    def test_large_scene_is_read_in_chunks(self):
        filler = [fnv1a32(c) for _, c in self.other]
        self.set_objects([self.h] * 3 + filler * 800)             # 4003 rows, 80060 bytes: over one read
        out = self.tools.engine_list(filter='SKY')
        self.assertEqual(out['unknownObjects'], 0)
        self.assertGreater(len(self.reads), 1)
        self.assertTrue(all(n <= 65536 and n % 20 == 0 for _, n in self.reads))
        self.assertEqual(sum(n for _, n in self.reads), 4003 * 20)
        self.assertEqual(next(r for r in out['rows'] if r['name'] == 'SKYTEST.pso')['liveObjects'], 3)

    def test_replace_rescans_right_before_the_swap(self):
        self.tools.engine_list()                                  # the cached scan: 3 objects hold the code
        self.assertEqual(self.exports().count('sfx_scan'), 1)
        self.native.objects += [self.h] * 2                       # a new cell made two more
        out = self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        self.assertEqual(self.exports().count('sfx_scan'), 2)
        self.assertLess(len(self.exports()) - 1 - self.exports()[::-1].index('sfx_scan'),
                        self.exports().index('sfx_swap'))
        self.assertEqual(out['objects'], 5)

    def test_files_are_written_only_after_the_swap(self):
        self.set_objects([fnv1a32(c) for _, c in self.other])
        with self.assertRaises(ConvError):
            self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        self.assertEqual(list((self.state / 'fnv/shaders/live').glob('engine-*')), [])

    def test_scan_cached_per_game(self):
        self.tools.engine_list()
        self.tools.engine_list()
        self.assertEqual(self.exports().count('sfx_scan'), 1)
        self.live.game_identity.return_value = dict(OTHER)
        self.tools.engine_list()
        self.assertEqual(self.exports().count('sfx_scan'), 2)


class RestoreTests(Replace):
    def test_restore_all(self):
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        out = self.tools.restore('all')
        self.assertIn('sfx_restore_all', self.exports())
        self.assertEqual(self.manifest()['replacements'], {})
        self.assertEqual(out['restored'], ['SKYTEST.pso'])

    def test_restore_one_and_undo_puts_it_back(self):
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        self.tools.restore('SKYTEST.pso')
        self.assertEqual([a for e, a in self.native.calls if e == 'sfx_restore'], [[self.h]])
        self.assertEqual(self.manifest()['replacements'], {})
        undo = self.live.journal.add.call_args_list[-1][0][0]['undo']
        self.assertEqual(undo['op'], 'shader_replace')
        self.assertIsNone(self.run_undo(-1))
        self.assertEqual(len(self.manifest()['replacements']), 1)
        self.assertEqual(len([1 for e, _ in self.native.calls if e == 'sfx_swap']), 2)

    def test_not_replaced(self):
        with self.assertRaises(ConvError) as cm:
            self.tools.restore('SKYTEST.pso')
        self.assertEqual(cm.exception.code, 'NOT_REPLACED')

    def test_undo_replace_restores_the_original(self):
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        self.assertIsNone(self.run_undo(0))
        self.assertEqual([a for e, a in self.native.calls if e == 'sfx_restore'], [[self.h]])
        self.assertEqual(self.manifest()['replacements'], {})

    def test_undo_replace_over_a_replacement_brings_back_the_first(self):
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        second = OK_SRC.replace('1 - ', '0.5 * ')
        self.tools.replace('SKYTEST.pso', second, photo=False)
        self.assertIsNone(self.run_undo(1))
        (saved,) = self.manifest()['replacements'].values()
        self.assertEqual((self.state / 'fnv/shaders/live' / saved['source_file']).read_text('utf-8'), OK_SRC)
        self.assertNotIn('sfx_restore', self.exports())

    def test_undos_after_a_restart_do_nothing(self):
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        self.tools.restore('SKYTEST.pso')
        self.live.game_identity.return_value = dict(OTHER)
        out = self.run_undo(-1)
        self.assertFalse(out['undone'])
        self.assertTrue(out['reason'])
        out = self.run_undo(0)
        self.assertFalse(out['undone'])

    def test_undo_ops_registered(self):
        for op in ('shader_restore', 'shader_replace'):
            self.assertIn(op, UNDO_OPS)

    def test_restart_forgets_replacements(self):
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        self.live.game_identity.return_value = dict(OTHER)
        with self.assertRaises(ConvError) as cm:
            self.tools.restore('SKYTEST.pso')
        self.assertEqual(cm.exception.code, 'NOT_REPLACED')


class ShowTests(Replace):
    def test_show_disasm_marked_game_content(self):
        out = self.tools.engine_show('SKYTEST.pso', disasm=True)
        self.assertTrue(out['gameContent'])
        self.assertIn('ps_3_0', out['disassembly'])
        self.assertEqual(out['liveObjects'], 3)
        self.assertEqual(out['kind'], 'ps')
        self.assertEqual(out['model'], 'ps_3_0')
        self.assertIn('tint:c0', out['constants'])
        self.assertIn('tex:s0', out['samplers'])
        self.tools.replace('SKYTEST.pso', OK_SRC, photo=False)
        line = out['disassembly'].splitlines()[2]
        for path in self.state.rglob('*'):
            if path.is_file():
                self.assertNotIn(line, path.read_text('latin-1'))

    def test_show_without_disasm_has_no_game_content(self):
        out = self.tools.engine_show('SKYTEST.pso')
        self.assertNotIn('disassembly', out)
        self.assertNotIn('gameContent', out)

    def test_template_with_name_and_without(self):
        out = self.tools.template('SKYTEST.pso')
        self.assertEqual(out['profile'], 'ps_3_0')
        self.assertIn('register(c0)', out['source'])
        self.assertTrue(compile_hlsl(out['source'], 'ps_3_0')['ok'])
        plain = self.tools.template()
        self.assertEqual(plain['profile'], 'ps_3_0')
        self.assertIn('main', plain['source'])

    def test_list_rows_and_filter(self):
        out = self.tools.engine_list()
        row = next(r for r in out['rows'] if r['name'] == 'SKYTEST.pso')
        self.assertEqual(row['kind'], 'ps')
        self.assertEqual(row['model'], 'ps_3_0')
        self.assertEqual(row['sharedWith'], [])
        self.assertEqual(out['unknownObjects'], 0)
        only = self.tools.engine_list(filter='sky')
        self.assertEqual([r['name'] for r in only['rows']], ['SKYTEST.pso'])


class PackageTests(Replace):
    def test_package_from_flags_marks_not_guessed(self):
        pkg = self.tools.package()
        self.assertIsInstance(pkg, Package)
        self.assertFalse(pkg.guessed)
        self.assertEqual(pkg.index, 3)

    def test_other_flags_pick_another_package(self):
        self.live.u32s.return_value = [5, 0, 1, 0]                     # hdr: package 10
        self.write_package(self.game / 'Data' / 'Shaders', [('HDR.pso', self.code)], index=10)
        self.assertEqual(self.tools.package().index, 10)
        self.assertEqual(self.tools.package().names(), ['HDR.pso'])

    def test_no_game_guesses_from_prefs(self):
        self.live.game_identity.return_value = None
        user = Path(self.tmp.name) / 'user'
        user.mkdir()
        (user / 'FalloutPrefs.ini').write_text('[Display]\nbDoHighDynamicRange=1\n')
        self.write_package(self.game / 'Data' / 'Shaders', [('HDR.pso', self.code)], index=10)
        pkg = self.tools.package()
        self.assertTrue(pkg.guessed)
        self.assertEqual(pkg.index, 10)
        out = self.tools.engine_list()
        self.assertTrue(out['guessed'])
        self.assertNotIn('sfx_scan', self.exports())

    def test_mo2_highest_priority_mod_holding_the_package_wins(self):
        mo2 = Path(self.tmp.name) / 'mo2'
        profile = mo2 / 'profiles' / 'dev'
        profile.mkdir(parents=True)
        (profile / 'modlist.txt').write_text('# mods\n+TopMod\n-OffMod\n+LowMod\n+EmptyMod\n')
        self.write_package(mo2 / 'mods' / 'LowMod' / 'Shaders', [('LOW.pso', self.code)])
        self.write_package(mo2 / 'mods' / 'OffMod' / 'Shaders', [('OFF.pso', self.code)])
        (mo2 / 'mods' / 'TopMod' / 'Shaders').mkdir(parents=True)          # a mod without this package
        (mo2 / 'mods' / 'TopMod' / 'Shaders' / 'shaderpackage001.sdp').write_bytes(b'x')
        self.live.game.layout.return_value = self.layout(MO2, mo2_root=mo2, profile='dev')
        self.assertEqual(self.tools.data_shaders_dir(), mo2 / 'mods' / 'LowMod' / 'Shaders')
        self.assertEqual(self.tools.package().names(), ['LOW.pso'])

    def test_direct_install_uses_game_data(self):
        self.assertEqual(self.tools.data_shaders_dir(), self.game / 'Data' / 'Shaders')


if __name__ == '__main__':
    unittest.main()
