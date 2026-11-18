"""Bug hunt E (2026-11-18): the debugger, script debugger, watch, perf, sampling, coverage and deep tools. Each test
failed on the code before its fix. No game is needed."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp import fnv_conv, fnv_deep, fnv_link, fnv_perf, fnv_sampling, fnv_watch  # noqa: E402
from test_fnv_debug import DebugTestBase  # noqa: E402
from test_fnv_script_debug import LINE, ScriptDebugTestBase  # noqa: E402


class DebuggerSlotLeakTests(DebugTestBase):
    """A link error (not a ConvError) while a break is set must give back its predicate slot."""

    def test_hook_hold_link_error_releases_the_when_slot(self):
        with mock.patch.object(self.dbg, '_hook_hold', side_effect=fnv_link.LinkError('LINK', 'gone')):
            with self.assertRaises(fnv_link.LinkError):
                self.run_tool('break', op='set', location='Actor::ApplyDamage', when='frame >= 1')
        self.assertEqual(self.dbg._claimed_slots, set())

    def test_trap_link_error_releases_the_when_slot(self):
        with mock.patch.object(self.dbg, '_set_bp', side_effect=fnv_link.LinkError('LINK', 'gone')):
            with self.assertRaises(fnv_link.LinkError):
                self.run_tool('break', op='set', location='src/actor.cpp:7', when='frame >= 1')
        self.assertEqual(self.dbg._claimed_slots, set())

    def test_pred_set_link_error_releases_the_claim(self):
        real = self.link.one

        def one(*op, timeout=None):
            if op[0] == 'pred' and op[1] == 'set':
                raise fnv_link.LinkError('LINK', 'gone')
            return real(*op, timeout=timeout)
        self.link.one = one
        with self.assertRaises(fnv_link.LinkError):
            self.run_tool('break', op='set', location='src/actor.cpp:7', when='frame >= 1')
        self.assertEqual(self.dbg._claimed_slots, set())


class ProfileNameTests(unittest.TestCase):
    def test_sample_with_a_dotted_name_reports_the_file_it_wrote(self):
        with tempfile.TemporaryDirectory() as tmp:
            live = SimpleNamespace(
                project=SimpleNamespace(state_dir=tmp),
                game=SimpleNamespace(process=lambda: (1, 0), ping=lambda timeout=None: {'mainThreadId': 7}),
                engine=lambda: None)
            tools = fnv_perf.ProfileTools(live, sleep=lambda s: None)
            proc = mock.MagicMock()
            proc.__enter__.return_value = proc
            proc.modules.return_value = []
            proc.threads.return_value = [7]
            namer = lambda address, return_address=False: {'function': 'F', 'start': 1, 'line': None}  # noqa: E731
            with mock.patch.object(fnv_perf.fnv_procmem, 'GameProcess', return_value=proc), \
                    mock.patch.object(fnv_perf, 'sample_threads', return_value={7: [[0x401000]]}), \
                    mock.patch.object(fnv_perf, 'Namer', return_value=namer):
                out = tools.sample(seconds=0.1, name='my.run')
            self.assertTrue(Path(out['profile']).exists(), out['profile'])
            self.assertIn(Path(out['profile']).name, out['reason'])


class WatchTests(unittest.TestCase):
    def setUp(self):
        fnv_watch._DR_CLAIMED.clear()
        self.addCleanup(fnv_watch._DR_CLAIMED.clear)

    def test_journal_failure_frees_the_debug_register(self):
        ops = []

        class Link:
            def one(self, *op, **kw):
                ops.append(op)
                if op[:2] == ('dbg', 'set'):
                    return {'ok': True, 'seq': 1}
                return {'ok': True}

        def broken_add(entry):
            raise OSError('disk full')
        live = SimpleNamespace(link=Link(), in_game=lambda: None, journal=SimpleNamespace(add=broken_add))
        tools = fnv_watch.FieldWatchTools(live, None)
        with self.assertRaises(OSError):
            tools.watch(address=0x1000)
        self.assertEqual(fnv_watch._DR_CLAIMED, set())
        self.assertIn(('dbg', 'clear', '0'), ops)

    def test_find_follows_a_shared_pointer_once(self):
        import struct
        target = 0x02000000
        data = struct.pack('<4I', target, 0, target, 0)
        reads = []

        class Link:
            def read(self, address, length):
                reads.append(address)
                return data[:length] if address == 0x01000000 else bytes(length)

            def console(self, line, ref=None):
                return {'ok': True}

        live = SimpleNamespace(link=Link(), in_game=lambda: None, engine=lambda: SimpleNamespace(),
                               rtti=lambda ptrs: {p: {'class': 'Thing'} for p in ptrs})
        tools = fnv_watch.FieldWatchTools(live, None)
        tools.field_name = lambda cls, off: None
        tools.find(ref=0x01000000, size=16, follow=16, seconds=0)
        self.assertEqual(reads.count(target), 2)       # once before and once after, not once per pointer field


class ScriptStepTests(ScriptDebugTestBase):
    def test_step_rearms_when_the_hold_fails(self):
        self.run_tool('break', op='set', script='QuestScript', line=LINE)
        self.stop_statement()
        for mode in ('line', 'over', 'out'):
            with self.subTest(mode=mode):
                rearmed = []
                with mock.patch.object(self.tool.eval, 'hold', side_effect=fnv_link.LinkError('LINK', 'gone')),                         mock.patch.object(self.tool, '_arm', side_effect=lambda: rearmed.append(1)):
                    with self.assertRaises(fnv_link.LinkError):
                        self.run_tool('step', mode=mode)
                self.assertTrue(rearmed)


class ScriptTraceStopTests(unittest.TestCase):
    def test_stop_removes_the_hook_even_when_the_script_no_longer_resolves(self):
        removed = []

        class Link:
            def one(self, *op, **kw):
                if op[0] == 'hooks':
                    return {'ok': True, 'hooks': [{'id': 5, 'label': 'script_line', 'stolen': 5}]}
                if op[0] == 'unhook':
                    removed.append(op[1])
                return {'ok': True}

        def resolve(ref):
            raise ValueError('gone')
        live = SimpleNamespace(link=Link(), in_game=lambda: None, resolve=resolve)
        deep = fnv_deep.DeepTools(live)
        out = deep.script_trace('GoneScript', action='stop')
        self.assertEqual(removed, ['5'])
        self.assertTrue(out['removed'])


class SinkTests(unittest.TestCase):
    def test_bad_message_number_is_a_clear_error(self):
        tools = fnv_sampling.SinkTools(SimpleNamespace(link=SimpleNamespace(one=lambda *a, **k: {'ok': True, 'slot': 1})))
        for bad in ('msg:abc', 'msg:', 'msg:-1', 'msg:0x1FFFFFFFF'):
            with self.subTest(bad=bad):
                with self.assertRaises(fnv_conv.ConvError) as cm:
                    tools.subscribe([bad])
                self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')
        self.assertEqual(tools.subscribe(['msg:0x100']), {'msg:0x100': 1})


if __name__ == '__main__':
    unittest.main()
