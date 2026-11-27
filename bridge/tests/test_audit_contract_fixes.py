"""Open audit bugs in the tool contract area: Y3 (#33: read actions that hook the engine for the call), Y5 (#35:
EngineAITools._hook removed a same-label hook of another call), Y6 (#36: fnv_window persist and fnv_warnings enable
wrote files under read) and W8 (#20: fnv_crawl cells moved the player with an unjournaled cow)."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp import Client  # noqa: E402

from fnv_mcp import fnv_engine_ai  # noqa: E402
from fnv_mcp.fnv_crawl import CrawlTools  # noqa: E402
from fnv_mcp.fnv_live_tools import FNV_LIVE_TOOLS, gated_kind, needs_full  # noqa: E402
from fnv_mcp.server import make_server  # noqa: E402
from test_stab_gate import GateFixture  # noqa: E402


class GateTests(GateFixture, unittest.IsolatedAsyncioTestCase):
    """Under engine_control = "read" every call that hooks the engine or writes a file outside the state folder is
    refused before any pipe op; the plain reads of the same tools are not."""

    REFUSED = [
        ('fnv_detection', {'observer': 'player', 'recompute': True}),        # Y3
        ('fnv_form', {'action': 'bytes', 'form': 'player'}),                  # Y3
        ('fnv_anim', {'action': 'events', 'ref': 'player'}),                  # Y3 (bug hunt 2026-11-18)
        ('fnv_audio', {'action': 'log'}),
        ('fnv_audio', {'action': 'subtitles'}),
        ('fnv_physics', {'action': 'triggers'}),
        ('fnv_window', {'action': 'status', 'persist': True}),               # Y6
        ('fnv_window', {'action': 'status', 'persist': False}),
        ('fnv_warnings', {'action': 'enable'}),                               # Y6
    ]
    ALLOWED = [
        ('fnv_detection', {'observer': 'player'}),
        ('fnv_form', {'action': 'changes'}),
        ('fnv_window', {'action': 'status'}),
        ('fnv_warnings', {'action': 'status'}),
    ]

    async def test_hooking_and_file_writing_calls_are_refused_when_read(self):
        wrong = []
        async with Client(make_server(self.project)) as client:
            for tool, args in self.REFUSED:
                self.pipe_ops.clear()
                code, _ = await self.call(client, tool, args)
                if code != 'CONTROL_DISABLED' or self.pipe_ops:
                    wrong.append((tool, args, code, len(self.pipe_ops)))
        self.assertEqual(wrong, [])

    async def test_their_reads_are_not_refused(self):
        wrong = []
        async with Client(make_server(self.project)) as client:
            for tool, args in self.ALLOWED:
                code, _ = await self.call(client, tool, args)
                if code == 'CONTROL_DISABLED':
                    wrong.append((tool, args))
        self.assertEqual(wrong, [])

    def test_tables(self):
        self.assertEqual(gated_kind('fnv_detection', 'recompute', 'read'), 'act')
        self.assertEqual(gated_kind('fnv_detection', 'read', 'read'), 'read')
        self.assertNotEqual(FNV_LIVE_TOOLS['fnv_detection'][0], 'read')
        self.assertTrue(needs_full('fnv_window', 'persist'))
        self.assertFalse(needs_full('fnv_window', 'status'))
        self.assertTrue(needs_full('fnv_warnings', 'enable'))
        self.assertFalse(needs_full('fnv_warnings', 'read'))


def engine_ai(hooks, ops):
    removed = []
    deep = SimpleNamespace(hooks=lambda: list(hooks), remove=lambda hid: removed.append(hid),
                           records=lambda h, since, limit=64: (0, [], None))

    def one(*args, **kw):
        ops.append(args)
        if args[0] == 'call':
            return {'ok': True, 'eax': '0x00000003'}
        return {'ok': True, 'hook': {'id': 9, 'seqAddress': '0x10'}}
    link = SimpleNamespace(one=one, ping=lambda: {'mainThreadId': 1})
    live = SimpleNamespace(reads=lambda spans: [b'\x55\x8b\xec\x83\xec\x10' + b'\x90' * 18 for _ in spans],
                           u32s=lambda addrs: [0 for _ in addrs])
    ai = fnv_engine_ai.EngineAITools(SimpleNamespace(live=live, link=link, deep=deep, _buffer=lambda: 0x2000))
    return ai, removed


class HookTests(unittest.TestCase):
    def test_y5_a_same_label_hook_is_not_removed(self):
        ops = []
        held = {'id': 4, 'target': '0x%08X' % fnv_engine_ai.DETECTION_FORMULA, 'label': 'fnvmcp_detection'}
        ai, removed = engine_ai([held], ops)
        with self.assertRaises(fnv_engine_ai.HookError) as cm:
            ai._hook(fnv_engine_ai.DETECTION_FORMULA, 'fnvmcp_detection', 8)
        self.assertEqual(cm.exception.code, 'BUSY')
        self.assertEqual(removed, [], "the other call's hook stays")
        self.assertEqual([op for op in ops if op[0] == 'hook'], [])

    def test_y3_detection_level_is_called_guarded(self):
        ops = []
        ai, removed = engine_ai([], ops)
        ai._hook = lambda *a, **kw: {'id': 9, 'seqAddress': '0x10'}
        ai._seq = lambda h: 0
        out = ai._recompute(0x1000, 0x2000)
        calls = [op for op in ops if op[0] == 'call']
        self.assertEqual(calls[0][1:3], ('%x' % fnv_engine_ai.CALC_DETECTION, 'ig'))
        self.assertEqual(out['level'], 3)


class CrawlJournalTests(unittest.TestCase):
    def test_w8_an_exterior_cell_visit_is_journaled_first(self):
        order = []
        before = {'position': {'x': 1.0, 'y': 2.0, 'z': 3.0}, 'heading': 90.0, 'cell': '0000ABCD',
                  'interior': True, 'worldspace': None}
        live = SimpleNamespace(
            resolve=lambda spec: {'formId': '00000014', 'ptr': 0x14},
            changing=lambda reason: order.append(('backup', reason)),
            journal=SimpleNamespace(add=lambda entry: order.append(('journal', entry)) or {'id': 1}),
            console=lambda line, ref=0: order.append(('console', line)) or {'accepted': True})
        agent = SimpleNamespace(_place=lambda r: dict(before),
                                _undo_place=lambda b: ['MoveToCell 0000ABCD 1.0 2.0 3.0', 'SetAngle Z 90.0'])
        crawl = CrawlTools(live, agent=agent, sleep=lambda s: None)
        crawl._edid = lambda form_id: 'WastelandNV'
        crawl._wait_loaded = lambda: None
        crawl._go({'interior': False, 'editorId': None, 'grid': (3, -4), 'world': 0x000DA726, 'formId': 0x01000ABC})
        self.assertEqual([k for k, _ in order], ['backup', 'journal', 'console'])
        entry = order[1][1]
        self.assertEqual(entry['kind'], 'move')
        self.assertEqual(entry['before'], before)
        self.assertEqual(entry['undo'], {'op': 'console_ref', 'ref': '00000014',
                                         'lines': ['MoveToCell 0000ABCD 1.0 2.0 3.0', 'SetAngle Z 90.0']})
        self.assertEqual(order[2][1], 'cow WastelandNV 3 -4')


if __name__ == '__main__':
    unittest.main()
