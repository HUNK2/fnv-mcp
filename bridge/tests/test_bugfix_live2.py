"""Fixes found while running the bug log's live checks (2026-11-12, second pass): the envelope's game identity
without a ping, native module calls after a server restart and their faults, file-log false misses, the wait
timeout message, and the debugger's temporary stop sites."""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_create, fnv_game, fnv_live  # noqa: E402
from geck_mcp.fnv_conv import ConvError  # noqa: E402
from geck_mcp.fnv_native import NativeTools  # noqa: E402


class IdentityTests(unittest.TestCase):
    def service(self, hello, handle, last_frame=None, proc=(1796, 'FalloutNV.exe')):
        link = SimpleNamespace(hello=hello, handle=handle, last_frame=last_frame)
        live = fnv_live.LiveService.__new__(fnv_live.LiveService)
        live._game = SimpleNamespace(link=link, process=lambda: proc)
        return live

    def test_a_connected_link_without_a_ping_still_names_the_game(self):
        live = self.service(None, object(), last_frame=42)
        with mock.patch.object(fnv_game, 'process_started', return_value=134389228281425941):
            self.assertEqual(live.identity(), {'pid': 1796, 'started': '134389228281425941', 'frame': 42})

    def test_a_closed_link_names_no_game(self):
        self.assertIsNone(self.service(None, None).identity())

    def test_no_game_process_names_no_game(self):
        self.assertIsNone(self.service(None, object(), proc=None).identity())

    def test_the_hello_wins(self):
        live = self.service({'pid': 1, 'started': '2', 'frame': 3}, object())
        self.assertEqual(live.identity(), {'pid': 1, 'started': '2', 'frame': 3})


class FakeModLink:
    def __init__(self, call_result):
        self.calls = []
        self.call_result = call_result

    def one(self, *op, timeout=None):
        self.calls.append(op)
        if op[:2] == ('mod', 'list'):
            return {'ok': True, 'modules': [{'id': 2, 'name': 'sehtest-abc.dll', 'base': '0x01a50000'}]}
        if op[0] == 'call':
            return self.call_result
        return {'ok': True}


class NativeAfterRestartTests(unittest.TestCase):
    def tools(self, call_result):
        link = FakeModLink(call_result)
        live = SimpleNamespace(link=link, ready=lambda: None, project=SimpleNamespace(state_dir=tempfile.gettempdir()))
        t = NativeTools(live)
        t._log_tail = lambda: []
        t._export_in_memory = lambda base, name: 0x01A51090 if name == 'seh_catch' else None
        return t, link

    def test_a_module_loaded_by_an_earlier_server_is_called_through_its_export_table(self):
        t, link = self.tools({'ok': True, 'eax': '0x0000002a'})
        out = t.call('sehtest', 'seh_catch')
        self.assertEqual(out['value'], 42)
        self.assertEqual(int(link.calls[-1][1], 16), 0x01A51090)

    def test_an_unknown_export_still_says_so(self):
        t, _ = self.tools({'ok': True, 'eax': '0x0'})
        with self.assertRaises(ConvError) as cm:
            t.call('sehtest', 'nothing')
        self.assertEqual(cm.exception.code, 'NO_EXPORT')

    def test_a_fault_is_call_faulted(self):
        t, _ = self.tools({'ok': False, 'fault': {'code': '0xc0000005', 'address': '0x00000000', 'access': 'read'},
                           'stateSuspect': True})
        with self.assertRaises(ConvError) as cm:
            t.call('sehtest', 'seh_catch')
        self.assertEqual(cm.exception.code, 'CALL_FAULTED')
        self.assertIn('0x00000000', str(cm.exception))

    def test_loading_a_module_an_earlier_server_loaded_is_refused(self):
        t, _ = self.tools({'ok': True})
        live = t.live
        live.changing = lambda reason: None
        with tempfile.TemporaryDirectory() as tmp:
            dll = Path(tmp) / 'sehtest-abc.dll'
            dll.write_bytes(b'MZ')
            with self.assertRaises(ConvError) as cm:
                t.load(str(dll))
        self.assertEqual(cm.exception.code, 'ALREADY_LOADED')

    def test_a_silent_build_failure_says_what_happened(self):
        t, _ = self.tools({'ok': True})
        t._zig = lambda: 'zig'
        with tempfile.TemporaryDirectory() as tmp:
            t.live.project.state_dir = tmp
            (Path(tmp) / 'fnv' / 'sdk').mkdir(parents=True)
            (Path(tmp) / 'fnv' / 'sdk' / 'fnvmcp_module.h').write_text('', encoding='utf-8')
            t.write_sdk = lambda: None
            result = SimpleNamespace(returncode=5, stderr='', stdout='')
            with mock.patch('subprocess.run', return_value=result):
                try:
                    t.build('int x;', 'empty')
                except ConvError as e:
                    self.assertEqual(e.code, 'BUILD_FAILED')
                    self.assertIn('exit code 5', str(e))
                else:
                    self.fail('the build did not fail')


class FileLogPathTests(unittest.TestCase):
    def test_a_data_prefix_is_dropped(self):
        self.assertEqual(fnv_create._data_rel('Data\\Textures\\interface\\faders\\black.dds'),
                         'textures\\interface\\faders\\black.dds')
        self.assertEqual(fnv_create._data_rel('textures\\a.dds'), 'textures\\a.dds')

    def test_audio_extensions_are_the_ones_the_sound_code_tries(self):
        self.assertEqual(fnv_create.AUDIO_EXTS, ('.ogg', '.wav', '.mp3'))


class WaitTimeoutMessageTests(unittest.TestCase):
    def test_a_wait_that_ran_out_of_time_names_timeout(self):
        from geck_mcp import fnv_time
        wait = fnv_time.WaitFor.__new__(fnv_time.WaitFor)
        snap = {'held': False, 'fixedFps': 0, 'realFps': 60, 'multiplier': 1.0}
        wait.control = SimpleNamespace(require_game=lambda: 1, ensure_gate=lambda: None, snapshot=lambda measure=False: snap)
        wait.base_frame = lambda timeout: 100
        wait.until_long = lambda text, limit, snap, timeout: {'met': False, 'frames': 3600, 'frame': 3700,
                                                              'predicateOps': 3}
        with self.assertRaises(ConvError) as cm:
            wait._run(frames=10000)
        self.assertEqual(cm.exception.code, 'WAIT_TIMEOUT')
        self.assertIn('ran out of time', str(cm.exception))
        self.assertIn('raise timeout', str(cm.exception))


from test_fnv_debug import LINE7, DebugTestBase, stop_at  # noqa: E402


class UntilAndTemporarySiteTests(DebugTestBase):
    def test_until_that_stops_elsewhere_says_so_and_keeps_the_site_listed(self):
        self.stop_bp()
        self.link.on_resume = lambda op: setattr(self.link, 'stops', [stop_at(2, LINE7, stop_id=0)])
        out = self.run_tool('until', location='0x00401010')
        self.assertFalse(out['reached'])
        self.assertIn('before reaching', out['verdict'])
        self.link.stops = []
        status = self.run_tool('status')
        self.assertEqual(status['pendingTo'], ['0x00401010'])
        # A restarted server still knows it (state file), and break clear sweeps it.
        from geck_mcp import fnv_debugger
        fresh = fnv_debugger.Debugger(self.live, sleep=lambda s: None, clock=lambda: 0.0)
        fresh.pid, fresh.start = self.dbg.pid, self.dbg.start
        fresh._restore()
        self.assertEqual(fresh.pending_to, {0x401010})
        swept = self.run_tool('break', op='clear')['swept']
        self.assertIn('toSites', swept)
        self.assertIsNone(self.run_tool('status')['pendingTo'])

    def test_until_that_arrives_says_reached(self):
        self.stop_bp()
        self.link.on_resume = lambda op: setattr(self.link, 'stops', [stop_at(2, 0x401010, kind='step')])
        out = self.run_tool('until', location='0x00401010')
        self.assertTrue(out['reached'])


if __name__ == '__main__':
    unittest.main()
