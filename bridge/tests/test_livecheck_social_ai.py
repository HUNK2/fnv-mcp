"""Live-check fixes (2026-11-18): dialogue tree goodbye flag and negated speaker conditions, dialogue_choose numbering,
flee pathing goal, quest objective without targets, fnv_health object count per mark and cell change."""
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import struct
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp import fnv_social  # noqa: E402
from geck_mcp.fnv_social import SocialTools  # noqa: E402


class TreeLive:
    """Just enough live for SocialTools.dialogue_tree: a topic with one quest and two responses."""

    def __init__(self, info_raws):
        self.info_raws = info_raws          # info ptr -> 0x50 bytes
        self.link = SimpleNamespace(batch=lambda ops: [{'ok': True, 'eax': '1'} for _ in ops])

    def in_game(self):
        pass

    def resolve(self, ref):
        return {'ptr': {'npc': 0x100, 'player': 0x200, 'topic': 0x300}[ref], 'formId': ref}

    def form_ids(self, ptrs):
        return [{'formId': '%08X' % p, 'formType': 0x45} if p else None for p in ptrs]

    def u32s(self, addrs):
        return [0 for _ in addrs]

    def walk_list(self, head, limit=500):
        return [0x400]

    def names(self, ids):
        return {}

    def reads(self, spec):
        out = []
        for addr, n in spec:
            if addr == 0x400:      # quest info: quest ptr, base, _, size
                out.append(struct.pack('<IIIIII', 0x500, 0, 0x600, 0, len(self.info_raws), 0) + bytes(4))
            elif addr == 0x600:
                out.append(struct.pack('<%dI' % len(self.info_raws), *self.info_raws))
            elif addr == 0x500 + 0x3C:
                out.append(bytes([1, 50]))
            else:
                out.append(self.info_raws[addr] if addr in self.info_raws else bytes(n))
        return out


def info_raw(goodbye_flag, said=False):
    raw = bytearray(0x50)
    raw[0x25] = 0x01 if goodbye_flag else 0
    raw[0x22] = 1 if said else 0
    return bytes(raw)


def item(ptr, name_id, value, comparison='=='):
    return {'ptr': ptr, 'runOn': 0, 'function': name_id, 'value': value, 'comparison': comparison, 'or': False}


class DialogueTreeTests(unittest.TestCase):
    def build(self, lists):
        infos = {0x701: info_raw(True, said=False), 0x702: info_raw(False, said=True)}
        tools = SocialTools(TreeLive(infos))
        tools.scratch = lambda: 0x1000
        tools.commands = lambda: {0x1000 + 1: {'name': 'GetIsID'}, 0x1000 + 2: {'name': 'GetIsVoiceType'}}
        tools._texts = lambda ptrs, limit=400: [None for _ in ptrs]
        tools.read_condition_lists = lambda addrs: [lists[a - 0x18] for a in addrs]
        tools.read_conditions = lambda addr: lists[addr - 0x18]

        def evaluate(jobs, describe=True):
            return [{'conditions': [{'passed': True, 'function': 'f'} for _ in items], 'result': True}
                    for items, _, _ in jobs]
        tools.evaluate_jobs = evaluate
        return tools

    def test_goodbye_comes_from_the_flag_and_the_said_mark_has_its_own_name(self):
        tools = self.build({0x701: [], 0x702: []})
        out = tools.dialogue_tree('npc', topic='topic', scope='all')
        first, second = out['responses']
        self.assertIn('goodbye', first['flags'])
        self.assertTrue(first['goodbye'])
        self.assertFalse(first['alreadySaid'])
        self.assertNotIn('goodbye', second['flags'])
        self.assertFalse(second['goodbye'])
        self.assertTrue(second['alreadySaid'])

    def test_a_negated_speaker_condition_is_not_written_for_the_speaker(self):
        negative = item(1, 2, 0)                    # GetIsVoiceType X == 0
        not_one = item(2, 2, 1, '!=')               # GetIsVoiceType X != 1
        positive = item(3, 1, 1)                    # GetIsID X == 1
        tools = self.build({0x701: [negative], 0x702: [positive]})
        out = tools.dialogue_tree('npc', topic='topic', scope='speaker')
        by_info = {r['info']: r for r in out['responses']}
        self.assertFalse(by_info['00000701']['writtenForSpeaker'])
        self.assertTrue(by_info['00000702']['writtenForSpeaker'])
        self.assertFalse(fnv_social._positive(negative))
        self.assertFalse(fnv_social._positive(not_one))
        self.assertTrue(fnv_social._positive(positive))


class ChooseNumberingTests(unittest.TestCase):
    def test_menu_state_options_count_from_zero_skipping_the_spoken_node(self):
        class Live:
            def __init__(self):
                menu = bytearray(0x108)
                struct.pack_into('<II', menu, 0x28, 2, 0)
                struct.pack_into('<I', menu, 0x70, 0x900)
                self.menu = bytes(menu)

            def reads(self, spec):
                return [self.menu if a == 0x1000 else (bytes(0x2C) if a in (0x4000, 0x4100) else bytes(n))
                        for a, n in spec]

            def u32s(self, addrs):
                return [0x3000 if a == 0x904 else 0x1000 for a in addrs]

            def walk_list(self, head, limit=500):
                return [0x4000, 0x4100]

            def form_ids(self, ptrs):
                return [None for _ in ptrs]
        tools = SocialTools(Live())
        tools._menu = lambda: 0x1000
        tools._texts = lambda ptrs, limit=400: ['spoken', 'first']
        tools._listbox = lambda menu: []
        options = tools.menu_state()['options']
        self.assertEqual([o['index'] for o in options], [0])
        self.assertEqual(options[0]['prompt'], 'first')


class PathingTests(unittest.TestCase):
    def test_a_flee_goal_is_null_with_a_note(self):
        from geck_mcp.fnv_engine_ai import EngineAITools
        flt_max = 3.4028234663852886e+38
        req = bytearray(0xB0)
        struct.pack_into('<3f', req, 0x0C + 4, 1.0, 2.0, 3.0)
        struct.pack_into('<3f', req, 0x34 + 4, flt_max, flt_max, flt_max)
        mover = bytearray(0x88)
        struct.pack_into('<III', mover, 0x1C, 0x8000, 0, 0)

        class Live:
            def in_game(self):
                pass

            def u32s(self, addrs):
                return [0x7000 if a == 0x190 + 0x100 else 0 for a in addrs]

            def reads(self, spec):
                return [bytes(mover) if a == 0x7000 else (bytes(req) if a == 0x8000 else bytes(n)) for a, n in spec]

            def rtti(self, ptrs):
                return {p: {'class': 'FleeRequest'} for p in ptrs}
        tools = object.__new__(EngineAITools)
        tools.agent = SimpleNamespace(live=Live())
        tools._actor = lambda a: {'ptr': 0x100, 'formId': '0000AAAA'}
        tools._names = lambda ptrs: (lambda x: None)
        try:
            out = tools.pathing('x')
        except Exception as e:      # the layout reads further; this test only needs the request block
            self.fail('pathing raised %r' % e)
        dest = out['request']['destination']
        self.assertIsNone(dest['position'])
        self.assertIn('flee', dest['positionNote'])
        self.assertEqual(out['request']['origin']['position'], {'x': 1.0, 'y': 2.0, 'z': 3.0})
        self.assertFalse(any('goal has no navmesh' in w for w in out.get('why', [])))


class QuestTargetsTests(unittest.TestCase):
    def test_an_objective_without_targets_says_why_it_has_no_marker(self):
        from geck_mcp.fnv_agent import AgentTools
        qraw = bytearray(0x64)
        qraw[0x3C] = 1
        oraw = bytearray(0x24)
        struct.pack_into('<II', oraw, 4, 10, 0)
        struct.pack_into('<I', oraw, 0x20, 1)

        class Live:
            def in_game(self):
                pass

            def resolve(self, ref):
                return {'ptr': {'q': 0x100, 'player': 0x200}[ref if ref in ('q', 'player') else 'q'], 'formId': '0000ABCD'}

            def form_ids(self, ptrs):
                return [{'formType': 0x47, 'formId': '0000ABCD'} for _ in ptrs]

            def reads(self, spec):
                out = []
                for a, n in spec:
                    out.append(bytes(qraw) if a == 0x100 else (bytes(oraw) if a == 0x900 else bytes(n)))
                return out

            def walk_list(self, head, limit=500):
                return [0x900] if head == 0x100 + 0x4C else []
        tools = object.__new__(AgentTools)
        tools.live = Live()
        tools.social = SimpleNamespace(_texts=lambda ptrs: ['Find them'])
        out = tools.quest_targets('q')
        obj = out['objectives'][0]
        self.assertEqual(obj['targets'], [])
        self.assertIn('no targets', obj['whyNoMarker'])


class HealthObjectsTests(unittest.TestCase):
    def setUp(self):
        from w1_support import W1Live
        from geck_mcp.fnv_scenario import ScenarioRunner
        self.live = W1Live()
        mark = ScenarioRunner(self.live).mark()
        self.live.session_state = {'start': 'continue', 'since': time.time(), 'deterministic': None,
                                   'baseline': {'realFps': None, 'refs': 100, 'cell': 'A'}, 'journal': mark['journal'],
                                   'mark': mark, 'marks': [{'label': 'session start', **mark}]}
        for target, kwargs in (('geck_mcp.fnv_session.warnings_for_live', {}), ('geck_mcp.fnv_deep.context_files',
                                                                                  {'return_value': []})):
            patcher = mock.patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        wf = mock.patch('geck_mcp.fnv_session.warnings_for_live')
        self.warn = wf.start()
        self.addCleanup(wf.stop)
        self.warn.return_value.summary.return_value = {'byCategory': {}}

    def check(self, refs, cell):
        from geck_mcp.fnv_session import HealthTools
        with mock.patch('geck_mcp.fnv_session._refs', return_value=refs),                 mock.patch('geck_mcp.fnv_session._cell', return_value=cell):
            return HealthTools(self.live).check()

    def test_a_rise_in_the_same_cell_is_reported_since_the_session_start(self):
        out = self.check(5000, 'A')
        self.assertFalse(out['ok'])
        self.assertIn('since the session start', out['items'][0]['what'])

    def test_a_rise_after_a_cell_change_is_not_reported(self):
        self.assertTrue(self.check(5000, 'B')['ok'])

    def test_the_baseline_is_taken_at_each_mark(self):
        from geck_mcp.fnv_session import SessionTools
        with mock.patch('geck_mcp.fnv_session._refs', return_value=4000),                 mock.patch('geck_mcp.fnv_session._cell', return_value='A'):
            SessionTools(self.live).mark('before-undo')
        self.assertTrue(self.check(4500, 'A')['ok'])        # 500 above the mark, under the threshold
        out = self.check(6000, 'A')
        self.assertIn('since before-undo', out['items'][0]['what'])


if __name__ == '__main__':
    unittest.main()
