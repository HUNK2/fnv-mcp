"""Engine companion, wire version 2: hook holds, skip stubs, crash holds and the stop ops, over the real DLL and a fake
xNVSE host. Skipped when no Zig is found (GECK_MCP_ZIG, or fnv.zig in a projects/*.json file). Numbers on the wire are hex."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_link  # noqa: E402

PROLOGUE_LEN = 6  # hookme starts with 55 89 e5 83 ec 08: no relative jump, so no relocations


# The shared helpers live in engine_host; re-exported here so old imports keep working.
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/: engine_host
from engine_host import StopHost, StopTestCase, build, hexint, needs_zig, zig_path  # noqa: E402,F401


@needs_zig
class HookStopTests(StopTestCase):
    """Hook holds on the main thread (through the scheduler, so the call is not inside a served request) and on a
    worker thread. One host serves the whole class; each test leaves the hook disarmed and every stop released."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        receipt = build(cls.tmp.name)
        cls.host = StopHost(receipt, Path(receipt['dll']).parent, cls.tmp.name, 300)
        target = hexint(cls.host.t['hookme'])
        result = cls.host.link.one('hook', target, hexint(PROLOGUE_LEN), '-', hexint(0), hexint(0), hexint(0),
                                   hexint(0), hexint(1), '0', '0', hexint(0), hexint(0), fnv_link.hexs('hookme'))
        cls.hook_id = result['hook']['id']

    @classmethod
    def tearDownClass(cls):
        cls.host.close()
        cls.tmp.cleanup()

    def setUp(self):
        self.assertEqual(self.host.link.one('stops')['stops'], [])

    def tearDown(self):
        self.disarm(self.hook_id)
        for stop in self.host.link.one('stops')['stops']:
            self.host.link.one('resume', hexint(stop['seq']), 'run')

    def test_hold_parks_a_scheduled_call_until_resumed(self):
        self.hold(self.hook_id)
        self.host.link.schedule(0, 'hookme 5')
        stops = self.wait_stops()
        self.assertEqual(len(stops), 1)
        stop = stops[0]
        self.assertEqual((stop['kind'], stop['id'], stop['main']), ('hook', self.hook_id, True))
        ping = self.host.link.ping()
        self.assertEqual(ping['parked'], 1)
        self.assertTrue(ping['held'])
        self.assertEqual(self.host.link.one('resume', hexint(stop['seq']), 'run')['ok'], True)
        self.assertTrue(self.wait_line('hookme 1005'))
        self.assertEqual(self.host.link.ping()['parked'], 0)

    def test_skip_returns_the_given_value_without_running_the_function(self):
        self.hold(self.hook_id)
        self.host.link.schedule(0, 'hookme 5')
        stop = self.wait_stops()[0]
        self.assertTrue(self.host.link.one('resume', hexint(stop['seq']), 'skip', hexint(77))['ok'])
        self.assertTrue(self.wait_line('hookme 77'))

    def test_next_run_stops_the_next_call_once_more(self):
        self.hold(self.hook_id)
        self.host.link.schedule(0, 'hookme 1')
        first = self.wait_stops()[0]
        self.disarm(self.hook_id)
        self.assertTrue(self.host.link.one('resume', hexint(first['seq']), 'next', hexint(self.hook_id))['ok'])
        self.assertTrue(self.wait_line('hookme 1001'))
        self.host.link.schedule(0, 'hookme 2')
        second = self.wait_stops()[0]
        self.assertEqual((second['kind'], second['id']), ('hook', self.hook_id))
        self.assertTrue(self.host.link.one('resume', hexint(second['seq']), 'run')['ok'])
        self.assertTrue(self.wait_line('hookme 1002'))

    def test_worker_thread_call_is_parked_and_listed_as_not_main(self):
        self.hold(self.hook_id)
        self.host.link.console('hookthread 4')
        stops = self.wait_stops()
        self.assertEqual(len(stops), 1)
        self.assertFalse(stops[0]['main'])
        self.assertEqual(self.host.link.ping()['parked'], 1)  # one stop, on the worker thread
        self.assertTrue(self.host.link.one('resume', hexint(stops[0]['seq']), 'run')['ok'])
        self.assertTrue(self.wait_line('hookthread 1004'))

    def test_refusals_while_a_stop_is_parked(self):
        self.hold(self.hook_id)
        self.host.link.schedule(0, 'hookme 3')
        stop = self.wait_stops()[0]
        self.assertEqual(self.host.link.one('step', '1').get('error'), 'stopped')
        self.assertEqual(self.host.link.one('resume', hexint(99999), 'run').get('error'), 'no_stop')
        self.assertEqual(self.host.link.one('resume', hexint(stop['seq']), 'step').get('error'), 'bad_mode')
        self.assertEqual(self.host.link.one('resume', hexint(stop['seq']), 'skip', hexint(1), hexint(2)).get('error'),
                         'bad_arguments')
        self.assertEqual(self.host.link.one('hookact', hexint(9999), 'hold', 'ff').get('error'), 'unknown_hook')
        self.assertEqual(self.host.link.one('hookact', hexint(self.hook_id), 'hold', 'zz').get('error'),
                         'bad_arguments')
        self.assertTrue(self.host.link.one('resume', hexint(stop['seq']), 'run')['ok'])
        self.assertTrue(self.wait_line('hookme 1003'))

    def test_faulthold_reports_and_toggles(self):
        self.assertTrue(self.host.link.one('faulthold', 'on')['faultHold'])
        self.assertFalse(self.host.link.one('faulthold', 'off')['faultHold'])
        self.assertEqual(self.host.link.one('faulthold', 'maybe').get('error'), 'bad_arguments')


@needs_zig
class StopDeadManTests(StopTestCase):
    """A parked stop is released by the dead-man switch when no request or ping arrives for the gate timeout."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.receipt = build(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.tmp2 = tempfile.TemporaryDirectory()
        self.host = StopHost(self.receipt, Path(self.receipt['dll']).parent, self.tmp2.name, 300)
        result = self.host.link.one('hook', hexint(self.host.t['hookme']), hexint(PROLOGUE_LEN), '-', hexint(0),
                                    hexint(0), hexint(0), hexint(0), hexint(1), '0', '0', hexint(0), hexint(0),
                                    fnv_link.hexs('hookme'))
        self.hook_id = result['hook']['id']

    def tearDown(self):
        self.host.close()
        self.tmp2.cleanup()

    def test_silent_stop_is_released_by_the_dead_man_switch(self):
        self.hold(self.hook_id)
        self.host.link.schedule(0, 'hookme 3')
        self.assertEqual(len(self.wait_stops()), 1)
        self.assertTrue(self.host.link.one('hold', 'on', hexint(300))['held'])
        time.sleep(1.2)  # no request and no ping: the stop is released after the 300 ms timeout
        self.assertEqual(self.host.link.one('stops')['stops'], [])  # the request above is a contact, so check after sleep
        types = [e['type'] for e in self.host.link.events(0)['events']]
        self.assertIn('deadman', types)
        self.assertTrue(self.wait_line('hookme 1003'))


@needs_zig
class FaultStopTests(StopTestCase):
    """A crash on the main thread or a worker. The fake host ends the process with exit code 77 when a fault is not
    handled, so each test starts its own host."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.receipt = build(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def start(self):
        self.tmp2 = tempfile.TemporaryDirectory()
        self.host = StopHost(self.receipt, Path(self.receipt['dll']).parent, self.tmp2.name, 300)

    def resume_into_exit(self, seq):
        """The resumed fault ends the process, so the pipe may close before the reply arrives."""
        try:
            self.host.link.one('resume', hexint(seq), 'run')
        except fnv_link.LinkError:
            pass

    def finish(self):
        self.host.close()
        self.tmp2.cleanup()

    def test_unheld_crash_ends_the_process(self):
        self.start()
        try:
            self.assertEqual(self.host.link.one('faulthold', 'off')['faultHold'], False)
            self.host.link.schedule(0, 'crash')
            self.assertEqual(self.host.process.wait(timeout=10), 77)
        finally:
            self.finish()

    def test_held_main_thread_crash_is_listed_and_resume_ends_it(self):
        self.start()
        try:
            self.assertTrue(self.host.link.one('faulthold', 'on')['faultHold'])
            self.host.link.schedule(0, 'crash')
            stops = self.wait_stops()
            self.assertEqual(len(stops), 1)
            self.assertEqual((stops[0]['kind'], stops[0]['main']), ('fault', True))
            self.assertIn('exception', stops[0])
            self.resume_into_exit(stops[0]['seq'])
            self.assertEqual(self.host.process.wait(timeout=10), 77)
        finally:
            self.finish()

    def test_held_worker_crash_is_listed_as_not_main(self):
        self.start()
        try:
            self.assertTrue(self.host.link.one('faulthold', 'on')['faultHold'])
            self.host.link.console('crashthread')
            stops = self.wait_stops()
            self.assertEqual(len(stops), 1)
            self.assertEqual((stops[0]['kind'], stops[0]['main']), ('fault', False))
            self.resume_into_exit(stops[0]['seq'])
            self.assertEqual(self.host.process.wait(timeout=10), 77)
        finally:
            self.finish()


if __name__ == '__main__':
    unittest.main()
