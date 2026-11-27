"""Regression tests for the Gemini audit, area 5 (BUG-067 .. BUG-082)."""
import struct
import unittest

from fnv_mcp import fnv_agent, fnv_crawl, fnv_inject, fnv_social
from fnv_mcp.fnv_conv import ConvError


def _mem(kind, raw):
    prepared = {'type': kind, 'size': len(raw)}
    return fnv_agent.AgentTools._mem_value(prepared, {'ok': True, 'hex': raw.hex()})


class MemValue(unittest.TestCase):
    def test_signed_and_wide_types(self):
        self.assertEqual(_mem('SInt8', b'\xff'), -1)
        self.assertEqual(_mem('char', b'\xfe'), -2)
        self.assertEqual(_mem('SInt16', struct.pack('<h', -5)), -5)
        self.assertEqual(_mem('short', struct.pack('<h', -7)), -7)
        self.assertEqual(_mem('u16', struct.pack('<H', 65000)), 65000)
        self.assertEqual(_mem('i32', struct.pack('<i', -9)), -9)
        self.assertEqual(_mem('f32', struct.pack('<f', 1.5)), 1.5)
        self.assertEqual(_mem('f64', struct.pack('<d', 2.25)), 2.25)
        self.assertEqual(_mem('uint32', struct.pack('<I', 4000000000)), 4000000000)
        self.assertEqual(_mem('Foo*', struct.pack('<I', 0x1234)), '0x00001234')

    def test_span_size_follows_type(self):
        from fnv_mcp.fnv_live import SIMPLE_TYPES
        for kind, size in (('SInt8', 1), ('i16', 2), ('f64', 8), ('f32', 4)):
            self.assertEqual(SIMPLE_TYPES[kind][1], size)


class Positive(unittest.TestCase):
    def test_global_compare_uses_resolved_value(self):
        item = {'comparison': '==', 'value': 1e-40, 'useGlobal': 4}
        self.assertFalse(fnv_social._positive(item))
        self.assertTrue(fnv_social._positive(item, 1.0))
        self.assertTrue(fnv_social._positive({'comparison': '==', 'value': 1.0}))


class Rewear(unittest.TestCase):
    def test_failures_are_reported(self):
        class Live:
            def console(self, line):
                raise ConvError('X', 'boom')
        crawl = fnv_crawl.CrawlTools.__new__(fnv_crawl.CrawlTools)
        crawl.live = Live()
        with self.assertRaises(RuntimeError):
            crawl._rewear(['0001', '0002'])


class InjectorAlso(unittest.TestCase):
    def _injector(self, results):
        calls, recorded = [], []

        class Live:
            replay_recorder = staticmethod(lambda op, frame: recorded.append(op))

        inj = fnv_inject.Injector(Live())
        inj.ensure = lambda: None
        inj.batch = lambda ops: calls.append(ops) or results[:len(ops)] if ops != [('inp', 'clear')] else calls.append(ops) or []
        inj.wait_frames = lambda n: None
        return inj, calls, recorded

    def test_clear_when_main_op_refused(self):
        inj, calls, _ = self._injector([{'ok': False, 'error': 'x'}, {'ok': True}])
        with self.assertRaises(ConvError):
            inj.job('hold', 'd', 5, ('inp', 'a'), False, also=('inp', 'b'))
        self.assertIn([('inp', 'clear')], calls)

    def test_also_is_recorded(self):
        inj, _, recorded = self._injector([{'ok': True, 'frame': 3}, {'ok': True, 'frame': 3}])
        try:
            inj.job('hold', 'd', 5, ('inp', 'a'), False, also=('inp', 'b'))
        except Exception:
            pass
        self.assertEqual(recorded, [('inp', 'a'), ('inp', 'b')])


if __name__ == '__main__':
    unittest.main()
