"""The bridge marks the requests of a read-only tool call, so the engine companion serves them in one game frame.

The companion takes one request per frame; after a request whose id starts with "r" it waits a moment for the next
one and serves it in the same frame (tests/test_engine_same_frame.py). Which requests get that mark is decided here:
only requests made while a read-only tool call runs, and only when every op in them reads. A request that may change
the game is never marked, so a frame still passes after it, as before."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_link, fnv_live  # noqa: E402

HEX = set('0123456789abcdef')


class RecordingLink(fnv_link.EngineLink):
    """An EngineLink with no pipe: keeps every request line and answers it as the companion would."""

    def __init__(self):
        super().__init__(pipe='unused')
        self.lines = []

    def _exchange(self, line, timeout):
        self.lines.append(line)
        request_id, *ops = line.split('\t')
        return {'id': request_id, 'frame': 7, 'results': [{'ok': True} for _ in ops]}

    def ids(self):
        return [line.split('\t')[0] for line in self.lines]


def marked(request_id):
    return request_id.startswith('r')


class MarkingTests(unittest.TestCase):

    def setUp(self):
        self.link = RecordingLink()

    def test_a_request_outside_a_read_call_is_not_marked(self):
        self.link.batch([('rd', '401000', '4')])
        self.link.one('eval', '00', '0')
        self.assertEqual([marked(i) for i in self.link.ids()], [False, False])

    def test_reads_inside_a_read_call_are_marked(self):
        with fnv_link.read_call():
            self.link.batch([('rd', '401000', '4'), ('rs', '401000', '80')])
        (request_id,) = self.link.ids()
        self.assertTrue(marked(request_id))
        self.assertEqual(len(request_id), 12)                  # as long as before: the companion keeps 64 characters
        self.assertTrue(set(request_id[1:]) <= HEX)

    def test_every_reading_op_can_be_marked(self):
        with fnv_link.read_call():
            for op in ('rd', 'rs', 'rp', 'ls', 'info', 'eval', 'call'):
                self.link.batch([(op, '0')])
        self.assertEqual([marked(i) for i in self.link.ids()], [True] * 7)

    def test_a_request_with_any_other_op_is_never_marked(self):
        """wr, con and run change the game; hold, step and until are the frame gate; the rest installs or sets
        something. One such op in a request and the whole request keeps its own frame."""
        others = ('wr', 'con', 'run', 'sched', 'every', 'compile', 'events', 'hook', 'unhook', 'hookctl', 'hooks',
                  'hookfilter', 'hookact', 'probe', 'dbg', 'pred', 'gatecfg', 'hold', 'step', 'until', 'gate', 'samp',
                  'sink', 'inpcfg', 'inp', 'stops', 'resume', 'faulthold', 'bp', 'trace', 'mod', 'hooknative', 'code',
                  'cov', 'linger', 'new_op_of_tomorrow')
        with fnv_link.read_call():
            for op in others:
                self.link.batch([(op, '0')])
                self.link.batch([('rd', '401000', '4'), (op, '0'), ('rd', '401000', '4')])
        self.assertEqual([i for i in self.link.ids() if marked(i)], [])

    def test_the_mark_ends_with_the_read_call(self):
        with fnv_link.read_call():
            with fnv_link.read_call():
                self.link.batch([('rd', '0', '4')])
            self.link.batch([('rd', '0', '4')])                # the inner call ended, the outer one still runs
        self.link.batch([('rd', '0', '4')])
        self.assertEqual([marked(i) for i in self.link.ids()], [True, True, False])

    def test_the_mark_ends_when_the_read_call_fails(self):
        with self.assertRaises(RuntimeError):
            with fnv_link.read_call():
                raise RuntimeError('tool failed')
        self.link.batch([('rd', '0', '4')])
        self.assertEqual([marked(i) for i in self.link.ids()], [False])

    def test_the_mark_belongs_to_the_thread_of_the_read_call(self):
        """A sampler or the supervisor sends its own requests from its own thread while a read tool runs: those
        are not part of the read."""
        with fnv_link.read_call():
            other = threading.Thread(target=lambda: self.link.batch([('rd', '0', '4')]))
            other.start()
            other.join()
            self.link.batch([('rd', '0', '4')])
        self.assertEqual([marked(i) for i in self.link.ids()], [False, True])

    def test_a_marked_reply_is_matched_by_its_whole_id(self):
        self.link._exchange = lambda line, timeout: {'id': line.split('\t')[0][1:], 'frame': 1, 'results': [{'ok': True}]}
        self.link.close = lambda: None
        with fnv_link.read_call():
            with self.assertRaises(fnv_link.LinkError) as caught:
                self.link.batch([('rd', '0', '4')])
        self.assertEqual(caught.exception.code, 'COMPANION_PROTOCOL')


class SwitchTests(unittest.TestCase):
    """FNV_MCP_SAME_FRAME=0: the bridge marks nothing and every request waits for its own frame, as before."""

    def ids_in_a_read_call(self, value):
        env = {k: v for k, v in os.environ.items() if k != 'FNV_MCP_SAME_FRAME'}
        if value is not None:
            env['FNV_MCP_SAME_FRAME'] = value
        with mock.patch.dict(os.environ, env, clear=True):
            link = RecordingLink()
            with fnv_link.read_call():
                link.batch([('rd', '0', '4')])
            return [marked(i) for i in link.ids()]

    def test_on_unless_set_to_zero(self):
        self.assertEqual(self.ids_in_a_read_call(None), [True])
        self.assertEqual(self.ids_in_a_read_call('1'), [True])
        self.assertEqual(self.ids_in_a_read_call(''), [True])

    def test_zero_turns_it_off(self):
        self.assertEqual(self.ids_in_a_read_call('0'), [False])


class ToolCallTests(unittest.TestCase):
    """LiveService.run is where a tool call's kind is known: a read call marks its requests, an act call never."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.link = RecordingLink()
        project = SimpleNamespace(state_dir=self.tmp.name, project_id='p', fnv=SimpleNamespace(engine_control='full'))
        self.live = fnv_live.LiveService(project)
        self.live._game = SimpleNamespace(link=self.link)
        self.live.identity = lambda: None

    def call(self, kind):
        return self.live.run('fnv_test_tool', lambda: self.link.batch([('rd', '0', '4')]), kind)

    def test_a_read_call_marks_its_requests(self):
        self.assertTrue(self.call('read')['ok'])
        self.assertEqual([marked(i) for i in self.link.ids()], [True])

    def test_an_act_call_does_not(self):
        self.assertTrue(self.call('act')['ok'])
        self.assertEqual([marked(i) for i in self.link.ids()], [False])

    def test_a_control_call_does_not(self):
        self.assertTrue(self.call('control')['ok'])
        self.assertEqual([marked(i) for i in self.link.ids()], [False])

    def test_no_mark_is_left_after_a_read_call(self):
        self.call('read')
        self.link.batch([('rd', '0', '4')])
        self.assertEqual([marked(i) for i in self.link.ids()], [True, False])

    def test_no_mark_is_left_after_a_failing_read_call(self):
        def fails():
            raise ValueError('bad argument')
        self.assertFalse(self.live.run('fnv_test_tool', fails, 'read')['ok'])
        self.link.batch([('rd', '0', '4')])
        self.assertEqual([marked(i) for i in self.link.ids()], [False])


if __name__ == '__main__':
    unittest.main()
