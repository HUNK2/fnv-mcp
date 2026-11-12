"""Races inside the engine companion, made repeatable with the seam build (PLAN-3 S0.2, S1.1).

A seam build parks the thread that reaches SEAM(n) while fnvmcp_seam == n. The test writes fnvmcp_seam and the fake
host's race_go flag straight into the host process (WriteProcessMemory), so it works while the companion's main
thread is the one parked."""
import ctypes
import sys
import tempfile
import threading
import time
import unittest
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/: engine_host
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine_host import StopHost, StopTestCase, build, hexint, needs_zig  # noqa: E402
from geck_mcp import fnv_link  # noqa: E402

SEAM = ('FNVMCP_TEST_SEAM',)
BP_TARGET_RESULT = 12   # bp_target(a, 5) = bp_callee() + 5 = 12 for every a


def poke(process, address, value):
    """Write one 32-bit value into the host process."""
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(0x0008 | 0x0020, False, process.pid)   # VM_OPERATION | VM_WRITE
    if not handle:
        raise OSError(ctypes.get_last_error(), 'OpenProcess')
    try:
        data = ctypes.c_uint32(value)
        written = ctypes.c_size_t()
        if not kernel32.WriteProcessMemory(handle, ctypes.c_void_p(address), ctypes.byref(data), 4,
                                           ctypes.byref(written)):
            raise OSError(ctypes.get_last_error(), 'WriteProcessMemory')
    finally:
        kernel32.CloseHandle(handle)


@needs_zig
class CoverageRaceTests(StopTestCase):
    """R1: two threads trap on one coverage line, and a release (a hook on the function) races a trap."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.receipt = build(cls.tmp.name, defines=SEAM)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.host = StopHost(self.receipt, Path(self.receipt['dll']).parent, self.tmp.name, 120)
        self.addCleanup(self.host.close)
        self.t = self.host.t
        self.assertTrue(self.t['seam'], 'not a seam build')
        self.original = self.host.link.one('rd', hexint(self.t['bpTarget']), '1')['hex']
        link = self.host.link
        self.assertEqual(link.one('cov', 'add', self.t['bpTarget'].to_bytes(4, 'little').hex())['added'], 1)
        self.assertEqual(link.one('cov', 'arm')['armed'], 1)
        self.assertEqual(link.one('rd', hexint(self.t['bpTarget']), '1')['hex'], 'cc')

    def read32(self, key):
        return int.from_bytes(bytes.fromhex(self.host.link.one('rd', hexint(self.t[key]), '4')['hex']), 'little')

    def start_race(self, threads, iters):
        self.host.link.schedule(0, 'race %d %d' % (threads, iters))
        end = time.time() + 5
        while self.read32('raceStarted') < threads and time.time() < end:
            time.sleep(0.01)
        self.assertEqual(self.read32('raceStarted'), threads)

    def wait_done(self, threads, timeout=10.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.host.process.poll() is not None:
                return
            if self.read32('raceDone') == threads:
                return
            time.sleep(0.02)

    def release_seam(self):
        if self.host.process.poll() is None:              # a dead host is reported by the assertions below
            poke(self.host.process, self.t['seam'], 0)

    def hits(self):
        return self.host.link.one('cov', 'read', '0')['hits']

    def test_cov_two_threads_same_line_no_crash(self):
        poke(self.host.process, self.t['seam'], 1)        # the first thread to win the line parks before the restore
        self.start_race(2, 1)
        poke(self.host.process, self.t['raceGo'], 1)
        time.sleep(0.5)                                   # the second thread traps on the 0xCC meanwhile
        self.release_seam()
        self.wait_done(2)
        self.assertIsNone(self.host.process.poll(), 'the host died (exit 0x%08X)' % ((self.host.process.poll() or 0) & 0xFFFFFFFF))
        self.assertEqual(self.read32('raceDone'), 2)
        self.assertEqual(self.read32('raceBad'), 0)
        self.assertEqual(self.hits(), 1)
        self.assertEqual(self.host.link.one('rd', hexint(self.t['bpTarget']), '1')['hex'], self.original)

    def test_cov_release_races_with_trap(self):
        poke(self.host.process, self.t['seam'], 2)        # the release parks between its claim and the restore
        self.start_race(2, 1)
        result = {}

        def hook():
            result['hook'] = self.host.link.one(
                'hook', hexint(self.t['bpTarget']), hexint(6), '-', hexint(0), hexint(0), hexint(0), hexint(0),
                hexint(1), '0', '0', hexint(0), hexint(0), fnv_link.hexs('race'))
        caller = threading.Thread(target=hook, daemon=True)
        caller.start()
        time.sleep(0.5)                                   # the main thread is parked in cov_release_range now
        poke(self.host.process, self.t['raceGo'], 1)      # the workers trap on the 0xCC the release still has to undo
        time.sleep(0.5)
        self.release_seam()
        caller.join(10)
        self.wait_done(2)
        self.assertIsNone(self.host.process.poll(), 'the host died (exit 0x%08X)' % ((self.host.process.poll() or 0) & 0xFFFFFFFF))
        self.assertEqual(self.read32('raceDone'), 2)
        self.assertEqual(self.read32('raceBad'), 0)
        self.assertTrue(result.get('hook', {}).get('ok'), result)
        self.assertEqual(self.hits(), 0)                  # a released line goes idle unhit
        self.assertTrue(self.host.link.one('unhook', hexint(result['hook']['hook']['id'])).get('ok'))
        self.assertEqual(self.host.link.one('rd', hexint(self.t['bpTarget']), '1')['hex'], self.original)

    def test_release_with_threads_suspended_while_a_hit_is_in_flight(self):
        # A breakpoint is written with every other thread suspended (isite_add). If a thread is suspended while it
        # owns a coverage line (state 3, 0xCC still there), the release must not wait for it, or the main thread
        # waits forever; and the breakpoint must still save the real original byte, not 0xCC.
        poke(self.host.process, self.t['seam'], 1)
        self.start_race(1, 1)
        poke(self.host.process, self.t['raceGo'], 1)
        time.sleep(0.5)                                   # the worker owns the line and is parked before the restore
        result = {}

        def set_bp():
            result['bp'] = self.host.link.one('bp', 'set', hexint(1), hexint(self.t['bpTarget']), 'int3', 'ff', '0')
        caller = threading.Thread(target=set_bp, daemon=True)
        caller.start()
        caller.join(5)
        returned = not caller.is_alive()
        self.release_seam()
        caller.join(5)
        self.assertTrue(returned, 'bp set did not return while a coverage hit was in flight (deadlock)')
        self.assertTrue(result.get('bp', {}).get('ok'), result)
        self.assertEqual(self.host.link.one('bp', 'clear', hexint(1)).get('cleared'), 1)
        self.wait_done(1)
        self.assertIsNone(self.host.process.poll(), 'the host died (exit 0x%08X)' % ((self.host.process.poll() or 0) & 0xFFFFFFFF))
        self.assertEqual(self.read32('raceDone'), 1)
        self.assertEqual(self.read32('raceBad'), 0)
        self.assertEqual(self.host.link.one('rd', hexint(self.t['bpTarget']), '1')['hex'], self.original)

    def test_rows_do_not_move_while_a_hit_is_in_flight(self):
        # cov add re-sorts the rows and cov reset empties them; a hit in flight still writes its row by index.
        poke(self.host.process, self.t['seam'], 1)
        self.start_race(1, 1)
        poke(self.host.process, self.t['raceGo'], 1)
        time.sleep(0.5)
        link = self.host.link
        self.assertTrue(link.one('cov', 'disarm').get('ok'))
        self.assertEqual(link.one('rd', hexint(self.t['bpTarget']), '1')['hex'], self.original)
        lower = self.t['bpTarget'] - 0x100     # sorts before the in-flight row, so its index would move
        while link.one('rd', hexint(lower), '1')['hex'] == 'cc':
            lower += 1
        self.assertEqual(link.one('cov', 'add', lower.to_bytes(4, 'little').hex()).get('error'), 'busy')
        self.assertEqual(link.one('cov', 'reset').get('error'), 'busy')
        self.release_seam()
        self.wait_done(1)
        self.assertEqual(self.read32('raceDone'), 1)
        self.assertEqual(self.hits(), 1)
        self.assertEqual(link.one('cov', 'add', lower.to_bytes(4, 'little').hex())['added'], 1)
        self.assertTrue(link.one('cov', 'reset').get('ok'))


if __name__ == '__main__':
    unittest.main()
