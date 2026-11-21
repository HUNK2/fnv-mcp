"""Bug hunt F (2026-11-18): fnv_native unload by id, hook leak on a failed attach, fnv_inspect perk failing list,
fnv_engine_ai perk recording left half started, fnv_code edit of a copy with no tag, crawl file handles."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_native import NativeTools  # noqa: E402
from fnv_mcp import fnv_inspect, fnv_engine_ai  # noqa: E402
from fnv_mcp.fnv_code import CodeTools  # noqa: E402


class FakeLink:
    def __init__(self, modules=(), fail_hooknative=False):
        self.modules = list(modules)
        self.calls = []
        self.fail_hooknative = fail_hooknative

    def one(self, *args, **kw):
        self.calls.append(args)
        if args[0] == 'mod' and args[1] == 'list':
            return {'ok': True, 'modules': self.modules}
        if args[0] == 'mod' and args[1] == 'unload':
            return {'ok': True}
        if args[0] == 'mod' and args[1] == 'log':
            return {'lines': []}
        if args[0] == 'mod' and args[1] == 'load':
            return {'ok': True, 'module': {'id': 7, 'base': '0x1000', 'hooks': [{'target': '0x00401000', 'request': 0}]},
                    'exports': []}
        if args[0] == 'hook':
            return {'ok': True, 'hook': {'id': 99}}
        if args[0] == 'hooknative':
            if self.fail_hooknative:
                raise RuntimeError('link dropped')
            return {'ok': True}
        return {'ok': True}


class NativeTests(unittest.TestCase):
    def test_unload_by_id_forgets_the_loaded_record(self):
        link = FakeLink([{'id': 7, 'name': 'mymod-abc123', 'base': '0x1000'}])
        live = SimpleNamespace(ready=lambda: None, link=link, journal=None)
        t = NativeTools(live)
        t.loaded['mymod'] = {'id': 7, 'dll': 'x', 'exports': {}, 'hooks': []}
        t.unload('7')
        self.assertNotIn('mymod', t.loaded)

    def test_build_refuses_a_name_with_no_letters(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = SimpleNamespace(project=SimpleNamespace(state_dir=tmp))
            with self.assertRaises(ConvError) as cm:
                NativeTools(live, zig='zig').build('int x;', '???')
            self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')

    def test_hook_installed_but_not_attached_is_removed_when_attach_raises(self):
        link = FakeLink(fail_hooknative=True)
        journal = SimpleNamespace(add=lambda e: None)
        live = SimpleNamespace(ready=lambda: None, link=link, journal=journal, changing=lambda *a: None,
                               reads=lambda spans: [b'\x55\x8b\xec\x83\xec\x10' + b'\x90' * 18],
                               engine=lambda: SimpleNamespace(summary=lambda *a, **k: {}))
        with tempfile.TemporaryDirectory() as tmp:
            dll = Path(tmp) / 'mymod-abc.dll'
            dll.write_bytes(b'')
            t = NativeTools(live)
            out = t.load(str(dll))
        self.assertTrue(out['hooksFailed'])
        self.assertIn(('unhook', '63'), link.calls)


class InspectTests(unittest.TestCase):
    def test_failing_conditions_skip_rescued_or_members_and_use_compareTo(self):
        lists = [{'list': 'perk requirements', 'passes': False, 'conditions': [
            {'index': 0, 'function': 'GetLevel', 'or': True, 'passed': False, 'value': 3, 'compareTo': 5.0},
            {'index': 1, 'function': 'GetAV', 'or': False, 'passed': True, 'value': 9, 'compareTo': 5.0},
            {'index': 2, 'function': 'HasPerk', 'or': False, 'passed': False, 'value': 0, 'compareTo': 1.0}]},
            {'list': 'other', 'passes': True, 'conditions': [{'index': 0, 'or': False, 'passed': False}]}]
        out = fnv_inspect._failing_conditions(lists)
        self.assertEqual([c['index'] for c in out], [2])
        self.assertEqual(out[0]['compare'], 1.0)

    def test_failing_conditions_list_every_member_of_a_failed_or_group(self):
        lists = [{'list': 'l', 'passes': False, 'conditions': [
            {'index': 0, 'or': True, 'passed': False}, {'index': 1, 'or': False, 'passed': False}]}]
        self.assertEqual([c['index'] for c in fnv_inspect._failing_conditions(lists)], [0, 1])


class EngineAiTests(unittest.TestCase):
    def test_perk_recording_start_removes_the_first_hook_when_the_second_fails(self):
        removed = []
        ai = fnv_engine_ai.EngineAITools(SimpleNamespace(
            live=SimpleNamespace(in_game=lambda: None), link=None,
            deep=SimpleNamespace(remove=lambda hid: removed.append(hid))))
        calls = []

        def hook(address, label, nargs, **kw):
            calls.append(label)
            if len(calls) == 2:
                raise fnv_engine_ai.HookError('HOOK_REFUSED', 'no')
            return {'id': 5, 'seqAddress': '0x10'}
        ai._hook = hook
        ai._seq = lambda h: 0
        with self.assertRaises(fnv_engine_ai.HookError):
            ai.perk_entries('start')
        self.assertEqual(removed, [5])


class CodeTests(unittest.TestCase):
    def test_edit_without_a_tag_in_the_copy_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / 'unit.cpp'
            copy.write_text('int x;\n', encoding='utf-8')
            t = CodeTools(SimpleNamespace())
            t._target = lambda f: (0x401000, {}, {})
            t._meta = lambda a: {'copy': str(copy)}
            with self.assertRaises(ConvError) as cm:
                t.edit('f', 'int', 'long')
            self.assertEqual(cm.exception.code, 'NOT_IN_COPY')


if __name__ == '__main__':
    unittest.main()
