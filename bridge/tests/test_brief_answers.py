"""Tool quality part 5: brief answers by default for the tools whose usual answer is over 10,000 characters
(fnv_threads list, the hang report, fnv_scene_graph); brief=false gives the full answer, unchanged."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp import Client  # noqa: E402

import project_fixture  # noqa: E402
from geck_mcp import fnv_brief, fnv_deep, fnv_perf, fnv_world  # noqa: E402
from geck_mcp.action_params import ACTION_PARAMS  # noqa: E402
from geck_mcp.server import make_server  # noqa: E402


def size(answer):
    return len(json.dumps(answer))


def thread_list(n=60):
    rows = [{'threadId': 1000 + i, 'main': i == 0, 'start': 'FalloutNV!Thread%02d::Run' % (i % 9),
             'startAddress': '0x%08X' % (0x00401000 + i), 'now': 'ntdll.dll!NtWaitForSingleObject',
             'gameFunction': 'BSTaskManagerThread::ThreadProc' if i % 2 else None,
             'stack': ['ntdll.dll!NtWaitForSingleObject', 'KERNELBASE.dll!WaitForSingleObjectEx',
                       'BSTaskManagerThread::ThreadProc', 'BSThread::Callback', 'KERNEL32.DLL!BaseThreadInitThunk',
                       'ntdll.dll!RtlUserThreadStart']} for i in range(n)]
    return {'verdict': '%d threads; main 1000' % n, 'reason': 'start is the routine ...', 'threads': rows,
            'locks': [{'lock': 'global', 'address': '0x011F11A0', 'ownerThread': None, 'count': 0}]}


def frame(i):
    return {'address': '0x%08X' % (0x00800000 + i), 'function': 'Engine::Function%02d' % i, 'status': 'proven',
            'offset': i, 'source': 'src/engine%02d.cpp:%d' % (i, 100 + i),
            'locals': [{'name': 'local%d' % k, 'offset': -4 * k, 'value': k} for k in range(4)]}


def hang_report(threads=4, samples=5):
    out = []
    for t in range(threads):
        out.append({'threadId': 2000 + t, 'main': t == 0, 'topGameFunction': 'TES::LoadInterior',
                    'sameInSamples': '%d/%d' % (samples, samples), 'cpuPercent': 90 - t,
                    'samples': [{'frames': [frame(i) for i in range(24)],
                                 'stackScan': [dict(frame(i), at='0x%X' % (4 * i)) for i in range(12)]}
                                for _ in range(samples)]})
    fault = {'threadId': 2001, 'mainThread': False, 'code': '0xC0000005', 'address': '0x00812345', 'access': 'read',
             'accessAddress': '0x00000010', 'request': None, 'consoleLine': None, 'context': 'ctx.json',
             'function': frame(3), 'frames': [frame(i) for i in range(16)],
             'stackScan': [dict(frame(i), at='0x%X' % i) for i in range(16)], 'pathsOnStack': ['meshes\\a.nif']}
    return {'pid': 4242, 'state': 'hung: no frame for 9.0 s', 'verdict': 'Main thread hung ...', 'mainThreadId': 2000,
            'threads': out, 'busyThreads': [2001], 'heldFaults': [fault], 'threadCount': 70,
            'ping': {'frame': 5, 'msSinceFrame': 9000, 'busy': False, 'error': None}, 'note': 'Frames come from ...'}


def scene(n=300):
    nodes = []
    for i in range(n):
        parent = None if i == 0 else '0x%08X' % (0x10000000 + (i - 1) // 3)
        node = {'node': '0x%08X' % (0x10000000 + i), 'class': 'NiTriShape' if i % 3 == 2 else 'NiNode',
                'name': 'Bip01 Bone %03d' % i, 'depth': 0 if i == 0 else 1 + (i - 1) // 3 // 30, 'parent': parent,
                'flags': '0x80E', 'hidden': i % 50 == 7,
                'local': {'translate': [1.5, 2.5, 3.5], 'scale': 1.0},
                'world': {'translate': [1001.5, 2002.5, 3003.5], 'scale': 1.0}, 'childCount': 3}
        if i % 3 == 2:
            node['geometryInfo'] = {'alpha': False, 'material': True, 'skinned': True, 'vertices': 900,
                                    'triangles': 1200, 'shader': 'BSShaderPPLightingProperty'}
            node['textures'] = {'diffuse': 'textures\\armor\\body%02d.dds' % (i % 7),
                                'normal': 'textures\\armor\\body%02d_n.dds' % (i % 7)}
        if i == 0:
            node['controllers'] = [{'class': 'NiControllerManager', 'flags': '0x48',
                                    'sequences': [{'name': 'Idle', 'active': True}, {'name': 'Walk', 'active': False}]}]
            node['extraData'] = [{'class': 'BSXFlags', 'name': 'BSX', 'value': 194}]
        nodes.append(node)
    textures = sorted({t for n in nodes for t in (n.get('textures') or {}).values()})
    return {'ref': {'formId': '0x00000014', 'name': 'Player'}, 'model': 'meshes\\characters\\_male\\skeleton.nif',
            'root': '0x10000000', 'nodeCount': len(nodes), 'capped': False, 'nodes': nodes, 'textures': textures,
            'uses': ['ref +0x64 loaded data ...']}


class BriefShapeTests(unittest.TestCase):
    def test_thread_list(self):
        full = thread_list()
        brief = fnv_brief.threads(full)
        self.assertGreater(size(full), 10000)
        self.assertLess(size(brief), 10000)
        self.assertEqual(brief['verdict'], full['verdict'])
        self.assertEqual(brief['locks'], full['locks'])
        self.assertEqual([r['threadId'] for r in brief['threads']], [r['threadId'] for r in full['threads']])
        self.assertEqual(brief['threads'][1]['gameFunction'], 'BSTaskManagerThread::ThreadProc')
        self.assertNotIn('stack', brief['threads'][1])
        self.assertIn('brief=false', brief['brief'])
        self.assertIn('stack', full['threads'][1])                    # the full answer is not changed

    def test_hang_report(self):
        full = hang_report()
        brief = fnv_brief.hang(full)
        self.assertGreater(size(full), 10000)
        self.assertLess(size(brief), 10000)
        for key in ('pid', 'state', 'verdict', 'mainThreadId', 'busyThreads', 'threadCount', 'ping'):
            self.assertEqual(brief[key], full[key])
        main = brief['threads'][0]
        self.assertEqual((main['threadId'], main['topGameFunction'], main['sameInSamples'], main['cpuPercent']),
                         (2000, 'TES::LoadInterior', '5/5', 90))
        self.assertEqual(main['stack'][:2], ['Engine::Function00 (src/engine00.cpp:100)',
                                             'Engine::Function01 (src/engine01.cpp:101)'])
        self.assertNotIn('samples', main)
        fault = brief['heldFaults'][0]
        self.assertEqual((fault['code'], fault['function'], fault['pathsOnStack']),
                         ('0xC0000005', 'Engine::Function03 (src/engine03.cpp:103)', ['meshes\\a.nif']))
        self.assertNotIn('frames', fault)
        self.assertIn('brief=false', brief['brief'])
        self.assertIn('samples', full['threads'][0])

    def test_hang_frame_names(self):
        self.assertEqual(fnv_brief.frame_name({'address': '0x10001234', 'module': 'nvse_1_4.dll', 'offset': '0x1234'}),
                         'nvse_1_4.dll+0x1234')
        self.assertEqual(fnv_brief.frame_name({'address': '0x00000010', 'kind': 'unknown'}), '0x00000010')
        self.assertEqual(fnv_brief.frame_name(None), None)

    def test_scene_graph(self):
        full = scene()
        brief = fnv_brief.scene(full)
        self.assertGreater(size(full), 10000)
        self.assertLess(size(brief), size(full) / 3)
        for key in ('ref', 'model', 'root', 'nodeCount', 'capped', 'textures'):
            self.assertEqual(brief[key], full[key])
        tree = brief['tree']
        self.assertEqual(len(tree), 300)
        self.assertEqual(tree[0], 'NiNode "Bip01 Bone 000" 0x10000000 (sequences: Idle*, Walk; extra: BSX=194)')
        self.assertEqual(tree[1], '  NiNode "Bip01 Bone 001" 0x10000001')
        self.assertEqual(tree[2], '    NiNode "Bip01 Bone 004" 0x10000004')             # depth first
        self.assertIn('  NiTriShape "Bip01 Bone 002" 0x10000002 1200 tris, skinned', tree)
        self.assertIn('hidden', next(line for line in tree if '"Bip01 Bone 007"' in line))
        self.assertEqual(brief['classes'], {'NiNode': 200, 'NiTriShape': 100})
        self.assertNotIn('nodes', brief)
        self.assertIn('brief=false', brief['brief'])

    def test_scene_without_3d(self):
        unloaded = {'ref': {'name': 'x'}, 'model': None, 'loaded': False, 'note': 'no 3D'}
        self.assertEqual(fnv_brief.scene(unloaded), unloaded)


class BriefToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = project_fixture.ProjectFixture()
        self.fixture.setUp()

    async def asyncTearDown(self):
        self.fixture.tearDown()

    async def call(self, tool, args):
        async with Client(make_server(self.fixture.p)) as client:
            schema = {t.name: t.input_schema for t in (await client.list_tools()).tools}[tool]
            answer = (await client.call_tool(tool, args)).structured_content
        return schema, answer

    def test_action_params(self):
        threads = ACTION_PARAMS['fnv_threads']['actions']
        self.assertIn('brief', threads['list'])
        self.assertIn('brief', threads['hang'])

    async def test_threads_list_is_brief_by_default(self):
        with mock.patch.object(fnv_perf.ThreadTools, 'list', lambda self: thread_list()):
            schema, brief = await self.call('fnv_threads', {})
            _, full = await self.call('fnv_threads', {'brief': False})
        self.assertIs(schema['properties']['brief']['default'], True)
        self.assertTrue(schema['properties']['brief'].get('description'))
        self.assertTrue(brief['ok'], brief)
        self.assertNotIn('stack', brief['data']['threads'][1])
        self.assertEqual(full['data']['threads'][1]['stack'], thread_list()['threads'][1]['stack'])

    async def test_hang_is_brief_by_default(self):
        with mock.patch.object(fnv_deep.DeepTools, 'hang_doctor', lambda self, *a: hang_report(threads=1, samples=1)):
            _, brief = await self.call('fnv_threads', {'action': 'hang'})
            _, full = await self.call('fnv_threads', {'action': 'hang', 'brief': False})
        self.assertTrue(brief['ok'], brief)
        self.assertIn('stack', brief['data']['threads'][0])
        self.assertIn('samples', full['data']['threads'][0])

    async def test_scene_graph_is_brief_by_default(self):
        with mock.patch.object(fnv_world.WorldTools, 'scene_graph', lambda self, *a: scene(30)):
            schema, brief = await self.call('fnv_scene_graph', {})
            _, full = await self.call('fnv_scene_graph', {'brief': False})
        self.assertIs(schema['properties']['brief']['default'], True)
        self.assertTrue(brief['ok'], brief)
        self.assertIn('tree', brief['data'])
        self.assertEqual(len(full['data']['nodes']), 30)
        self.assertIn('local', full['data']['nodes'][0])


if __name__ == '__main__':
    unittest.main()
