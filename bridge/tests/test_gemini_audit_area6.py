"""Gemini audit area 6 (BUG-083 .. BUG-101): regression tests for the fixes that run without the game."""
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp import fnv_calllog, fnv_warnings, server  # noqa: E402
from fnv_mcp.fnv_link import EngineLink, LinkError  # noqa: E402

BS = chr(92)
FAKE_HOME = 'C:' + BS + 'Users' + BS + 'Zed'      # built at run time: no user-folder path in the source


class CallLogScrubTests(unittest.TestCase):         # BUG-100
    def test_every_form_is_scrubbed(self):
        with mock.patch.dict(os.environ, {'USERPROFILE': FAKE_HOME}):
            value = {'p': FAKE_HOME + BS + 'x', 'q': ['C:/Users/Zed/y', FAKE_HOME.lower() + BS + 'Z'],
                     'r': {FAKE_HOME + BS + 'k': 'C:' + BS + BS + 'Users' + BS + BS + 'Zed' + BS + BS + 'w'},
                     'n': 3}
            out = fnv_calllog._value(value)
            text = json.dumps(out)
            self.assertNotIn('Zed', text)
            self.assertIn('%USERPROFILE%', text)
            self.assertEqual(out['n'], 3)
            self.assertNotIn('Zed', json.dumps(fnv_calllog._value(FAKE_HOME + BS + 'plain')))
            self.assertNotIn('Zed', fnv_calllog.scrub('"C:' + BS + BS + 'Users' + BS + BS + 'Zed' + BS + BS + 'a"'))

    def test_long_value_is_scrubbed_too(self):
        with mock.patch.dict(os.environ, {'USERPROFILE': FAKE_HOME}):
            out = fnv_calllog._value([FAKE_HOME + BS + 'a' * 300])
            self.assertNotIn('Zed', out)


class CapAnswerTests(unittest.TestCase):            # BUG-084, BUG-085
    def test_list_offset_does_not_raise(self):
        big = {'data': {'rows': [{'i': i, 'pad': 'x' * 50} for i in range(2000)]}}
        out = server.cap_answer(big, {'offset'}, {'offset': [1, 2, 3]}, cap=5000)
        self.assertTrue(out['data']['truncated'])
        self.assertIn('offset=%d' % len(out['data']['rows']), out['data']['truncatedNote'])
        out = server.cap_answer(big, {'offset'}, {'offset': True}, cap=5000)
        self.assertIn('offset=%d' % len(out['data']['rows']), out['data']['truncatedNote'])

    def test_int_offset_pages(self):
        big = {'rows': [{'i': i, 'pad': 'x' * 50} for i in range(2000)]}
        out = server.cap_answer(big, {'offset'}, {'offset': 100}, cap=5000)
        self.assertIn('offset=%d' % (100 + len(out['rows'])), out['truncatedNote'])

    def test_small_list_is_kept(self):
        big = {'pos': [1, 2, 3], 'text': 'y' * 50000}
        self.assertEqual(server.cap_answer(big, set(), {}), big)

    def test_big_string_and_big_list(self):
        big = {'rows': [{'i': i} for i in range(5)], 'text': 'y' * 50000}
        self.assertEqual(server.cap_answer(big, set(), {}), big)
        huge = {'rows': [{'i': i} for i in range(500)], 'text': 'y' * 50000}
        self.assertEqual(server.cap_answer(huge, set(), {}), huge)


class PrintfTests(unittest.TestCase):               # BUG-094
    def test_wild_width_and_precision(self):
        text, _ = fnv_warnings.format_printf('%*d|', [0xFFFFFFFF, 5], lambda p: None)
        self.assertEqual(text, '5|')
        text, _ = fnv_warnings.format_printf('%.*f|', [0xFFFFFFFF, 0, 0x3FF00000], lambda p: None)
        self.assertTrue(text.endswith('|'))
        text, _ = fnv_warnings.format_printf('%*d|', [100000000, 5], lambda p: None)
        self.assertLessEqual(len(text), 600)

    def test_string_conversion_with_width_is_found(self):
        self.assertTrue(fnv_warnings._has_string('a %-20s b'))
        self.assertTrue(fnv_warnings._has_string('%.32s'))
        self.assertFalse(fnv_warnings._has_string('100%% done %d'))


class CallerNameTests(unittest.TestCase):           # BUG-086
    def test_return_address_inside_function_is_named(self):
        class Engine:
            def containing(self, a):
                return (0x400000, a - 0x400000) if 0x400000 <= a < 0x400100 else None

            def summary(self, a):
                if a != 0x400000:
                    raise KeyError(a)
                return {'name': 'Fn'}

        live = mock.Mock()
        live.engine.return_value = Engine()
        live.deep.records.return_value = (None, [self._record()], 0)
        w = fnv_warnings.WarningCapture.__new__(fnv_warnings.WarningCapture)
        w.live, w.since, w._strings_cache = live, 0, {}
        w.hook = lambda: {'id': 1}
        w._read_strings = lambda ptrs, fresh=False: {p: 'plain text' for p in ptrs}
        rows, _ = w.rows()
        self.assertEqual(rows[0]['callerName'], 'Fn')

    @staticmethod
    def _record():
        rec = [0] * 48
        rec[0], rec[2], rec[5], rec[12] = 1, 7, 0x400010, 0x500000
        return rec


class LinkTests(unittest.TestCase):                 # BUG-083, BUG-101
    def test_close_is_idempotent_and_waits_for_the_lock(self):
        link = EngineLink(pipe='nonexistent-test')
        link.close()
        link.close()
        self.assertIsNone(link.handle)
        done = []
        with link.lock:
            t = threading.Thread(target=lambda: (link.close(), done.append(1)))
            t.start()
            t.join(0.3)
            self.assertFalse(done)          # blocked while another thread holds the link
        t.join(8)
        self.assertTrue(done)

    def test_ping_reply_for_another_request(self):
        link = EngineLink(pipe='nonexistent-test')
        link._exchange = lambda line, timeout: {'id': 'other', 'ok': True}
        with self.assertRaises(LinkError) as ctx:
            link.ping()
        self.assertEqual(ctx.exception.code, 'COMPANION_PROTOCOL')


class GuideTests(unittest.TestCase):                # BUG-101
    def test_filler_only_query_is_refused(self):
        from fnv_mcp.fnv_guide import GuideTools
        with self.assertRaises(ValueError):
            GuideTools().engine('the of a')


if __name__ == '__main__':
    unittest.main()
