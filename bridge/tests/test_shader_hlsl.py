import os
import unittest

from fnv_mcp import fnv_shader_hlsl as H
from fnv_mcp.fnv_conv import ConvError

PS = ('sampler2D s:register(s0); float4 k:register(c2); '
      'float4 main(float2 uv:TEXCOORD0):COLOR0{return tex2D(s,uv)*k;}')


class ShaderHlslTests(unittest.TestCase):
    def test_compiles_ps_3_0(self):
        r = H.compile_hlsl(PS)
        self.assertTrue(r['ok'], r['errors'])
        self.assertEqual(r['version'], 0xFFFF0300)
        self.assertIn(H.Constant('k', 'float4', 2, 1), r['constants'])
        self.assertIn(H.Constant('s', 'sampler', 0, 1), r['constants'])
        self.assertIsInstance(r['slots'], int)

    def test_errors_have_line_col_code_and_no_path(self):
        r = H.compile_hlsl('float4 main():COLOR0{return oops;}')
        self.assertFalse(r['ok'])
        e = r['errors'][0]
        self.assertEqual(e['line'], 1)
        self.assertEqual(e['code'], 'X3004')
        self.assertIn('oops', e['text'])
        for x in r['errors']:
            self.assertNotIn(':\\', x['text'])
            self.assertNotIn(os.getcwd(), x['text'])

    def test_include_refused(self):
        with self.assertRaises(ConvError) as c:
            H.compile_hlsl('#include "x.h"\nfloat4 main():COLOR0{return 0;}')
        self.assertEqual(c.exception.code, 'INCLUDE_REFUSED')

    def test_bad_profile(self):
        with self.assertRaises(ConvError) as c:
            H.compile_hlsl(PS, profile='ps_5_0')
        self.assertEqual(c.exception.code, 'BAD_PROFILE')

    def test_ps_2_a_version_token(self):
        r = H.compile_hlsl('float4 main():COLOR0{return float4(1,0,0,1);}', profile='ps_2_a')
        self.assertTrue(r['ok'], r['errors'])
        self.assertEqual(r['version'], 0xFFFF0201)

    def test_inputs_from_disassembly(self):
        r = H.compile_hlsl('float4 main(float4 c:COLOR0):COLOR0{return c;}', profile='ps_2_0')
        self.assertTrue(r['ok'], r['errors'])
        self.assertTrue(any(i.startswith('v0') for i in r['inputs']), r['inputs'])

    def test_vs_outputs(self):
        src = ('struct O{float4 p:POSITION; float2 t:TEXCOORD0;};'
               'O main(float4 p:POSITION, float2 t:TEXCOORD0){O o; o.p=p; o.t=t; return o;}')
        r = H.compile_hlsl(src, profile='vs_3_0')
        self.assertTrue(r['ok'], r['errors'])
        outs = H.outputs_of(H.disassemble(r['bytecode']))
        self.assertTrue(any(o.startswith('o0') and 'position' in o for o in outs), outs)
        self.assertTrue(any(o.startswith('o1') and 'texcoord' in o for o in outs), outs)

    def test_slots_and_version_of(self):
        r = H.compile_hlsl(PS)
        self.assertEqual(H.version_of(r['bytecode']), 0xFFFF0300)
        self.assertEqual(H.parse_ctab(r['bytecode']), r['constants'])


if __name__ == '__main__':
    unittest.main()
