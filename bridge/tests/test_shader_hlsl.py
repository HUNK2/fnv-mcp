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

    def test_ctab_default_values(self):
        src = ('sampler2D s : register(s0);\nfloat4 tint : register(c2) = float4(1, 0.5, 0, 1);\n'
               'float4 plain : register(c3);\n'
               'float4 main(float2 uv:TEXCOORD0):COLOR0{return tex2D(s,uv)*tint*plain;}')
        by = {c.name: c for c in H.compile_hlsl(src)['constants']}
        self.assertEqual(by['tint'].default, (1.0, 0.5, 0.0, 1.0))
        self.assertIsNone(by['plain'].default)
        self.assertIsNone(by['s'].default)
        self.assertIsNone(H.Constant('x', 'float4', 2, 1).default)

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

    def test_clean_strips_paths(self):
        raw = ('D:\\some dir\\effect.hlsl(3,5-9): error X3004: undeclared identifier \'q\'\n'
               'error X3501: cannot open C:\\x\\y.fx\n\0')
        out = H._clean(raw)
        self.assertNotIn(':\\', out)
        self.assertNotIn('\0', out)
        self.assertIn('effect.hlsl(3,5-9): error X3004', out)
        errors, _ = H._messages(raw)
        self.assertEqual([(e['line'], e['col'], e['code']) for e in errors], [(3, 5, 'X3004'), (0, 0, 'X3501')])
        for e in errors:
            self.assertNotIn(':\\', e['text'])

    def test_clean_strips_paths_with_spaces(self):
        raw = ('error X3501: cannot open C:\\Users\\First Last\\dir\\x.fx for reading\n'
               'C:\\Users\\First Last\\dir\\effect.hlsl(2,1): error X3004: bad C:\\Users\\First Last\\dir\\y.fx\n')
        out = H._clean(raw)
        errors, _ = H._messages(raw)
        for s in [out] + [e['text'] for e in errors]:
            for bad in ('First', 'Last', ':\\'):
                self.assertNotIn(bad, s)
        self.assertEqual([(e['line'], e['code']) for e in errors], [(0, 'X3501'), (2, 'X3004')])

    def test_clean_strips_paths_with_quote_and_paren(self):
        raw = ("error X3501: cannot open C:\\Users\\O'Brien Smith\\x.fx: denied\n"
               "error X3501: cannot open C:\\Users\\Sam (Work) Lee\\x.fx: denied\n"
               "C:\\Users\\Sam (Work) O'Lee\\effect.hlsl(4,7-9): error X3004: bad\n")
        out = H._clean(raw)
        errors, _ = H._messages(raw)
        for s in [out] + [e['text'] for e in errors]:
            for bad in ('Brien', 'Smith', 'Work', 'Sam', 'Lee', ':\\', "O'"):
                self.assertNotIn(bad, s)
        self.assertEqual([(e['line'], e['col'], e['code']) for e in errors],
                         [(0, 0, 'X3501'), (0, 0, 'X3501'), (4, 7, 'X3004')])

    def test_entrypoint_not_found(self):
        r = H.compile_hlsl('float4 f():COLOR0{return 0;}')
        self.assertFalse(r['ok'])
        e = r['errors'][0]
        self.assertEqual(e['code'], 'X3501')
        self.assertNotIn('\0', e['text'])
        self.assertNotIn('\n', e['text'])
        self.assertIn('entrypoint not found', e['text'])
        self.assertFalse(e['text'].startswith('error'))

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
