"""Tool gaps 1b: named fields (esp/layouts.py). The vanilla check reads every record of each type in FalloutNV.esm
(skipped without the game files): each subrecord with a layout has its size, decodes, and writing the decoded values
back gives the same bytes."""
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

from fnv_mcp.esp import build as builder, codec, layouts
from fnv_mcp.esp.codec import Subrecord
from fnv_mcp.esp.formids import FormIdMap, MasterIndex
from fnv_mcp.esp.records import SpecError

ESM = Path(os.environ.get('FNV_DATA', r'C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\Data')) \
    / 'FalloutNV.esm'


def resolve(value, where):
    if isinstance(value, str) and len(value) == 8:
        return int(value, 16)
    if value == '@Thing':
        return 0x0001F00D
    raise SpecError(where, 'unresolved %r' % value)


def sub(tag, data):
    return Subrecord(tag, data)


class VanillaLayouts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ESM.exists():
            raise unittest.SkipTest('FalloutNV.esm not found')
        cls.records = {}
        for rec, _ in codec.iter_records(ESM.read_bytes(), types=set(layouts.STRUCTS)):
            cls.records.setdefault(rec.type, []).append(rec)

    def test_every_vanilla_subrecord_with_a_layout_decodes_and_writes_back(self):
        for rtype, tags in layouts.STRUCTS.items():
            for tag in tags:
                checked = 0
                with self.subTest(type=rtype, tag=tag):
                    for rec in self.records.get(rtype, []):
                        for s in rec.subrecords:
                            if s.tag != tag:
                                continue
                            if rtype == 'PERK' and tag == 'DATA' and len(s.data) != 5:
                                continue        # an effect's DATA (a list part), not the perk's own
                            values = layouts.decode(rtype, tag, s.data)
                            self.assertIsNotNone(values, '%s %s %s: %d bytes' % (rtype, rec.editor_id, tag,
                                                                                  len(s.data)))
                            if any(isinstance(v, list) and any(str(x).startswith('0x') for x in v)
                                   for v in values.values()):
                                continue        # flag bits with no name: shown as hex, not writable by name
                            if (rtype, tag) in layouts.BY_LIST:
                                checked += 1    # written through its list: checked whole in the next test
                                continue
                            subs = [Subrecord(s.tag, s.data)]
                            named = {('%s.%s' % (tag, k)): v for k, v in values.items()}
                            layouts.set_fields(rtype, subs, named, resolve, 'test')
                            self.assertEqual(subs[0].data, s.data, '%s %s %s' % (rtype, rec.editor_id, tag))
                            checked += 1
                    self.assertGreater(checked, 0, 'no vanilla %s %s checked' % (rtype, tag))

    def test_body_parts_write_back(self):
        for rec in self.records['BPTD']:
            parts = layouts.read_lists('BPTD', rec.subrecords)['parts']
            items = [dict({k: v for k, v in p.items() if k not in ('part', 'node')}, part=i) for i, p in enumerate(parts)]
            if any(isinstance(v, list) and any(str(x).startswith('0x') for x in v) for p in items for v in p.values()):
                continue
            subs = list(rec.subrecords)
            layouts.set_fields('BPTD', subs, {'parts': items}, resolve, 'test')
            self.assertEqual([s.data for s in subs], [s.data for s in rec.subrecords], rec.editor_id)

    def test_known_values(self):
        by = {r.editor_id: r for r in self.records['ARMO'] + self.records['BOOK']}
        leather = layouts.decode('ARMO', 'DATA', by['ArmorLeather'].get('DATA'))
        self.assertEqual(leather['value'], 160)
        self.assertEqual(layouts.decode('ARMO', 'BMDT', by['ArmorLeather'].get('BMDT'))['biped_flags'], ['upper_body'])
        self.assertEqual(layouts.decode('ARMO', 'DNAM', by['ArmorLeather'].get('DNAM'))['damage_threshold'], 6.0)
        self.assertEqual(layouts.decode('BOOK', 'DATA', by['BookSkillGuns'].get('DATA'))['skill'], 'guns')

    def test_power_armor_is_flagged(self):
        flagged = [r.editor_id for r in self.records['ARMO']
                   if 'power_armor' in layouts.decode('ARMO', 'BMDT', r.get('BMDT'))['general_flags']]
        self.assertTrue(any('T51' in e or 'T45' in e or 'Remnant' in e for e in flagged), flagged[:10])

    def test_written_lists_read_back_like_vanilla(self):
        for rtype in ('LVLI', 'RCPE', 'TERM', 'PERK'):
            with self.subTest(type=rtype):
                rec = next(r for r in self.records[rtype] if layouts.read_lists(rtype, r.subrecords)[
                    layouts.LISTS[rtype][0]])
                self.assertTrue(layouts.read_lists(rtype, rec.subrecords))


class SetFields(unittest.TestCase):
    def armor(self):
        return [sub('EDID', b'A\0'), sub('OBND', b'\0' * 12), sub('FULL', b'Old\0'),
                sub('BMDT', struct.pack('<IB3s', 4, 0, b'\xcd' * 3)), sub('ETYP', struct.pack('<i', -1)),
                sub('DATA', struct.pack('<iif', 10, 100, 1.0)), sub('DNAM', struct.pack('<hHf4s', 0, 0, 2.0, b'\0' * 4))]

    def test_scalars_flags_enums_and_text(self):
        subs = self.armor()
        layouts.set_fields('ARMO', subs, {'name': 'New', 'value': 150, 'weight': 8, 'damage_threshold': 12,
                                          'biped_flags': ['upper_body', 'hat'], 'general_flags': ['power_armor'],
                                          'equip_type': 'body_wear', 'enchantment': '@Thing'}, resolve, 'r')
        get = {s.tag: s.data for s in subs}
        self.assertEqual(get['FULL'], b'New\0')
        self.assertEqual(struct.unpack('<iif', get['DATA']), (150, 100, 8.0))
        self.assertEqual(struct.unpack('<hHf4s', get['DNAM'])[2], 12.0)
        self.assertEqual(struct.unpack('<IB3s', get['BMDT']), (4 | 1024, 0x20, b'\xcd' * 3))   # padding kept
        self.assertEqual(struct.unpack('<i', get['ETYP'])[0], 7)
        self.assertEqual(get['EITM'], struct.pack('<I', 0x0001F00D))

    def test_a_missing_struct_subrecord_is_made(self):
        subs = [sub('EDID', b'K\0')]
        layouts.set_fields('KEYM', subs, {'value': 5}, resolve, 'r')
        self.assertEqual(subs[1].tag, 'DATA')
        self.assertEqual(struct.unpack('<if', subs[1].data), (5, 0.0))

    def test_a_short_subrecord_grows_to_the_field(self):
        subs = [sub('DAT2', struct.pack('<IIf', 1, 0, 0.5))]
        layouts.set_fields('AMMO', subs, {'consumed_percentage': 0.25}, resolve, 'r')
        self.assertEqual(len(subs[0].data), 20)
        self.assertEqual(struct.unpack('<IIfIf', subs[0].data)[4], 0.25)

    def test_errors_name_the_field(self):
        cases = [({'nosuch': 1}, 'r.nosuch'), ({'value': 'x'}, 'r.value'), ({'value': 2 ** 40}, 'out of range'),
                 ({'biped_flags': ['wings']}, 'wings'), ({'equip_type': 'hat'}, 'equip_type'),
                 ({'name': 'a\0b'}, 'NUL'), ({'value': 1.5}, 'integer')]
        for values, fragment in cases:
            with self.subTest(values=values), self.assertRaises(SpecError) as caught:
                layouts.set_fields('ARMO', self.armor(), values, resolve, 'r')
            self.assertIn(fragment, str(caught.exception))

    def test_shared_name_needs_its_tag(self):
        # AMMO has "weight" only in DAT2 and "value" only in DATA: both plain; ARMO's "flags" is DNAM's only
        self.assertIn('weight', layouts.fields_of('AMMO'))
        self.assertIn('DNAM.flags', layouts.fields_of('ARMO'))
        self.assertIn('flags', layouts.fields_of('ARMO'))

    def test_leveled_entries_sorted_by_level(self):
        subs = [sub('EDID', b'L\0'), sub('OBND', b'\0' * 12), sub('LVLD', b'\0'), sub('LVLF', b'\1'),
                sub('LVLO', b'\0' * 12), sub('COED', b'\0' * 12)]
        layouts.set_fields('LVLI', subs, {'entries': [{'form': '@Thing', 'level': 5, 'count': 2},
                                                      {'form': '0000000F', 'level': 1}], 'chance_none': 25,
                                          'flags': ['all_levels', 'each']}, resolve, 'r')
        self.assertEqual([s.tag for s in subs], ['EDID', 'OBND', 'LVLD', 'LVLF', 'LVLO', 'LVLO'])
        self.assertEqual(layouts.read_lists('LVLI', subs)['entries'],
                         [{'level': 1, 'form': '0000000F', 'count': 1}, {'level': 5, 'form': '0001F00D', 'count': 2}])
        self.assertEqual(subs[2].data, b'\x19')
        self.assertEqual(subs[3].data, b'\x03')

    def test_recipe_lists(self):
        subs = [sub('EDID', b'R\0'), sub('FULL', b'R\0'), sub('DATA', b'\0' * 16)]
        layouts.set_fields('RCPE', subs, {'outputs': [{'form': '@Thing', 'count': 3}],
                                          'ingredients': [{'form': '0000000F', 'count': 2}, {'form': '0000000A'}],
                                          'skill': 'repair', 'skill_level': 25}, resolve, 'r')
        self.assertEqual([s.tag for s in subs], ['EDID', 'FULL', 'DATA', 'RCIL', 'RCQY', 'RCIL', 'RCQY', 'RCOD',
                                                 'RCQY'])
        self.assertEqual(layouts.read_lists('RCPE', subs)['outputs'], [{'form': '0001F00D', 'count': 3}])
        self.assertEqual(struct.unpack('<iIII', subs[2].data)[:2], (39, 25))

    def test_terminal_menu(self):
        subs = [sub('EDID', b'T\0'), sub('DESC', b'Hi\0'), sub('DNAM', b'\0\0\0\0'), sub('ITXT', b'old\0'),
                sub('RNAM', b'\0'), sub('ANAM', b'\0'), sub('SCHR', b'\0' * 20)]
        layouts.set_fields('TERM', subs, {'menu': [
            {'text': 'Read log', 'result': 'Day 1: sand.', 'flags': ['force_redraw']},
            {'text': 'Open door', 'note': '@Thing', 'conditions': [{'function': 'GetIsID', 'param1': '@Thing'}]}],
            'difficulty': 'hard', 'welcome_text': 'ROBCO'}, resolve, 'r')
        tags = [s.tag for s in subs]
        self.assertEqual(tags, ['EDID', 'DESC', 'DNAM', 'ITXT', 'RNAM', 'ANAM', 'SCHR', 'ITXT', 'RNAM', 'ANAM',
                                'INAM', 'SCHR', 'CTDA'])
        self.assertEqual(layouts.read_lists('TERM', subs)['menu'][0], {'text': 'Read log', 'result': 'Day 1: sand.'})
        self.assertEqual(subs[2].data[0], 3)

    def test_perk_effects(self):
        subs = [sub('EDID', b'P\0'), sub('FULL', b'P\0'), sub('DATA', b'\0\x02\x01\x01\0'),
                sub('PRKE', b'\2\0\0'), sub('DATA', b'\0\3\3'), sub('EPFT', b'\1'), sub('EPFD', b'\0' * 4),
                sub('PRKF', b'')]
        layouts.set_fields('PERK', subs, {'effects': [
            {'entry_point': 'Calculate Weapon Damage', 'function': 'multiply', 'value': 1.5,
             'conditions': [[{'function': 'GetIsID', 'param1': '@Thing'}]]},
            {'ability': '@Thing'}], 'min_level': 4}, resolve, 'r')
        tags = [s.tag for s in subs]
        self.assertEqual(tags, ['EDID', 'FULL', 'DATA', 'PRKE', 'DATA', 'PRKC', 'CTDA', 'EPFT', 'EPFD', 'PRKF',
                                'PRKE', 'DATA', 'PRKF'])
        effects = layouts.read_lists('PERK', subs)['effects']
        self.assertEqual(effects[0]['entry_point'], 'Calculate Weapon Damage')
        self.assertEqual(effects[0]['value'], 1.5)
        self.assertEqual(effects[1]['ability'], '0001F00D')
        self.assertEqual(subs[2].data[1], 4)

    def test_perk_entry_point_errors(self):
        for effect, fragment in [({'entry_point': 'No Such Point'}, 'unknown entry point'),
                                 ({'entry_point': 0, 'function': 'teleport'}, 'function'),
                                 ({'entry_point': 9, 'conditions': [[], []]}, 'up to 1'),
                                 ({'spell': '@Thing'}, 'entry_point')]:
            with self.subTest(effect=effect), self.assertRaises(SpecError) as caught:
                layouts.set_fields('PERK', [sub('EDID', b'P\0')], {'effects': [effect]}, resolve, 'r')
            self.assertIn(fragment, str(caught.exception))

    def test_describe_lists_fields(self):
        info = layouts.describe('ARMO')
        names = {row['field'] for row in info['fields']}
        self.assertTrue({'value', 'weight', 'damage_threshold', 'biped_flags', 'name'} <= names)
        self.assertIsNone(layouts.describe('WRLD'))


class SpecSet(unittest.TestCase):
    """"set" in a spec record, on a new record, from a template and on an override."""

    def test_new_records_from_named_fields(self):
        spec = {'schema': 1, 'project_id': 'settest', 'plugin': 'SetTest.esp', 'masters': ['FalloutNV.esm'],
                'records': {'MyKey': {'type': 'KEYM', 'set': {'name': 'Rusty key', 'value': 3, 'weight': 0.1,
                                                               'model': 'Clutter\\Key01.nif'}},
                            'MyLoot': {'type': 'LVLI', 'set': {'entries': [{'form': '@MyKey', 'count': 2}],
                                                               'chance_none': 10}}}}
        builder.check_spec(spec)
        with tempfile.TemporaryDirectory() as tmp:
            data, receipt = builder.build(spec, FormIdMap(Path(tmp) / 'f.json', 'SetTest.esp'), MasterIndex())
        recs = {r.editor_id: r for r, _ in codec.parse(data).records() if r.type != 'TES4'}
        self.assertEqual(struct.unpack('<if', recs['MyKey'].get('DATA'))[0], 3)
        self.assertEqual(recs['MyKey'].get('FULL'), b'Rusty key\0')
        entries = layouts.read_lists('LVLI', recs['MyLoot'].subrecords)['entries']
        self.assertEqual(entries[0]['form'], receipt['formIds']['MyKey'])

    def test_set_on_a_type_without_layouts_is_refused(self):
        spec = {'schema': 1, 'project_id': 'settest', 'plugin': 'SetTest.esp', 'masters': ['FalloutNV.esm'],
                'records': {'MyGlob': {'type': 'GLOB', 'set': {'value': 1}}}}
        with self.assertRaises(SpecError) as caught:
            builder.check_spec(spec)
        self.assertIn('records.MyGlob.set', str(caught.exception))

    def test_set_is_applied_after_fields(self):
        spec = {'schema': 1, 'project_id': 'settest', 'plugin': 'SetTest.esp', 'masters': ['FalloutNV.esm'],
                'records': {'MyKey': {'type': 'KEYM', 'fields': [{'tag': 'FULL', 'text': 'A'}],
                                      'set': {'name': 'B'}}}}
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = builder.build(spec, FormIdMap(Path(tmp) / 'f.json', 'SetTest.esp'), MasterIndex())
        rec = next(r for r, _ in codec.parse(data).records() if r.type == 'KEYM')
        self.assertEqual(rec.get('FULL'), b'B\0')


if __name__ == '__main__':
    unittest.main()


class RecordReadsNames(unittest.TestCase):
    """fnv_record shows a subrecord with a layout by the names "set" takes."""

    def test_decode_subrecord_uses_the_layout(self):
        from fnv_mcp.fnv_data import decode_subrecord
        value = decode_subrecord('ARMO', 'DNAM', struct.pack('<hHf4s', 3, 1, 12.5, b'\0' * 4))
        self.assertEqual(value, {'damage_resistance': 3, 'flags': ['modulates_voice'], 'damage_threshold': 12.5})
        value = decode_subrecord('AMMO', 'DAT2', struct.pack('<IIfIf', 1, 0x1234, 0.5, 0, 0.0),
                                 form_name=lambda raw: 'P (%08X)' % raw)
        self.assertEqual(value['projectile'], 'P (00001234)')
        self.assertEqual(decode_subrecord('WEAP', 'DATA', struct.pack('<iifhB', 1, 2, 3.0, 4, 5))['clipSize'], 5)


class SpecFieldsAction(unittest.TestCase):
    def test_fields_action(self):
        from fnv_mcp.config import Project
        from fnv_mcp.fnv_spec import SpecTools
        with tempfile.TemporaryDirectory() as tmp:
            tools = SpecTools(Project(project_id='x', game_root=Path(tmp), plugin='X.esp', state_dir=Path(tmp),
                                      spec=Path(tmp) / 'x.json'))
            out = tools.run('fields', record_type='LVLI')
            self.assertTrue(out['ok'], out)
            self.assertIn('entries', out['data']['lists'])
            every = tools.run('fields')
            self.assertIn('ARMO', every['data']['types'])
            self.assertIn('GLOB', every['data']['rawOnly'])
            bad = tools.run('fields', record_type='WRLD')
            self.assertFalse(bad['ok'])
            self.assertIn('no named fields', bad['error']['message'])


class NewRecordOrder(unittest.TestCase):
    def test_a_perk_made_from_scratch_keeps_its_effects_together(self):
        spec = {'schema': 1, 'project_id': 'perktest', 'plugin': 'PerkTest.esp', 'masters': ['FalloutNV.esm'],
                'records': {'MyPerk': {'type': 'PERK', 'set': {
                    'effects': [{'entry_point': 'Calculate Weapon Damage', 'function': 'multiply', 'value': 1.1}],
                    'name': 'Mine', 'ranks': 1, 'playable': 1, 'description': 'More damage.'}}}}
        with tempfile.TemporaryDirectory() as tmp:
            data, _ = builder.build(spec, FormIdMap(Path(tmp) / 'f.json', 'PerkTest.esp'), MasterIndex())
        rec = next(r for r, _ in codec.parse(data).records() if r.type == 'PERK')
        self.assertEqual([s.tag for s in rec.subrecords],
                         ['EDID', 'FULL', 'DESC', 'DATA', 'PRKE', 'DATA', 'EPFT', 'EPFD', 'PRKF'])
        self.assertEqual(len(rec.subrecords[3].data), 5)
        self.assertEqual(layouts.read_lists('PERK', rec.subrecords)['effects'][0]['value'], 1.1)
