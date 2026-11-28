"""Gemini audit, area 2 (security, safety gates, path boundaries): BUG-023 to BUG-029."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fnv_mcp import fnv_coverage, fnv_perf, fnv_time  # noqa: E402
from fnv_mcp.fnv_conv import ConvError  # noqa: E402
from fnv_mcp.fnv_live import LiveService  # noqa: E402
from fnv_mcp.safe_paths import check_includes  # noqa: E402

UNC = '\\\\evil-host\\share\\x.json'


class ConsoleRefusedTests(unittest.TestCase):
    def test_every_command_of_the_line_counts(self):
        for line in ('qqq', 'player.qqq', 'x;qqq', 'x\rqqq', 'x\x0bqqq', '(QuitGame)', 'save"x"', 'eval (QuitGame)',
                     'runscript QuitGame', 'player.additem f 1; save x', '  ( ( exit ) )'):
            self.assertIsNotNone(LiveService.console_refused(line), line)

    def test_ordinary_lines_pass(self):
        for line in ('player.additem f 1', 'GetStage QuestName', 'coc GoodSprings', 'player.setav health 100',
                     'x ; a comment', ''):
            self.assertIsNone(LiveService.console_refused(line), line)

    def test_run_console_refuses_a_carriage_return(self):
        live = LiveService.__new__(LiveService)
        with self.assertRaises(ValueError):
            live.run_console('x\rqqq')


class CheckIncludesTests(unittest.TestCase):
    def test_lone_cr_formfeed_vtab_and_blank_splices_do_not_hide_an_include(self):
        for source in ('x\r#include "C:/a.h"', '\f#include "C:/a.h"', '#\x0binclude "C:/a.h"',
                       '#\\ \n include "C:/a.h"', '#\\\r\ninclude "../a.h"', '#include\f"/etc/a.h"'):
            with self.assertRaises(ValueError, msg=repr(source)):
                check_includes(source)

    def test_plain_includes_pass(self):
        check_includes('#include "fnvmcp.h"\r\n#include <stdint.h>\n#  include "sub/a.h"\n')


class FnvCodeEditTests(unittest.TestCase):
    def test_edit_checks_includes_before_anything_else(self):
        from fnv_mcp.fnv_code import CodeTools
        tools = CodeTools.__new__(CodeTools)
        tools._target = lambda f: self.fail('the include check comes first')
        with self.assertRaises(ValueError):
            tools.edit('f', 'a', '#include "C:/x.h"')


class UncPathTests(unittest.TestCase):
    def test_profile_diff_refuses_a_network_path_before_touching_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = fnv_perf.ProfileTools(SimpleNamespace(project=SimpleNamespace(state_dir=tmp)))
            for ref in (UNC, '//host/share/x', '\\\\?\\UNC\\host\\share\\x'):
                with self.assertRaises(ConvError) as cm:
                    tools._load(ref)
                self.assertEqual(cm.exception.code, 'BAD_PATH')

    def test_coverage_diff_refuses_a_network_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = fnv_coverage.CoverageTools(SimpleNamespace(project=SimpleNamespace(state_dir=tmp)))
            with self.assertRaises(ConvError) as cm:
                tools.diff(UNC, 'b')
            self.assertEqual(cm.exception.code, 'BAD_PATH')

    def test_name_export_refuses_a_network_path(self):
        from fnv_mcp.fnv_naming import NamingTools
        from fnv_mcp.fnv_probe import ProbeError
        tools = NamingTools.__new__(NamingTools)
        tools.live = SimpleNamespace(project=SimpleNamespace(state_dir=None))
        with self.assertRaises(ProbeError) as cm:
            tools._export_path(UNC)
        self.assertEqual(cm.exception.code, 'BAD_PATH')

    def test_asset_check_refuses_a_network_plugin_path(self):
        from fnv_mcp import fnv_records
        from fnv_mcp.fnv_data import DataError
        source = Path(fnv_records.__file__).read_text(encoding='utf-8')
        self.assertIn("no_unc(plugin, 'plugin')", source)
        self.assertLess(source.index("no_unc(plugin, 'plugin')"), source.index('path.resolve()'))
        self.assertTrue(issubclass(DataError, Exception))

    def test_run_all_refuses_a_network_folder(self):
        from fnv_mcp.fnv_scenario import ScenarioError, ScenarioRunner
        runner = ScenarioRunner.__new__(ScenarioRunner)
        runner.live = SimpleNamespace(project=SimpleNamespace(fnv=SimpleNamespace(tests_dir=None)))
        with self.assertRaises(ScenarioError) as cm:
            runner.run_all('\\\\host\\share')
        self.assertEqual(cm.exception.code, 'BAD_PATH')


class ModuleNameTests(unittest.TestCase):
    def test_a_name_with_no_identifier_characters_is_refused(self):
        from fnv_mcp.fnv_plugin_build import PluginTools
        tools = PluginTools(SimpleNamespace(project=SimpleNamespace(state_dir='x')))
        for name in ('---', '..', '___'):
            with self.assertRaises(ConvError) as cm:
                tools.scaffold(name)
            self.assertEqual(cm.exception.code, 'BAD_NAME')
        self.assertTrue(str(tools._folder('my_hooks')).endswith('my_hooks'))


class FullMatchTests(unittest.TestCase):
    def test_a_trailing_newline_is_not_a_name(self):
        from fnv_mcp import fnv_game, fnv_link
        from fnv_mcp.fnv_args import EDITOR_ID
        self.assertIsNone(fnv_game.CELL_NAME.fullmatch('Cell\n'))
        self.assertIsNone(fnv_link.SAFE_NAME.fullmatch('Mod\n'))
        self.assertIsNone(EDITOR_ID.fullmatch('Foo\n'))
        source = (Path(fnv_game.__file__)).read_text(encoding='utf-8')
        self.assertNotIn('CELL_NAME.match(', source)
        self.assertNotIn('SAFE_NAME.match(', source)


class GateTests(unittest.TestCase):
    def test_package_with_a_module_and_build_is_full_only(self):
        from fnv_mcp.fnv_live_tools import needs_full
        self.assertTrue(needs_full('fnv_author', 'package_build'))
        self.assertFalse(needs_full('fnv_author', 'package'))
        self.assertFalse(needs_full('fnv_author', 'scaffold'))

    def test_companion_install_to_the_game_needs_the_direct_install_switch(self):
        from fnv_mcp.fnv_game import Game, GameError
        game = Game.__new__(Game)
        from fnv_mcp.fnv_layout import DIRECT
        off = SimpleNamespace(kind=DIRECT, changes_allowed=False)
        with self.assertRaises(GameError) as cm:
            game.guard_direct('install the companion', off)
        self.assertEqual(cm.exception.code, 'DIRECT_INSTALL_PROTECTED')
        game.guard_direct('install the companion', SimpleNamespace(kind=DIRECT, changes_allowed=True))

    def test_script_decompile_is_guarded(self):
        from fnv_mcp.fnv_game import GameError
        from fnv_mcp.fnv_scriptcov import ScriptCoverage
        calls = []

        def guard(what):
            calls.append(what)
            raise GameError('DIRECT_INSTALL_PROTECTED', 'refused')
        cov = ScriptCoverage.__new__(ScriptCoverage)
        cov.live = SimpleNamespace(in_game=lambda: None, game=SimpleNamespace(guard_direct=guard),
                                   console=lambda *a: self.fail('the console must not be reached'))
        with self.assertRaises(GameError):
            cov.decompile('0x14')
        self.assertEqual(len(calls), 1)


class UntilLongTests(unittest.TestCase):
    def make(self, fail):
        sent = []

        def batch(ops, timeout=None):
            sent.append([op[0] for op in ops])
            if ops and ops[0][0] == 'pred' and fail:
                raise RuntimeError('pipe closed')
            return [{'ok': True}] * len(ops)
        wait = fnv_time.WaitFor.__new__(fnv_time.WaitFor)
        wait.control = SimpleNamespace(batch=batch, clock=lambda: 0.0)
        wait.check_readable = lambda compiled: None
        return wait, sent

    def test_a_failed_fast_request_reopens_the_gate(self):
        wait, sent = self.make(True)
        snap = {'held': False, 'fixedFps': 60, 'realFps': 60}
        with self.assertRaises(RuntimeError):
            wait.until_long('frame >= 5', 5, snap, 10.0)
        self.assertEqual(sent[-1], ['hold'])

    def test_a_good_fast_request_sends_no_extra_hold_off(self):
        wait, sent = self.make(False)
        wait.until = lambda *a, **k: {'met': True, 'frames': 1}
        snap = {'held': False, 'fixedFps': 60, 'realFps': 60}
        self.assertTrue(wait.until_long('frame >= 5', 5, snap, 10.0)['met'])
        self.assertEqual(sent, [])


if __name__ == '__main__':
    unittest.main()
