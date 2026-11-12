"""PLAN-3 S6 batch A: debugger and hooks (R5, R11, R13, G11, G13)."""
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp.fnv_conv import ConvError  # noqa: E402
from geck_mcp.fnv_flow import FlowTools  # noqa: E402


class _FlowLink:
    def __init__(self):
        self.log = []

    def one(self, *op, **kw):
        self.log.append(op)
        if op[:2] == ('bp', 'list'):
            return {'ok': True, 'bps': []}
        if op[:2] == ('bp', 'set'):
            return {'ok': True, 'kind': 'hw'}
        return {'ok': True}

    def write(self, *a):
        self.log.append(('write',) + a)


def _flow(signature='int __fastcall F(int a, int b)', exports=None, resolve=None):
    """The repro's fakes (research/stabilize/repro/flow/repro_flow_bp_left.py)."""
    link = _FlowLink()

    class Game:
        def process(self):
            return (1234, 5678)

    class Live:
        def __init__(self):
            self.link, self.game = link, Game()

        def ready(self):
            return {}

        def _signature(self, a):
            return (None, None, signature, None)

        def resolve(self, a):
            if resolve is None:
                raise AssertionError('no name to resolve here')
            return resolve(a)

    class DB:
        def function(self, a):
            return {'addr': 0x401000, 'name': 'F', 'size': 0x40}

        def lines_of(self, a):
            return [1]

        def locals_of(self, a):
            return []

    class Dbg:
        lock = threading.RLock()

        def _symdb(self, required=False):
            return DB()

        def _resolve(self, f):
            return (0x401000, 0x401000, None)

        def _free_bp_id(self):
            return 3

        def _set_bp(self, bp_id, addr, mech, pred, flags, log_hex):
            return link.one('bp', 'set', bp_id, addr, mech, pred, flags)

        def release_pred_slot(self, s):
            pass

        def _stops(self):
            return []

    class Native:
        loaded = {'fnvmcp_flowcall': {'exports': dict(exports or {'fnvmcp_flow_slot': '0x10000000'})}}

        def flow_slot(self):
            return 0x10000000

    return FlowTools(Live(), debugger=Dbg(), native=Native(), sleep=lambda s: None), link


class FlowCallSetupTests(unittest.TestCase):
    def assert_no_bp_left(self, link):
        sets = [op for op in link.log if op[:2] == ('bp', 'set')]
        clears = [op for op in link.log if op[:2] == ('bp', 'clear')]
        self.assertEqual(len(sets), len(clears), 'a breakpoint was left armed: %s' % link.log)

    def test_flow_call_setup_failure_leaves_no_bp(self):
        flow, link = _flow()                         # fastcall with a module built before version 2
        with self.assertRaises(ConvError) as caught:
            flow.trace('F', call=(None, [1, 2]))
        self.assertEqual(caught.exception.code, 'OLD_MODULE')
        self.assert_no_bp_left(link)
        self.assertFalse([op for op in link.log if op[:2] == ('bp', 'set')], 'checked before anything is armed')

    def test_too_many_words_refused_before_arming(self):
        flow, link = _flow(signature='int __cdecl F(void)')
        with self.assertRaises(ConvError) as caught:
            flow.trace('F', call=(None, list(range(13))))
        self.assertEqual(caught.exception.code, 'BAD_ARGUMENTS')
        self.assertFalse([op for op in link.log if op[:2] == ('bp', 'set')])

    def test_unknown_argument_name_refused_before_arming(self):
        def resolve(name):
            raise ConvError('REF_NOT_FOUND', 'no %s' % name)
        flow, link = _flow(signature='int __cdecl F(void)', resolve=resolve)
        with self.assertRaises(ConvError):
            flow.trace('F', call=(None, ['NoSuchRef']))
        self.assertFalse([op for op in link.log if op[:2] == ('bp', 'set')])

    def test_a_failure_after_arming_clears_the_bp_and_the_slot(self):
        flow, link = _flow(signature='int __cdecl F(void)')

        def boom(*a, **kw):
            raise RuntimeError('the link dropped')
        flow._callee_ranges = boom
        with self.assertRaises(RuntimeError):
            flow.trace('F', call=(None, [1]), depth=1)
        self.assert_no_bp_left(link)


if __name__ == '__main__':
    unittest.main()
