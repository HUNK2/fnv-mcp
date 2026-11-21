"""The live warning capture: the printf formatter over stack words, the locked log files of a running game, and the
rows built from hook records. Fakes only; no game."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp import fnv_warnings  # noqa: E402
from fnv_mcp.fnv_warnings import WarningCapture, Warnings, format_printf  # noqa: E402


def dwords(x):
    return list(struct.unpack('<II', struct.pack('<d', x)))


class FormatTests(unittest.TestCase):
    def test_integers_hex_strings_and_percent(self):
        strings = {0x500000: 'Goodsprings'}
        text, used = format_printf("MASTERFILE: Form '%s' (%08X) has %d refs, 100%%", [0x500000, 0x104C80, 3],
                                   strings.get)
        self.assertEqual(text, "MASTERFILE: Form 'Goodsprings' (00104C80) has 3 refs, 100%")
        self.assertEqual(used, 3)

    def test_negative_and_double(self):
        text, used = format_printf('%d at %.2f', [0xFFFFFFFF] + dwords(1.5), lambda p: None)
        self.assertEqual(text, '-1 at 1.50')
        self.assertEqual(used, 3)

    def test_missing_and_unreadable_arguments(self):
        text, _ = format_printf('%s and %d', [0x1234], lambda p: None)
        self.assertEqual(text, '<?0x00001234> and <?>')
        self.assertEqual(format_printf('%s', [0], lambda p: None)[0], '(null)')


class LockedFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / 'falloutnv_error.log').write_text('')
        (root / 'falloutnv_havok.log').write_text('')
        self.root = root

    def locked(self):
        real = Path.read_bytes

        def read_bytes(path):
            if path.name.startswith('falloutnv_'):
                raise PermissionError(13, 'Permission denied')
            return real(path)
        return mock.patch.object(Path, 'read_bytes', read_bytes)

    def test_locked_files_without_capture_say_how_to_see_warnings(self):
        w = Warnings(self.root, None, 'Dev', self.root / 'state')
        with self.locked():
            out = w.read()
            w.clear()
        self.assertEqual(out['total'], 0)
        self.assertTrue(any('fnv_warnings capture' in n for n in out['notes']))

    def test_locked_engine_log_reads_the_capture(self):
        capture = mock.MagicMock()
        capture.rows.return_value = ([{'seq': 1, 'source': 'engine', 'category': 'MODELS',
                                       'text': 'MODELS: missing x.nif', 'caller': '0x00400000'}], 0)
        w = Warnings(self.root, None, 'Dev', self.root / 'state', capture)
        with self.locked():
            out = w.read()
        self.assertEqual(out['total'], 1)
        self.assertEqual(out['files']['engine']['from'], 'live capture')


class FakeLink:
    def __init__(self, hooks):
        self.hooks = hooks

    def one(self, *op):
        if op[0] == 'hooks':
            return {'hooks': self.hooks}
        return {'ok': True}


class CaptureRowsTests(unittest.TestCase):
    def test_rows_format_each_record_and_handle_the_leading_int(self):
        fmt1, fmt2, name = 0x01000000, 0x01000100, 0x02000000
        mem = {fmt1: b'MODELS: missing %s\0', fmt2: b'AI: actor %08X\0', name: b'meshes\\x.nif\0'}

        def record(seq, args):
            r = [0] * 48
            r[0], r[2], r[5] = seq, 77, 0x00401234
            r[12:12 + len(args)] = args
            snap = (mem.get(args[1]) or b'').ljust(96, b'\0')[:96]    # the hook copies the bytes behind word 1
            r[24:48] = struct.unpack('<24I', snap)
            return tuple(r)

        live = mock.MagicMock()
        live.link = FakeLink([{'label': fnv_warnings.CAPTURE_LABEL, 'stolen': 5, 'id': 9, 'seqAddress': '0x10'}])
        live.reads.side_effect = lambda spans: [mem.get(a) for a, _ in spans]
        live.deep.records.return_value = (2, [record(1, [fmt1, name]), record(2, [3, fmt2, 0x104C80])], 0)
        cap = WarningCapture(live)
        rows, lost = cap.rows()
        self.assertEqual([r['text'] for r in rows], ['MODELS: missing meshes\\x.nif', 'AI: actor 00104C80'])
        self.assertEqual(rows[0]['category'], 'MODELS')
        self.assertEqual(rows[0]['caller'], '0x00401234')
        self.assertEqual(lost, 0)

    def test_the_copy_made_at_the_call_wins_over_a_later_read(self):
        fmt, buf = 0x01000000, 0x02000000
        live = mock.MagicMock()
        live.link = FakeLink([{'label': fnv_warnings.CAPTURE_LABEL, 'stolen': 5, 'id': 9, 'seqAddress': '0x10'}])
        later = {fmt: b'MODELS: %s\0', buf: b'reused buffer\0'}
        live.reads.side_effect = lambda spans: [later.get(a) for a, _ in spans]
        r = [0] * 48
        r[0], r[12], r[13] = 1, fmt, buf
        r[24:48] = struct.unpack('<24I', b'at the call\0'.ljust(96, b'\0'))
        live.deep.records.return_value = (1, [tuple(r)], 0)
        rows, _ = WarningCapture(live).rows()
        self.assertEqual(rows[0]['text'], 'MODELS: at the call')


if __name__ == '__main__':
    unittest.main()
