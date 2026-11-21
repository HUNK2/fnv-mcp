"""Bridge core of fnv_procmem (W2): structure sizes, the CONTEXT layout, suspend and resume, a context round trip and a
minidump, on a 32-bit child process started here (SysWOW64 ping.exe). No game is needed."""
import ctypes
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp import fnv_procmem  # noqa: E402

CHILD = Path(os.environ.get('SYSTEMROOT', r'C:\Windows')) / 'SysWOW64' / 'ping.exe'


class StructureTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows processes and structures')
    def test_sizes(self):
        self.assertEqual(ctypes.sizeof(fnv_procmem.THREADENTRY32), 28)
        # pointer, DWORD, pointer: 24 bytes in a 64-bit bridge (the game's side is 32-bit, the bridge is not)
        self.assertEqual(ctypes.sizeof(fnv_procmem.MODULEINFO), 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 12)
        self.assertEqual(fnv_procmem.WOW64_CONTEXT_SIZE, 0x2CC)

    def test_context_offsets(self):
        # winnt.h WOW64_CONTEXT: FloatSave is 0x70 bytes at 0x1C, the segments follow, Edi at 0x9C, Esp at 0xC4.
        offsets = fnv_procmem.CONTEXT_OFFSETS
        self.assertEqual(offsets['dr7'], 0x18)
        self.assertEqual(offsets['edi'], 0x9C)
        self.assertEqual(offsets['eax'], 0xB0)
        self.assertEqual(offsets['eip'], 0xB8)
        self.assertEqual(offsets['eflags'], 0xC0)
        self.assertEqual(offsets['esp'], 0xC4)
        self.assertLess(max(offsets.values()) + 4, fnv_procmem.WOW64_CONTEXT_SIZE)

    def test_parse_context(self):
        raw = bytearray(fnv_procmem.WOW64_CONTEXT_SIZE)
        struct.pack_into('<I', raw, fnv_procmem.CONTEXT_OFFSETS['eip'], 0x00401012)
        struct.pack_into('<I', raw, fnv_procmem.CONTEXT_OFFSETS['esp'], 0x0019F000)
        struct.pack_into('<I', raw, fnv_procmem.CONTEXT_OFFSETS['dr7'], 0x400)
        regs = fnv_procmem.parse_context(bytes(raw))
        self.assertEqual((regs['eip'], regs['esp'], regs['dr7'], regs['ebp']), (0x00401012, 0x0019F000, 0x400, 0))
        self.assertEqual(set(regs), set(fnv_procmem.CONTEXT_OFFSETS))
        with self.assertRaises(fnv_procmem.ProcMemError) as cm:
            fnv_procmem.parse_context(b'\0' * 16)
        self.assertEqual(cm.exception.code, 'CONTEXT_SIZE')


@unittest.skipUnless(CHILD.exists(), 'needs SysWOW64\\ping.exe (a 32-bit child process)')
class ChildProcessTests(unittest.TestCase):
    def setUp(self):
        self.child = subprocess.Popen([str(CHILD), '-n', '60', '127.0.0.1'], stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
        self.addCleanup(self.stop_child)
        self.proc = fnv_procmem.GameProcess(self.child.pid)
        self.addCleanup(self.proc.close)
        self.main = self.proc.main_thread()

    def stop_child(self):
        if self.child.poll() is None:
            self.child.kill()
        self.child.wait(timeout=10)

    def test_suspend_all_but_one_and_resume(self):
        import time
        time.sleep(0.3)
        threads = self.proc.threads()
        self.assertIn(self.main, threads)
        suspended = self.proc.suspend_all(keep=[self.main])
        self.assertEqual(sorted(suspended), sorted(t for t in threads if t != self.main))
        self.assertEqual(sorted(self.proc.resume_all(suspended)), sorted(suspended))

    def test_context_round_trip(self):
        # held for the whole round trip: a child still starting up runs between the reads (seen on the CI runner)
        held = self.proc.suspend_all(keep=[])
        self.addCleanup(self.proc.resume_all, held)
        raw = self.proc.get_context(self.main)
        self.assertEqual(len(raw), fnv_procmem.WOW64_CONTEXT_SIZE)
        before = fnv_procmem.parse_context(raw)
        self.assertNotEqual(before['eip'], 0)
        self.assertTrue(self.proc.set_context(self.main, raw))
        after = fnv_procmem.parse_context(self.proc.get_context(self.main))
        for name in ('eip', 'esp', 'ebp', 'eax', 'ecx'):
            self.assertEqual(before[name], after[name], name)

    def test_minidump(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'child.dmp'
            size = self.proc.minidump(str(path))
            self.assertGreater(size, 1000)
            self.assertEqual(path.read_bytes()[:4], b'MDMP')

    def test_context_on_missing_thread(self):
        with self.assertRaises(fnv_procmem.ProcMemError) as cm:
            self.proc.get_context(0x7FFFFFF0)
        self.assertEqual(cm.exception.code, 'THREAD_UNREADABLE')


if __name__ == '__main__':
    unittest.main()
