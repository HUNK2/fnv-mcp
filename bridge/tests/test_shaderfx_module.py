"""Text and build checks for the bundled shaderfx native module (no game needed)."""
import re
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fnv_mcp import fnv_shader_hlsl as H
from fnv_mcp.fnv_link import LinkError, find_zig
from fnv_mcp.fnv_native import NativeTools

SOURCE = Path(__file__).resolve().parent.parent / 'native' / 'modules' / 'fnvmcp_shaderfx.c'
EXPORTS = ('sfx_buffer', 'sfx_effect_set', 'sfx_effect_param', 'sfx_effect_enable', 'sfx_effect_free',
           'sfx_effect_state', 'sfx_cost_us', 'sfx_info', 'sfx_scan', 'sfx_table', 'sfx_swap', 'sfx_restore',
           'sfx_restore_all')


def function_body(text, signature):
    start = text.index(signature)
    brace = text.index('{', start)
    depth = 0
    for i in range(brace, len(text)):
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            return text[brace:i + 1]
    raise AssertionError('unbalanced braces after %s' % signature)


def pe_exports(path):
    data = Path(path).read_bytes()
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    nsec = struct.unpack_from('<H', data, pe + 6)[0]
    opt = pe + 24
    rva = struct.unpack_from('<I', data, opt + 96)[0]
    sections = []
    for i in range(nsec):
        s = opt + struct.unpack_from('<H', data, pe + 20)[0] + 40 * i
        vsize, va, rawsize, raw = struct.unpack_from('<IIII', data, s + 8)
        sections.append((va, max(vsize, rawsize), raw))

    def off(r):
        for va, size, raw in sections:
            if va <= r < va + size:
                return raw + r - va
        raise AssertionError('rva outside sections')
    base = off(rva)
    num_names = struct.unpack_from('<I', data, base + 24)[0]
    names_rva = struct.unpack_from('<I', data, base + 32)[0]
    out = set()
    for i in range(num_names):
        n = off(struct.unpack_from('<I', data, off(names_rva) + 4 * i)[0])
        out.add(data[n:data.index(b'\0', n)].decode('ascii'))
    return out


class ShaderfxModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SOURCE.read_text(encoding='utf-8')

    def test_patches_only_the_call_site(self):
        for token in ('0x00B55AD6', '0x00B97900', '0x00B55ADB'):
            self.assertIn(token, self.text)
        self.assertIn('shaderfx: call site 0x00B55AD6 is not the expected call', self.text)
        self.assertEqual(self.text.count('->write('), 1)
        self.assertIn('->write(', function_body(self.text, 'static int patch_call_site(void)'))
        self.assertEqual(self.text.count('volatile uint32_t *'), 1)
        self.assertIn('volatile uint32_t *', function_body(
            self.text, 'static int swap_pointer(uint32_t obj, uint32_t off, uint32_t value)'))
        self.assertIn('sfx_restore_all()', function_body(self.text, 'void fnvmcp_module_exit(void)'))

    def test_releases_on_device_lost(self):
        self.assertIn('0x0086BA10', self.text)
        self.assertRegex(self.text, r'->hook\(\s*0x0086BA10|->hook\(\s*FN_DEVICE_LOST|->hook\(\s*ADDR_DEVICE_LOST')
        draw = function_body(self.text, 'static void draw_effects(void)')
        self.assertIn('vf(D, 3)', draw)  # TestCooperativeLevel
        self.assertLess(draw.index('vf(D, 3)'), draw.index('vf(D, DV_STRETCH_RECT)'))

    def test_engine_constants(self):
        for token in ('0x010EF7D4', '0x010EF87C', '0x2C', '0x34', '0x011F9508', '0x288'):
            self.assertIn(token, self.text)

    def test_vertex_shader_bytes_are_the_compiled_passthrough(self):
        hlsl = re.search(r'VS_HLSL_BEGIN\n(.*?)\nVS_HLSL_END', self.text, re.S).group(1)
        body = re.search(r'VS_BYTES\[\]\s*=\s*\{(.*?)\};', self.text, re.S).group(1)
        embedded = bytes(int(x, 16) for x in re.findall(r'0x([0-9A-Fa-f]{2})', body))
        result = H.compile_hlsl(hlsl, 'vs_3_0')
        self.assertTrue(result['ok'], result['errors'])
        self.assertEqual(embedded, result['bytecode'])

    def test_scan_reaches_the_top_of_the_address_space(self):
        body = function_body(self.text, 'int sfx_scan(void)')
        self.assertNotIn('0x7FFF0000', body)                  # a large-address-aware exe reaches 4 GB
        self.assertIn('GetSystemInfo', body)
        self.assertIn('lpMaximumApplicationAddress', body)
        self.assertIn('VirtualQuery', body)

    def test_builds_with_zig(self):
        try:
            find_zig(None)
        except LinkError:
            self.skipTest('no zig found')
        with tempfile.TemporaryDirectory() as tmp:
            tools = NativeTools(SimpleNamespace(project=SimpleNamespace(state_dir=tmp)))
            result = tools.build(str(SOURCE), 'fnvmcp_shaderfx')
            exports = pe_exports(result['dll'])
        missing = [name for name in EXPORTS if name not in exports]
        self.assertEqual(missing, [])
        self.assertIn('fnvmcp_module_init', exports)


if __name__ == '__main__':
    unittest.main()
