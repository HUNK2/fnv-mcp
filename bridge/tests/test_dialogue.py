"""Dialogue records in the spec builder (scenario S1): topics, lines with conditions, choices and result scripts,
and new lines added to a master topic (GREETING) through an override copied from its template."""
import json
import struct
import tempfile
import unittest
from pathlib import Path

from geck_mcp.esp import build as builder, codec, dialogue
from geck_mcp.esp.formids import FormIdMap, MasterIndex
from geck_mcp.esp.records import SpecError
from geck_mcp.esp.validate import validate


def sub(tag, data):
    return {'tag': tag, 'size': len(data), 'hex': data.hex()}


def greeting_template():
    """GREETING as FalloutNV.esm has it: QSTI runs, one quest followed by its INFC/INFX, then FULL, PNAM, DATA."""
    return {'type': 'DIAL', 'formId': '000000C8', 'flags': 0, 'subrecords': [
        sub('EDID', b'GREETING\0'), sub('QSTI', struct.pack('<I', 0x0013A001)), sub('QSTI', struct.pack('<I', 0x0013A002)),
        sub('INFC', struct.pack('<I', 0x000E3F9E)), sub('INFX', struct.pack('<i', -1)),
        sub('FULL', b'GREETING\0'), sub('PNAM', struct.pack('<f', 50.0)), sub('DATA', b'\0\0')]}


def master_index():
    index = MasterIndex()
    index.masters = ['FalloutNV.esm']
    index.entries = {'greeting': [(0, 'DIAL', 0x000000C8, None, None, 'GREETING')],
                     'caps001': [(0, 'MISC', 0x0000000F, 'Bottle Cap', None, 'Caps001')]}
    return index


SPEC = {
    'schema': 1, 'project_id': 'dialoguetest', 'plugin': 'DialogueTest.esp', 'masters': ['FalloutNV.esm'],
    'records': {
        'TestQuest': {'type': 'QUST', 'fields': [{'tag': 'FULL', 'text': 'Test Quest'},
                                                  {'tag': 'DATA', 'pack': '<BBBBf', 'values': [1, 80, 0, 0, 0.5]}]},
        'TestNpc': {'type': 'NPC_', 'fields': [{'tag': 'FULL', 'text': 'Tester'}]},
        'TestAccept': {'type': 'DIAL', 'fields': [{'tag': 'FULL', 'text': 'Sure.'}, {'tag': 'DATA', 'hex': '0000'}],
                       'infos': [{'key': 'thanks', 'quest': '@TestQuest', 'flags': ['goodbye'],
                                  'responses': [{'text': 'Thanks!', 'emotion': 'happy'}],
                                  'conditions': [{'function': 'GetIsID', 'param1': '@TestNpc', 'value': 1}],
                                  'result': 'SetStage TestQuest 20'}]},
        'GREETING': {'type': 'DIAL', 'override': True,
                     'infos': [{'key': 'offer', 'quest': '@TestQuest', 'responses': ['Help me?'],
                                'conditions': [{'function': 'GetStage', 'param1': '@TestQuest', 'op': '<',
                                                'value': 20}],
                                'choices': ['@TestAccept']}]},
    },
}


class DialogueBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name) / 'templates' / 'FalloutNV.esm'
        folder.mkdir(parents=True)
        (folder / 'GREETING.json').write_text(json.dumps(greeting_template()))
        self.templates = [Path(self.tmp.name) / 'templates']

    def build(self, spec=None, cache=None):
        spec = json.loads(json.dumps(spec or SPEC))
        formids = FormIdMap(Path(self.tmp.name) / 'f.json', spec['plugin'])
        return builder.build(spec, formids, master_index(), script_cache=cache, templates=self.templates)

    def records(self, data):
        return [(r, [g.group_type for g in path]) for r, path in codec.parse(data).records()]

    def test_lines_sit_in_a_topic_children_group_after_their_topic(self):
        data, receipt = self.build()
        self.assertTrue(validate(data)['ok'], validate(data)['errors'])
        dial = [(r, g) for r, g in self.records(data) if r.type == 'DIAL']
        infos = [(r, g) for r, g in self.records(data) if r.type == 'INFO']
        self.assertEqual(len(dial), 2)
        self.assertEqual(len(infos), 2)
        self.assertTrue(all(groups == [0, 7] for _, groups in infos))
        self.assertEqual(receipt['recordCounts']['INFO'], 2)

    def test_master_topic_keeps_its_formid_and_gains_the_quest_after_infc_infx(self):
        data, _ = self.build()
        greeting = next(r for r, _ in self.records(data) if r.type == 'DIAL' and r.form_id == 0x000000C8)
        tags = [s.tag for s in greeting.subrecords]
        self.assertEqual(tags, ['EDID', 'QSTI', 'QSTI', 'INFC', 'INFX', 'QSTI', 'FULL', 'PNAM', 'DATA'])
        own_quest = struct.unpack('<I', greeting.subrecords[5].data)[0]
        self.assertEqual(own_quest >> 24, 1)          # the new quest is this plugin's own

    def test_conditions_choices_and_flags_are_packed_as_the_engine_reads_them(self):
        data, receipt = self.build()
        offer = next(r for r, _ in self.records(data) if r.type == 'INFO' and r.get('TCLT'))
        ctda = offer.get('CTDA')
        ctype, value, function, param1 = struct.unpack_from('<B3xfH2xI', ctda)
        self.assertEqual((ctype, value, function), (0x80, 20.0, dialogue.FUNCTIONS['getstage']))
        self.assertEqual(param1, int(receipt['formIds']['TestQuest'], 16))
        self.assertEqual(struct.unpack('<I', offer.get('TCLT'))[0], int(receipt['formIds']['TestAccept'], 16))
        thanks = next(r for r, _ in self.records(data) if r.type == 'INFO' and r.get('SCTX'))
        self.assertEqual(struct.unpack('<BBH', thanks.get('DATA'))[2] & dialogue.FLAGS['goodbye'], 1)

    def test_an_uncompiled_result_script_keeps_its_source_and_no_result_flag(self):
        data, receipt = self.build()
        thanks = next(r for r, _ in self.records(data) if r.type == 'INFO' and r.get('SCTX'))
        self.assertEqual(thanks.get('SCTX'), b'SetStage TestQuest 20')
        self.assertFalse(thanks.flags & dialogue.HAS_RESULT_SCRIPT)
        self.assertIn('TestAccept line thanks', receipt['uncompiledScripts'])

    def test_a_cached_result_script_is_used_and_flags_the_line(self):
        _, receipt = self.build()
        key = 'INFO:TestAccept:thanks'
        body = bytes.fromhex('39100a0002007201006e14000000')
        cache = {key: {'sourceSha256': dialogue.result_sha('SetStage TestQuest 20'), 'formId': receipt['formIds'][key],
                       'compiled': [('SCHR', struct.pack('<4sIIIHH', b'\0' * 4, 1, len(body), 0, 0, 1).hex()),
                                    ('SCDA', body.hex()), ('SCTX', ''),
                                    ('SCRO', struct.pack('<I', int(receipt['formIds']['TestQuest'], 16)).hex())]}}
        data, receipt = self.build(cache=cache)
        thanks = next(r for r, _ in self.records(data) if r.type == 'INFO' and r.get('SCTX'))
        self.assertTrue(thanks.flags & dialogue.HAS_RESULT_SCRIPT)
        self.assertEqual([s.tag for s in thanks.subrecords][-6:], ['SCHR', 'NEXT', 'SCHR', 'SCDA', 'SCTX', 'SCRO'])
        self.assertEqual(thanks.get('SCDA'), body)
        self.assertNotIn('TestAccept line thanks', receipt['uncompiledScripts'])

    def test_line_formids_are_kept_by_key(self):
        _, first = self.build()
        _, second = self.build()
        self.assertEqual(first['formIds']['INFO:GREETING:offer'], second['formIds']['INFO:GREETING:offer'])
        self.assertNotIn('GREETING', first['formIds'])     # an override keeps the master's FormID

    def test_bad_lines_are_refused(self):
        spec = json.loads(json.dumps(SPEC))
        spec['records']['TestAccept']['infos'][0].pop('quest')
        with self.assertRaises(SpecError):
            builder.check_spec(spec)
        spec = json.loads(json.dumps(SPEC))
        spec['records']['TestQuest']['override'] = True
        with self.assertRaises(SpecError):
            builder.check_spec(spec)
        spec = json.loads(json.dumps(SPEC))
        spec['records']['TestAccept']['infos'][0]['conditions'][0]['function'] = 'NoSuchFunction'
        with self.assertRaises(SpecError):
            self.build(spec)


if __name__ == '__main__':
    unittest.main()
