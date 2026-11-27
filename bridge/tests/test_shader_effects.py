"""fnv_shader screen effects (SHADER-PLAN Task 4): add / set / list / remove through a mock native module, the saved
manifest, the journal entries and the three undo ops. No game, no pipe; state goes to a temp dir."""
from pathlib import Path
from unittest import mock
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_live import LiveError, UNDO_OPS  # noqa: E402
from fnv_mcp.fnv_shader import ShaderTools, MODULE  # noqa: E402

INVERT = '''sampler2D scene : register(s0);
float4 main(float2 uv : TEXCOORD0) : COLOR0 { float4 c = tex2D(scene, uv); return float4(1 - c.rgb, c.a); }
'''
TINTED = '''sampler2D scene : register(s0);\nfloat4 tint : register(c2);
float4 main(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(scene, uv) * tint; }
'''
GAME = {'pid': 4242, 'started': '1000'}
OTHER = {'pid': 999, 'started': '5'}


class FakeNative:
    """NativeTools stand-in: records every module call; slots exist after sfx_effect_set until sfx_effect_free."""

    def __init__(self, loaded=False):
        self.loaded = loaded
        self.calls = []
        self.bundled_calls = []
        self.slots = {}
        self.bundled_error = None
        self.log_lines = []

    def is_loaded(self, key):
        return self.loaded

    def bundled(self, key):
        self.bundled_calls.append(key)
        if self.bundled_error:
            raise self.bundled_error
        self.loaded = True
        return True

    def log(self, since=0):
        return {'lines': self.log_lines}

    def call(self, name, export, args=None):
        args = list(args or [])
        self.calls.append((export, args))
        value = 0
        if export == 'sfx_buffer':
            value = 0x5000
        elif export == 'sfx_effect_set':
            self.slots[args[0]] = args[3]
        elif export == 'sfx_effect_free':
            self.slots.pop(args[0], None)
        elif export == 'sfx_effect_state':
            value = (1 | 2 | (self.slots[args[0]] << 8)) if args[0] in self.slots else 0
        elif export == 'sfx_info':
            value = 1
        elif export == 'sfx_cost_us':
            value = 120
        return {'value': value}

    def exports(self):
        return [c[0] for c in self.calls]


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.live = mock.MagicMock()
        self.live.project.state_dir = self.tmp.name
        self.live.game_identity.return_value = dict(GAME)
        self.live.journal.add.side_effect = lambda e: dict(e, id='j%d' % self.live.journal.add.call_count)
        self.live.eyes.photo.side_effect = self._photo
        self.native = FakeNative()
        self.tools = ShaderTools(self.live, self.native)

    @staticmethod
    def _photo(action, **kw):
        if action == 'view':
            return {'path': 'C:/pics/view-1.png'}
        return {'now': 'C:/pics/view-2.png', 'changedPercent': 41.5}

    def manifest(self):
        return json.loads((Path(self.tmp.name) / 'fnv/shaders/live/manifest.json').read_text('utf-8'))

    def stamp_manifest(self, effects, game):
        folder = Path(self.tmp.name) / 'fnv/shaders/live'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'old.hlsl').write_text(INVERT)
        (folder / 'old.ps').write_bytes(b'\x00\x03\xff\xff')
        (folder / 'manifest.json').write_text(json.dumps({'game': game, 'counter': 1, 'effects': effects}))

    @staticmethod
    def saved_effect(slot=0, order=0):
        return {'slot': slot, 'order': order, 'params': {}, 'source_file': 'old.hlsl', 'bytecode_file': 'old.ps',
                'enabled': True, 'constants': []}


class EffectTests(Harness):
    def test_add_compiles_uploads_and_journals(self):
        out = self.tools.effect_add('invert', INVERT)
        self.assertEqual(self.native.bundled_calls, [MODULE])
        order = self.native.exports()
        self.assertLess(order.index('sfx_buffer'), order.index('sfx_effect_set'))
        (buf_args,) = [a for e, a in self.native.calls if e == 'sfx_buffer']
        written = self.live.link.write.call_args[0]
        self.assertEqual(written[0], 0x5000)
        self.assertEqual(written[1][:2], b'\x00\x03')              # ps_3_0 version token, little endian
        self.assertEqual(buf_args, [len(written[1])])
        (set_args,) = [a for e, a in self.native.calls if e == 'sfx_effect_set']
        self.assertEqual(set_args, [0, 0x5000, len(written[1]), 0])
        (entry,) = [c[0][0] for c in self.live.journal.add.call_args_list]
        self.assertEqual(entry['kind'], 'shader')
        self.assertEqual(entry['undo']['op'], 'shader_effect_remove')
        self.assertIn('invert', self.manifest()['effects'])
        self.assertEqual(self.manifest()['game'], GAME)
        self.assertEqual(out['effect'], 'invert')
        self.assertEqual(out['changedPercent'], 41.5)
        self.assertEqual(out['costUs'], 120)
        self.assertEqual(out['before'], 'view-1.png')              # a file name, never a path
        self.assertNotIn('C:', json.dumps(out))

    def test_compile_error_sends_nothing(self):
        with self.assertRaises(ConvError) as cm:
            self.tools.effect_add('bad', 'float4 main( : COLOR0 { return 1 }')
        self.assertEqual(cm.exception.code, 'COMPILE_FAILED')
        self.assertTrue(cm.exception.evidence['errors'])
        self.assertEqual(self.native.calls, [])
        self.assertEqual(self.native.bundled_calls, [])
        self.live.journal.add.assert_not_called()
        self.live.link.write.assert_not_called()

    def test_duplicate_name_refused(self):
        self.tools.effect_add('invert', INVERT)
        with self.assertRaises(ConvError) as cm:
            self.tools.effect_add('invert', INVERT)
        self.assertEqual(cm.exception.code, 'EFFECT_EXISTS')

    def test_params_by_name(self):
        self.tools.effect_add('x', TINTED, photo=False)
        self.native.calls.clear()
        self.tools.effect_set('x', params={'tint': [1, 0.5, 0]}, photo=False)
        self.assertEqual(self.native.calls[0], ('sfx_effect_param', [0, 2, 1.0, 0.5, 0.0, 0.0]))
        self.assertEqual(self.manifest()['effects']['x']['params']['tint'], [1.0, 0.5, 0.0, 0.0])

    def test_unset_param_uses_ctab_default_else_zero(self):
        src = ('sampler2D scene : register(s0);\nfloat4 tint : register(c2) = float4(1, 0.5, 0, 1);\n'
               'float4 bare : register(c3);\n'
               'float4 main(float2 uv : TEXCOORD0) : COLOR0 { return tex2D(scene, uv) * tint * bare; }')
        out = self.tools.effect_add('d', src, photo=False)
        self.assertIn(('sfx_effect_param', [0, 2, 1.0, 0.5, 0.0, 1.0]), self.native.calls)
        self.assertIn(('sfx_effect_param', [0, 3, 0.0, 0.0, 0.0, 0.0]), self.native.calls)
        self.assertEqual(out['paramsNotSet'], ['bare'])
        self.native.calls.clear()
        self.tools.effect_set('d', params={'bare': 1}, photo=False)        # only the named param is sent
        self.assertEqual(self.native.calls[0], ('sfx_effect_param', [0, 3, 1.0, 0.0, 0.0, 0.0]))
        self.assertEqual([c for c in self.native.calls if c[0] == 'sfx_effect_param'], [self.native.calls[0]])

    def test_scalar_param_becomes_x(self):
        self.tools.effect_add('x', TINTED, params={'tint': 2}, photo=False)
        self.assertIn(('sfx_effect_param', [0, 2, 2.0, 0.0, 0.0, 0.0]), self.native.calls)

    def test_bad_param_refused(self):
        self.tools.effect_add('x', TINTED, photo=False)
        self.native.calls.clear()
        self.live.journal.add.reset_mock()
        for bad in ({'nope': 1}, {'tint': [1, 2, 3, 4, 5]}):
            with self.assertRaises(ConvError) as cm:
                self.tools.effect_set('x', params=bad, photo=False)
            self.assertEqual(cm.exception.code, 'BAD_PARAM')
            self.assertIn('tint', str(cm.exception))
            self.assertIn('c2', str(cm.exception))
        with self.assertRaises(ConvError):
            self.tools.effect_add('y', TINTED, params={'nope': 1}, photo=False)
        self.assertEqual(self.native.calls, [])
        self.live.journal.add.assert_not_called()

    def test_ninth_effect_refused(self):
        for i in range(8):
            self.tools.effect_add('e%d' % i, INVERT, photo=False)
        self.assertEqual(sorted(e['slot'] for e in self.manifest()['effects'].values()), list(range(8)))
        with self.assertRaises(ConvError) as cm:
            self.tools.effect_add('e8', INVERT, photo=False)
        self.assertEqual(cm.exception.code, 'TOO_MANY_EFFECTS')

    def test_lowest_free_slot_is_reused(self):
        for n in ('a', 'b', 'c'):
            self.tools.effect_add(n, INVERT, photo=False)
        self.tools.effect_remove('b', photo=False)
        self.tools.effect_add('d', INVERT, photo=False)
        self.assertEqual(self.manifest()['effects']['d']['slot'], 1)

    def test_remove_all_works_without_photos(self):
        self.tools.effect_add('a', INVERT, photo=False)
        self.tools.effect_add('b', INVERT, photo=False)
        self.live.eyes.photo.side_effect = LiveError('MENU_OPEN', 'A menu is open')
        out = self.tools.effect_remove('all')
        self.assertEqual(sorted(out['removed']), ['a', 'b'])
        self.assertEqual(self.native.slots, {})
        self.assertEqual(self.manifest()['effects'], {})
        undos = [c[0][0]['undo']['op'] for c in self.live.journal.add.call_args_list[-2:]]
        self.assertEqual(undos, ['shader_effect_add'] * 2)

    def test_menu_open_skips_photos(self):
        self.live.eyes.photo.side_effect = LiveError('MENU_OPEN', 'A menu is open (PauseMenu)')
        out = self.tools.effect_add('invert', INVERT)
        self.assertIn('invert', self.manifest()['effects'])
        self.assertIn('pictures need the game out of menus', out['note'])
        self.assertIsNone(out['changedPercent'])
        self.assertEqual(self.live.journal.add.call_count, 1)

    def test_restart_hides_stale_effects(self):
        self.stamp_manifest({'old': self.saved_effect()}, OTHER)
        out = self.tools.effect_list()
        self.assertEqual(out['effects'], [])
        self.assertEqual(out['savedSources'], ['old'])
        self.assertEqual(self.native.calls, [])

    def test_rebuild_after_bridge_restart(self):
        self.stamp_manifest({'old': self.saved_effect(slot=0, order=3)}, GAME)
        self.native.loaded = True
        self.native.slots[0] = 3
        fresh = ShaderTools(self.live, self.native)
        out = fresh.effect_list()
        self.assertEqual([e['name'] for e in out['effects']], ['old'])
        self.assertEqual(out['effects'][0]['order'], 3)
        self.assertTrue(out['effects'][0]['enabled'])
        self.assertEqual(out['savedSources'], [])
        self.assertEqual(self.native.bundled_calls, [])
        fresh.effect_add('new', INVERT, photo=False)                  # still without loading the module twice
        self.assertEqual(self.native.bundled_calls, [])
        self.assertEqual(self.manifest()['effects']['new']['slot'], 1)

    def test_effect_the_module_lost_moves_to_saved(self):
        self.stamp_manifest({'old': self.saved_effect()}, GAME)
        self.native.loaded = True                                     # loaded, but slot 0 does not exist
        out = self.tools.effect_list()
        self.assertEqual((out['effects'], out['savedSources']), ([], ['old']))

    def test_call_site_taken(self):
        self.native.bundled_error = ConvError('LOAD_FAILED', 'The companion could not load the module (init failed).')
        self.native.log_lines = [{'text': 'shaderfx: call site 0x00B55AD6 is not the expected call'}]
        with self.assertRaises(ConvError) as cm:
            self.tools.effect_add('invert', INVERT)
        self.assertEqual(cm.exception.code, 'CALL_SITE_TAKEN')
        self.assertIn('0x00B55AD6', str(cm.exception))
        self.live.journal.add.assert_not_called()
        self.assertEqual([e for e in self.native.exports() if e != 'sfx_info'], [])
        self.assertFalse((Path(self.tmp.name) / 'fnv/shaders/live/invert-1.ps').exists())

    def test_other_load_failure_passes_through(self):
        self.native.bundled_error = ConvError('LOAD_FAILED', 'no such dll')
        with self.assertRaises(ConvError) as cm:
            self.tools.effect_add('invert', INVERT)
        self.assertEqual(cm.exception.code, 'LOAD_FAILED')


class UndoTests(Harness):
    def test_undo_ops_registered(self):
        for op in ('shader_effect_remove', 'shader_effect_add', 'shader_effect_params'):
            self.assertIn(op, UNDO_OPS)

    def run_undo(self, journal_index):
        entry = self.live.journal.add.call_args_list[journal_index][0][0]
        return UNDO_OPS[entry['undo']['op']](self.live, entry, entry['undo'])

    def test_undo_add_frees_the_slot(self):
        self.tools.effect_add('invert', INVERT, photo=False)
        self.live.shader_tools = self.tools
        self.assertIsNone(self.run_undo(0))
        self.assertEqual(self.native.slots, {})
        self.assertEqual(self.manifest()['effects'], {})
        self.assertIn('not in the running game', self.run_undo(0)['reason'])

    def test_undo_remove_adds_it_back_with_params(self):
        self.tools.effect_add('x', TINTED, params={'tint': [1, 2, 3, 4]}, photo=False)
        self.tools.effect_remove('x', photo=False)
        self.live.shader_tools = self.tools
        self.native.calls.clear()
        self.assertIsNone(self.run_undo(1))
        self.assertIn(0, self.native.slots)
        self.assertIn(('sfx_effect_param', [0, 2, 1.0, 2.0, 3.0, 4.0]), self.native.calls)   # set zeroed them: resent
        self.assertIn('x', self.manifest()['effects'])

    def test_undo_remove_after_restart_cannot_act(self):
        self.tools.effect_add('x', INVERT, photo=False)
        self.tools.effect_remove('x', photo=False)
        self.live.shader_tools = self.tools
        self.live.game_identity.return_value = dict(OTHER)
        out = self.run_undo(1)
        self.assertIs(out['undone'], False)
        self.assertIn('restarted', out['reason'])

    def test_undo_set_puts_back_params_order_enabled(self):
        self.tools.effect_add('x', TINTED, params={'tint': [1, 1, 1, 1]}, order=0, photo=False)
        self.tools.effect_set('x', params={'tint': [0, 0, 0, 1]}, order=2, enabled=False, photo=False)
        e = self.manifest()['effects']['x']
        self.assertEqual((e['order'], e['enabled']), (2, False))
        self.live.shader_tools = self.tools
        self.native.calls.clear()
        self.assertIsNone(self.run_undo(1))
        e = self.manifest()['effects']['x']
        self.assertEqual((e['order'], e['enabled'], e['params']['tint']), (0, True, [1.0, 1.0, 1.0, 1.0]))
        self.assertIn(('sfx_effect_param', [0, 2, 1.0, 1.0, 1.0, 1.0]), self.native.calls)
        self.assertEqual(self.native.slots[0], 0)


if __name__ == '__main__':
    unittest.main()
