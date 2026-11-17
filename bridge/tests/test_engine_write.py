"""Engine companion: a wr into plain data memory takes the fast way, without VirtualQuery.

VirtualQuery also sizes the page's region. The game's heap is one region of 80 MB and more, where a VirtualQuery
takes about 0.2 ms; a write made three of them (and WriteProcessMemory a fourth), about 0.6 ms per write. fnv_dialogue
tree sent 250 writes per request. Resident, writable, non-executable pages are now told apart by the working-set
query (cost per page) and written with NtWriteVirtualMemory. Code and every other page go the old way. The seam build
counts the writes that go the old way."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/: engine_host
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine_host import StopHost, build, hexint, needs_zig  # noqa: E402

SEAM = ('FNVMCP_TEST_SEAM',)


@needs_zig
class WritePathTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.receipt = build(cls.tmp.name, defines=SEAM)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.host = StopHost(self.receipt, Path(self.receipt['dll']).parent, self.tmp.name, 60)
        self.addCleanup(self.host.close)
        self.t, self.link = self.host.t, self.host.link
        self.assertTrue(self.t.get('slowWrites'), 'not a seam build')

    def slow_writes(self):
        return int.from_bytes(bytes.fromhex(self.link.one('rd', hexint(self.t['slowWrites']), '4')['hex']), 'little')

    def test_writes_into_data_memory_take_the_fast_way(self):
        buffer = self.t['buffer']
        self.link.one('rd', hexint(buffer), '400')                       # resident, as live game objects are
        before = self.slow_writes()
        replies = self.link.batch([('wr', hexint(buffer + 8 * k), '%016x' % k) for k in range(50)])
        self.assertTrue(all(r.get('ok') for r in replies), replies[:3])
        self.assertEqual(self.slow_writes(), before)
        self.assertEqual(self.link.one('rd', hexint(buffer + 8 * 7), '8')['hex'], '%016x' % 7)

    def test_a_write_across_a_page_boundary_is_written_whole(self):
        buffer = self.t['buffer']
        at = ((buffer + 0x1000) & ~0xFFF) - 3                             # 3 bytes on one page, 5 on the next
        self.link.one('rd', hexint(at), '8')
        reply = self.link.one('wr', hexint(at), '0102030405060708')
        self.assertTrue(reply.get('ok'), reply)
        self.assertEqual(self.link.one('rd', hexint(at), '8')['hex'], '0102030405060708')

    def test_a_write_into_code_still_goes_the_old_way(self):
        target = hexint(self.t['add3'])
        original = self.link.one('rd', target, '2')['hex']
        before = self.slow_writes()
        reply = self.link.one('wr', target, original)
        self.assertTrue(reply.get('ok'), reply)
        self.assertEqual(reply['old'], original)
        self.assertEqual(self.slow_writes() - before, 1)
        self.assertEqual(self.link.one('rd', target, '2')['hex'], original)

    def test_a_write_into_unmapped_memory_still_fails(self):
        self.assertEqual(self.link.one('wr', '10000', '00').get('error'), 'unreadable')


if __name__ == '__main__':
    unittest.main()
