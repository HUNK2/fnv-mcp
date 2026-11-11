"""fnv_native: C type mapping, the generated header (typed pointers with calling conventions, padded structs) and a
real Zig build of a module that includes it. The load path is covered by test_engine_mod (fake host) and live."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_native  # noqa: E402
from geck_mcp.fnv_conv import ConvError  # noqa: E402
from geck_mcp.fnv_native import NativeTools, c_name, c_type  # noqa: E402


class FakeDb:
    def __init__(self):
        self.types = {
            1: {'id': 1, 'kind': 'mfunc', 'target_id': 10, 'attrs': '{"call":"thiscall","this":"x"}'},
            2: {'id': 2, 'kind': 'mfunc', 'target_id': 11, 'attrs': '{"call":"cdecl","this":null}'},
        }
        self.texts = {10: 'float', 11: 'TESForm *', 20: 'int', 21: 'Actor *', 22: 'SomeStruct'}
        self.argrows = {1: [], 2: [{'type_id2': 21}, {'type_id2': 20}]}
        self.funcs = {0x00867DA0: {'addr': 0x00867DA0, 'type_id': 1}, 0x00401000: {'addr': 0x00401000, 'type_id': 2}}

    def function(self, a):
        return self.funcs.get(a)

    def _type(self, i):
        return self.types.get(i)

    def _attrs(self, row):
        import json
        return json.loads(row['attrs'])

    def type_text(self, i):
        return self.texts[i]

    def _all(self, sql, args):
        return self.argrows[args[0]]

    def layout(self, name):
        if name != 'Thing':
            return None
        return {'name': 'Thing', 'kind': 'class', 'size': 0x18, 'members': [
            {'name': 'refID', 'offset': 0x0C, 'size': 4, 'type': 'unsigned int'},
            {'name': 'parent', 'offset': 0x10, 'size': 4, 'type': 'TESObjectCELL *'},
            {'name': 'pos', 'offset': 0x14, 'size': 4, 'type': 'float'}]}


class TypeTests(unittest.TestCase):
    def test_c_types(self):
        self.assertEqual(c_type('TESForm *'), 'void *')
        self.assertEqual(c_type('const char *'), 'void *')
        self.assertEqual(c_type('bool'), 'unsigned char')
        self.assertEqual(c_type('unsigned int'), 'unsigned int')
        self.assertEqual(c_type('enum ActorValue'), 'int')
        self.assertIsNone(c_type('NiPoint3'))            # a struct by value: no C spelling
        self.assertEqual(c_name('TESObjectREFR::Activate'), 'TESObjectREFR_Activate')

    def test_signatures(self):
        db = FakeDb()
        self.assertEqual(NativeTools._signature(db, 0x00867DA0), ('float', 'thiscall', ['void *self']))
        self.assertEqual(NativeTools._signature(db, 0x00401000), ('void *', 'cdecl', ['void * a0', 'int a1']))

    def test_struct_has_padding_and_a_size_check(self):
        text = '\n'.join(NativeTools._struct(FakeDb(), 'Thing'))
        self.assertIn('unsigned char _pad0[12];', text)
        self.assertIn('unsigned int refID; /* +0xC unsigned int */', text)
        self.assertIn('void * parent;', text)
        self.assertIn('_Static_assert(sizeof(Thing) == 24', text)


class HeaderAndBuildTests(unittest.TestCase):
    def tools(self, tmp):
        engine = SimpleNamespace(resolve=lambda n: {'Calendar::GetGameTime': 0x00867DA0, 'Helper': 0x00401000}[n])
        live = SimpleNamespace(project=SimpleNamespace(state_dir=tmp), engine=lambda: engine)
        t = NativeTools(live, zig=os.environ.get('GECK_MCP_ZIG'))
        return t

    def test_headers_and_a_module_that_uses_them_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = self.tools(tmp)
            db = FakeDb()
            orig = fnv_native.NativeTools.headers

            def headers(self, functions=None, classes=None):
                import geck_mcp.fnv_symdb as symdb
                real = symdb.load
                symdb.load = lambda path: db
                try:
                    return orig(self, functions, classes)
                finally:
                    symdb.load = real
            out = headers(t, ['Calendar::GetGameTime', 'Helper', 'Nope'], ['Thing', 'Missing'])
            self.assertEqual([f['name'] for f in out['functions']], ['Calendar::GetGameTime', 'Helper'])
            self.assertEqual(out['notFound'], ['Nope'])
            self.assertEqual(out['structsNotFound'], ['Missing'])
            header = (Path(tmp) / 'fnv' / 'sdk' / 'fnv_engine.h').read_text()
            self.assertIn('typedef float (__attribute__((thiscall)) *FnvFn_Calendar_GetGameTime)(void *self);',
                          header)
            self.assertIn('#define FNV_ADDR_Helper 0x00401000u', header)
            if not os.environ.get('GECK_MCP_ZIG'):
                self.skipTest('set GECK_MCP_ZIG to build')
            src = ('#include "fnvmcp_module.h"\n#include "fnv_engine.h"\n'
                   'static const FnvmcpApi *api;\n'
                   'FNVMCP_EXPORT int fnvmcp_module_init(const FnvmcpApi *a) { api = a; return 0; }\n'
                   'FNVMCP_EXPORT float hour(void *cal) { return FNV_Calendar_GetGameTime(cal); }\n'
                   'FNVMCP_EXPORT unsigned id_of(Thing *t) { return t->refID; }\n')
            built = t.build(src, 'uses headers')
            self.assertTrue(Path(built['dll']).is_file())
            self.assertEqual(t._newest('uses headers'), Path(built['dll']))

    def test_compiler_errors_come_back(self):
        if not os.environ.get('GECK_MCP_ZIG'):
            self.skipTest('set GECK_MCP_ZIG to build')
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ConvError) as cm:
                self.tools(tmp).build('int fnvmcp_module_init(void) { return undefined_thing; }', 'bad')
            self.assertEqual(cm.exception.code, 'BUILD_FAILED')
            self.assertIn('undefined_thing', str(cm.exception))


if __name__ == '__main__':
    unittest.main()
