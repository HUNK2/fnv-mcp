"""fnv_blame (W2): field and ref arguments, the form stub, the report of an empty ring and culprit grouping, against a
fake companion. Hit collection from a real ring needs a game; the grouping is tested here on stored hits."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from geck_mcp import fnv_blame, fnv_conv  # noqa: E402
from test_fnv_debug import FakeLink  # noqa: E402


def make_live(running=True):
    link = FakeLink([])
    deep = SimpleNamespace(hooks=lambda: [], records=lambda h, since=0, limit=4096: (0, [], 0))
    return SimpleNamespace(link=link, ready=lambda: None, deep=deep,
                           game=SimpleNamespace(process=lambda: (1234, 0) if running else None),
                           resolve=lambda ref: {'ptr': 0x30000000, 'formId': '00000014', 'expr': ref})


class BlameTests(unittest.TestCase):
    def tool(self, live=None):
        live = live or make_live()
        return fnv_blame.BlameTools(live, sleep=lambda s: None, clock=lambda: 0.0)

    def test_unknown_action(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.tool().run('jump')
        self.assertEqual(cm.exception.code, 'UNKNOWN_ACTION')

    def test_form_needs_a_ref(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.tool().run('form')
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')

    def test_field_refuses_a_bad_access(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.tool().field('0x011DEA0C', access='read')
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')

    def test_field_refuses_a_bad_length(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.tool().field('0x011DEA0C', length=0)
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')

    def test_field_needs_the_game_running(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.tool(make_live(running=False)).field('0x011DEA0C')
        self.assertEqual(cm.exception.code, 'NOT_RUNNING')

    def test_ref_refuses_an_unknown_preset(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.tool().ref('player', what='NoSuchPreset')
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')

    def test_ref_needs_a_reference(self):
        with self.assertRaises(fnv_conv.ConvError) as cm:
            self.tool().ref('')
        self.assertEqual(cm.exception.code, 'BAD_ARGUMENTS')

    def test_report_with_no_hits_says_what_to_do(self):
        out = self.tool().report()
        self.assertEqual(out['total'], 0)
        self.assertEqual(out['groups'], [])

    def test_report_groups_stored_hits_by_culprit(self):
        tool = self.tool()
        script_hit = {'what': 'field', 'thread': '1000', 'script': {'script': 'QuestScript', 'line': 12}}
        engine_hit = {'what': 'SetActorValue', 'thread': '1001', 'caller': {'function': 'Actor::ApplyDamage'}}
        tool.hits = [script_hit, script_hit, engine_hit]
        out = tool.report()
        self.assertEqual(out['total'], 3)
        self.assertEqual(out['groups'][0]['hits'], 2)
        self.assertIn('QuestScript', out['groups'][0]['culprit'])
        self.assertEqual(out['groups'][1]['culprit'], 'engine Actor::ApplyDamage')


if __name__ == '__main__':
    unittest.main()
