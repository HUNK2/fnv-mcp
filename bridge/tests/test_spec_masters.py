"""Tool gaps 1a: record templates read straight from the master files (no dump file) and overrides of any master
record, not only a DIAL topic."""
import gzip
import json
from pathlib import Path
import struct
import tempfile
import unittest

import esm_index
from fnv_mcp.esp import build as builder, codec
from fnv_mcp.esp.codec import Group, Plugin, Record, Subrecord
from fnv_mcp.esp.formids import FormIdMap, MasterIndex
from fnv_mcp.esp.masters import MasterRecords
from fnv_mcp.esp.records import SpecError


def sub(tag, data):
    return Subrecord(tag, data)


def z(text):
    return text.encode('cp1252') + b'\0'


def armo(form_id, edid, name, value, flags=0):
    return Record('ARMO', form_id, flags=flags, _subrecords=[
        sub('EDID', z(edid)), sub('OBND', struct.pack('<6h', -5, -5, 0, 5, 5, 10)), sub('FULL', z(name)),
        sub('ETYP', struct.pack('<i', -1)), sub('DATA', struct.pack('<iif', value, 100, 2.5))])


def write_master(folder, name, masters, records):
    """A small ESM: a TES4 header with the given masters and one top group per record type."""
    subs = [sub('HEDR', struct.pack('<fII', 1.34, len(records), 0x800)), sub('CNAM', z('tests'))]
    for m in masters:
        subs += [sub('MAST', z(m)), sub('DATA', b'\0' * 8)]
    header = Record('TES4', 0, flags=1, _subrecords=subs)
    groups = {}
    for rec in records:
        if rec.flags & codec.COMPRESSED:
            rec._compressed = codec.compress(codec.encode_subrecords(rec._subrecords))
        groups.setdefault(rec.type, []).append(rec)
    plugin = Plugin(header, [Group(t.encode(), 0, children=recs) for t, recs in groups.items()])
    path = Path(folder) / name
    path.write_bytes(codec.write(plugin))
    return path


def write_index(folder, path):
    out = Path(folder) / (path.name + '.json.gz')
    with gzip.open(out, 'wt', encoding='utf-8') as stream:
        json.dump(esm_index.build_index(path), stream)
    return out


def spec_of(records, masters=('FalloutNV.esm',)):
    return {'schema': 1, 'project_id': 'masterstest', 'plugin': 'MastersTest.esp', 'masters': list(masters),
            'records': records}


class MasterTemplates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.nv = write_master(self.root, 'FalloutNV.esm', [], [
            armo(0x0001ABCD, 'TestLeather', 'Leather Armor', 160),
            armo(0x0001ABCE, 'TestPacked', 'Packed Armor', 77, flags=codec.COMPRESSED)])
        self.dm = write_master(self.root, 'DeadMoney.esm', ['FalloutNV.esm'], [
            armo(0x0001ABCD, 'TestLeather', 'Leather Armor (Dead Money)', 999),     # an override of the NV record
            armo(0x0100A001, 'TestDMSuit', 'Sierra Madre Suit', 50)])
        self.index = MasterIndex([write_index(self.root, self.nv), write_index(self.root, self.dm)])
        self.sources = MasterRecords([self.nv, self.dm])

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, records, masters=('FalloutNV.esm',)):
        spec = spec_of(records, masters)
        index = MasterIndex([write_index(self.root, self.root / m) for m in masters])
        data, receipt = builder.build(spec, FormIdMap(self.root / 'f.json', spec['plugin']), index,
                                      master_records=self.sources)
        return {r.editor_id: r for r, _ in codec.parse(data).records() if r.type != 'TES4'}, receipt

    def test_template_by_editor_id_comes_from_the_master(self):
        recs, _ = self.build({'MyArmor': {'type': 'ARMO', 'template': '@TestLeather',
                                          'fields': [{'tag': 'FULL', 'text': 'My Armor'}]}})
        mine = recs['MyArmor']
        self.assertEqual(mine.form_id >> 24, 1)                        # a new record of this plugin
        self.assertEqual(mine.get('FULL'), z('My Armor'))
        self.assertEqual(struct.unpack('<iif', mine.get('DATA'))[0], 160)
        self.assertEqual([s.tag for s in mine.subrecords], ['EDID', 'OBND', 'FULL', 'ETYP', 'DATA'])

    def test_plain_template_name_without_a_dump_falls_back_to_the_master(self):
        recs, _ = self.build({'MyArmor': {'type': 'ARMO', 'template': 'TestLeather'}})
        self.assertEqual(recs['MyArmor'].get('FULL'), z('Leather Armor'))

    def test_compressed_master_record_is_read(self):
        recs, _ = self.build({'MyPacked': {'type': 'ARMO', 'template': '@TestPacked'}})
        self.assertEqual(recs['MyPacked'].get('FULL'), z('Packed Armor'))
        self.assertFalse(recs['MyPacked'].flags & codec.COMPRESSED)

    def test_template_of_the_wrong_type_is_refused(self):
        with self.assertRaises(SpecError) as caught:
            self.build({'MyMisc': {'type': 'MISC', 'template': '@TestLeather'}})
        self.assertIn('ARMO', str(caught.exception))

    def test_override_keeps_the_master_form_id(self):
        recs, receipt = self.build({'TestLeather': {'type': 'ARMO', 'override': True,
                                                    'fields': [{'tag': 'DATA', 'patch': [[0, '<i', 200]]}]}})
        over = recs['TestLeather']
        self.assertEqual(over.form_id, 0x0001ABCD)
        self.assertEqual(struct.unpack('<iif', over.get('DATA'))[0], 200)
        self.assertEqual(over.get('FULL'), z('Leather Armor'))
        self.assertNotIn('TestLeather', receipt['formIds'])
        self.assertEqual(receipt['overrides'], {'TestLeather': {'formId': '0001ABCD', 'from': 'FalloutNV.esm'}})

    def test_override_takes_the_winning_master_version(self):
        recs, receipt = self.build({'TestLeather': {'type': 'ARMO', 'override': True}},
                                   masters=('FalloutNV.esm', 'DeadMoney.esm'))
        self.assertEqual(recs['TestLeather'].get('FULL'), z('Leather Armor (Dead Money)'))
        self.assertEqual(receipt['overrides']['TestLeather']['from'], 'DeadMoney.esm')

    def test_override_of_a_dlc_record_keeps_its_form_id(self):
        recs, _ = self.build({'TestDMSuit': {'type': 'ARMO', 'override': True}},
                             masters=('FalloutNV.esm', 'DeadMoney.esm'))
        self.assertEqual(recs['TestDMSuit'].form_id, 0x0100A001)

    def test_override_by_form_id_under_a_label(self):
        recs, _ = self.build({'LeatherFix': {'type': 'ARMO', 'override': '0001ABCD'}})
        self.assertEqual(recs['TestLeather'].form_id, 0x0001ABCD)        # the master's EditorID stays

    def test_override_of_an_unknown_record_is_refused(self):
        with self.assertRaises(SpecError) as caught:
            self.build({'NoSuchArmor': {'type': 'ARMO', 'override': True}})
        self.assertIn('NoSuchArmor', str(caught.exception))

    def test_override_of_the_wrong_type_is_refused(self):
        with self.assertRaises(SpecError):
            self.build({'TestLeather': {'type': 'WEAP', 'override': True}})

    def test_check_spec_accepts_override_on_any_type(self):
        builder.check_spec(spec_of({'TestLeather': {'type': 'ARMO', 'override': True}}))

    def test_a_template_whose_masters_are_not_listed_first_is_refused(self):
        # DeadMoney.esm's FormIDs count FalloutNV.esm as 00 and itself as 01: the plugin must list them so.
        with self.assertRaises(SpecError) as caught:
            self.build({'MySuit': {'type': 'ARMO', 'template': '@TestDMSuit'}},
                       masters=('DeadMoney.esm', 'FalloutNV.esm'))
        self.assertIn('DeadMoney.esm', str(caught.exception))

    def test_no_master_file_says_so(self):
        spec = spec_of({'MyArmor': {'type': 'ARMO', 'template': '@TestLeather'}})
        with self.assertRaises(SpecError) as caught:
            builder.build(spec, FormIdMap(self.root / 'g.json', spec['plugin']), self.index,
                          master_records=MasterRecords([]))
        self.assertIn('FalloutNV.esm', str(caught.exception))


if __name__ == '__main__':
    unittest.main()


class AuthoringReadsMasters(unittest.TestCase):
    """fnv_spec through Authoring: the game's master file is found in Data, its index is made when missing."""

    def setUp(self):
        from fnv_mcp.config import Project
        from fnv_mcp.fnv_spec import SpecTools
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        data = root / 'game' / 'Data'
        data.mkdir(parents=True)
        write_master(data, 'FalloutNV.esm', [], [armo(0x0001ABCD, 'TestLeather', 'Leather Armor', 160)])
        self.spec_path = root / 'm.spec.json'
        self.spec_path.write_text(json.dumps(spec_of({
            'MyArmor': {'type': 'ARMO', 'template': '@TestLeather'},
            'TestLeather': {'type': 'ARMO', 'override': True}})))
        self.state = root / 'state'
        self.p = Project(project_id='masterstest', game_root=root / 'game', plugin='MastersTest.esp',
                         state_dir=self.state, spec=self.spec_path)
        self.tools = SpecTools(self.p)
        from unittest import mock
        # the code root's own state/index holds the real FalloutNV.esm index: keep it out of this test
        self.patch = mock.patch('fnv_mcp.authoring.CODE_STATE', root / 'no-code-state')
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_build_reads_the_master_and_indexes_it(self):
        result = self.tools.run('build')
        self.assertTrue(result['ok'], result)
        self.assertTrue((self.state / 'index' / 'FalloutNV.esm.json.gz').exists())
        self.assertEqual(result['data']['recordCounts'].get('ARMO'), 2)
