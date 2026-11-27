"""PLAN-3 S6 batch B review fixes (fresh Opus review of R9, R16, P2-P10)."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_stab_s6b import plugin, record, sub  # noqa: E402

FLT_MAX = struct.unpack('<f', bytes.fromhex('ffff7f7f'))[0]


class RotationTests(unittest.TestCase):
    def initial(self, rotation):
        from types import SimpleNamespace
        from fnv_mcp.fnv_save import Save
        block = b'\x01\x00\x00' + struct.pack('<6f', 1.0, 2.0, 3.0, *rotation)
        return Save.__new__(Save)._initial(4, block, SimpleNamespace(resolve=lambda raw: 0x3C))

    def test_i1_only_the_sentinel_axis_is_unset(self):
        info = self.initial((FLT_MAX, 0.0, 1.5))
        self.assertEqual(info['rotation'], {'x': None, 'y': 0.0, 'z': 1.5}, 'the heading (z) is kept')
        self.assertEqual(info['rotationUnset'], ['x'])


class EncodeTests(unittest.TestCase):
    def test_i2_console_text_with_an_undefined_byte_encodes(self):
        from fnv_mcp import fnv_link
        self.assertEqual(bytes.fromhex(fnv_link.hexs('SetName "Caf\x81"')), b'SetName "Caf\x81"')

    def test_i2_no_strict_cp1252_encode_or_read_left(self):
        import re
        root = Path(__file__).resolve().parents[1] / 'fnv_mcp'
        strict = re.compile(r"""(\.encode\(\s*['"]cp1252['"]\s*\)|encoding=['"]cp1252['"]\s*\))""")
        found = ['%s:%d' % (path.relative_to(root), n) for path in root.rglob('*.py')
                 for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1) if strict.search(line)]
        self.assertEqual(found, [])


class LintTests(unittest.TestCase):
    def test_m4_extra_tokens_after_a_zero_parameter_command_are_info(self):
        from test_stab_s6b import LintVanillaTests
        from fnv_mcp.fnv_data import lint_script
        engine = LintVanillaTests.engine()
        problems = lint_script('scn T\nBegin GameMode\n  EvaluatePackage MyNPC\nEnd\n', engine, None)['problems']
        self.assertIn(('info', 'EXTRA_TOKENS_IGNORED'), [(p['severity'], p['code']) for p in problems])


class ValidateWarningTests(unittest.TestCase):
    def test_m5_validate_lists_record_warnings(self):
        import zlib
        from fnv_mcp.esp import codec, validate
        body = sub(b'EDID', b'Thing\0') + sub(b'DATA', b'\x01' * 12)
        stream = bytearray(zlib.compress(body))
        stream[-1] ^= 0xFF
        rec = record(b'MISC', 0x01000800, struct.pack('<I', len(body)) + bytes(stream), flags=codec.COMPRESSED)
        group = b'GRUP' + struct.pack('<I', 24 + len(rec)) + b'MISC' + struct.pack('<iII', 0, 0, 0) + rec
        out = validate.validate(plugin() + group)
        self.assertTrue(any('checksum' in w for w in out['warnings']), out)


class OffsetTests(unittest.TestCase):
    def test_m6_bad_subrecord_tag_reports_its_file_offset(self):
        from fnv_mcp.esp import codec
        data = b'EDID' + struct.pack('<H', 2) + b'x\0' + b'\xffBAD' + struct.pack('<H', 0)
        with self.assertRaises(codec.FormatError) as caught:
            codec.parse_subrecords(data, base=1000)
        self.assertEqual(caught.exception.offset, 1008)


class FormRuleTests(unittest.TestCase):
    def test_m7_form_follows_the_index_rule(self):
        import sqlite3
        from types import SimpleNamespace
        from fnv_mcp.fnv_data import DataTools
        db = sqlite3.connect(':memory:')
        db.execute('create table records(file integer, owner text, local integer, type text, edid text, full text)')
        db.execute("insert into records values (1, 'mymod.esp', 0x800, 'MISC', 'Thing', null)")
        tools = DataTools.__new__(DataTools)
        tools._index = SimpleNamespace(db=db)
        # file ids as _active gives them: _form reads only the active plugins' rows (Z2)
        entries = [{'name': 'FalloutNV.esm', 'index': 0, 'fileId': 0}, {'name': 'MyMod.esp', 'index': 1, 'fileId': 1}]
        plugin_entry = {'name': 'MyMod.esp', 'masters': ['FalloutNV.esm']}
        # the index stores a record whose top byte is past the masters under the plugin itself; _form agrees
        self.assertEqual(tools._form(entries, plugin_entry, 0x05000800)['editorId'], 'Thing')


class VersionTests(unittest.TestCase):
    def test_m8_a_newer_index_is_not_rebuilt_by_an_older_server(self):
        import inspect
        from fnv_mcp import fnv_data, fnv_records
        self.assertIn('>= INDEX_VERSION', inspect.getsource(fnv_data))
        self.assertIn('>= REFS_VERSION', inspect.getsource(fnv_records))


class DeviceNameTests(unittest.TestCase):
    def test_m9_plugin_and_project_device_names_refused(self):
        from fnv_mcp.esp import build, records
        base = {'schema': build.SCHEMA, 'project_id': 'p', 'plugin': 'x.esp', 'masters': ['FalloutNV.esm']}
        for field, value in (('plugin', 'aux.esp'), ('project_id', 'con')):
            with self.assertRaises(records.SpecError, msg=field):
                build.check_spec(dict(base, **{field: value}))


if __name__ == '__main__':
    unittest.main()
