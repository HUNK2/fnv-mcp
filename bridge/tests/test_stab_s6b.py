"""PLAN-3 S6 batch B: records, parsers, saves (R9/P1, R16, P2-P10)."""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp import fnv_records  # noqa: E402
from geck_mcp.fnv_data import decode_subrecord  # noqa: E402


def game_data():
    """The game's Data folder from the project file, or None (tests that need it are skipped)."""
    try:
        from geck_mcp.config import Project
        root = Path(__file__).resolve().parents[1] / 'projects' / 'hellowasteland.windows.json'
        data = Project.load(str(root)).game_root / 'Data'
        return data if (data / 'FalloutNV.esm').exists() else None
    except Exception:  # noqa: BLE001
        return None


DATA = game_data()


def sub(tag, payload):
    return tag + struct.pack('<H', len(payload)) + payload


def record(tag, form_id, body, flags=0):
    return tag + struct.pack('<IIIII', len(body), flags, form_id, 0, 0) + body


def plugin(*records, masters=('FalloutNV.esm',)):
    head = sub(b'HEDR', struct.pack('<fiI', 1.34, len(records), 0x800))
    for m in masters:
        head += sub(b'MAST', m.encode() + b'\0') + sub(b'DATA', b'\0' * 8)
    return record(b'TES4', 0, head) + b''.join(records)


def write(tmp, name, data):
    path = Path(tmp) / name
    path.write_bytes(data)
    return path


class FormIdTextTests(unittest.TestCase):
    """R9/P1: a 4-byte field whose bytes look like text (printable, last byte 0) was skipped as text."""

    @unittest.skipUnless(DATA, 'needs the game Data folder')
    def test_formid_with_printable_bytes_is_reference(self):
        esm = DATA / 'FalloutNV.esm'
        data = esm.read_bytes()
        ids, pos = set(), 24 + struct.unpack_from('<I', data, 4)[0]
        while pos + 24 <= len(data):
            tag, size, _, fid = struct.unpack_from('<4sIII', data, pos)
            if tag == b'GRUP':
                pos += 24
                continue
            ids.add(fid)
            pos += 24 + size
        refs = fnv_records.scan_references(esm, lambda v: v in ids)
        races = {r[4] for r in refs if r[1] == 'NPC_' and r[3] == 'RNAM'}
        self.assertTrue({0x000987DC, 0x000987DD, 0x000987DE, 0x000987DF} <= races, sorted('%08X' % r for r in races))

    def test_elijah_rnam_reference(self):
        # NVDLC01Elijah (01001300 in the DLC) names its race with RNAM; a race id with printable low bytes and a 0 top
        # byte reads as text ("ab\x09\0" style). Crafted here with such an id that exists in the master.
        race = 0x000A6261                      # bytes 61 62 0A 00: printable, ends in 0
        npc = record(b'NPC_', 0x01001300, sub(b'EDID', b'NVDLC01Elijah\0') + sub(b'RNAM', struct.pack('<I', race)))
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, 'DeadMoney.esm', plugin(npc))
            refs = fnv_records.scan_references(path, lambda v: v == race)
        self.assertIn((0x01001300, 'NPC_', race), [(r[0], r[1], r[4]) for r in refs])

    def test_text_that_is_not_a_record_stays_text(self):
        npc = record(b'NPC_', 0x01001300, sub(b'RNAM', b'ab\n\0'))
        with tempfile.TemporaryDirectory() as tmp:
            refs = fnv_records.scan_references(write(tmp, 'x.esp', plugin(npc)), lambda v: False)
        self.assertEqual(refs, [])

    def test_decode_subrecord_shows_a_form_not_text(self):
        race = 0x000A6261
        self.assertEqual(decode_subrecord('NPC_', 'RNAM', struct.pack('<I', race)), 'ab\n')
        self.assertIsNone(decode_subrecord('NPC_', 'RNAM', struct.pack('<I', race), is_form=lambda v: v == race))

    def test_refs_version_bumped(self):
        self.assertGreaterEqual(fnv_records.REFS_VERSION, 4)


class Cp1252Tests(unittest.TestCase):
    """R16: strict cp1252 decodes raised on the bytes cp1252 leaves undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D)."""

    def test_undefined_cp1252_byte_in_full_modl_mast(self):
        from geck_mcp.esp import codec
        for raw in (b'Caf\x81', b'meshes\\x\x8d.nif', b'Mod\x9d.esp'):
            text = codec.text(raw)
            self.assertEqual(text.encode('cp1252', 'cp1252-bytes'), raw, 'reads back to the same bytes')

    def test_script_variable_with_an_undefined_byte(self):
        from geck_mcp.esp import scripts
        slsd = struct.pack('<I', 1) + b'\0' * 12 + b'\0' + b'\0' * 7
        layout = scripts.semantic_layout([('SLSD', slsd.hex()), ('SCVR', b'var\x81\0'.hex())])
        self.assertEqual(layout[0][0], 'var\x81')

    def test_no_strict_cp1252_decode_left(self):
        import re
        root = Path(__file__).resolve().parents[1] / 'geck_mcp'
        strict = re.compile(r"""\.decode\(\s*['"]cp1252['"]\s*\)""")
        found = ['%s:%d' % (path.relative_to(root), n) for path in root.rglob('*.py')
                 for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1) if strict.search(line)]
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
