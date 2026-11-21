"""The frame sampler (fnv_watch frames=N) and the xNVSE event sinks (fnv_events subscribe) against a fake companion."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_pred import compile_predicate  # noqa: E402
from fnv_mcp.fnv_sampling import ALIASES, FrameSampler, SinkTools  # noqa: E402


class FakeLink:
    def __init__(self):
        self.frame = 1000
        self.ops = []
        self.samplers = {}
        self.sinks = []

    def ping(self):
        self.frame += 30
        return {'frame': self.frame}

    def one(self, *op):
        self.ops.append(op)
        if op[:2] == ('samp', 'list'):
            return {'samplers': [{'id': i} for i in self.samplers]}
        if op[:2] == ('samp', 'set'):
            self.samplers[int(op[2], 16)] = {'every': int(op[3], 16), 'code': op[4], 'start': self.frame}
            return {'ok': True}
        if op[:2] == ('samp', 'read'):
            s = self.samplers[int(op[2], 16)]
            since = int(op[3], 16)
            out = []
            for n in range(since + 1, 61):
                frame = s['start'] + 1 + (n - 1) * s['every']
                out.append({'seq': n, 'frame': frame, 'values': [frame, 10.0 if n < 31 else 20.0]})
            return {'samples': out[:int(op[4], 16)], 'next': 60, 'stopped': False, 'dropped': 0}
        if op[:2] == ('samp', 'clear'):
            self.samplers.pop(int(op[2], 16), None)
            return {'ok': True}
        if op[:2] == ('sink', 'add'):
            self.sinks.append(op[3])
            return {'ok': True, 'slot': len(self.sinks) - 1}
        if op[:2] == ('sink', 'read'):
            return {'records': [{'seq': 1, 'frame': 5, 'source': 'onactivate', 'ref': '0x00104c80',
                                 'words': ['0x41016270', '0x00000014'] + ['0x00000000'] * 4}], 'next': 1, 'dropped': 0}
        if op[:2] == ('sink', 'remove'):
            return {'ok': True, 'removed': 1}
        return {'ok': False, 'error': 'unknown'}


class FakeLive:
    def __init__(self):
        self.link = FakeLink()

    def in_game(self):
        return {}

    def reads(self, spans):
        import struct as st
        known = {0x41016270: st.pack('<IIII', 0x0102F55C, 0, 0, 0x00107C77)}
        return [known.get(a) for a, _ in spans]


class SamplerTests(unittest.TestCase):
    def test_items_compile_into_one_program_and_change_points_are_found(self):
        live = FakeLive()
        out = FrameSampler(live, sleep=lambda s: None).sample(['frame', 'player.z'], frames=60)
        set_op = [op for op in live.link.ops if op[:2] == ('samp', 'set')][0]
        self.assertEqual(set_op[4], compile_predicate('frame')['hex'] + compile_predicate(ALIASES['player.z'])['hex'])
        self.assertEqual(out['samples'], 60)
        self.assertEqual(out['missedFrames'], 0)
        z = out['items'][1]
        self.assertEqual((z['first'], z['last'], z['changes']), (10.0, 20.0, 1))
        self.assertEqual(z['timeline'][1]['frame'], out['frames'][0] + 30)
        self.assertNotIn(int(set_op[2], 16), live.link.samplers)            # cleared at the end

    def test_too_many_items_refused(self):
        with self.assertRaises(ConvError):
            FrameSampler(FakeLive()).sample(['frame'] * 17, frames=10)

    def test_bad_item_text_names_the_column(self):
        with self.assertRaises(ConvError) as cm:
            FrameSampler(FakeLive()).sample(['[0x10]:q9'], frames=10)
        self.assertEqual(cm.exception.code, 'BAD_PREDICATE')


class SinkTests(unittest.TestCase):
    def test_subscribe_and_read(self):
        live = FakeLive()
        sinks = SinkTools(live)
        self.assertEqual(sinks.subscribe(['OnActivate']), {'onactivate': 0})
        out = sinks.read(0)
        self.assertEqual(out['records'][0]['event'], 'onactivate')
        self.assertEqual(out['records'][0]['ref'], '00104C80')
        self.assertEqual(out['records'][0]['args'][:2], ['00107C77', '0x00000014'])   # a form pointer resolved
        self.assertEqual(sinks.unsubscribe(), ['onactivate'])

    def test_unknown_event_refused(self):
        with self.assertRaises(ConvError) as cm:
            SinkTools(FakeLive()).subscribe(['onteleportdance'])
        self.assertEqual(cm.exception.code, 'UNKNOWN_EVENT')


if __name__ == '__main__':
    unittest.main()
