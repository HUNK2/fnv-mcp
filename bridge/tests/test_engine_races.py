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

    def start_race(self, threads, iters, yield_between=False):
        self.host.link.schedule(0, 'race %d %d %d' % (threads, iters, 1 if yield_between else 0))
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
        # waits forever. The owner would put the original byte back later, over the new 0xCC, so the breakpoint is
        # refused as busy until the hit is done; then it is set with the real original byte.
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
        self.assertEqual(result.get('bp', {}).get('error'), 'busy', result)
        self.wait_done(1)
        again = self.host.link.one('bp', 'set', hexint(1), hexint(self.t['bpTarget']), 'int3', 'ff', '0')
        self.assertTrue(again.get('ok'), again)
        self.assertEqual(self.host.link.one('rd', hexint(self.t['bpTarget']), '1')['hex'], 'cc')
        self.assertEqual(self.host.link.one('bp', 'clear', hexint(1)).get('cleared'), 1)
        self.assertIsNone(self.host.process.poll(), 'the host died (exit 0x%08X)' % ((self.host.process.poll() or 0) & 0xFFFFFFFF))
        self.assertEqual(self.read32('raceDone'), 1)
        self.assertEqual(self.read32('raceBad'), 0)
        self.assertEqual(self.host.link.one('rd', hexint(self.t['bpTarget']), '1')['hex'], self.original)

    def test_a_hit_in_flight_never_writes_over_a_new_patch(self):
        # Seam 5 parks the hit's owner after it saw the 0xCC and before it writes the original byte back. A hook
        # written meanwhile would be overwritten by that late write (its jump's first byte replaced): the code
        # write is refused as busy until the hit is done.
        poke(self.host.process, self.t['seam'], 5)
        self.start_race(1, 1)
        poke(self.host.process, self.t['raceGo'], 1)
        time.sleep(0.5)
        link = self.host.link
        args = (hexint(self.t['bpTarget']), hexint(6), '-', hexint(0), hexint(0), hexint(0), hexint(0), hexint(1),
                '0', '0', hexint(0), hexint(0), fnv_link.hexs('race'))
        first = link.one('hook', *args)
        self.release_seam()
        self.wait_done(1)
        self.assertEqual(self.read32('raceDone'), 1)
        if first.get('ok'):                               # the old behaviour: the late restore broke the jump
            self.assertEqual(link.one('rd', hexint(self.t['bpTarget']), '1')['hex'], 'e9', 'hook jump overwritten')
            link.one('unhook', hexint(first['hook']['id']))
        self.assertEqual(first.get('error'), 'busy', first)
        hook = link.one('hook', *args)
        self.assertTrue(hook.get('ok'), hook)
        self.assertEqual(link.one('rd', hexint(self.t['bpTarget']), '1')['hex'], 'e9')
        link.schedule(0, 'bpcall 1')
        self.assertTrue(self.wait_line('bpcall 12'))
        self.assertTrue(link.one('unhook', hexint(hook['hook']['id'])).get('ok'))
        self.assertEqual(link.one('rd', hexint(self.t['bpTarget']), '1')['hex'], self.original)

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


@needs_zig
class BreakHookRaceTests(StopTestCase):
    """S4.3: breakpoints, hooks and coverage under several threads (seam build)."""

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
        self.original = self.host.link.one('rd', hexint(self.t['bpTarget']), '6')['hex']

    read32 = CoverageRaceTests.read32
    start_race = CoverageRaceTests.start_race
    wait_done = CoverageRaceTests.wait_done
    release_seam = CoverageRaceTests.release_seam

    def alive(self):
        return 'the host died (exit 0x%08X)' % ((self.host.process.poll() or 0) & 0xFFFFFFFF)

    def test_bp_two_threads_same_int3(self):
        link = self.host.link
        self.assertTrue(link.one('bp', 'set', hexint(1), hexint(self.t['bpTarget']), 'int3', 'ff', '0').get('ok'))
        poke(self.host.process, self.t['seam'], 3)        # the first thread parks after the trap, before its stop
        self.start_race(2, 1)
        poke(self.host.process, self.t['raceGo'], 1)
        time.sleep(0.5)                                   # the second thread traps on the same int3 meanwhile
        self.release_seam()
        stops = self.wait_stops(2)
        self.assertIsNone(self.host.process.poll(), self.alive())
        self.assertEqual(len([s for s in stops if s.get('kind') == 'bp']), 2, stops)
        self.assertEqual(link.one('bp', 'clear', hexint(1)).get('cleared'), 1)
        link.one('resume', 'ff', 'run')
        self.wait_done(2)
        self.assertEqual(self.read32('raceDone'), 2)
        self.assertEqual(self.read32('raceBad'), 0)
        self.assertEqual(link.one('rd', hexint(self.t['bpTarget']), '6')['hex'], self.original)

    def test_hook_from_8_threads_during_unhook(self):
        link = self.host.link
        args = (hexint(self.t['bpTarget']), hexint(6), '-', hexint(0), hexint(0), hexint(0), hexint(0), hexint(1),
                '0', '0', hexint(0), hexint(0), fnv_link.hexs('race'))
        self.start_race(8, 200000, yield_between=True)
        poke(self.host.process, self.t['raceGo'], 1)
        cycles = busy = 0
        deadline = time.monotonic() + 60                  # a hook left in place would refuse every later one
        while cycles < 200 and self.host.process.poll() is None and time.monotonic() < deadline:
            hook = link.one('hook', *args)
            if not hook.get('ok'):
                busy += hook.get('error') == 'busy'      # a worker stood inside the prologue: refused, not patched
                continue
            cycles += 1
            removed = link.one('unhook', hexint(hook['hook']['id']))
            self.assertTrue(removed.get('ok') or removed.get('error') == 'busy', removed)
            if not removed.get('ok'):                     # retry until the prologue is free
                for _ in range(100):
                    if link.one('unhook', hexint(hook['hook']['id'])).get('ok'):
                        break
        self.wait_done(8, timeout=120)
        self.assertIsNone(self.host.process.poll(), self.alive())
        self.assertEqual(self.read32('raceDone'), 8)
        self.assertEqual(self.read32('raceBad'), 0)
        self.assertGreater(cycles, 0)
        self.assertEqual(link.one('rd', hexint(self.t['bpTarget']), '6')['hex'], self.original)

    def test_probe_budget_disarm_under_8_threads(self):
        # An int3 probe puts its byte back by itself at its call budget, inside the trap handler of whichever
        # threads reach it. Many threads at once must not fault (the N13 pattern, review S4 I1).
        import struct
        link = self.host.link
        info = link.one('probe', 'info')
        if info.get('ok') and info.get('armed'):
            link.one('probe', 'disarm')
        self.assertTrue(link.one('probe', 'init', '4', '2', '0', '0')['ok'])
        record = struct.pack('<IBBB3sH', self.t['bpTarget'], 2, 1, 0, b'\0\0\0', 0)   # push ebp: 1 byte
        self.assertEqual(link.one('probe', 'add', record.hex())['added'], 1)
        self.assertTrue(link.one('probe', 'int3budget', '20')['ok'])
        self.start_race(8, 2000000)
        poke(self.host.process, self.t['raceGo'], 1)
        hot = cycles = 0
        deadline = time.monotonic() + 30
        while cycles < 150 and time.monotonic() < deadline and self.host.process.poll() is None:
            arm = link.one('probe', 'arm')
            if not arm.get('ok'):
                continue
            cycles += 1
            hot = link.one('probe', 'info').get('int3Hot', hot)
        self.assertIsNone(self.host.process.poll(), self.alive())
        link.one('probe', 'disarm')
        self.assertGreater(cycles, 10)
        self.assertEqual(self.read32('raceBad'), 0)
        self.assertEqual(link.one('rd', hexint(self.t['bpTarget']), '6')['hex'], self.original)

    def test_stress_cov_arm_cycle(self):
        link = self.host.link
        sites = b''.join(self.t[k].to_bytes(4, 'little') for k in ('bpTarget', 'bpCall', 'bpAfter', 'bpRet')).hex()
        self.start_race(8, 1000000)
        poke(self.host.process, self.t['raceGo'], 1)
        end = time.monotonic() + 2.0
        cycles = 0
        while cycles < 200 and self.host.process.poll() is None:
            self.assertTrue(link.one('cov', 'disarm').get('ok'))
            reset = link.one('cov', 'reset')
            if reset.get('ok'):
                link.one('cov', 'add', sites)
            self.assertTrue(link.one('cov', 'arm').get('ok'))
            cycles += 1
            if time.monotonic() > end and cycles >= 50:
                break
        self.assertIsNone(self.host.process.poll(), self.alive())
        link.one('cov', 'disarm')
        self.assertEqual(self.read32('raceBad'), 0)
        self.assertEqual(link.one('rd', hexint(self.t['bpTarget']), '6')['hex'], self.original)


@needs_zig
class DeadmanContactTests(StopTestCase):
    """K2: pings and requests the main thread never served kept the dead-man clock fresh, so a worker parked at a stop
    the main thread waited on was never released while the agent kept polling."""

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
        self.addCleanup(lambda: poke(self.host.process, self.t['seam'], 0))

    def test_a_stuck_main_thread_does_not_keep_a_worker_stop_alive(self):
        link = self.host.link
        hook = link.one('hook', hexint(self.t['hookme']), hexint(6), '-', hexint(0), hexint(0), hexint(0), hexint(0),
                        hexint(1), '0', '0', hexint(0), hexint(0), fnv_link.hexs('k2'))
        self.assertTrue(hook.get('ok'), hook)
        self.assertTrue(link.one('hold', 'on', hexint(3000)).get('held'))      # a 3 s dead-man
        link.one('hold', 'off')
        self.hold(hook['hook']['id'])
        link.console('hookthread 4')
        self.wait_stops()
        self.assertEqual(link.ping()['parked'], 1)
        poke(self.host.process, self.t['seam'], 6)          # the main thread stops before it takes a request
        with self.assertRaises(fnv_link.LinkError):
            link.one('stops', timeout=1.0)                  # never served: the pipe thread drops it
        end = time.monotonic() + 8
        parked = 1
        while time.monotonic() < end and parked:
            parked = link.ping()['parked']                  # the agent keeps polling
            time.sleep(0.3)
        self.assertEqual(parked, 0, 'the dead-man released the worker')


@needs_zig
class HookReuseTests(StopTestCase):
    """K3: hook stubs (64 KB for about 1000 hooks) and record rings (768 KB each) were never reused."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.receipt = build(cls.tmp.name, defines=SEAM)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.host = StopHost(self.receipt, Path(self.receipt['dll']).parent, self.tmp.name, 300)
        self.addCleanup(self.host.close)
        self.t = self.host.t
        self.assertTrue(self.t['hookReuseMs'], 'not a seam build')

    def test_installs_past_the_stub_area_reuse_removed_hooks(self):
        poke(self.host.process, self.t['hookReuseMs'], 0)    # no grace period in the test
        link = self.host.link
        for n in range(1100):                               # more than the 1024 stubs of the 64 KB area
            hook = link.one('hook', hexint(self.t['hookme']), hexint(6), '-', hexint(2), hexint(0), hexint(0),
                            hexint(0), hexint(1), '0', '0', hexint(0), hexint(0), fnv_link.hexs('k3'))
            self.assertTrue(hook.get('ok'), (n, hook))
            self.assertTrue(link.one('unhook', hexint(hook['hook']['id'])).get('ok'), n)
        self.host.link.schedule(0, 'hookme 5')
        self.assertTrue(self.wait_line('hookme 1005'), 'the function runs normally after all that')

    def install(self, flags=0, label='k3r'):
        return self.host.link.one('hook', hexint(self.t['hookme']), hexint(6), '-', hexint(flags), hexint(0),
                                  hexint(0), hexint(0), hexint(1), '0', '0', hexint(0), hexint(0), fnv_link.hexs(label))

    def test_review_i3_the_spare_list_has_no_bound(self):
        # Removals beyond the 32 listed + 32 spare slots were dropped with their memory. The lost memory does not show
        # on the wire (each install can reuse the hook just removed), so the source is checked: the spare list is
        # linked through the hooks, with no slot array.
        source = (Path(__file__).resolve().parents[1] / 'native' / 'engine' / 'fnvmcp_engine.c').read_text(
            encoding='utf-8')
        self.assertIn('static Hook *hook_spare;', source)
        self.assertNotIn('hook_spare[', source)

    def test_review_i1_a_call_still_inside_a_removed_hook_keeps_it(self):
        link = self.host.link
        poke(self.host.process, self.t['hookReuseMs'], 0)
        hook = self.install(1)                              # exit capture: the call keeps a shadow frame
        self.assertTrue(link.one('bp', 'set', hexint(1), hexint(self.t['hookme'] + 6), 'int3', 'ff',
                                 hexint(0)).get('ok'))
        link.schedule(0, 'hookme 5')
        stop = self.wait_stops()[0]                         # parked inside hookme, below the hook's frame
        self.assertTrue(link.one('unhook', hexint(hook['hook']['id'])).get('ok'))
        other = link.one('hook', hexint(self.t['bpTarget']), hexint(6), '-', hexint(0), hexint(0), hexint(0),
                         hexint(0), hexint(1), '0', '0', hexint(0), hexint(0), fnv_link.hexs('k3o'))
        self.assertTrue(other.get('ok'), other)
        self.assertNotEqual(other['hook']['hook'], hook['hook']['hook'], 'the parked call still uses it')
        link.one('bp', 'clear', 'ff')
        self.assertTrue(link.one('resume', hexint(stop['seq']), 'run').get('ok'))
        self.assertTrue(self.wait_line('hookme 1005'))
        link.one('unhook', hexint(other['hook']['id']))

    def test_the_grace_period_keeps_a_fresh_removal(self):
        link = self.host.link        # the default grace period: a hook removed just now is not reused
        first = link.one('hook', hexint(self.t['hookme']), hexint(6), '-', hexint(0), hexint(0), hexint(0), hexint(0),
                         hexint(1), '0', '0', hexint(0), hexint(0), fnv_link.hexs('k3a'))
        link.one('unhook', hexint(first['hook']['id']))
        second = link.one('hook', hexint(self.t['hookme']), hexint(6), '-', hexint(0), hexint(0), hexint(0), hexint(0),
                          hexint(1), '0', '0', hexint(0), hexint(0), fnv_link.hexs('k3b'))
        self.assertNotEqual(first['hook']['hook'], second['hook']['hook'], 'a new stub, not the fresh one')
        link.one('unhook', hexint(second['hook']['id']))


if __name__ == '__main__':
    unittest.main()
