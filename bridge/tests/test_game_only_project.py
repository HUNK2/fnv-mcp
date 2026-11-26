"""Release plan Task 4.3 (audit H8): a project file for the game alone, with no plugin settings, is valid.

- Project.load accepts projects/example-fnv.json (no plugin or spec).
- fnv_spec on such a project answers NO_SPEC; the other fnv_* tools work.
- An invalid project file ends run_mcp.py with one line on stderr and exit code 2, no traceback.
- fnv_plugin_build plans prologues from fnv.clean_exe, then FNV_CLEAN_EXE, then ~/.fnvdecomp/FalloutNV.exe."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402

from fnv_mcp import fnv_plugin_build, locate  # noqa: E402
from fnv_mcp.config import Project  # noqa: E402
from fnv_mcp.server import make_server  # noqa: E402

BRIDGE = Path(__file__).resolve().parents[1]
EXAMPLE = BRIDGE / 'projects' / 'example-fnv.json'


def game_only(folder):
    path = Path(folder) / 'game.json'
    path.write_text(json.dumps({'project_id': 'gameonly', 'game_root': str(folder), 'state_dir': 'state',
                                'fnv': {'engine_control': 'read'}}), encoding='utf-8')
    return Project.load(path)


class LoadTests(unittest.TestCase):
    def test_the_example_loads(self):
        from fakes import load_example
        project = load_example(EXAMPLE.name)
        self.assertIsNone(project.plugin)
        self.assertIsNone(project.spec)
        self.assertEqual(project.fnv.engine_control, 'read')

class NoSpecTests(unittest.IsolatedAsyncioTestCase):
    async def test_fnv_spec_answers_no_spec_and_the_rest_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = game_only(tmp)
            async with Client(make_server(project)) as client:
                result = await client.call_tool('fnv_spec', {'action': 'build'})
                self.assertEqual(((result.structured_content or {}).get('error') or {}).get('code'), 'NO_SPEC')
                result = await client.call_tool('fnv_guide', {'topic': 'crash'})
                self.assertFalse(result.is_error, result)


class ReadmeCountTests(unittest.TestCase):
    """Task 4.2: the README's tool counts are the server's, not typed numbers that drift."""

    def test_the_readme_counts_match_the_server(self):
        import re
        from fnv_mcp.server import server_capabilities
        text = (BRIDGE.parent / 'README.md').read_text(encoding='utf-8')
        block = re.search(r'<!-- counts -->(.*?)<!-- /counts -->', text, re.S).group(1)
        numbers = [int(n) for n in re.findall(r'\d+', block)]
        caps = server_capabilities(make_server(Project.load(EXAMPLE)))
        self.assertEqual(numbers, [caps['toolCount']])


class InvalidProjectTests(unittest.TestCase):
    def run_mcp(self, project, *extra):
        env = {k: v for k, v in os.environ.items() if k != 'GECK_MCP_RELOAD'}
        env['GECK_SUPERVISOR_LOG'] = ''
        return subprocess.run([sys.executable, 'run_mcp.py', '--project', str(project), *extra], cwd=str(BRIDGE),
                              env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)

    def check(self, out):
        self.assertEqual(out.returncode, 2, out.stderr)
        self.assertNotIn('Traceback', out.stderr)
        self.assertEqual(len(out.stderr.strip().splitlines()), 1, out.stderr)

    def test_a_bad_key_exits_with_code_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'bad.json'
            path.write_text(json.dumps({'project_id': 'bad', 'game_root': tmp, 'state_dir': 's', 'nonsense': 1}),
                            encoding='utf-8')
            for extra in ((), ('--reload',)):
                with self.subTest(extra=extra):
                    out = self.run_mcp(path, *extra)
                    self.check(out)
                    self.assertIn('nonsense', out.stderr)

    def test_a_missing_file_and_broken_json_exit_with_code_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.check(self.run_mcp(Path(tmp) / 'missing.json'))
            broken = Path(tmp) / 'broken.json'
            broken.write_text('{"project_id": ', encoding='utf-8')
            self.check(self.run_mcp(broken))


class CleanExeTests(unittest.TestCase):
    def tools(self, fnv):
        live = SimpleNamespace(project=SimpleNamespace(fnv=fnv, state_dir='.'))
        return fnv_plugin_build.PluginTools(live)

    def test_the_order_is_project_then_environment_then_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            own, env = Path(tmp) / 'own.exe', Path(tmp) / 'env.exe'
            own.write_bytes(b'MZ')
            env.write_bytes(b'MZ')
            with mock.patch.dict(os.environ, {'FNV_CLEAN_EXE': str(env)}):
                self.assertEqual(self.tools(SimpleNamespace(clean_exe=own))._image_path(), own)
                self.assertEqual(self.tools(SimpleNamespace(clean_exe=None))._image_path(), env)
                self.assertEqual(self.tools(None)._image_path(), env)
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(self.tools(None)._image_path(), locate.clean_image(None))


if __name__ == '__main__':
    unittest.main()
