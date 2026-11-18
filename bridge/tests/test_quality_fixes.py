"""The rough edges of the tool quality live check (2026-11-18) that are not errors: readable journal values for a
memory write, lock and function names in fnv_threads' full answer, the call log's cut count explained, the save
inspector's globals with a filter, and fnv_guide find for plain-word tasks. Fakes only; no game."""
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp.fnv_live import LiveService  # noqa: E402


class JournalValueTests(unittest.TestCase):
    def test_a_float_write_has_its_values(self):
        self.assertEqual(LiveService._decoded('f32', struct.pack('<f', 7.5)), 7.5)
        self.assertEqual(LiveService._decoded('u32', struct.pack('<I', 12)), 12)
        self.assertIsNone(LiveService._decoded('bytes', b'\x01\x02'))
        self.assertIsNone(LiveService._decoded('f32', b'\x01'))


class ThreadNameTests(unittest.TestCase):
    def test_the_full_answer_has_no_glued_address_and_no_minus_one_lock(self):
        from geck_mcp import fnv_perf
        engine = SimpleNamespace(symbolize=lambda address, mods, return_address=False: {
            'kind': 'function', 'name': 'E7CCD0Renderer::PresentFrame', 'functionAddress': '0x00E7CCD0'})
        self.assertEqual(fnv_perf.Namer(engine, [])(0xE7CCD4)['function'], 'Renderer::PresentFrame')
        proc = SimpleNamespace(read=lambda address, n: struct.pack('<II', 0xFFFFFFFF, 0xFFFFFFFF))
        for row in fnv_perf.ThreadTools._locks(proc):
            self.assertIsNone(row['ownerThread'])
            self.assertIsNone(row['count'])
            self.assertIn('not a valid lock state', row['note'])


class CallLogTests(unittest.TestCase):
    def test_cut_is_explained(self):
        from geck_mcp.fnv_calllog import CallLog
        with tempfile.TemporaryDirectory() as d:
            log = CallLog(d)
            log.record('fnv_threads', 'list', True, None, 5, 30000, True)
            summary = log.summary()
        self.assertEqual(summary['cut'], 1)
        self.assertIn('shortened', summary['cutMeans'])


class SaveInspectTests(unittest.TestCase):
    def test_a_filter_leaves_the_globals_out(self):
        from geck_mcp import fnv_save
        from geck_mcp.fnv_tools import FnvService
        service = FnvService.__new__(FnvService)
        service.run = lambda tool, action: action()
        service._save_path = lambda save: Path('x.fos')
        service._namer = lambda: None
        seen = []

        def inspect(path, types, forms, limit, offset, shot, include_globals=True, namer=None):
            seen.append(include_globals)
            return {}
        with mock.patch.object(fnv_save, 'inspect', inspect):
            plain = service.save_inspect('x')
            filtered = service.save_inspect('x', types=['NPC_'])
            service.save_inspect('x', types=['GLOB'])
        self.assertEqual(seen, [True, False, True])
        self.assertNotIn('globalVariablesNote', plain)
        self.assertIn('GLOB', filtered['globalVariablesNote'])


class SaveNameTests(unittest.TestCase):
    def test_a_trailing_newline_is_not_a_save_name(self):
        # bug hunt 2026-11-18: SAVE_NAME.match let "x\n" through ("$" matches before a final newline)
        from geck_mcp.fnv_game import GameError, parse_start
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'x.fos').write_bytes(b'')
            self.assertEqual(parse_start('save:x', d), ('save', 'x'))
            with self.assertRaises(GameError) as caught:
                parse_start('save:x\n', d)
        self.assertEqual(caught.exception.code, 'BAD_SAVE')


class FindTests(unittest.TestCase):
    def test_plain_words_find_the_tool(self):
        from geck_mcp.fnv_guide import GuideTools
        guide = GuideTools()
        for words, tool, action in (('make it night time', 'fnv_time', 'calendar'),
                                    ('the game froze', 'fnv_threads', 'hang'),
                                    ('game is not responding', 'fnv_threads', 'hang')):
            best = guide.find(words, limit=1)['rows'][0]
            self.assertEqual((best['name'], best['action']), (tool, action), words)


if __name__ == '__main__':
    unittest.main()
