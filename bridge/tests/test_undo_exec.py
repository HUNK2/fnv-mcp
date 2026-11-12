"""PLAN-3 S3.1: the undo executor tells the truth (N1, N2)."""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakes import FakeLive  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[1] / 'geck_mcp'


class UndoExecutorTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeLive()
        self.addCleanup(self.fake.close)
        self.live = self.fake.live

    def test_unknown_op_not_marked_undone(self):
        e = self.live.journal.add({'kind': 'x', 'undo': {'op': 'no_such_op'}})
        out = self.live.undo()
        r = out['results'][0]
        self.assertFalse(r['undone'])
        self.assertEqual(r['error']['code'], 'UNKNOWN_UNDO_OP')
        self.assertFalse(self.live.journal.get(e['id'])['undone'])
        self.assertFalse(out['changed'])

    def test_rejected_console_undo_not_marked_undone(self):
        real = self.fake.link._op

        def reject(name, args):
            r = real(name, args)
            if name == 'con':
                r['accepted'] = False
            return r
        self.fake.link._op = reject
        e = self.live.journal.add({'kind': 'setting', 'undo': {'op': 'console', 'line': 'SetGS fX 1'}})
        r = self.live.undo()['results'][0]
        self.assertFalse(r['undone'])
        self.assertIn('output', r)
        self.assertFalse(self.live.journal.get(e['id'])['undone'])

    def test_compile_error_console_ref_undo_not_marked_undone(self):
        real = self.fake.link._op

        def bad(name, args):
            r = real(name, args)
            if name == 'con':
                r['output'] = "Script 'SysWindowCompileAndRun', line 1:\r\nCompiled script not saved!\r\n"
            return r
        self.fake.link._op = bad
        self.live.resolve = lambda spec: {'ptr': 0x1000, 'refId': 0x14}
        e = self.live.journal.add({'kind': 'actor', 'undo': {'op': 'console_ref', 'ref': '14', 'lines': ['Disable']}})
        r = self.live.undo()['results'][0]
        self.assertFalse(r['undone'])
        self.assertFalse(self.live.journal.get(e['id'])['undone'])

    def test_accepted_console_undo_is_marked_undone(self):
        e = self.live.journal.add({'kind': 'setting', 'undo': {'op': 'console', 'line': 'SetGS fX 1'}})
        self.assertTrue(self.live.undo()['results'][0]['undone'])
        self.assertTrue(self.live.journal.get(e['id'])['undone'])

    def test_registry_covers_every_op_written(self):
        from geck_mcp.fnv_live import UNDO_OPS
        written = set()
        for path in PACKAGE.rglob('*.py'):
            written |= set(re.findall(r"""['"]op['"]:\s*['"]([a-z_]+)['"]""", path.read_text(encoding='utf-8')))
        self.assertTrue(written)
        self.assertEqual(sorted(written - set(UNDO_OPS)), [])


if __name__ == '__main__':
    unittest.main()
