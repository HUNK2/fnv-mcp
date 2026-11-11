"""Plan 2 completion pass: the pure parts of the actions added to finish the plan (no game needed).

Voice file names (fnv_crawl / fnv_audio voice_check), NIF child links and DDS checks (fnv_asset nif / dds), the menu
price rounding (fnv_systems barter), the fnv_orig trampoline binding and the patch-set loader (fnv_code)."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_code, fnv_create, fnv_crawl, fnv_inspect  # noqa: E402
from geck_mcp.esp.assets import nif_block_types  # noqa: E402


def nif(blocks):
    """A minimal NIF 20.2.0.7 / 11 / 34 file: blocks = [(type name, body bytes)]."""
    types = []
    for name, _ in blocks:
        if name not in types:
            types.append(name)
    out = bytearray(b'Gamebryo File Format, Version 20.2.0.7\n')
    out += struct.pack('<IBII', 0x14020007, 1, 11, len(blocks)) + struct.pack('<I', 34)
    for text in (b'test', b'', b''):
        out += bytes([len(text)]) + text
    out += struct.pack('<H', len(types))
    for t in types:
        out += struct.pack('<I', len(t)) + t.encode('ascii')
    out += struct.pack('<%dH' % len(blocks), *[types.index(n) for n, _ in blocks])
    out += struct.pack('<%dI' % len(blocks), *[len(b) for _, b in blocks])
    out += struct.pack('<II', 0, 0) + struct.pack('<I', 0)
    for _, body in blocks:
        out += body
    return bytes(out)


def node(children):
    body = struct.pack('<iIi', 0, 0, -1) + struct.pack('<I', 0x0008000E) + b'\0' * 12
    body += struct.pack('<9f', 1, 0, 0, 0, 1, 0, 0, 0, 1) + struct.pack('<f', 1.0)
    body += struct.pack('<Ii', 0, -1) + struct.pack('<I', len(children)) + struct.pack('<%di' % len(children),
                                                                                       *children)
    return body + struct.pack('<I', 0)


class VoiceNameTests(unittest.TestCase):
    def test_long_names_cut_the_quest_to_ten(self):
        self.assertEqual(fnv_crawl.voice_stem('VFreeformGoodsprings', 'PLAYERTHROWGRENADE', 0x001254A9, 1),
                         'vfreeformg_playerthrowgren_001254a9_1')

    def test_short_quest_leaves_the_topic_the_rest_of_25(self):
        self.assertEqual(fnv_crawl.voice_stem('VDoctors', 'DoctorMedical33YES', 0x0014C000, 2),
                         'vdoctors_doctormedical33ye_0014c000_2')

    def test_names_that_fit_are_not_cut_and_the_prefix_is_00(self):
        self.assertEqual(fnv_crawl.voice_stem('VDialogueBenny', 'GREETING', 0x05012345, 1),
                         'vdialoguebenny_greeting_00012345_1')

    def test_response_number_comes_from_trdt_byte_12(self):
        field = {'hex': ' '.join(['00'] * 12 + ['03'] + ['00'] * 11)}
        self.assertEqual(fnv_crawl._response_number(field, 0), 3)
        self.assertEqual(fnv_crawl._response_number({}, 1), 2)


class NifTests(unittest.TestCase):
    def test_a_child_link_to_strip_data_is_found(self):
        data = nif([('BSFadeNode', node([1])), ('NiTriStripsData', b'\0' * 8)])
        head = nif_block_types(data)
        self.assertEqual(fnv_create._node_children(data, head, 0), [1])
        self.assertFalse(fnv_create._is_av_object(head['blocks'][1]))

    def test_a_shape_child_is_fine_and_an_impossible_count_is_outside(self):
        data = nif([('NiNode', node([1, -1])), ('NiTriStrips', b'\0' * 8)])
        head = nif_block_types(data)
        self.assertEqual(fnv_create._node_children(data, head, 0), [1, -1])
        self.assertTrue(fnv_create._is_av_object('NiTriStrips'))
        broken = bytearray(data)
        count_at = head['offsets'][0] + 4 + 4 + 4 + 4 + 12 + 36 + 4 + 4 + 4
        struct.pack_into('<I', broken, count_at, 0x10000000)
        self.assertGreaterEqual(fnv_create._node_children(bytes(broken), head, 0)[0], 2)


class DdsTests(unittest.TestCase):
    def tool(self, data):
        tool = fnv_create.AssetTools(SimpleNamespace())
        tool._file_bytes = lambda path: (data, 'test')
        return tool

    def header(self, w, h, mips, fourcc=b'DXT1'):
        raw = bytearray(128)
        raw[0:4] = b'DDS '
        struct.pack_into('<II', raw, 12, h, w)
        struct.pack_into('<I', raw, 28, mips)
        struct.pack_into('<I4sI', raw, 80, 0x4, fourcc, 0)
        return bytes(raw)

    def test_full_chain_power_of_two_passes(self):
        out = self.tool(self.header(256, 128, 9)).dds('x.dds')
        self.assertTrue(out['ok'])
        self.assertEqual((out['format'], out['fullMipChain'], out['warnings']), ('DXT1', 9, []))

    def test_odd_size_fails_and_a_short_chain_warns(self):
        out = self.tool(self.header(300, 128, 1)).dds('x.dds')
        self.assertFalse(out['ok'])
        self.assertTrue(any('mip' in w for w in out['warnings']))


class BarterRoundingTests(unittest.TestCase):
    def test_menu_rounding(self):
        self.assertEqual(fnv_inspect._menu_round(12.6), 13)
        self.assertEqual(fnv_inspect._menu_round(0.46), 0.5)


class CodeLinkTests(unittest.TestCase):
    def test_orig_relocation_binds_to_the_trampoline(self):
        link = {'code': '90e800000000c3', 'relocs': [{'offset': 2, 'kind': fnv_code.REL32, 'orig': 0x00643C20,
                                                      'addend': -4}]}
        code = fnv_code.CodeTools._final(link, 0x10000000, {0x00643C20: 0x20000000})
        self.assertEqual(struct.unpack_from('<i', code, 2)[0], 0x20000000 - 4 - (0x10000000 + 2 + 4))

    def test_orig_name_pattern(self):
        self.assertEqual(fnv_code.ORIG_RE.match('_fnv_orig_00643C20').group(1), '00643C20')
        self.assertIsNone(fnv_code.ORIG_RE.match('_fnv_orig_643C20'))

    def test_loader_source_checks_bytes_and_binds_relocations(self):
        link = {'code': 'e800000000c3', 'relocs': [{'offset': 1, 'kind': fnv_code.REL32, 'absolute': 0x00401000},
                                                   {'offset': 1, 'kind': fnv_code.DIR32, 'blob': 4}]}
        text = fnv_code.CodeTools._loader_source(0x00643C20, b'\x55\x8b\xec', link)
        self.assertIn('#define TARGET 0x00643C20u', text)
        self.assertIn('{1, 1, 0x00401000u, 0}', text)
        self.assertIn('{1, 0, 4u, 1}', text)
        self.assertIn('0x55, 0x8b, 0xec', text)


class BisectRecordTests(unittest.TestCase):
    def test_variant_keeps_only_the_records_turned_on(self):
        from geck_mcp import fnv_suite
        from geck_mcp.esp import codec, records
        statics = [records.stat(0x01000800 + i, 'BisectStat%d' % i, {'model': 'clutter\\box.nif'}) for i in range(3)]
        plugin = codec.Plugin(records.tes4(['FalloutNV.esm'], 4, 0x803),
                              [codec.Group(b'STAT', 0, children=statics)])
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'Bisect.esp'
            path.write_bytes(codec.write(plugin))
            tool = fnv_suite.BisectTools(SimpleNamespace(), None, None, quit_game=lambda: None)
            tool.plugin = 'Bisect.esp'
            tool._plugin_file = lambda: path
            self.assertEqual(tool._record_candidates(None), ['01000800', '01000801', '01000802'])
            tool._write_variant({'01000801'})
            kept = [('%08X' % r.form_id) for r, _ in codec.parse(path.read_bytes()).records() if r.type == 'STAT']
            self.assertEqual(kept, ['01000801'])
            tool._restore_plugin()
            self.assertEqual(path.read_bytes(), codec.write(plugin))


if __name__ == '__main__':
    unittest.main()
