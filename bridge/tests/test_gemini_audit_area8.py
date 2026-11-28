"""Gemini audit area 8 (BUG-109..125): record index, ESP/ESM, assets and plugins."""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from fnv_mcp import fnv_plugin_build  # noqa: E402
from fnv_mcp.esp import assets, codec, formids, generic, layouts, validate  # noqa: E402
from fnv_mcp.esp.build import SpecError  # noqa: E402,F401

PROLOGUE = bytes([0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x10]) + bytes(18)


def fake_image(path, base=0x400000, va=0x1000):
    """A minimal PE with one section holding PROLOGUE at base + va."""
    pe, opt = 0x40, 0xE0
    data = bytearray(0x400)
    struct.pack_into('<I', data, 0x3C, pe)
    data[pe:pe + 4] = b'PE\0\0'
    struct.pack_into('<H', data, pe + 6, 1)
    struct.pack_into('<H', data, pe + 20, opt)
    struct.pack_into('<I', data, pe + 24 + 28, base)
    sec = pe + 24 + opt
    struct.pack_into('<IIII', data, sec + 8, 0x100, va, 0x100, 0x200)
    data[0x200:0x200 + len(PROLOGUE)] = PROLOGUE
    Path(path).write_bytes(bytes(data))


class PluginPlanWithImage(unittest.TestCase):
    def test_plans_with_a_clean_image_do_not_double_wrap(self):          # BUG-109
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / 'FalloutNV.exe'
            fake_image(image)
            live = SimpleNamespace(engine=lambda: SimpleNamespace(resolve=lambda n: 0x401000),
                                   project=SimpleNamespace(state_dir=tmp))
            tools = fnv_plugin_build.PluginTools(live)
            tools._image_path = lambda: image
            rows, plans = tools._plans(['0x00401000'])
        self.assertEqual(plans[0]['from'], 'local game image')
        self.assertEqual(plans[0]['stolen'], 6)
        self.assertEqual(len(rows), 1)


class PerkConditions(unittest.TestCase):
    def test_effect_ctda_equal_to_a_record_ctda_is_kept(self):           # BUG-110
        same = b'\1' * 28
        subs = [codec.Subrecord('EDID', b'P\0'), codec.Subrecord('CTDA', same), codec.Subrecord('PRKE', b'x'),
                codec.Subrecord('CTDA', same), codec.Subrecord('DATA', b'd')]
        layouts.record_conditions('PERK', subs, [], lambda v, w: 0, 'records.P')
        self.assertEqual([s.tag for s in subs], ['EDID', 'PRKE', 'CTDA', 'DATA'])


class OverrideSpec(unittest.TestCase):
    def test_build_override_refuses_unknown_keys(self):                  # BUG-119
        with self.assertRaises(SpecError) as cm:
            generic.build_override('X', {'type': 'WEAP', 'override': True, 'feilds': []}, None, [], None)
        self.assertIn('feilds', str(cm.exception))

    def test_override_topic_passes_set(self):                            # BUG-119
        from fnv_mcp.esp import build
        seen = {}
        resolver = SimpleNamespace(resolve=lambda ref, expect_types=None: 0x00001234)
        with mock.patch.object(build.generic, 'build_generic',
                               lambda form, edid, spec, *a, **k: seen.update(spec) or 'rec'):
            build.override_topic('GREETING', {'type': 'DIAL', 'override': True, 'set': {'x': 1}}, resolver, ['A.esm'],
                                 None)
        self.assertEqual(seen['set'], {'x': 1})


class ResolverChecks(unittest.TestCase):
    def test_own_record_respects_expect_types_and_master_case(self):     # BUG-120
        r = formids.Resolver(['FalloutNV.esm'], {'Mine': 0x01000800}, own_types={'Mine': 'WEAP'})
        with self.assertRaises(formids.FormIdError):
            r.resolve('@Mine', expect_types={'DIAL'})
        self.assertEqual(r.resolve('@Mine', expect_types={'WEAP'}), 0x01000800)
        self.assertEqual(r._from_master('FALLOUTNV.ESM', 0x123), 0x123)
        r2 = formids.Resolver(['falloutnv.esm'], {})
        r2.index.masters.append('FalloutNV.esm')
        r2.index.entries['thing'] = [(0, 'MISC', 0x00000456, None, None, 'Thing')]
        self.assertEqual(r2.resolve('@Thing'), 0x00000456)


def plugin_with(form_id):
    header = codec.Record('TES4', 0, _subrecords=[codec.Subrecord('HEDR', struct.pack('<fII', 1.34, 1, 0x800)),
                                                 codec.Subrecord('MAST', b'FalloutNV.esm\0')])
    rec = codec.Record('MISC', form_id, _subrecords=[codec.Subrecord('EDID', b'Thing\0')])
    return codec.write(codec.Plugin(header, [codec.Group(b'MISC', 0, children=[rec])]))


class ValidateByte(unittest.TestCase):
    def test_load_order_byte_past_own_is_an_error(self):                 # BUG-118
        out = validate.validate(plugin_with(0x05000800))
        self.assertTrue(any('load-order byte' in e for e in out['errors']), out)
        out = validate.validate(plugin_with(0x01000800))
        self.assertFalse(any('load-order byte' in e for e in out['errors']), out)


class BsaSize(unittest.TestCase):
    def test_negative_embedded_size_is_refused(self):                    # BUG-121
        folder, name = b'meshes\0', b'a.nif\0'
        header = b'BSA\0' + struct.pack('<8I', 104, 36, 0x100 | 0x3, 1, 1, 7, len(name), 0)
        body = struct.pack('<QII', 0, 1, 36 + 16 + 1 + len(folder) + 16 + len(name))
        body += bytes([len(folder)]) + folder + struct.pack('<QII', 0, 2, 0)
        # data offset points at a record whose path bstring is longer than its size
        data_at = 36 + len(body) + len(name)
        body = struct.pack('<QII', 0, 1, 36 + 16) + bytes([len(folder)]) + folder + struct.pack('<QII', 0, 2, data_at)
        blob = header + body + name + bytes([20]) + b'x' * 20
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.bsa'
            path.write_bytes(blob)
            with self.assertRaises(assets.AssetError) as cm:
                assets._bsa_extract(path, {'meshes\\a.nif'})
        self.assertIn('impossible size', str(cm.exception))


class ModPriority(unittest.TestCase):
    def test_mod_loose_file_beats_data(self):                            # BUG-113
        from fnv_mcp.authoring import Authoring
        with tempfile.TemporaryDirectory() as tmp:
            data, mod = Path(tmp) / 'Data', Path(tmp) / 'mod'
            for root in (data, mod):
                (root / 'meshes').mkdir(parents=True)
                (root / 'meshes' / 'a.nif').write_bytes(b'x')
            auth = Authoring.__new__(Authoring)
            auth.data_dir = data
            auth.mod_roots = lambda: [mod]
            hit = auth.asset_index().find('meshes\\a.nif')
        self.assertEqual(Path(hit['path']), mod / 'meshes' / 'a.nif')


class ForgetPlugin(unittest.TestCase):
    def test_non_ascii_path_case_is_matched(self):                       # BUG-114
        from fnv_mcp.fnv_data import RecordIndex, forget_plugin
        with tempfile.TemporaryDirectory() as tmp:
            index = RecordIndex(tmp)
            index.db.execute('insert into files(id, path, name, size, mtime, version, masters, records, built) '
                             'values (1, ?, ?, 1, 1, 1, ?, 0, 1)', ('C:\\Data\\Ärger.esp', 'Ärger.esp', '[]'))
            index.db.commit()
            index.db.close()
            index._local.db = None
            self.assertEqual(forget_plugin(tmp, 'c:\\data\\ärger.esp'), 1)


class ProfileBackupName(unittest.TestCase):
    def test_retention_prunes_profile_backups(self):                     # BUG-123
        from fnv_mcp import fnv_state
        src = Path(fnv_state.__file__).read_text(encoding='utf-8')
        self.assertIn("'profile-backups'", src)


if __name__ == '__main__':
    unittest.main()
