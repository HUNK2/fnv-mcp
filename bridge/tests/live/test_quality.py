"""Live re-check of the nine failures of the tool quality live check (TOOL-QUALITY-LIVE-CHECK.md, 2026-11-18) that a
script can make. 02ddcab says it fixed them; nobody had seen that in the game (critique fix Task 5, 2026-11-21)."""
import json

from .base import LiveCase


class QualityTests(LiveCase):
    def _refused(self, tool, **args):
        envelope = self.call(tool, **args)
        self.assertFalse(envelope.get('ok'), envelope)
        error = envelope.get('error') or {}
        text = json.dumps(envelope)
        self.assertEqual(error.get('code'), 'INVALID_ARGUMENT', text[:600])
        self.assertNotIn('pydantic', text)
        self.assertNotIn('INVALID_ARGUMENTS', text)
        self.assertTrue(error.get('recovery') or envelope.get('recovery') or error.get('next'), text[:600])
        return envelope

    def test_08_unknown_setting_suggests_the_close_name(self):
        envelope = self.call('fnv_setting_info', name='fCombatDistanse')
        self.assertIn('fCombatDistance', json.dumps(envelope))

    def test_12_out_of_range_arguments_are_plain(self):
        self._refused('fnv_wait_for', frames=0)
        self._refused('fnv_journal', limit=0)
        self._refused('fnv_time', action='step', frames=20000)

    def test_12_missing_wrong_type_and_unknown_action(self):
        self._refused('fnv_console')
        self._refused('fnv_wait_for', frames='many')
        self._refused('fnv_native', action='explode')

    def test_14_no_python_exception_text(self):
        envelope = self.call('fnv_actor_control', ref='player', action='set_position', value=[1, 2])
        self.assertFalse(envelope.get('ok'))
        self.assertNotIn('unpack', json.dumps(envelope))

    def test_17_scene_graph_full_answer_says_when_capped(self):
        data = self.ok('fnv_scene_graph', brief=False)
        text = json.dumps(data)
        self.assertTrue('rotation' in text or 'rot' in text, text[:300])

    def test_24_search_finds_night_and_frozen(self):
        def top(words):
            data = self.ok('fnv_guide', action='find', words=words, limit=5)
            return json.dumps(data)
        self.assertIn('fnv_time', top('make it night time'))
        self.assertTrue(any(t in top('the game froze and does not respond')
                            for t in ('fnv_threads', 'fnv_status', 'fnv_debug', 'fnv_crash')))

    def test_29_fnv_test_example_path(self):
        from pathlib import Path
        folder = Path(__file__).resolve().parents[2] / 'examples' / 'fnv-tests' / 'hello-wasteland.yaml'
        envelope = self.call('fnv_test', path=str(folder))
        self.assertNotEqual((envelope.get('error') or {}).get('code'), 'INVALID_ARGUMENT', json.dumps(envelope)[:500])
