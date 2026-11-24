"""Tool gaps 3 (2026-11-24): meshes, textures and animations made from source files, offline (fnv_mcp/assets, ported
from the user's robot project rotf-fnv: tools/nif, textures.py, gltf.py, clips.py, body.py, work/render.py).

Vanilla NIF and KF files read from the game's archives are read and written back byte for byte (skipped without the
game); a synthetic .glb cube becomes a NIF whose vertices read back in game axes and units; a PNG becomes a DXT1 or
DXT5 DDS with its whole mip chain; a KF made from a JSON key list reads back with its keys and text keys; a preview
draws something."""
import json
import os
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from fnv_mcp.assets import convert, dds, kf, mesh, nif, render
from fnv_mcp.assets.convert import AssetMakeError

DATA = Path(os.environ.get('FNV_DATA', r'C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\Data'))
UNITS = 1 / 0.0142875

CORPUS = ['meshes\\characters\\_male\\skeleton.nif', 'meshes\\creatures\\protectron\\mtidle.kf',
          'meshes\\creatures\\protectron\\locomotion\\mtforward.kf', 'meshes\\creatures\\protectron\\protectron.nif',
          'meshes\\characters\\_male\\upperbody.nif']


def vanilla(rels):
    from fnv_mcp.esp.assets import bsa_extract
    out = {}
    if not DATA.is_dir():
        return out
    want = {r.lower() for r in rels}
    for bsa in sorted(DATA.glob('*.bsa')):
        try:
            out.update(bsa_extract(bsa, want - set(out)))
        except Exception:  # noqa: BLE001 - an archive of another kind
            continue
    return out


FILES = vanilla(CORPUS)


def write_glb(path, positions, indices, normals=None, uvs=None, name='Cube', image_png=None, animation=None,
              nodes=None):
    """A small .glb: one mesh node (or `nodes` given whole), optional embedded image as base colour, optional
    animation [(node, path, times, values)]."""
    blob = bytearray()
    views, accessors = [], []

    def add(array, ctype, kind, target=None):
        array = np.ascontiguousarray(array)
        while len(blob) % 4:
            blob.append(0)
        views.append({'buffer': 0, 'byteOffset': len(blob), 'byteLength': array.nbytes,
                      **({'target': target} if target else {})})
        blob.extend(array.tobytes())
        acc = {'bufferView': len(views) - 1, 'componentType': ctype, 'count': len(array), 'type': kind}
        if kind == 'VEC3' and ctype == 5126:
            acc['min'] = array.min(axis=0).tolist()
            acc['max'] = array.max(axis=0).tolist()
        accessors.append(acc)
        return len(accessors) - 1

    attrs = {'POSITION': add(np.asarray(positions, np.float32), 5126, 'VEC3', 34962)}
    if normals is not None:
        attrs['NORMAL'] = add(np.asarray(normals, np.float32), 5126, 'VEC3', 34962)
    if uvs is not None:
        attrs['TEXCOORD_0'] = add(np.asarray(uvs, np.float32), 5126, 'VEC2', 34962)
    prim = {'attributes': attrs, 'indices': add(np.asarray(indices, np.uint16).reshape(-1), 5123, 'SCALAR', 34963)}
    doc = {'asset': {'version': '2.0'}, 'scene': 0, 'meshes': [{'primitives': [prim]}],
           'accessors': accessors, 'bufferViews': views, 'buffers': [{}]}
    if image_png is not None:
        png = Path(image_png).read_bytes()
        while len(blob) % 4:
            blob.append(0)
        views.append({'buffer': 0, 'byteOffset': len(blob), 'byteLength': len(png)})
        blob.extend(png)
        doc['images'] = [{'bufferView': len(views) - 1, 'mimeType': 'image/png', 'name': 'crate_d'}]
        doc['textures'] = [{'source': 0}]
        doc['materials'] = [{'pbrMetallicRoughness': {'baseColorTexture': {'index': 0}}}]
        prim['material'] = 0
    if nodes is None:
        nodes = [{'name': name, 'mesh': 0}]
    doc['nodes'] = nodes
    doc['scenes'] = [{'nodes': [i for i, n in enumerate(nodes) if not any(i in m.get('children', [])
                                                                          for m in nodes)]}]
    if animation:
        samplers, channels = [], []
        for node, target, times, values in animation:
            width = {'translation': 'VEC3', 'rotation': 'VEC4', 'scale': 'VEC3'}[target]
            samplers.append({'input': add(np.asarray(times, np.float32), 5126, 'SCALAR'),
                             'output': add(np.asarray(values, np.float32), 5126, width)})
            channels.append({'sampler': len(samplers) - 1, 'target': {'node': node, 'path': target}})
        doc['animations'] = [{'name': 'Wave', 'samplers': samplers, 'channels': channels}]
    doc['buffers'][0]['byteLength'] = len(blob)
    text = json.dumps(doc).encode()
    text += b' ' * (-len(text) % 4)
    blob += b'\0' * (-len(blob) % 4)
    out = struct.pack('<4sII', b'glTF', 2, 12 + 8 + len(text) + 8 + len(blob))
    out += struct.pack('<II', len(text), 0x4E4F534A) + text + struct.pack('<II', len(blob), 0x004E4942) + bytes(blob)
    Path(path).write_bytes(out)


def cube():
    """A 1 m cube from 0 to 1 on every glTF axis (y up), 24 vertices, 12 triangles."""
    pos, nrm, uv, tri = [], [], [], []
    for axis in range(3):
        for side in (0, 1):
            n = [0, 0, 0]
            n[axis] = 1 if side else -1
            a, b = [k for k in range(3) if k != axis]
            base = len(pos)
            for u, v in ((0, 0), (1, 0), (1, 1), (0, 1)):
                p = [0.0, 0.0, 0.0]
                p[axis], p[a], p[b] = side, u, v
                pos.append(p)
                nrm.append(n)
                uv.append((u, v))
            quad = [(base, base + 1, base + 2), (base, base + 2, base + 3)]
            e1, e2 = np.subtract(pos[base + 1], pos[base]), np.subtract(pos[base + 2], pos[base])
            if np.dot(np.cross(e1, e2), n) < 0:      # counter-clockwise seen from outside, as glTF front faces are
                quad = [t[::-1] for t in quad]
            tri += quad
    return np.array(pos), np.array(nrm, float), np.array(uv, float), np.array(tri)


def faces_point_out(vertices, triangles):
    """True when every triangle's winding normal points away from the mesh's centre (front faces outside)."""
    v = np.asarray(vertices, np.float64)
    t = np.asarray(triangles, np.int64)
    centre = v.mean(axis=0)
    normal = np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])
    return bool(np.all(np.einsum('ij,ij->i', normal, v[t].mean(axis=1) - centre) > 0))


@unittest.skipUnless(FILES, 'needs the New Vegas archives (FNV_DATA)')
class VanillaRoundTrip(unittest.TestCase):
    def test_vanilla_files_write_back_byte_for_byte(self):
        seen = set()
        for rel, data in FILES.items():
            with self.subTest(rel):
                f = nif.read(data)
                seen.update(b.type for b in f.blocks if b.fields is not None)
                self.assertEqual(f.write(), data)
        for kind in ('NiNode', 'NiTriShape', 'NiTriShapeData', 'BSShaderPPLightingProperty', 'BSShaderTextureSet',
                     'NiControllerSequence', 'NiTransformInterpolator', 'NiTransformData', 'NiTextKeyExtraData'):
            self.assertIn(kind, seen)


class Textures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def header(self, path):
        data = Path(path).read_bytes()
        h, w = struct.unpack_from('<II', data, 12)
        return w, h, struct.unpack_from('<I', data, 28)[0], data[84:88].decode()

    def test_opaque_png_is_dxt1_with_every_mip(self):
        png = self.root / 'a.png'
        Image.new('RGB', (64, 32), (200, 10, 10)).save(png)
        report = dds.make(png, self.root / 'a.dds')
        self.assertEqual(self.header(self.root / 'a.dds'), (64, 32, 7, 'DXT1'))
        self.assertEqual((report['format'], report['mipLevels']), ('DXT1', 7))

    def test_alpha_png_is_dxt5_and_odd_sizes_are_resized(self):
        png = self.root / 'b.png'
        Image.new('RGBA', (100, 60), (10, 10, 10, 128)).save(png)
        report = dds.make(png, self.root / 'b.dds')
        self.assertEqual(self.header(self.root / 'b.dds')[3], 'DXT5')
        self.assertEqual((report['width'], report['height']), (128, 64))
        self.assertTrue(report['warnings'])

    def test_normal_map_carries_specular_in_alpha(self):
        normal, spec = self.root / 'n.png', self.root / 's.png'
        Image.new('RGB', (16, 16), (128, 128, 255)).save(normal)
        Image.new('L', (16, 16), 200).save(spec)
        report = dds.make(normal, self.root / 'n.dds', {'normal': True, 'specular': str(spec)})
        self.assertEqual(report['format'], 'DXT5')


class Meshes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def shapes(self, f):
        return [b for b in f.blocks if b.type == 'NiTriShape']

    def test_a_glb_cube_becomes_a_static_nif_in_game_axes_and_units(self):
        pos, nrm, uv, tri = cube()
        write_glb(self.root / 'cube.glb', pos, tri, nrm, uv)
        out = self.root / 'Data' / 'meshes' / 'test' / 'cube.nif'
        report = mesh.make(self.root / 'cube.glb', out, {}, rel='meshes\\test\\cube.nif')
        f = nif.read(out.read_bytes())
        self.assertEqual(f.blocks[0].type, 'BSFadeNode')
        self.assertEqual(f.roots, [0])
        shapes = self.shapes(f)
        self.assertEqual(len(shapes), 1)
        data = f.blocks[shapes[0]['Data']]
        v = np.array(data['Vertices'])
        self.assertEqual(len(v), 24)
        self.assertEqual(len(data['Triangles']), 12)
        # glTF y (up) is the game's z; glTF z (toward the viewer) is the game's -y; metres become game units
        expect = np.stack([pos[:, 0], -pos[:, 2], pos[:, 1]], axis=1) * UNITS
        np.testing.assert_allclose(v, expect, atol=1e-3)
        self.assertTrue(faces_point_out(pos, tri), 'the test cube itself')
        # the engine culls back faces: front faces must stay outside (live run 2026-11-24 showed an inside-out cube)
        self.assertTrue(faces_point_out(v, data['Triangles']))
        self.assertEqual(report['triangles'], 12)
        self.assertEqual(report['bounds']['min'], [0.0, round(-UNITS, 2), 0.0])
        flags = [b for b in f.blocks if b.type == 'BSXFlags']
        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0]['Integer Data'], 0)                # no collision: no Havok flag

    def test_a_glb_texture_is_written_as_dds_next_to_the_mesh(self):
        pos, nrm, uv, tri = cube()
        png = self.root / 'crate_d.png'
        Image.new('RGB', (32, 32), (90, 60, 30)).save(png)
        write_glb(self.root / 'crate.glb', pos, tri, nrm, uv, image_png=png)
        data_root = self.root / 'Data'
        report = mesh.make(self.root / 'crate.glb', data_root / 'meshes' / 'test' / 'crate.nif', {},
                           rel='meshes\\test\\crate.nif')
        f = nif.read((data_root / 'meshes' / 'test' / 'crate.nif').read_bytes())
        sets = [b for b in f.blocks if b.type == 'BSShaderTextureSet']
        self.assertEqual(sets[0]['Textures'][0], 'textures\\test\\crate_d.dds')
        self.assertEqual(sets[0]['Textures'][1], 'textures\\test\\crate_d_n.dds')
        self.assertTrue((data_root / 'textures' / 'test' / 'crate_d.dds').is_file())
        self.assertTrue((data_root / 'textures' / 'test' / 'crate_d_n.dds').is_file())
        self.assertEqual(sorted(report['textures']), ['textures\\test\\crate_d.dds', 'textures\\test\\crate_d_n.dds'])

    def test_options_texture_scale_and_z_up(self):
        pos, nrm, uv, tri = cube()
        write_glb(self.root / 'c.glb', pos, tri, nrm, uv)
        out = self.root / 'c.nif'
        mesh.make(self.root / 'c.glb', out, {'scale': 10, 'up': 'z', 'texture': 'textures\\a\\b.dds'})
        f = nif.read(out.read_bytes())
        v = np.array(f.blocks[self.shapes(f)[0]['Data']]['Vertices'])
        np.testing.assert_allclose(v, pos * 10, atol=1e-4)
        self.assertEqual([b for b in f.blocks if b.type == 'BSShaderTextureSet'][0]['Textures'][0],
                         'textures\\a\\b.dds')

    def test_a_bad_source_is_refused(self):
        (self.root / 'x.glb').write_bytes(b'not a glb')
        with self.assertRaises(AssetMakeError):
            mesh.make(self.root / 'x.glb', self.root / 'x.nif', {})
        with self.assertRaises(AssetMakeError) as caught:
            mesh.make(self.root / 'x.glb', self.root / 'x.nif', {'collision': 'box'})
        self.assertIn('collision', str(caught.exception))

    def test_preview_draws_the_mesh(self):
        pos, nrm, uv, tri = cube()
        write_glb(self.root / 'cube.glb', pos, tri, nrm, uv)
        mesh.make(self.root / 'cube.glb', self.root / 'cube.nif', {})
        report = render.preview(self.root / 'cube.nif', self.root / 'cube.png')
        img = np.asarray(Image.open(self.root / 'cube.png').convert('RGB'))
        self.assertGreater(int((img != img[0, 0]).any(axis=2).sum()), 1000)
        self.assertEqual(report['triangles'], 12)


class Animations(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def sequence(self, path):
        f = nif.read(Path(path).read_bytes())
        seq = next(b for b in f.blocks if b.type == 'NiControllerSequence')
        return f, seq

    def test_a_kf_from_json_keys(self):
        keys = {'name': 'Wave', 'loop': True, 'length': 1.0, 'text_keys': [[0.5, 'Hit']],
                'nodes': {'Bip01 R UpperArm': {
                    'rotation': [[0, [1, 0, 0, 0]], [0.5, [0.7071, 0, 0, 0.7071]], [1.0, [1, 0, 0, 0]]],
                    'translation': [[0, [0, 0, 0]], [1.0, [0, 0, 5]]]}}}
        (self.root / 'wave.json').write_text(json.dumps(keys), encoding='utf-8')
        report = kf.make(self.root / 'wave.json', self.root / 'wave.kf', {})
        f, seq = self.sequence(self.root / 'wave.kf')
        self.assertEqual((seq['Name'], seq['Cycle Type'], seq['Stop Time']), ('Wave', 0, 1.0))
        names = [b['Node Name'] for b in seq['Controlled Blocks']]
        self.assertEqual(names, ['Bip01 R UpperArm'])
        data = f.blocks[f.blocks[seq['Controlled Blocks'][0]['Interpolator']]['Data']]
        self.assertEqual([k['time'] for k in data['Rotations']['keys']], [0.0, 0.5, 1.0])
        self.assertEqual(data['Translations']['keys'][-1]['value'], (0.0, 0.0, 5.0))
        text = [k['Value'] for k in f.blocks[seq['Text Keys']]['Text Keys']]
        self.assertEqual(text, ['start', 'Hit', 'end'])
        self.assertEqual(report['nodes'], 1)

    def test_key_times_are_cleaned_for_the_engine(self):
        keys = {'name': 'X', 'length': 1.0, 'nodes': {'Bip01': {
            'translation': [[0.2, [0, 0, 0]], [0.2, [1, 0, 0]], [0.6, [2, 0, 0]], [3.0, [3, 0, 0]]]}}}
        (self.root / 'x.json').write_text(json.dumps(keys), encoding='utf-8')
        kf.make(self.root / 'x.json', self.root / 'x.kf', {})
        f, seq = self.sequence(self.root / 'x.kf')
        data = f.blocks[f.blocks[seq['Controlled Blocks'][0]['Interpolator']]['Data']]
        times = [k['time'] for k in data['Translations']['keys']]
        np.testing.assert_allclose(times, [0.0, 0.2, 0.6, 1.0], atol=1e-6)    # held from 0, repeat dropped, clamped

    def test_unknown_skeleton_nodes_are_reported(self):
        skel = nif.new_file()
        root = skel.add('NiNode', Name='Scene Root')
        child = skel.add('NiNode', Name='Bip01')
        skel.blocks[root]['Children'].append(child)
        (self.root / 'skeleton.nif').write_bytes(skel.write())
        keys = {'name': 'X', 'length': 1.0, 'nodes': {'Bip01': {'translation': [[0, [0, 0, 0]]]},
                                                      'Nope': {'translation': [[0, [0, 0, 0]]]}}}
        (self.root / 'x.json').write_text(json.dumps(keys), encoding='utf-8')
        report = kf.make(self.root / 'x.json', self.root / 'x.kf', {'skeleton': str(self.root / 'skeleton.nif')})
        self.assertEqual(report['notInSkeleton'], ['Nope'])

    def test_a_kf_from_a_glb_animation(self):
        pos, nrm, uv, tri = cube()
        nodes = [{'name': 'Root', 'children': [1]}, {'name': 'Arm', 'children': [2]}, {'name': 'Box', 'mesh': 0}]
        write_glb(self.root / 'arm.glb', pos, tri, nrm, uv, nodes=nodes,
                  animation=[(1, 'translation', [0, 1], [[0, 0, 0], [0, 1, 0]])])
        kf.make(self.root / 'arm.glb', self.root / 'arm.kf', {'animation': 'Wave'})
        f, seq = self.sequence(self.root / 'arm.kf')
        self.assertEqual([b['Node Name'] for b in seq['Controlled Blocks']], ['Arm'])
        data = f.blocks[f.blocks[seq['Controlled Blocks'][0]['Interpolator']]['Data']]
        end = data['Translations']['keys'][-1]['value']
        np.testing.assert_allclose(end, (0, 0, UNITS), atol=1e-3)     # glTF y up is game z, metres to units


class OutputPaths(unittest.TestCase):
    def test_out_is_inside_the_data_tree(self):
        root = Path(tempfile.mkdtemp())
        self.assertEqual(convert.target(root, 'meshes\\a\\b.nif', '.nif'), root / 'meshes' / 'a' / 'b.nif')
        for bad in ('..\\x.nif', 'C:\\x.nif', 'meshes\\..\\..\\x.nif', 'meshes\\a.dds'):
            with self.subTest(bad), self.assertRaises(AssetMakeError):
                convert.target(root, bad, '.nif')
        with self.assertRaises(AssetMakeError):
            convert.target(root, 'textures\\a.nif', '.nif')       # a mesh goes under meshes


class ToolActions(unittest.TestCase):
    """fnv_asset make_nif, make_kf, make_dds, extract and preview on an MO2 dev profile laid out in a temp folder."""

    def setUp(self):
        from types import SimpleNamespace
        from unittest import mock
        from fnv_mcp import fnv_layout, fnv_records
        from fnv_mcp.fnv_create import AssetTools
        from fnv_mcp.fnv_data import DataTools
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.mo2, self.data = root / 'MO2', root / 'Game' / 'Data'
        self.profile = self.mo2 / 'profiles' / 'Dev'
        for folder in (self.profile, self.data, self.mo2 / 'mods', root / 'state'):
            folder.mkdir(parents=True)
        (self.data / 'FalloutNV.esm').write_bytes(b'TES4' + struct.pack('<IIIII', 0, 0, 0, 0, 0))
        (self.profile / 'modlist.txt').write_text('# header\n', encoding='utf-8')
        (self.profile / 'plugins.txt').write_text('*FalloutNV.esm\n', encoding='utf-8')
        (self.profile / 'loadorder.txt').write_text('FalloutNV.esm\n', encoding='utf-8')
        self.project = SimpleNamespace(fnv=SimpleNamespace(mo2_root=str(self.mo2), mo2_profile='Dev'),
                                       game_root=str(root / 'Game'), state_dir=str(root / 'state'),
                                       plugin='MyMod.esp')
        patch = mock.patch.object(fnv_layout, 'running_game', lambda project: None)
        patch.start()
        self.addCleanup(patch.stop)
        data = DataTools(self.project)
        self.addCleanup(lambda: data.index.db.close())
        self.journal = []
        self.tools = AssetTools(SimpleNamespace(project=self.project, journal=SimpleNamespace(add=self.journal.append)),
                                fnv_records.RecordsTools(data))
        self.src = root / 'state' / 'src'
        self.src.mkdir()
        pos, nrm, uv, tri = cube()
        write_glb(self.src / 'crate.glb', pos, tri, nrm, uv)

    def test_make_nif_goes_in_the_plugins_mod_folder_and_turns_it_on(self):
        report = self.tools.run('make_nif', path=str(self.src / 'crate.glb'), out='meshes/mymod/crate.nif')
        made = self.mo2 / 'mods' / 'MyMod' / 'meshes' / 'mymod' / 'crate.nif'
        self.assertTrue(made.is_file())
        self.assertTrue((self.mo2 / 'mods' / 'MyMod' / 'textures' / 'mymod' / 'crate_white.dds').is_file())
        self.assertEqual((report['mod'], report['out']), ('MyMod', 'meshes\\mymod\\crate.nif'))
        self.assertIn('+MyMod', (self.profile / 'modlist.txt').read_text(encoding='utf-8'))
        self.assertEqual(report['obnd'][3:], [70, 0, 70])
        self.assertEqual(self.journal[-1]['kind'], 'asset_make')

    def test_options_mod_and_refusals_before_any_write(self):
        self.tools.run('make_nif', path=str(self.src / 'crate.glb'), out='meshes\\a\\b.nif', options={'mod': 'Other'})
        self.assertTrue((self.mo2 / 'mods' / 'Other' / 'meshes' / 'a' / 'b.nif').is_file())
        for out in ('..\\..\\x.nif', 'C:\\x.nif', 'textures\\x.nif'):
            with self.subTest(out), self.assertRaises(ValueError):
                self.tools.run('make_nif', path=str(self.src / 'crate.glb'), out=out, options={'mod': 'Bad'})
        self.assertFalse((self.mo2 / 'mods' / 'Bad').exists())
        with self.assertRaises(ValueError):
            self.tools.run('make_nif', path=str(Path(tempfile.gettempdir()) / 'elsewhere.glb'), out='meshes\\x.nif')
        with self.assertRaises(ValueError):
            self.tools.run('make_nif', path=str(self.src / 'crate.glb'), out='meshes\\x.nif', options={'mod': '..'})

    def test_make_kf_reads_the_skeleton_from_the_load_order(self):
        skel = nif.new_file()
        skel.add('NiNode', Name='Bip01')
        (self.data / 'meshes' / 'test').mkdir(parents=True)
        (self.data / 'meshes' / 'test' / 'skeleton.nif').write_bytes(skel.write())
        keys = {'name': 'X', 'length': 1.0, 'nodes': {'Bip01': {'translation': [[0, [0, 0, 0]]]},
                                                      'Arm': {'translation': [[0, [0, 0, 0]]]}}}
        (self.src / 'x.json').write_text(json.dumps(keys), encoding='utf-8')
        report = self.tools.run('make_kf', path=str(self.src / 'x.json'), out='meshes\\test\\x.kf',
                                options={'skeleton': 'meshes\\test\\skeleton.nif'})
        self.assertEqual(report['notInSkeleton'], ['Arm'])
        self.assertTrue(Path(report['path']).is_file())

    def test_make_dds_extract_and_preview(self):
        Image.new('RGB', (8, 8), (1, 2, 3)).save(self.src / 'a.png')
        report = self.tools.run('make_dds', path=str(self.src / 'a.png'), out='textures\\t\\a.dds')
        self.assertEqual(report['format'], 'DXT1')
        mesh.make(self.src / 'crate.glb', self.data / 'meshes' / 'v' / 'crate.nif', {'texture': 'textures\\x.dds'})
        got = self.tools.run('extract', path='meshes/v/crate.nif')
        self.assertTrue(got['file'].endswith(str(Path('assets', 'extract', 'meshes', 'v', 'crate.nif'))))
        self.assertEqual(Path(got['file']).read_bytes(), (self.data / 'meshes' / 'v' / 'crate.nif').read_bytes())
        shot = self.tools.run('preview', path='meshes\\v\\crate.nif')
        self.assertTrue(Path(shot['file']).is_file())
        self.assertEqual(shot['triangles'], 12)

    def test_making_files_needs_full_control(self):
        from fnv_mcp.fnv_live_tools import FULL_ONLY_ACTIONS
        self.assertEqual(FULL_ONLY_ACTIONS['fnv_asset'], {'make_nif', 'make_kf', 'make_dds'})

    def test_the_direct_install_is_refused_without_the_setting(self):
        from fnv_mcp.fnv_layout import LayoutError
        self.project.fnv = direct_settings()
        with self.assertRaises(LayoutError):
            self.tools.run('make_nif', path=str(self.src / 'crate.glb'), out='meshes\\x.nif')


def direct_settings():
    from types import SimpleNamespace
    return SimpleNamespace(mo2_root=None, mo2_profile=None, allow_direct_install_changes=False)


if __name__ == '__main__':
    unittest.main()
