import tempfile
import unittest
from pathlib import Path

from fnv_mcp import fnv_shader_hlsl as H
from fnv_mcp import fnv_shader_pkg as P
from fnv_mcp.fnv_conv import ConvError

# Original-style pixel shader: Params at c4, BaseMap at s0, texcoord0 input.
ORIG_SRC = ('sampler2D BaseMap:register(s0); float4 Params:register(c4); '
            'float4 main(float2 uv:TEXCOORD0):COLOR0{return tex2D(BaseMap,uv)*Params;}')


def build(src, profile='ps_3_0'):
    r = H.compile_hlsl(src, profile)
    assert r['ok'], r['errors']
    return r['bytecode']


class SdpTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = Path(self.dir.name) / 'test.sdp'

    def test_round_trip(self):
        a, b = build(ORIG_SRC), build('float4 main():COLOR0{return float4(1,0,0,1);}')
        entries = [('A.pso', a), ('B.pso', b)]
        P.write_sdp(self.path, entries)
        self.assertEqual(P.read_sdp(self.path), entries)

    def test_header_layout(self):
        import struct
        P.write_sdp(self.path, [('A.pso', b'\x01\x02\x03')])
        raw = self.path.read_bytes()
        self.assertEqual(struct.unpack_from('<III', raw, 0), (100, 1, 256 + 4 + 3))
        self.assertEqual(len(raw), 12 + 256 + 4 + 3)

    def test_bad_package_refused(self):
        self.path.write_bytes(b'\x07\0\0\0' + b'\0' * 8)
        with self.assertRaises(ConvError) as cm:
            P.read_sdp(self.path)
        self.assertEqual(cm.exception.code, 'BAD_PACKAGE')
        self.path.write_bytes(b'\x64\0\0\0\x05\0\0\0\0\0\0\0')
        with self.assertRaises(ConvError):
            P.read_sdp(self.path)

    def test_naming_by_hash_and_shared_code(self):
        a, b = build(ORIG_SRC), build('float4 main():COLOR0{return float4(1,0,0,1);}')
        P.write_sdp(self.path, [('A.pso', a), ('B.pso', a), ('C.pso', b)])
        pkg = P.Package(self.path)
        self.assertEqual(pkg.names(), ['A.pso', 'B.pso', 'C.pso'])
        self.assertEqual(pkg.code('C.pso'), b)
        self.assertEqual(pkg.shared_with('A.pso'), ['B.pso'])
        self.assertEqual(pkg.shared_with('C.pso'), [])
        h = pkg.hash_of('A.pso')
        self.assertEqual(h, P.fnv1a32(a))
        self.assertEqual(pkg.names_for_hash(h), ['A.pso', 'B.pso'])
        self.assertEqual(pkg.names_for_hash(0), [])
        with self.assertRaises(ConvError):
            pkg.code('missing.pso')

    def test_fnv1a32_known_values(self):
        self.assertEqual(P.fnv1a32(b''), 0x811C9DC5)
        self.assertEqual(P.fnv1a32(b'a'), 0xE40C292C)
        self.assertEqual(P.fnv1a32(b'foobar'), 0xBF9CF968)


class PackageIndexTests(unittest.TestCase):
    def test_package_index_matches_decomp(self):
        pi = P.package_index
        self.assertEqual(pi(5, 1, 1, 1), 13)
        self.assertEqual(pi(5, 1, 1, 0), 12)
        self.assertEqual(pi(5, 0, 0, 0), 3)
        self.assertEqual(pi(1, 0, 0, 0), 1)
        # one case per remaining switch arm
        self.assertEqual(pi(2, 1, 1, 1), 2)
        self.assertEqual(pi(3, 1, 1, 1), 5)
        self.assertEqual(pi(4, 1, 1, 1), 8)
        self.assertEqual(pi(5, 1, 0, 0), 4)
        self.assertEqual(pi(5, 1, 0, 1), 4)
        self.assertEqual(pi(5, 0, 1, 1), 11)
        self.assertEqual(pi(5, 0, 1, 0), 10)
        self.assertEqual(pi(6, 1, 1, 1), 17)
        self.assertEqual(pi(6, 1, 1, 0), 16)
        self.assertEqual(pi(6, 1, 0, 1), 7)
        self.assertEqual(pi(6, 0, 1, 1), 15)
        self.assertEqual(pi(6, 0, 1, 0), 14)
        self.assertEqual(pi(6, 0, 0, 1), 6)
        self.assertEqual(pi(7, 0, 1, 1), 19)
        self.assertEqual(pi(7, 1, 1, 0), 18)
        self.assertEqual(pi(7, 1, 0, 1), 9)
        self.assertEqual(pi(0, 1, 1, 1), 0)
        self.assertEqual(pi(8, 1, 1, 1), 0)

    def test_package_path(self):
        p = P.package_path(Path('data'), 13)
        self.assertEqual(p, Path('data') / 'Shaders' / 'shaderpackage013.sdp')


class CheckTests(unittest.TestCase):
    def codes(self, problems):
        return [p['code'] for p in problems]

    def test_check_accepts_same_registers(self):
        orig = build(ORIG_SRC)
        repl = build('float4 Params:register(c4); float4 main():COLOR0{return Params;}')
        self.assertEqual(P.check_replacement(orig, repl), [])

    def test_check_refuses_new_register(self):
        orig = build(ORIG_SRC)
        repl = build('float4 Extra:register(c9); float4 main():COLOR0{return Extra;}')
        problems = P.check_replacement(orig, repl)
        self.assertEqual(self.codes(problems), ['REGISTER'])
        self.assertIn('c9', problems[0]['text'])

    def test_check_covers_array_registers(self):
        orig = build('float4 M[3]:register(c4); float4 main():COLOR0{return M[0]+M[1]+M[2];}')
        ok = build('float4 Q:register(c6); float4 main():COLOR0{return Q;}')
        bad = build('float4 Q:register(c7); float4 main():COLOR0{return Q;}')
        self.assertEqual([p for p in P.check_replacement(orig, ok) if not p.get('warning')], [])
        self.assertEqual(self.codes([p for p in P.check_replacement(orig, bad) if not p.get('warning')]),
                         ['REGISTER'])

    def test_check_refuses_model_change(self):
        orig = build(ORIG_SRC, 'ps_2_a')
        repl = build(ORIG_SRC, 'ps_3_0')
        self.assertIn('MODEL', self.codes(P.check_replacement(orig, repl)))
        orig3 = build(ORIG_SRC, 'ps_3_0')
        repl2 = build(ORIG_SRC, 'ps_2_a')
        self.assertIn('MODEL', self.codes(P.check_replacement(orig3, repl2)))

    def test_check_allows_2_0_to_2_a(self):
        orig = build(ORIG_SRC, 'ps_2_0')
        repl = build(ORIG_SRC, 'ps_2_a')
        self.assertNotIn('MODEL', self.codes(P.check_replacement(orig, repl)))

    def test_check_refuses_kind_change(self):
        orig = build(ORIG_SRC)
        vs = build('float4 main(float4 p:POSITION):POSITION{return p;}', 'vs_3_0')
        self.assertEqual(self.codes(P.check_replacement(orig, vs)), ['KIND'])

    def test_check_lists_all_problems_at_once(self):
        orig = build(ORIG_SRC)
        repl = build('sampler2D T:register(s2); float4 Extra:register(c9); '
                     'float4 main(float2 uv:TEXCOORD0):COLOR0{return tex2D(T,uv)*Extra;}')
        problems = P.check_replacement(orig, repl)
        self.assertEqual(sorted(self.codes(problems)), ['REGISTER', 'SAMPLER'])
        self.assertIn('s2', [p for p in problems if p['code'] == 'SAMPLER'][0]['text'])

    def test_check_refuses_new_input_in_ps_3_0(self):
        orig = build(ORIG_SRC)
        repl = build('float4 main(float2 uv:TEXCOORD0, float4 c:COLOR0):COLOR0{return c+uv.xyxy;}')
        problems = P.check_replacement(orig, repl)
        self.assertEqual(self.codes(problems), ['INPUT'])
        self.assertNotIn('warning', problems[0])

    def test_check_warns_new_input_in_ps_2_x(self):
        orig = build(ORIG_SRC, 'ps_2_a')
        repl = build('float4 main(float2 uv:TEXCOORD0, float4 c:COLOR0):COLOR0{return c+uv.xyxy;}', 'ps_2_a')
        problems = P.check_replacement(orig, repl)
        self.assertEqual(self.codes(problems), ['INPUT'])
        self.assertTrue(problems[0]['warning'])

    def test_check_warns_on_renamed_register(self):
        orig = build(ORIG_SRC)
        repl = build('float4 Other:register(c4); float4 main():COLOR0{return Other;}')
        problems = P.check_replacement(orig, repl)
        self.assertEqual(self.codes(problems), ['NAME'])
        self.assertTrue(problems[0]['warning'])

    def test_check_vs_outputs_must_match(self):
        orig = build('struct O{float4 p:POSITION; float2 t:TEXCOORD0;}; '
                     'O main(float4 p:POSITION){O o; o.p=p; o.t=p.xy; return o;}', 'vs_3_0')
        same = build('struct O{float4 p:POSITION; float2 t:TEXCOORD0;}; '
                     'O main(float4 p:POSITION){O o; o.p=p; o.t=p.zw; return o;}', 'vs_3_0')
        more = build('struct O{float4 p:POSITION; float2 t:TEXCOORD0; float4 c:COLOR0;}; '
                     'O main(float4 p:POSITION){O o; o.p=p; o.t=p.xy; o.c=p; return o;}', 'vs_3_0')
        self.assertEqual(P.check_replacement(orig, same), [])
        self.assertEqual(self.codes(P.check_replacement(orig, more)), ['OUTPUT'])

    def test_profile_for(self):
        self.assertEqual(P.profile_for(build(ORIG_SRC, 'ps_3_0')), 'ps_3_0')
        self.assertEqual(P.profile_for(build(ORIG_SRC, 'ps_2_0')), 'ps_2_0')
        self.assertEqual(P.profile_for(build(ORIG_SRC, 'ps_2_a')), 'ps_2_a')
        vs = 'float4 main(float4 p:POSITION):POSITION{return p;}'
        self.assertEqual(P.profile_for(build(vs, 'vs_3_0')), 'vs_3_0')
        self.assertEqual(P.profile_for(build(vs, 'vs_2_0')), 'vs_2_0')
        with self.assertRaises(ConvError):
            P.profile_for(b'\x00\x00\x00\x00')


class TemplateTests(unittest.TestCase):
    def test_template_compiles_and_passes_check(self):
        for profile in ('ps_3_0', 'ps_2_a'):
            orig = build(ORIG_SRC, profile)
            self.assertEqual(P.profile_for(orig), profile)
            text = P.replacement_template('Shader1.pso', orig)
            self.assertIn('Shader1.pso', text)
            self.assertIn(profile, text)
            self.assertIn('register(c4)', text)
            self.assertIn('register(s0)', text)
            r = H.compile_hlsl(text, P.profile_for(orig))
            self.assertTrue(r['ok'], (profile, r['errors'], text))
            self.assertEqual(P.check_replacement(orig, r['bytecode']), [], (profile, text))

    def test_template_color_and_white_bodies(self):
        col = build('float4 main(float4 c:COLOR0):COLOR0{return c;}')
        text = P.replacement_template('C.pso', col)
        self.assertIn('COLOR0', text)
        self.assertTrue(H.compile_hlsl(text, 'ps_3_0')['ok'], text)
        white = build('float4 main():COLOR0{return float4(1,0,0,1);}')
        text = P.replacement_template('W.pso', white)
        self.assertIn('float4(1,1,1,1)', text)
        self.assertTrue(H.compile_hlsl(text, 'ps_3_0')['ok'], text)

    def test_template_array_constant(self):
        orig = build('float4 M[3]:register(c4); float4 main():COLOR0{return M[0]+M[1]+M[2];}')
        text = P.replacement_template('M.pso', orig)
        self.assertIn('M[3]', text)
        self.assertTrue(H.compile_hlsl(text, 'ps_3_0')['ok'], text)

    def test_template_vertex_shader_passes_check(self):
        orig = build('float4 W:register(c0); struct O{float4 p:POSITION; float2 t:TEXCOORD0;}; '
                     'O main(float4 p:POSITION){O o; o.p=p*W; o.t=p.xy; return o;}', 'vs_3_0')
        text = P.replacement_template('V.vso', orig)
        r = H.compile_hlsl(text, 'vs_3_0')
        self.assertTrue(r['ok'], (r['errors'], text))
        self.assertEqual(P.check_replacement(orig, r['bytecode']), [], text)

    def test_effect_template_compiles(self):
        text = P.effect_template()
        r = H.compile_hlsl(text, 'ps_3_0')
        self.assertTrue(r['ok'], (r['errors'], text))
        # The compiler drops constants the body does not read, so only s0 shows in the table.
        self.assertIn(('sampler', 0), {(c.regset, c.index) for c in r['constants']})
        for decl in ('register(s0)', 'register(s1)', 'register(c0)', 'register(c1)', 'register(c2)'):
            self.assertIn(decl, text)


if __name__ == '__main__':
    unittest.main()
