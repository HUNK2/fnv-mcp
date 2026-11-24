"""Round-3 audit X4..X7, X9: text encoding, group depth, truncated subrecords and archives."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp import fnv_records  # noqa: E402
from fnv_mcp.esp import assets, codec, dialogue, generic, records  # noqa: E402
from fnv_mcp.fnv_data import DataError  # noqa: E402


def sub(tag, payload):
    return tag + struct.pack('<H', len(payload)) + payload


def rec(tag, form_id, body, flags=0):
    return tag + struct.pack('<IIIII', len(body), flags, form_id, 0, 0) + body


def plugin_bytes(*items):
    head = sub(b'HEDR', struct.pack('<fiI', 1.34, 1, 0x800)) + sub(b'MAST', b'FalloutNV.esm\0') + sub(b'DATA', b'\0' * 8)
    return rec(b'TES4', 0, head) + b''.join(items)


class TextEncoding(unittest.TestCase):
    def test_c1_controls_that_cp1252_defines_are_refused(self):          # X4
        with self.assertRaises(records.SpecError):
            records.text('A\x80B\x9fC', 'where')

    def test_the_five_undefined_bytes_still_round_trip(self):
        for c in '\x81\x8d\x8f\x90\x9d':
            self.assertEqual(codec.text(records.text('A%sB' % c, 'where')[:-1]), 'A%sB' % c)

    def test_generic_text_with_a_nul_or_a_snowman_is_a_spec_error(self):  # X5
        for value in ('Hi\0there', 'snow ☃'):
            with self.assertRaises(records.SpecError, msg=value) as cm:
                generic.make_value({'tag': 'FULL', 'text': value}, None, 'records.X.fields[0]')
            self.assertIn('records.X.fields[0]', str(cm.exception))
        with self.assertRaises(records.SpecError):
            generic.make_value({'tag': 'FULL', 'texts': ['ok', 'a\0b']}, None, 'w')
        self.assertEqual(generic.make_value({'tag': 'FULL', 'text': 'Hi'}, None, 'w'), b'Hi\0')

    def test_dialogue_response_with_a_nul_is_a_spec_error(self):          # X5
        line = {'key': 'a', 'quest': '@Q', 'responses': ['Hi\0there']}
        with self.assertRaises(records.SpecError) as cm:
            dialogue.build_line(0x800, 'T', line, lambda v, w: 0x801, 'records.T.infos[0]')
        self.assertIn('responses[1].text', str(cm.exception))


class GroupDepth(unittest.TestCase):
    def test_deeply_nested_groups_are_a_format_error(self):               # X6
        data = plugin_bytes()
        inner = b''
        for _ in range(1200):
            inner = struct.pack('<4sI4sIII', b'GRUP', 24 + len(inner), b'WRLD', 0, 0, 0) + inner
        with self.assertRaises(codec.FormatError):
            codec.parse(data + inner)

    def test_ordinary_nesting_still_parses(self):
        inner = rec(b'MISC', 0x800, sub(b'EDID', b'A\0'))
        for _ in range(8):
            inner = struct.pack('<4sI4sIII', b'GRUP', 24 + len(inner), b'WRLD', 0, 0, 0) + inner
        self.assertEqual(len(list(codec.parse(plugin_bytes(inner)).records())), 2)


class TruncatedFiles(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def scan(self, body):
        path = self.tmp / 'cut.esp'
        path.write_bytes(plugin_bytes(rec(b'NPC_', 0x800, body)))
        return fnv_records.scan_references(path, lambda raw: True)

    def test_truncated_subrecords_are_a_data_error_naming_the_file(self):  # X7
        bodies = {
            'xxxx cut': sub(b'EDID', b'A\0') + b'XXXX\x04\x00',
            'xxxx then nothing': sub(b'EDID', b'A\0') + b'XXXX\x04\x00\x10\x00\x00\x00',
            'rnam cut': b'RNAM\x04\x00\x01\x02',
            'ctda cut': b'CTDA\x1c\x00' + b'\0' * 10,
        }
        for name, body in bodies.items():
            with self.assertRaises(DataError, msg=name) as cm:
                self.scan(body)
            self.assertIn('cut.esp', str(cm.exception), name)

    def test_whole_subrecords_still_scan(self):
        rows = self.scan(sub(b'EDID', b'A\0') + sub(b'RNAM', struct.pack('<I', 0x000A6261)))
        self.assertEqual([r[4] for r in rows], [0x000A6261])

    def test_truncated_bsa_extract_is_an_asset_error(self):              # X9
        header = b'BSA\0' + struct.pack('<8I', 104, 36, 3, 3, 3, 10, 10, 0)
        for name, data in (('empty', b''), ('short', b'BSA\0\x68'), ('folders', header)):
            path = self.tmp / (name + '.bsa')
            path.write_bytes(data)
            with self.assertRaises(assets.AssetError, msg=name) as cm:
                assets.bsa_extract(path, {'a\\b'})
            self.assertIn(name + '.bsa', str(cm.exception))
            with self.assertRaises(assets.AssetError, msg=name):
                assets.bsa_names(path)


if __name__ == '__main__':
    unittest.main()
