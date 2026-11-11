"""Fixes found while running the Plan 2 acceptance scenarios S1, S2, S4 and S6 in the game (2026-11-11), against
fakes: statement numbers to source lines, exact blame context, result-script slicing, the dialogue step, base forms
refused where a reference is needed, global names kept when compiling, held faults read from outside, mod folders
in the builder's asset check."""
import json
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from geck_mcp import fnv_blame, fnv_deep, fnv_scenario, fnv_scriptdbg
from geck_mcp.fnv_data import DataTools
from geck_mcp.fnv_live import LiveError, LiveService

SOURCE = '\n'.join([
    'scn HelloBugQuestScript',      # 1  statement 1
    '',                             # 2
    '; a comment',                  # 3
    'short counter',                # 4  declaration: no statement
    'begin GameMode',               # 5  statement 2
    '\tif GetStage HelloBugQuest < 10',      # 6  statement 3
    '\t\tset HelloBugLevel to HelloBugLevel + 1',  # 7 statement 4
    '\tendif',                      # 8  statement 5
    'end'])                         # 9  statement 6


class StatementLines(unittest.TestCase):
    def debugger(self, code_head=b'\x1d\0\0\0\x10\0\x06\0'):
        live = SimpleNamespace(
            deep=SimpleNamespace(script_form=lambda spec: (0x1000, {'formId': '0D000802'}, None),
                                 script_source=lambda ptr: None),
            u32s=lambda addrs: [0x2000], reads=lambda spans: [code_head])
        data = SimpleNamespace(script_text=lambda form: SOURCE)
        return fnv_scriptdbg.ScriptDebugger(live, eval_hook=object(), dbg=object(), data=data)

    def test_statement_lines_skip_blanks_comments_and_declarations(self):
        self.assertEqual(fnv_scriptdbg.statement_lines(SOURCE), [1, 5, 6, 7, 8, 9])

    def test_a_source_line_maps_to_its_statement_and_back(self):
        dbg = self.debugger()
        self.assertEqual(dbg._statement_of(0x0D000802, 7), (4, None))
        self.assertEqual(dbg._line_of(0x0D000802, 4, 0x1000), (7, 'set HelloBugLevel to HelloBugLevel + 1'))

    def test_a_line_without_a_statement_moves_to_the_next_one(self):
        statement, note = self.debugger()._statement_of(0x0D000802, 4)
        self.assertEqual(statement, 2)
        self.assertIn('line 5', note)

    def test_hot_swapped_code_counts_one_more_statement(self):
        # A compile in the running game keeps xNVSE's scriptname statement in front of the script's own.
        dbg = self.debugger(code_head=b'\x1d\0\0\0\x1d\0\0\0')
        self.assertEqual(dbg._statement_of(0x0D000802, 7), (5, None))
        self.assertEqual(dbg._line_of(0x0D000802, 5, 0x1000)[0], 7)


class BlameContext(unittest.TestCase):
    def test_a_stamped_hit_names_the_statement_that_wrote(self):
        record = [0] * 48
        record[0], record[12], record[18] = 77, 0x3E466900, 6
        hook = {'id': '3', 'label': 'script_line_dbg', 'records': '0x1000', 'seqAddress': '0x2000'}
        live = SimpleNamespace(deep=SimpleNamespace(hooks=lambda: [hook], record=lambda h, seq: record if seq == 77 else None),
                               form_ids=lambda ptrs: [{'formId': '0D000803'}])
        blame = fnv_blame.BlameTools(live, debugger=object(), sleep=lambda s: None, clock=lambda: 0.0,
                                     line_of=lambda form, statement, ptr: (8, 'set HelloBugLevel to 0'))
        row = blame._statement_at(3, 77)
        self.assertEqual(row['script'], '0D000803')
        self.assertEqual((row['line'], row['statement'], row['approximate']), (8, 6, False))
        self.assertEqual(blame._culprit({'script': row}), 'script 0D000803 line 8: set HelloBugLevel to 0')
        self.assertIsNone(blame._statement_at(3, 78))       # the ring moved on: no exact context


class ResultScripts(unittest.TestCase):
    def test_the_block_statements_are_cut_out_of_the_wrapper(self):
        body = bytes.fromhex('39100a0002007201006e14000000')
        scda = b'\x1d\0\0\0' + struct.pack('<HHHI', 0x10, 6, 0, len(body) + 4) + body + b'\x11\0\0\0'
        data = DataTools.__new__(DataTools)
        data.live = SimpleNamespace(link=SimpleNamespace(hello={'pid': 1}))
        data.compile_ingame = lambda text, include_bytecode=True: {
            'compiled': True, 'bytecode': (b'\x1d\0\0\0' + scda).hex(), 'variables': [],
            'references': [{'kind': 'form', 'formId': '0C000804'}]}
        spec = {'records': {'Accept': {'type': 'DIAL', 'infos': [{'key': 'thanks', 'quest': '@Q',
                                                                  'result': 'SetStage Q 20'}]}}}
        out = data._compile_result_scripts(spec, {'formIds': {'INFO:Accept:thanks': '01000815'}},
                                           lambda form: 0x01000804, 'ctx')
        entry = out['INFO:Accept:thanks']['entry']
        compiled = dict(entry['compiled'])
        self.assertEqual(bytes.fromhex(compiled['SCDA']), body)
        self.assertEqual(struct.unpack('<4sIIIHH', bytes.fromhex(compiled['SCHR']))[1:], (1, len(body), 0, 0, 1))
        self.assertEqual(compiled['SCRO'], struct.pack('<I', 0x01000804).hex())


class DialogueStep(unittest.TestCase):
    def runner(self, steps, open_menu=True):
        calls = []

        def choose(speaker, choices, start, timeout, close):
            calls.append((speaker, choices, start))
            return {'steps': [{'choice': c, 'accepted': True} for c in choices], 'final': {'open': False}}
        social = SimpleNamespace(menu_state=lambda: {'open': open_menu}, dialogue_choose=choose)
        runner = fnv_scenario.ScenarioRunner.__new__(fnv_scenario.ScenarioRunner)
        runner.live = SimpleNamespace(social=social)
        return runner, calls

    def test_choices_are_made_in_the_open_conversation(self):
        runner, calls = self.runner([])
        out = runner._dialogue_step({'choose': ['look for them']})
        self.assertTrue(out['passed'])
        self.assertEqual(calls, [(None, ['look for them'], False)])

    def test_with_no_conversation_a_speaker_is_needed(self):
        runner, _ = self.runner([], open_menu=False)
        self.assertFalse(runner._dialogue_step({'choose': [0]})['passed'])


class ReferencesOnly(unittest.TestCase):
    def live(self, form_type):
        live = LiveService.__new__(LiveService)
        live.resolve = lambda ref: {'ptr': 0x1000, 'formId': '0E000800', 'expr': ref}
        live.reads = lambda spans: [bytes([form_type])]
        return live

    def test_a_base_form_is_refused_before_a_reference_function_runs(self):
        with self.assertRaises(LiveError) as cm:
            self.live(32).resolve_ref('HelloS6Crate')
        self.assertEqual(cm.exception.code, 'NOT_A_REFERENCE')

    def test_placed_references_pass(self):
        for form_type in (58, 59, 60):
            self.assertEqual(self.live(form_type).resolve_ref('x')['formId'], '0E000800')


class GlobalNames(unittest.TestCase):
    def test_globals_keep_their_names_other_forms_become_ids(self):
        live = LiveService.__new__(LiveService)
        live._command_names = {'setstage'}
        found = {'HelloBugLevel': {'formId': '0D000800', 'type': 'GLOB'},
                 'HelloBugQuest': {'formId': '0D000804', 'type': 'QUST'}}
        live.editor_id = lambda token: found.get(token)
        text, subs = live.substitute_editor_ids('set HelloBugLevel to 1\nSetStage HelloBugQuest 10')
        self.assertIn('set HelloBugLevel to 1', text)
        self.assertIn('SetStage 0D000804 10', text)
        self.assertEqual(subs, {'HelloBugQuest': '0D000804'})


class HeldFaults(unittest.TestCase):
    def test_paths_on_the_stack_are_found(self):
        memory = {0x50000: b'hellocrash\\crate.nif\0', 0x60000: b'\x01\x02binary\0', 0x70000: b'not a path\0'}
        proc = SimpleNamespace(read=lambda addr, size: memory.get(addr))
        stack = struct.pack("<5I", 0x50000, 0x60000, 0x70000, 0x10, 0x50000)
        self.assertEqual(fnv_deep._paths_on_stack(proc, stack), ['hellocrash\\crate.nif'])

    def test_the_first_fault_of_a_held_thread_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for n, eip in ((1, '0x00a59d0a'), (2, '0x02060f50')):
                path = Path(tmp) / ('crash-context-99-%d.json' % n)
                path.write_text(json.dumps({'pid': 99, 'n': n, 'threadId': 7, 'mainThread': False, 'code': '0xc0000005',
                                            'address': eip, 'registers': {'eip': eip, 'esp': '0x1000', 'ebp': '0x0'}}))
                files.append(path)
            deep = fnv_deep.DeepTools.__new__(fnv_deep.DeepTools)
            original = fnv_deep.context_files
            fnv_deep.context_files = lambda: list(reversed(files))
            try:
                proc = SimpleNamespace(read=lambda addr, size: b'\0' * size, u32=lambda a: 0)
                out = deep._held_faults(proc, 99, [7, 8], [], None)
            finally:
                fnv_deep.context_files = original
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['address'], '0x00a59d0a')


class ModFolders(unittest.TestCase):
    def test_enabled_mods_and_the_plugins_own_folder_are_asset_roots(self):
        from geck_mcp.authoring import Authoring
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for mod in ('HelloS6', 'Other', 'Disabled'):
                (root / 'mods' / mod).mkdir(parents=True)
            (root / 'profiles' / 'Dev').mkdir(parents=True)
            (root / 'profiles' / 'Dev' / 'modlist.txt').write_text('# header\n+Other\n-Disabled\n')
            authoring = Authoring.__new__(Authoring)
            authoring.project = SimpleNamespace(plugin='HelloS6.esp',
                                                fnv=SimpleNamespace(mo2_root=root, mo2_profile='Dev'))
            roots = authoring.mod_roots()
        self.assertEqual([p.name for p in roots], ['HelloS6', 'Other'])


if __name__ == '__main__':
    unittest.main()
