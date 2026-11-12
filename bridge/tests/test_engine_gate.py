"""Engine companion, wire version 2: predicates and the frame gate, over the real DLL and a fake xNVSE host.
Skipped when no Zig is found (GECK_MCP_ZIG, or fnv.zig in a projects/*.json file). Numbers on the wire are hex."""
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_link  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/: engine_host
from engine_host import needs_zig, zig_path  # noqa: E402,F401


def hexint(value):
    return fnv_link.word(value)


def i32(value):
    return struct.pack('<i', value).hex()


def u32(value):
    return struct.pack('<I', value).hex()


@needs_zig
class GateCompanionTests(unittest.TestCase):
    """The real DLL over its pipe. The fake host pumps the main-loop message from its main thread every 5 ms."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        receipt = fnv_link.build(cls.tmp.name, zig_path(), with_fake_host=True)
        folder = Path(receipt['dll']).parent
        cls.pipe = 'fnvmcp-gate-' + uuid.uuid4().hex[:8]
        env = dict(os.environ, FNVMCP_PIPE=cls.pipe, FNVMCP_CONSOLE_LOG=str(Path(cls.tmp.name) / 'console.log'))
        cls.host = subprocess.Popen([str(folder / 'fnvmcp_fake_host.exe'), receipt['dll'], '300'], stdout=subprocess.PIPE,
                                    env=env, text=True)
        cls.t = {k: int(v, 16) for k, v in json.loads(cls.host.stdout.readline()).items()}
        cls.link = fnv_link.EngineLink(cls.pipe).connect(wait=10)
        cls.link.one('gatecfg', hexint(cls.t['timerDisable']), hexint(cls.t['timerEnable']), hexint(cls.t['timerGlobal']))

    @classmethod
    def tearDownClass(cls):
        cls.link.close()
        cls.host.kill()
        cls.host.wait()
        cls.host.stdout.close()
        cls.tmp.cleanup()

    def op(self, *args):
        return self.link.one(*args)

    def held(self):
        return self.link.ping()['held']

    def frame(self):
        return self.link.info()['frame']

    def counts(self):
        """(clock disables, clock enables) seen by the fake timer functions."""
        return (self.link.u32(self.t['timerDisables']), self.link.u32(self.t['timerEnables']))

    def test_ping_reports_version_and_gate_fields(self):
        ping = self.link.ping()
        self.assertEqual((ping['companion'], ping['wire']), (3, 2))     # 3: writes exit notes (S6 review C)
        for key in ('held', 'stepLeft', 'parked', 'lastFrameTick'):
            self.assertIn(key, ping)
        self.assertEqual(ping['parked'], 0)

    def test_predicate_set_eval_and_errors(self):
        lt = '01' + i32(3) + '01' + i32(5) + '22'  # 3 < 5
        gt = '01' + i32(5) + '01' + i32(3) + '22'  # 5 < 3
        r = self.op('pred', 'set', '2', lt)
        self.assertEqual((r['ok'], r['id'], r['ops']), (True, 2, 3))
        self.assertEqual(self.op('pred', 'set', '3', gt)['ops'], 3)
        ev = self.op('pred', 'eval', '2')
        self.assertEqual((ev['value'], ev['top'], ev['errors']), (True, 1, 0))
        self.assertFalse(self.op('pred', 'eval', '3')['value'])
        # a load from a bad address is false and counts one error
        self.op('pred', 'set', '5', '10' + '00' + u32(4) + '01' + i32(0) + '04')
        bad = self.op('pred', 'eval', '5')
        self.assertEqual((bad['value'], bad['errors']), (False, 1))
        # a load from a readable address (the fake buffer starts with "fnvmcp")
        self.op('pred', 'set', '6', '10' + '00' + u32(self.t['buffer']) + '01' + i32(0) + '00' + '01' + u32(0x66) + '20')
        self.assertTrue(self.op('pred', 'eval', '6')['value'])
        # an expression in compile cache slot 0 (the fake host compiles "form" to a form pointer)
        self.link.eval('form')
        self.op('pred', 'set', '7', '60' + '0000')
        self.assertTrue(self.op('pred', 'eval', '7')['value'])
        # refused programs
        self.assertEqual(self.op('pred', 'set', '4', 'ff')['error'], 'bad_program')
        self.assertEqual(self.op('pred', 'set', '4', '0102')['error'], 'bad_program')
        self.assertEqual(self.op('pred', 'set', '4', '22')['error'], 'bad_program')
        self.assertEqual(self.op('pred', 'set', '4', '51' * 65)['error'], 'too_long')
        self.assertEqual(self.op('pred', 'set', '40', lt)['error'], 'bad_id')
        self.assertEqual(self.op('pred', 'set', '4', '51' + 'x')['error'], 'bad_arguments')
        listed = {p['id']: p for p in self.op('pred', 'list')['preds']}
        self.assertEqual((listed[5]['errors'], listed[2]['ops']), (1, 3))
        cleared = self.op('pred', 'clear', 'ff')['cleared']
        self.assertGreaterEqual(cleared, 4)
        self.assertEqual(self.op('pred', 'eval', '2')['error'], 'bad_id')

    def test_hold_serves_requests_without_frames(self):
        self.op('hold', 'on')
        self.assertTrue(self.held())
        frame = self.frame()
        time.sleep(0.15)
        self.assertEqual(self.frame(), frame)
        ping = self.link.ping()
        self.assertEqual(ping['stepLeft'], 0)
        self.op('hold', 'off')
        self.assertFalse(self.held())
        before = self.frame()
        time.sleep(0.1)
        self.assertGreater(self.frame(), before)

    def test_step_runs_exact_frames_and_serves_the_rest(self):
        self.op('hold', 'on')
        frame = self.frame()
        res = self.link.batch([('step', hexint(5)), ('info',)])
        self.assertEqual((res[0]['stepped'], res[0]['frame']), (5, frame + 5))
        self.assertEqual(res[1]['frame'], frame + 5)
        self.assertTrue(self.held())
        # the top-level frame of the response is the stop frame too
        reply = self.link._exchange('%s\tstep %s\tinfo' % (uuid.uuid4().hex[:8], hexint(3)), 15.0)
        self.assertEqual(reply['frame'], frame + 8)
        self.assertEqual(reply['results'][1]['frame'], frame + 8)
        time.sleep(0.1)
        self.assertEqual(self.frame(), frame + 8)
        self.assertEqual(self.op('step', '0')['error'], 'bad_arguments')
        self.assertEqual(self.op('step', hexint(0x2711))['error'], 'bad_arguments')

    def test_until_stops_on_a_predicate_or_at_max(self):
        self.op('hold', 'on')
        frame = self.frame()
        self.op('pred', 'set', '8', '50' + '01' + i32(frame + 3) + '25')  # frame >= frame + 3
        met = self.op('until', '8', hexint(16))
        self.assertEqual((met['met'], met['frames'], met['frame']), (True, 3, frame + 3))
        self.assertTrue(self.held())
        now = self.frame()
        self.op('pred', 'set', '9', '50' + '01' + i32(0x7FFFFFFF) + '25')  # frame >= 0x7fffffff: never
        capped = self.op('until', '9', '2')
        self.assertEqual((capped['met'], capped['frames'], capped['frame']), (False, 2, now + 2))
        again = self.op('until', '8', hexint(16))
        self.assertEqual((again['met'], again['frames']), (True, 0))
        self.assertEqual(self.op('until', 'a', '2')['error'], 'bad_id')
        gate = self.op('gate')
        self.assertTrue(gate['held'])
        self.assertIsNone(gate['untilPred'])

    def test_clock_pause_stays_balanced(self):
        self.op('hold', 'off')
        disables, enables = self.counts()
        self.op('hold', 'on')
        self.assertEqual(self.counts(), (disables + 1, enables))
        gate = self.op('gate')
        self.assertTrue(gate['held'] and gate['clockPausedByUs'])
        self.op('step', '3')  # enables the clock for the frames, disables it again at the stop
        self.assertEqual(self.counts(), (disables + 2, enables + 1))
        self.op('hold', 'off')
        self.assertEqual(self.counts(), (disables + 2, enables + 2))
        gate = self.op('gate')
        self.assertFalse(gate['held'] or gate['clockPausedByUs'])

    def test_dead_man_opens_a_silent_gate(self):
        self.op('hold', 'on', hexint(300))
        time.sleep(0.8)  # no request and no ping: the dead-man switch opens the gate after 300 ms
        self.assertFalse(self.link.ping()['held'])
        types = [e['type'] for e in self.link.events(0)['events']]
        self.assertIn('deadman', types)
        before = self.frame()
        time.sleep(0.1)
        self.assertGreater(self.frame(), before)


if __name__ == '__main__':
    unittest.main()
