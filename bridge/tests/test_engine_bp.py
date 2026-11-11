"""Engine companion: trap breakpoints (int3 and hardware), the step, to, ret and trace resumes, and the log flag, over the
real DLL and a fake xNVSE host. Skipped unless GECK_MCP_ZIG is set (the companion is built with Zig). Numbers on the
wire are hex. The target is the fake host's bp_target: its layout (contract 8.2, 8.4) is
  +0 push ebp, +1 mov ebp,esp, +3 mov eax,[ebp+8], +6 call bp_callee, +11 add eax,[ebp+12], +14 pop ebp, +15 ret."""
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_engine_stops import StopHost, StopTestCase, build, hexint  # noqa: E402

TRACE_OFFSETS = (0, 1, 3, 6, 11, 14, 15)  # the instructions of bp_target that the trace visits
PRED_FALSE = '01' + struct.pack('<I', 0).hex()  # push 0: never passes
LOG_SEVEN = '01' + struct.pack('<I', 7).hex()  # push 7: one logged value


def address(text):
    return int(text, 16)


@unittest.skipUnless(os.environ.get('GECK_MCP_ZIG'), 'set GECK_MCP_ZIG to build the engine companion')
class BreakpointTests(StopTestCase):
    """One host serves the class. Every test clears its breakpoints and releases its stops in tearDown."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        receipt = build(cls.tmp.name)
        cls.host = StopHost(receipt, Path(receipt['dll']).parent, cls.tmp.name, 300)
        cls.t = cls.host.t

    @classmethod
    def tearDownClass(cls):
        cls.host.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.assertEqual(self.host.link.one('stops')['stops'], [])

    def tearDown(self):
        self.host.link.one('bp', 'clear', 'ff')
        self.host.link.one('dbg', 'clear', 'f')
        for stop in self.host.link.one('stops')['stops']:
            self.host.link.one('resume', hexint(stop['seq']), 'run')

    def set_bp(self, bp_id, addr, kind='int3', pred='ff', flags=0, log=None):
        args = ['bp', 'set', hexint(bp_id), hexint(addr), kind, pred, hexint(flags)]
        if log is not None:
            args.append(log)
        result = self.host.link.one(*args)
        self.assertTrue(result.get('ok'), result)
        return result

    def parked_bp_stop(self, text='bpcall 3'):
        self.host.link.schedule(0, text)
        stops = self.wait_stops()
        self.assertEqual(len(stops), 1, stops)
        return stops[0]

    def test_int3_breakpoint_parks_and_run_finishes_the_call(self):
        self.set_bp(0, self.t['bpTarget'])
        stop = self.parked_bp_stop()
        self.assertEqual((stop['kind'], stop['id'], stop['main']), ('bp', 0, True))
        self.assertEqual(address(stop['eip']), self.t['bpTarget'])
        self.assertTrue(self.host.link.one('resume', hexint(stop['seq']), 'run')['ok'])
        self.assertTrue(self.wait_line('bpcall 12'))  # bp_callee (7) + 5
        listed = {b['id']: b for b in self.host.link.one('bp', 'list')['bps']}
        self.assertEqual(listed[0]['hits'], 1)
        self.assertEqual(self.host.link.one('bp', 'clear', hexint(0))['cleared'], 1)

    def test_step_moves_one_instruction_and_stops(self):
        self.set_bp(0, self.t['bpTarget'])
        stop = self.parked_bp_stop()
        self.assertTrue(self.host.link.one('resume', hexint(stop['seq']), 'step')['ok'])
        step = [s for s in self.wait_stops() if s['kind'] == 'step']
        self.assertEqual(len(step), 1)
        self.assertEqual(address(step[0]['eip']), self.t['bpTarget'] + 1)  # after push ebp
        self.assertTrue(self.host.link.one('resume', hexint(step[0]['seq']), 'run')['ok'])
        self.assertTrue(self.wait_line('bpcall 12'))

    def test_to_runs_to_the_address_and_stops_there(self):
        self.set_bp(0, self.t['bpTarget'])
        stop = self.parked_bp_stop()
        self.assertTrue(self.host.link.one('resume', hexint(stop['seq']), 'to', hexint(self.t['bpAfter']))['ok'])
        reached = [s for s in self.wait_stops() if s['kind'] == 'step']
        self.assertEqual(len(reached), 1)
        self.assertEqual(address(reached[0]['eip']), self.t['bpAfter'])
        self.assertTrue(self.host.link.one('resume', hexint(reached[0]['seq']), 'run')['ok'])
        self.assertTrue(self.wait_line('bpcall 12'))

    def test_ret_resume_returns_the_given_value_from_the_function(self):
        self.set_bp(0, self.t['bpTarget'])
        stop = self.parked_bp_stop()
        self.assertTrue(self.host.link.one('resume', hexint(stop['seq']), 'ret', hexint(42), hexint(0))['ok'])
        self.assertTrue(self.wait_line('bpcall 42'))

    def test_hardware_breakpoint_parks_and_the_debug_registers_run_out(self):
        t = self.t
        self.set_bp(1, t['bpAfter'], kind='hw')
        stop = self.parked_bp_stop('bpcall 2')
        self.assertEqual((stop['kind'], stop['id']), ('bp', 1))
        self.assertEqual(address(stop['eip']), t['bpAfter'])
        self.assertTrue(self.host.link.one('resume', hexint(stop['seq']), 'run')['ok'])
        self.assertTrue(self.wait_line('bpcall 12'))
        self.set_bp(2, t['bpTarget'], kind='hw')
        self.set_bp(3, t['bpCall'], kind='hw')
        self.set_bp(4, t['bpRet'], kind='hw')
        full = self.host.link.one('bp', 'set', hexint(5), hexint(t['bpTarget'] + 1), 'hw', 'ff', hexint(0))
        self.assertEqual(full.get('error'), 'no_debug_register')
        taken = self.host.link.one('dbg', 'set', '0', hexint(t['bpTarget']), '1', '1', '0')
        self.assertEqual(taken.get('error'), 'no_debug_register')

    def test_predicate_that_fails_counts_hits_without_stopping(self):
        self.assertTrue(self.host.link.one('pred', 'set', '5', PRED_FALSE).get('ok'))
        self.set_bp(2, self.t['bpAfter'], pred='5')
        self.host.link.schedule(0, 'bpcall 3')
        self.assertTrue(self.wait_line('bpcall 12'))
        self.assertEqual(self.host.link.one('stops')['stops'], [])
        listed = {b['id']: b for b in self.host.link.one('bp', 'list')['bps']}
        self.assertEqual((listed[2]['hits'], listed[2]['pred']), (1, 5))

    def test_after_stops_only_from_the_nth_passing_hit(self):
        self.assertEqual(self.host.link.one('bp', 'after', hexint(0), hexint(3))['after'], 3)
        self.set_bp(0, self.t['bpTarget'])
        for _ in range(2):
            self.host.link.schedule(0, 'bpcall 3')
            self.assertTrue(self.wait_line('bpcall 12'))
        self.assertEqual(self.host.link.one('stops')['stops'], [])
        stop = self.parked_bp_stop()
        self.assertEqual((stop['kind'], stop['id']), ('bp', 0))
        listed = {b['id']: b for b in self.host.link.one('bp', 'list')['bps']}
        self.assertEqual((listed[0]['hits'], listed[0]['passed'], listed[0]['after']), (3, 3, 3))
        self.assertTrue(self.host.link.one('resume', hexint(stop['seq']), 'run')['ok'])
        self.assertTrue(self.wait_line('bpcall 12'))
        pending = self.host.link.one('bp', 'after', hexint(9), hexint(2))
        self.assertTrue(pending['ok'] and pending['pending'], pending)
        self.set_bp(9, self.t['bpTarget'])                     # takes the pending count
        listed = {b['id']: b for b in self.host.link.one('bp', 'list')['bps']}
        self.assertEqual((listed[9]['after'], listed[9]['passed']), (2, 0))

    def test_a_stop_during_a_step_ends_the_step_request_and_the_pipe_stays_usable(self):
        # Seen live: a breakpoint hit while fnv_wait_for ran frames left the request open forever and the one pipe
        # instance busy, so the resume could never be sent.
        self.set_bp(0, self.t['bpTarget'])
        self.host.link.schedule(300, 'bpcall 3')
        stepped = self.host.link.one('step', hexint(5000), timeout=30)
        self.assertEqual(stepped.get('error'), 'stopped', stepped)
        stops = self.wait_stops()
        self.assertEqual(len(stops), 1, stops)
        self.assertTrue(self.host.link.one('resume', hexint(stops[0]['seq']), 'run')['ok'])
        self.assertTrue(self.wait_line('bpcall 12'))

    def test_log_flag_records_the_values_without_stopping(self):
        self.set_bp(6,self.t['bpTarget'], flags=1, log=LOG_SEVEN)
        self.host.link.schedule(0, 'bpcall 3')
        self.assertTrue(self.wait_line('bpcall 12'))
        self.assertEqual(self.host.link.one('stops')['stops'], [])
        log = self.host.link.one('bp', 'log', hexint(6), hexint(0))
        self.assertTrue(log['ok'])
        self.assertEqual(len(log['entries']), 1)
        self.assertEqual(address(log['entries'][0]['eip']), self.t['bpTarget'])
        self.assertEqual(log['entries'][0]['values'], ['0x00000007'])
        self.assertEqual(log['dropped'], 0)

    def test_trace_records_the_body_and_steps_over_the_call(self):
        t = self.t
        self.set_bp(0, t['bpTarget'])
        stop = self.parked_bp_stop()
        started = self.host.link.one('resume', hexint(stop['seq']), 'trace', hexint(100), hexint(t['bpTarget']),
                                     hexint(t['bpTarget'] + 16))
        self.assertTrue(started['ok'], started)
        ended = [s for s in self.wait_stops() if s['kind'] == 'step']
        self.assertEqual(len(ended), 1)
        trace = self.host.link.one('trace', 'read', hexint(ended[0]['seq']), hexint(0), hexint(64))
        self.assertTrue(trace['ok'], trace)
        self.assertTrue(trace['done'])
        self.assertEqual(trace['reason'], 'returned')
        self.assertEqual([address(e['eip']) for e in trace['entries']],
                         [t['bpTarget'] + off for off in TRACE_OFFSETS])
        self.assertEqual([e['n'] for e in trace['entries']], list(range(1, len(TRACE_OFFSETS) + 1)))
        self.assertTrue(self.host.link.one('resume', hexint(ended[0]['seq']), 'run')['ok'])
        self.assertTrue(self.wait_line('bpcall 12'))

    def test_refusals(self):
        t = self.t
        self.assertEqual(self.host.link.one('bp', 'set', '1', '0', 'int3', 'ff', '0').get('error'), 'unreadable')
        self.assertEqual(self.host.link.one('bp', 'set', '1', hexint(t['bpTarget']), 'soft', 'ff', '0').get('error'),
                         'bad_arguments')
        self.assertEqual(self.host.link.one('bp', 'clear', hexint(63)).get('error'), 'bad_id')
        self.assertEqual(self.host.link.one('bp', 'clear', hexint(99)).get('error'), 'bad_arguments')
        self.assertEqual(self.host.link.one('bp', 'frob').get('error'), 'bad_arguments')
        self.assertEqual(self.host.link.one('trace', 'read', hexint(99999), hexint(0)).get('error'), 'no_trace')
        self.assertEqual(self.host.link.one('resume', hexint(99999), 'to', hexint(t['bpAfter'])).get('error'),
                         'no_stop')
        self.assertEqual(self.host.link.one('resume', 'ff', 'step').get('error'), 'bad_mode')


if __name__ == '__main__':
    unittest.main()
