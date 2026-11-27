"""fnv_shader registration (SHADER-PLAN Task 7): the tool's 12 actions, the gate tables, status, the run() dispatch
(bytecode is summarised, never returned) and the guide topic. No game, no pipe."""
from pathlib import Path
from unittest import mock
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_fixture  # noqa: E402
from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_guide import guide_names, guide_text  # noqa: E402
from fnv_mcp.fnv_live_tools import (FNV_LIVE_TOOLS, FULL_ONLY_ACTIONS, MUTATING_ACTIONS,  # noqa: E402
                                    gated_kind, needs_full)
from fnv_mcp.fnv_shader import ShaderTools  # noqa: E402
from fnv_mcp.server import make_server  # noqa: E402
from test_shader_effects import FakeNative, INVERT, Harness  # noqa: E402

ACTIONS = ['compile', 'engine_list', 'engine_show', 'template', 'effect_add', 'effect_set', 'effect_list',
           'effect_remove', 'replace', 'restore', 'status', 'export']
PARAMS = {'action', 'source', 'profile', 'name', 'filter', 'order', 'enabled', 'params', 'disasm', 'install', 'set',
          'photo'}


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        fixture = project_fixture.ProjectFixture()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        project = fixture.p.model_copy(update={'toolsets': ['game', 'editor', 'naming', 'legacy']})
        self.tools = {t.name: t.parameters for t in make_server(project)._tool_manager.list_tools()}

    def test_tool_registered_with_12_actions(self):
        schema = self.tools['fnv_shader']
        props = schema['properties']
        options = [props['action']] + props['action'].get('anyOf', [])
        enum = next(o['enum'] for o in options if 'enum' in o)
        self.assertEqual(enum, ACTIONS)
        self.assertEqual(set(props), PARAMS)

    def test_kinds_and_gates(self):
        self.assertEqual(FNV_LIVE_TOOLS['fnv_shader'][0], 'act')
        self.assertEqual(MUTATING_ACTIONS['fnv_shader'],
                         {'effect_add', 'effect_set', 'effect_remove', 'replace', 'restore'})
        self.assertEqual(FULL_ONLY_ACTIONS['fnv_shader'], {'effect_add', 'effect_set', 'replace', 'export'})
        for action in ('compile', 'engine_list', 'engine_show', 'template', 'effect_list', 'status'):
            self.assertEqual(gated_kind('fnv_shader', action, 'read'), 'read', action)
            self.assertFalse(needs_full('fnv_shader', action), action)
        for action in ('effect_add', 'effect_set', 'effect_remove', 'replace', 'restore'):
            self.assertEqual(gated_kind('fnv_shader', action, 'read'), 'act', action)
        self.assertTrue(needs_full('fnv_shader', 'export'))
        self.assertFalse(needs_full('fnv_shader', 'effect_remove'))


class StatusTests(Harness):
    def native_info(self, bits):
        real = self.native.call

        def call(name, export, args=None):
            if export == 'sfx_info':
                self.native.calls.append((export, []))
                return {'value': bits}
            return real(name, export, args)
        self.native.call = call

    def test_status_reports_renderer(self):
        self.native.loaded = True
        self.native_info(1 | 2 | 8)
        self.live.agent.menus.return_value = {'inMenuMode': False, 'menuStack': []}
        with mock.patch.object(ShaderTools, 'package', side_effect=ConvError('NO_PACKAGE', 'none')):
            out = self.tools.status()
        r = out['renderer']
        self.assertTrue(r['nonSystemD3D9'])
        self.assertFalse(r['hdrInput'])
        self.assertFalse(r['deviceLost'])
        self.assertFalse(r['drawing'])
        self.assertTrue(out['module'])
        self.assertFalse(out['inMenu'])
        self.assertTrue(any('ENB' in w for w in out['warnings']))
        self.assertIn('menus', out['note'])
        self.assertEqual(out['effects'], 0)
        self.assertEqual(out['replacements'], 0)

    def test_status_without_the_module_does_not_load_it(self):
        out = self.tools.status()
        self.assertFalse(out['module'])
        self.assertEqual(self.native.bundled_calls, [])
        self.assertIsNone(out['renderer'])
        self.assertFalse(out.get('warnings'))

    def test_status_in_menu(self):
        self.native.loaded = True
        self.native_info(1 | 16)
        self.live.agent.menus.return_value = {'inMenuMode': True, 'menuStack': [{'name': 'PauseMenu'}]}
        out = self.tools.status()
        self.assertTrue(out['inMenu'])
        self.assertTrue(out['renderer']['drawing'])
        self.assertTrue(out['renderer']['hdrInput'])


class RunTests(Harness):
    def test_compile_summarises_and_never_returns_bytes(self):
        out = self.tools.run('compile', source=INVERT)
        self.assertTrue(out['ok'])
        self.assertIsNone(out.get('bytecode'))
        self.assertGreater(out['bytecodeSize'], 0)
        self.assertIn('version', out)
        self.assertIn('constants', out)
        self.assertNotIn(b'\x00', repr(out).encode())      # nothing binary in the answer

    def test_compile_errors_come_back_as_data(self):
        out = self.tools.run('compile', source='float4 main( : COLOR0 { return 0; }')
        self.assertFalse(out['ok'])
        self.assertTrue(out['errors'])

    def test_compile_needs_source(self):
        with self.assertRaises(ConvError) as cm:
            self.tools.run('compile')
        self.assertEqual(cm.exception.code, 'MISSING_ARGUMENT')

    def test_template_effect(self):
        out = self.tools.run('template')
        self.assertIn('source', out)

    def test_effect_add_passes_arguments(self):
        out = self.tools.run('effect_add', name='invert', source=INVERT, order=1, photo=False)
        self.assertEqual(out['effect'], 'invert')
        self.assertEqual(out['order'], 1)

    def test_unknown_action(self):
        with self.assertRaises(ConvError) as cm:
            self.tools.run('nope')
        self.assertEqual(cm.exception.code, 'BAD_ACTION')

    def test_call_site_taken_message(self):
        text = ShaderTools._taken().args[0] if ShaderTools._taken().args else str(ShaderTools._taken())
        self.assertIn('0x00B55AD6', text)
        self.assertIn('fnvmcp_shaders_', text)
        self.assertIn('disable', text.lower())


class GuideTests(unittest.TestCase):
    def test_guide_topic_listed(self):
        self.assertIn('shaders', guide_names())

    def test_guide_says_the_rules(self):
        text = guide_text('shaders')
        for needle in ('CALL_SITE_TAKEN', 'fnvmcp_shaders_', 'gameContent', 'depth', 'per-object', '8 effects',
                       'fnv_undo', 'engine_show', 'menus', 'template', 'HDR'):
            self.assertIn(needle, text)


if __name__ == '__main__':
    unittest.main()
