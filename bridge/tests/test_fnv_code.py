"""fnv_code and fnv_plugin_build: the function's line range, relocation maths when the blob is placed, an offline
checkout/edit/build of a real decomp function (when the decomp and its compiler are on this machine), and a plugin
built from the scaffold with the standalone runtime (when Zig is set)."""
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp.fnv_code import DIR32, REL32, CodeTools  # noqa: E402
from geck_mcp.fnv_conv import ConvError  # noqa: E402
from geck_mcp.fnv_plugin_build import PluginTools  # noqa: E402

DECOMP = Path(r'C:\DecompModGithub\fnv_decomp')
SOURCE = '''// header
// @fnv 0x00401000
int A(int x)
{
    return x + 1;
}

// @fnv 0x00401020
int B(int y)
{
    return y * 2;
}
'''


class RangeAndRelocTests(unittest.TestCase):
    def test_range_covers_the_function_only(self):
        self.assertEqual(CodeTools._range(SOURCE, 0x00401000), (2, 6))
        self.assertEqual(CodeTools._range(SOURCE, 0x00401020), (8, 12))
        self.assertEqual(CodeTools._range(SOURCE, 0x00409999), (None, None))

    def test_relocations_are_bound_at_the_placed_address(self):
        code = bytearray(16)
        link = {'code': code.hex(), 'relocs': [
            {'offset': 1, 'kind': REL32, 'absolute': 0x00401000},       # call an original function
            {'offset': 6, 'kind': DIR32, 'absolute': 0x011F6238},       # a global
            {'offset': 11, 'kind': DIR32, 'blob': 0x0C}]}               # data copied into the blob
        out = CodeTools._final(link, 0x10000000)
        self.assertEqual(struct.unpack_from('<i', out, 1)[0], 0x00401000 - (0x10000000 + 1 + 4))
        self.assertEqual(struct.unpack_from('<I', out, 6)[0], 0x011F6238)
        self.assertEqual(struct.unpack_from('<I', out, 11)[0], 0x1000000C)


def live_for(tmp, resolve):
    engine = SimpleNamespace(resolve=resolve)
    project = SimpleNamespace(state_dir=tmp, fnv=SimpleNamespace(decomp_root=str(DECOMP), mo2_root=tmp,
                                                                 mo2_profile='Dev'))
    return SimpleNamespace(project=project, engine=lambda: engine, game=SimpleNamespace(process=lambda: None),
                           journal=SimpleNamespace(add=lambda e: e))


@unittest.skipUnless((DECOMP / 'build' / 'units.json').is_file() and os.environ.get('FNV_MSVC9_ROOT'),
                     'needs the decomp checkout and its compiler (FNV_MSVC9_ROOT)')
class OfflineBuildTests(unittest.TestCase):
    def test_checkout_edit_build_links_every_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = CodeTools(live_for(tmp, lambda n: 0x00643C20))
            out = tools.checkout('AIFormulas::CalculateSkillValue')
            self.assertIn('float result = base;', out['source'])
            tools.edit('AIFormulas::CalculateSkillValue', 'float result = base;', 'float result = base + 10.0f;')
            built = tools.build('AIFormulas::CalculateSkillValue')
            self.assertIsNone(built['unresolved'])
            self.assertIsNone(built['refused'])
            self.assertGreater(built['size'], built['originalSize'])
            link = json.loads((Path(tmp) / 'fnv' / 'code' / '00643C20' / 'link.json').read_text())
            self.assertTrue(all('absolute' in r or 'blob' in r for r in link['relocs']))
            with self.assertRaises(ConvError):
                tools.edit('AIFormulas::CalculateSkillValue', 'not in the function', 'x')
            # the decomp checkout itself is untouched
            self.assertIn('float result = base;', (DECOMP / 'src/fallout_shared/aiformulas.cpp').read_text())


@unittest.skipUnless(os.environ.get('GECK_MCP_ZIG'), 'set GECK_MCP_ZIG to build')
class PluginBuildTests(unittest.TestCase):
    def test_scaffold_builds_as_a_standalone_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = PluginTools(live_for(tmp, lambda n: 0x00401000), zig=os.environ['GECK_MCP_ZIG'])
            tools.scaffold('my plugin')
            out = tools.build('my plugin')
            self.assertTrue(Path(out['dll']).is_file())
            self.assertEqual(out['hooks'], [])
            plan = (Path(tmp) / 'fnv' / 'plugins' / 'my_plugin' / 'fnvmcp_plan.h').read_text()
            self.assertIn('#define FNVMCP_PLUGIN_NAME "my_plugin"', plan)
            with self.assertRaises(ConvError) as cm:
                tools.scaffold('my plugin')
            self.assertEqual(cm.exception.code, 'EXISTS')


if __name__ == '__main__':
    unittest.main()
