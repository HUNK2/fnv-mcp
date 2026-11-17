"""fnv_spec, the offline plugin builder's MCP tool: validate, build, inspect, lookup. (Installing a build is
fnv_install; the GECK-era geck_plugin_install and its guards were removed on 2026-11-18.)"""
import copy
import gzip
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from mcp import Client

from geck_mcp.config import Project
from geck_mcp.esp import build as builder
from geck_mcp.fnv_spec import SpecTools
from geck_mcp.server import make_server

FIXTURES = Path(__file__).parent / 'fixtures'
# Frozen copy of the original 13-reference CoolWorld spec; the live project spec keeps evolving.
SPEC = json.loads((FIXTURES / 'coolworld.spec.json').read_text())


class SpecFixture:
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        data = root / 'game' / 'Data'
        (data / 'meshes' / 'salvatore').mkdir(parents=True)
        shutil.copy(FIXTURES / 'meshes' / 'salvatore_animated_m5_upright.nif',
                    data / 'meshes' / 'salvatore' / 'salvatore_animated_m5_upright.nif')
        (data / 'textures' / 'salvatore').mkdir(parents=True)
        (data / 'textures' / 'salvatore' / 'front_atlas_m5.dds').write_bytes(b'DDS ')
        (root / 'state' / 'index').mkdir(parents=True)
        with gzip.open(root / 'state' / 'index' / 'FalloutNV.esm.json.gz', 'wt') as s:
            json.dump({'master': 'FalloutNV.esm', 'entries': {
                'FacRmFloor01': ['STAT', '00018EE1', None, 'Dungeons\\Facility\\Room\\FacRmFloor01.nif'],
                'FacRmFloor02': ['STAT', '00018EE2', None, 'Dungeons\\Facility\\Room\\FacRmFloor02.nif']}}, s)
        self.spec_path = root / 'coolworld.spec.json'
        self.spec_path.write_text(json.dumps(SPEC))
        self.p = Project(project_id='coolworld', game_root=root / 'game', plugin='CoolWorld.esp',
                         state_dir=root / 'state', spec=self.spec_path)
        self.tools = SpecTools(self.p)
        self.data = data
        self.build_dir = root / 'build'

    def tearDown(self):
        self.tmp.cleanup()


class SpecToolTests(SpecFixture, unittest.TestCase):
    def test_validate_is_a_dry_run(self):
        result = self.tools.run('validate')
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['operation'], 'fnv_spec')
        self.assertEqual(result['outcome'], 'unchanged')
        self.assertEqual(result['data']['validation']['checked']['textures'], 1)
        self.assertFalse((self.p.state_dir / 'formids' / 'coolworld.json').exists())
        self.assertFalse(self.build_dir.exists())

    def test_inline_spec_errors_are_reported(self):
        spec = copy.deepcopy(SPEC)
        spec['cells']['CoolWorld']['objects'][0]['base'] = '@Nowhere'
        result = self.tools.run('validate', spec=spec)
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'SPEC_INVALID')
        self.assertIn('objects[0].base', result['error']['message'])
        self.assertEqual(result['outcome'], 'failed_before_change')

    def test_missing_asset_fails_validation(self):
        (self.data / 'textures' / 'salvatore' / 'front_atlas_m5.dds').unlink()
        result = self.tools.run('build')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error']['code'], 'VALIDATION_FAILED')
        self.assertFalse((self.build_dir / 'CoolWorld.esp').exists())

    def test_build_is_stable_and_inspect_reads_it(self):
        built = self.tools.run('build')
        self.assertTrue(built['ok'], built)
        self.assertEqual(built['outcome'], 'built')
        self.assertIn('fnv_install', built['data']['next'])
        target = Path(built['data']['path'])
        self.assertEqual(target, self.build_dir / 'CoolWorld.esp')
        self.assertTrue(builder.is_generated(target.read_bytes(), 'coolworld'))
        again = self.tools.run('build')
        self.assertEqual(again['data']['sha256'], built['data']['sha256'])     # FormIDs and bytes are stable
        inspect = self.tools.run('inspect')['data']
        self.assertTrue(inspect['matchesBuild'])
        self.assertEqual({c['editorId']: c['references'] for c in inspect['cells'].values()}, {'CoolWorld': 13})

    def test_inspect_installed_copy(self):
        missing = self.tools.run('inspect', which='installed')
        self.assertEqual(missing['error']['code'], 'NOT_FOUND')
        built = self.tools.run('build')
        shutil.copy(built['data']['path'], self.data / 'CoolWorld.esp')   # what fnv_install does without MO2
        inspect = self.tools.run('inspect', which='installed')['data']
        self.assertEqual(Path(inspect['path']), self.data / 'CoolWorld.esp')
        self.assertTrue(inspect['matchesBuild'])

    def test_lookup_finds_project_and_master_records(self):
        result = self.tools.run('lookup', query='Floor', record_type='STAT')['data']
        self.assertEqual([r['editorId'] for r in result['masters']], ['FacRmFloor01', 'FacRmFloor02'])
        self.assertEqual(result['masters'][0]['reference'], '@FacRmFloor01')
        own = self.tools.run('lookup', query='salvatore')['data']['project']
        self.assertEqual(own[0]['reference'], '@CoolWorldSalvatore')

    def test_project_without_spec(self):
        p = self.p.model_copy(update={'plugin': None, 'spec': None})
        result = SpecTools(p).run('validate')
        self.assertEqual(result['error']['code'], 'NO_SPEC')
        self.assertEqual(result['outcome'], 'failed_before_change')


class SpecToolServerTests(SpecFixture, unittest.IsolatedAsyncioTestCase):
    """The same builder through the MCP server: one fnv_spec tool, no geck_* tools."""

    async def test_through_the_server(self):
        async with Client(make_server(self.p)) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            self.assertIn('fnv_spec', names)
            self.assertFalse([n for n in names if n.startswith('geck_')])
            result = (await client.call_tool('fnv_spec', {'action': 'validate'})).structured_content
            self.assertTrue(result['ok'], result)
            refused = await client.call_tool('fnv_spec', {'action': 'validate', 'which': 'installed'})
            self.assertTrue(refused.is_error)
            self.assertEqual((refused.structured_content or {}).get('error', {}).get('code'), 'INVALID_ARGUMENT')


if __name__ == '__main__':
    unittest.main()
