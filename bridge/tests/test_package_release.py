"""fnv_author package as one release (tool gap 6, 2026-11-24): the fnv_plugin_build module in Data/NVSE/Plugins,
manifest.json, build=true, and the refusal of decompiler text and paths of this machine. Through the real server
(engine_control read, no game), with CoolWorld.esp in a temporary dev profile."""
import hashlib
import json
import sys
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402

from fnv_mcp.fnv_create import package_leaks, private_markers  # noqa: E402
from fnv_mcp.fnv_plugin_build import PluginTools  # noqa: E402
from fnv_mcp.server import make_server  # noqa: E402
from test_release_safety import add_dev_plugin  # noqa: E402
from test_stab_gate import GateFixture  # noqa: E402

DLL = b'MZ\x90\x00' + b'\x00' * 60 + b'my own module'


class ReleasePackageTests(GateFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        add_dev_plugin(self)
        self.state = Path(self.project.state_dir)
        self.packages = self.state / 'fnv' / 'packages'

    def module(self, dll=DLL, source='int mine(void) { return 1; }\n', hooks=None):
        folder = self.state / 'fnv' / 'plugins' / 'mymod'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'mymod.dll').write_bytes(dll)
        (folder / 'mymod.c').write_text(source, encoding='utf-8')
        if hooks is not None:
            (folder / 'build.json').write_text(json.dumps({'hooks': hooks}), encoding='utf-8')
        return folder

    async def package(self, **extra):
        async with Client(make_server(self.project)) as client:
            code, result = await self.call(client, 'fnv_author', {'action': 'package', 'plugin': 'CoolWorld.esp',
                                                                  **extra})
        sc = result.structured_content or {}
        return code, (sc.get('data') or sc), sc.get('error')

    def nothing_left(self):
        left = sorted(p.name for p in self.packages.iterdir()) if self.packages.exists() else []
        self.assertEqual(left, [], 'a refused package left files behind')

    async def test_the_manifest_lists_every_file_with_its_hash(self):
        code, out, err = await self.package(version='2.1')
        self.assertIsNone(code, err)
        manifest = json.loads((Path(out['folder']) / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual((manifest['version'], manifest['plugin'], manifest['module']), ('2.1', 'CoolWorld.esp', None))
        self.assertIn('FalloutNV.esm', manifest['masters'])
        files = {f['path']: f for f in manifest['files']}
        plugin = Path(out['folder']) / 'Data' / 'CoolWorld.esp'
        self.assertEqual(files['Data/CoolWorld.esp']['sha256'], hashlib.sha256(plugin.read_bytes()).hexdigest())
        self.assertIn('README.txt', files)
        with zipfile.ZipFile(out['archive']) as z:
            self.assertIn('manifest.json', z.namelist())

    async def test_the_module_ships_in_nvse_plugins(self):
        self.module()
        code, out, err = await self.package(module='mymod')
        self.assertIsNone(code, err)
        self.assertEqual(out['module'], 'Data/NVSE/Plugins/mymod.dll')
        with zipfile.ZipFile(out['archive']) as z:
            self.assertEqual(z.read('Data/NVSE/Plugins/mymod.dll'), DLL)
            self.assertNotIn('mymod.c', ' '.join(z.namelist()))             # the source is checked, not shipped
            self.assertIn('xNVSE', z.read('README.txt').decode())

    async def test_a_module_not_built_is_named(self):
        code, _out, err = await self.package(module='nothere')
        self.assertEqual(code, 'MODULE_NOT_BUILT', err)
        self.nothing_left()

    async def test_decompiler_text_in_the_module_is_refused(self):
        self.module(dll=DLL + b'\x00FUN_00401000\x00')
        code, _out, err = await self.package(module='mymod')
        self.assertEqual(code, 'PACKAGE_REFUSED', err)
        self.assertIn('FUN_00401000', err['message'])
        self.nothing_left()

    async def test_decompiled_code_in_the_module_source_is_refused(self):
        self.module(source='void f(void) { *(int *)DAT_011c3b3c = 0; }\n')
        code, _out, err = await self.package(module='mymod')
        self.assertEqual(code, 'PACKAGE_REFUSED', err)
        self.assertIn('module source', err['message'])
        self.nothing_left()

    async def test_a_path_of_this_machine_is_refused_without_printing_it(self):
        home = str(Path.home())
        self.module(dll=DLL + b'\x00' + home.encode('utf-8') + b'\\x.pdb\x00')
        code, _out, err = await self.package(module='mymod')
        self.assertEqual(code, 'PACKAGE_REFUSED', err)
        self.assertIn('your user profile', err['message'])
        self.assertNotIn(home, err['message'])
        self.nothing_left()

    async def test_build_rebuilds_the_module_with_the_hooks_of_its_last_build(self):
        folder = self.module(hooks=['0x00401000'])
        # building runs zig cc: a full-control action since the Gemini audit (BUG-023); read refuses it
        code, _out, err = await self.package(module='mymod', build=True)
        self.assertEqual(code, 'CONTROL_DISABLED', err)
        self.project = self.project.model_copy(update={'fnv': self.project.fnv.model_copy(
            update={'engine_control': 'full'})})
        with mock.patch.object(PluginTools, 'build', autospec=True,
                               return_value={'dll': str(folder / 'mymod.dll')}) as build:
            code, out, err = await self.package(module='mymod', build=True)
        self.assertIsNone(code, err)
        build.assert_called_once()
        self.assertEqual(build.call_args.kwargs['hooks'], ['0x00401000'])
        self.assertEqual(out['built']['module']['hooks'], ['0x00401000'])
        self.assertFalse(out['built']['spec']['built'])                # the project spec makes another plugin

    async def test_a_bad_version_is_refused(self):
        code, _out, err = await self.package(version='1.0/../x')
        self.assertEqual(code, 'INVALID_ARGUMENT', err)


class LeakCheckTests(unittest.TestCase):
    def test_markers_cover_the_profile_and_user_name_in_either_slash(self):
        project = mock.Mock()
        project.fnv.mo2_root = None
        markers = dict(private_markers(project))
        home = str(Path.home())
        import re
        self.assertTrue(re.search(markers['your user profile'], home.replace('\\', '/').upper().encode()))
        self.assertTrue(re.search(markers['your user profile'], home.encode()))

    def test_clean_files_pass(self):
        project = mock.Mock()
        project.fnv.mo2_root = None
        import tempfile
        with tempfile.NamedTemporaryFile('wb', suffix='.esp', delete=False) as f:
            f.write(b'TES4 plugin text, Data\\meshes\\mymod\\x.nif')
        try:
            self.assertEqual(package_leaks([('Data/x.esp', f.name)], project), [])
        finally:
            Path(f.name).unlink()

    def test_saved_hooks(self):
        live = mock.Mock()
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            live.project.state_dir = d
            tools = PluginTools(live)
            self.assertEqual(tools.saved_hooks('m'), [])
            folder = tools._folder('m')
            folder.mkdir(parents=True)
            (folder / 'build.json').write_text('{"hooks": ["Actor::Kill"]}', encoding='utf-8')
            self.assertEqual(tools.saved_hooks('m'), ['Actor::Kill'])


if __name__ == '__main__':
    unittest.main()
