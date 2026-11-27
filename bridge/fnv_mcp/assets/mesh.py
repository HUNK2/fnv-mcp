"""A static New Vegas mesh (NIF) from a glTF binary (.glb), ported from the robot project's body.py.

The NIF is laid out as vanilla static meshes are: a BSFadeNode root named after the file, a BSXFlags, and one
NiTriShape per glTF primitive under the root, each node's transform baked into its vertices. Each shape gets its own
BSShaderPPLightingProperty (the engine keeps per-geometry passes in the shader property, so a shared one draws only
one shape), a BSShaderTextureSet (diffuse, normal) and an NiMaterialProperty; NiTriShapeData holds normals, tangents,
bitangents and one UV set (BS Data Flags 0x1001). A primitive with more than 65,535 vertices or triangles is split.

Axes and units: glTF is y up in metres; the game is z up in game units (about 70 per metre). By default a point
(x, y, z) becomes (x, -z, y) * 69.99, a proper rotation, so triangles keep their winding. options.up = "z" keeps the
axes (a source already z up); options.scale sets the game units per source unit.

Textures: the base colour texture of a primitive's material (embedded or beside the .glb) is written as a DXT DDS
under textures\\<the mesh's folder below meshes>\\<image name>.dds, and its normal texture (or a flat one) as
<image name>_n.dds. options.texture names a diffuse DDS path instead (nothing is written for it). A material with
no texture gets a 4x4 texture of its base colour factor. Not made yet: collision (the mesh has none; the player
walks through it), skinned meshes (a skin is drawn at its rest pose, rigid)."""
import io
import struct
from pathlib import Path, PureWindowsPath

import numpy as np
from PIL import Image

from . import dds, gltf, nif
from .errors import AssetMakeError

UNITS_PER_M = 1 / 0.0142875
NODE_FLAGS = 0x8000E
SHAPE_FLAGS = 0x8000E
SHADER_FLAGS = 0x82000001       # specular, shadow map, z test (vanilla rigid meshes)
SHADER_FLAGS_2 = 0x1            # z write
MAX_SHAPE = 65535               # NiTriShapeData's Num Vertices and Num Triangles are ushorts
Y_UP_TO_Z_UP = np.array([[1.0, 0, 0], [0, 0, -1.0], [0, 1.0, 0]])    # (x, y, z) -> (x, -z, y)
KNOWN_OPTIONS = {'scale', 'up', 'texture', 'normal_texture', 'collision', 'mod', 'name'}


def _vec(v):
    return tuple(float(x) for x in v)


def _normalise(v):
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1
    return v / n


def tangents(pos, nrm, uv, tri):
    """Per-vertex tangent (along +u) and bitangent (along +v), orthogonal to the normal."""
    t = np.zeros_like(pos)
    b = np.zeros_like(pos)
    p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    w0, w1, w2 = uv[tri[:, 0]], uv[tri[:, 1]], uv[tri[:, 2]]
    e1, e2 = p1 - p0, p2 - p0
    d1, d2 = w1 - w0, w2 - w0
    r = d1[:, 0] * d2[:, 1] - d2[:, 0] * d1[:, 1]
    r = np.where(np.abs(r) < 1e-12, 1e-12, r)
    sdir = (e1 * d2[:, 1:2] - e2 * d1[:, 1:2]) / r[:, None]
    tdir = (e2 * d1[:, 0:1] - e1 * d2[:, 0:1]) / r[:, None]
    for k in range(3):
        np.add.at(t, tri[:, k], sdir)
        np.add.at(b, tri[:, k], tdir)
    t = _normalise(t - nrm * np.sum(nrm * t, axis=1, keepdims=True))
    b = _normalise(b - nrm * np.sum(nrm * b, axis=1, keepdims=True))
    return t, b


def split_triangles(tri, vertex_count, limit=MAX_SHAPE):
    """Triangle index ranges, in order, each using at most `limit` vertices and `limit` triangles."""
    if vertex_count <= limit and len(tri) <= limit:
        return [np.arange(len(tri))]
    chunks, start = [], 0
    while start < len(tri):
        used, end = set(), start
        while end < len(tri) and end - start < limit:
            new = set(int(k) for k in tri[end]) - used
            if len(used) + len(new) > limit:
                break
            used |= new
            end += 1
        chunks.append(np.arange(start, end))
        start = end
    return chunks


def _flat_normals(pos, tri):
    """Area-weighted vertex normals, for a primitive with none."""
    n = np.zeros_like(pos)
    face = np.cross(pos[tri[:, 1]] - pos[tri[:, 0]], pos[tri[:, 2]] - pos[tri[:, 0]])
    for k in range(3):
        np.add.at(n, tri[:, k], face)
    return _normalise(n)


class Materials:
    """Texture paths per glTF material; each texture is written once."""

    def __init__(self, g, src, data_root, tex_dir, options, report):
        self.g, self.src, self.data_root, self.tex_dir = g, Path(src), data_root, tex_dir
        self.options, self.report = options, report
        self.cache = {}

    def _image(self, index):
        """(name, PIL image) of a glTF image: embedded in a buffer view, or a file beside the .glb."""
        img = self.g.doc['images'][index]
        if 'bufferView' in img:
            view = self.g.doc['bufferViews'][img['bufferView']]
            start = view.get('byteOffset', 0)
            data = self.g.bin[start:start + view['byteLength']]
            name = img.get('name') or '%s_%d' % (self.src.stem, index)
            return Path(name).stem, Image.open(io.BytesIO(data))
        uri = img.get('uri', '')
        if uri.startswith('data:') or not uri:
            raise AssetMakeError('image %d: a data: URI or empty image is not read; embed it in the .glb' % index)
        path = (self.src.parent / uri).resolve()
        if not path.is_relative_to(self.src.parent.resolve()):
            raise AssetMakeError('image %d: %s is outside the .glb\'s folder' % (index, uri))
        return path.stem, dds.open_image(path)

    def _write(self, name, rgba, fmt=None):
        rel = '%s\\%s.dds' % (self.tex_dir, name)
        if self.data_root is None:
            self.report['warnings'].append('%s not written: the mesh has no Data folder (out is not under meshes\\)'
                                           % rel)
            return rel
        if rel not in self.report['textures']:
            dds.write(self.data_root / PureWindowsPath(rel), rgba, fmt, self.report['warnings'])
            self.report['textures'].append(rel)
        return rel

    def paths(self, index):
        """(diffuse, normal) texture paths of a material (None: no material)."""
        if index in self.cache:
            return self.cache[index]
        if self.options.get('texture'):
            out = (self.options['texture'], self.options.get('normal_texture') or '')
            self.cache[index] = out
            return out
        mat = self.g.doc['materials'][index] if index is not None else {}
        pbr = mat.get('pbrMetallicRoughness', {})
        if 'baseColorTexture' in pbr:
            name, img = self._image(self.g.doc['textures'][pbr['baseColorTexture']['index']]['source'])
        else:
            name = '%s_%s' % (self.src.stem, 'mat%d' % index if index is not None else 'white')
            color = np.clip(np.array(pbr.get('baseColorFactor', [1, 1, 1, 1]), np.float64) * 255, 0, 255)
            img = Image.fromarray(np.tile(color.astype(np.uint8), (4, 4, 1)), 'RGBA')
        diffuse = self._write(name, dds.rgba_of(img))
        if 'normalTexture' in mat:
            _, nimg = self._image(self.g.doc['textures'][mat['normalTexture']['index']]['source'])
            normal = self._write(name + '_n', dds.rgba_of(nimg, normal=True), 'DXT5')
        else:
            flat = np.zeros((4, 4, 4), np.uint8)
            flat[:, :] = (128, 128, 255, 32)
            normal = self._write(name + '_n', flat, 'DXT5')
        self.cache[index] = (diffuse, normal)
        return self.cache[index]


def _properties(f, diffuse, normal):
    tset = f.add('BSShaderTextureSet', Textures=[diffuse, normal, '', '', '', ''])
    shader = f.add('BSShaderPPLightingProperty', Flags=1, **{
        'Shader Type': 1, 'Shader Flags': SHADER_FLAGS, 'Shader Flags 2': SHADER_FLAGS_2, 'Texture Set': tset})
    material = f.add('NiMaterialProperty', **{'Specular Color': (1.0, 1.0, 1.0), 'Glossiness': 60.0})
    return [shader, material]


def _add_shape(f, parent, name, pos, nrm, uv, tri, props):
    t, b = tangents(pos, nrm, uv, tri)
    center = (pos.min(axis=0) + pos.max(axis=0)) / 2
    radius = float(np.max(np.linalg.norm(pos - center, axis=1))) if len(pos) else 0.0
    data = f.add('NiTriShapeData', **{
        'Vertices': [_vec(v) for v in pos], 'BS Data Flags': 0x1001, 'Has Normals': 1,
        'Normals': [_vec(v) for v in nrm], 'Tangents': [_vec(v) for v in t], 'Bitangents': [_vec(v) for v in b],
        'Center': _vec(center), 'Radius': radius, 'UV Sets': [_vec(v) for v in uv],
        'Num Triangles': len(tri), 'Triangles': [tuple(int(k) for k in r) for r in tri]})
    shape = f.add('NiTriShape', Name=name, Flags=SHAPE_FLAGS, Properties=list(props), Data=data,
                  **{'Active Material': 0})
    f.blocks[parent]['Children'].append(shape)
    return shape


def _primitive(g, prim):
    a = prim['attributes']
    if 'POSITION' not in a:
        raise AssetMakeError('a primitive has no POSITION')
    if prim.get('mode', 4) != 4:
        raise AssetMakeError('only triangle primitives are read (mode 4), not mode %d' % prim['mode'])
    pos = g.accessor(a['POSITION']).astype(np.float64)
    idx = g.accessor(prim['indices']).astype(np.int64) if 'indices' in prim else np.arange(len(pos))
    tri = idx.reshape(-1, 3)
    nrm = g.accessor(a['NORMAL']).astype(np.float64) if 'NORMAL' in a else _flat_normals(pos, tri)
    uv = g.accessor(a['TEXCOORD_0']).astype(np.float64) if 'TEXCOORD_0' in a else np.zeros((len(pos), 2))
    return pos, nrm, uv, tri


def texture_dir(rel):
    """textures\\<the mesh's folder below meshes>, from the mesh's Data-relative path."""
    parts = PureWindowsPath(rel).parts
    below = parts[1:-1] if parts and parts[0].lower() == 'meshes' else parts[:-1]
    return '\\'.join(('textures',) + tuple(below))


def read_glb(src):
    try:
        return gltf.Glb(src)
    except (ValueError, KeyError, IndexError, OSError, TypeError, struct.error) as exc:
        raise AssetMakeError('%s is not a glTF binary (.glb) this reads: %s' % (Path(src).name, exc)) from exc


def axes(options):
    """(3x3 axis map, game units per source unit) from options.up and options.scale."""
    up = str(options.get('up') or 'y').lower()
    if up not in ('y', 'z'):
        raise AssetMakeError('options.up is "y" (glTF, the default) or "z" (already the game\'s axes)')
    scale = options.get('scale', UNITS_PER_M)
    if not isinstance(scale, (int, float)) or isinstance(scale, bool) or not 0 < scale < 1e6:
        raise AssetMakeError('options.scale is the game units per source unit, a positive number')
    return (Y_UP_TO_Z_UP if up == 'y' else np.eye(3)), float(scale)


def make(src, out, options=None, rel=None):
    """Write the NIF `out` from the .glb `src`. rel: out's Data-relative path (meshes\\...), so textures go beside
    it under the same Data folder. Returns a report."""
    options = dict(options or {})
    if options.get('collision'):
        raise AssetMakeError('options.collision: collision is not made yet. Leave it out: the mesh is made with no '
                             'collision (the player and physics pass through it).')
    if Path(src).suffix.lower() != '.glb':
        raise AssetMakeError('a mesh is made from a glTF binary (.glb); export one from Blender (File > Export > '
                             'glTF 2.0, format glTF Binary)')
    amap, scale = axes(options)
    g = read_glb(src)
    out = Path(out)
    data_root = None
    if rel is not None:
        depth = len(PureWindowsPath(rel).parts)
        data_root = out.parents[depth - 1]
    report = {'file': str(out), 'shapes': 0, 'vertices': 0, 'triangles': 0, 'textures': [], 'warnings': []}
    mats = Materials(g, src, data_root, texture_dir(rel or out.name), options, report)
    f = nif.new_file()
    root = f.add('BSFadeNode', Name=out.stem, Flags=NODE_FLAGS)
    bsx = f.add('BSXFlags', Name='BSX', **{'Integer Data': 0})
    f.blocks[root]['Extra Data List'] = [bsx]
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    for i, _parent in g.tree():
        node = g.nodes[i]
        if 'mesh' not in node:
            continue
        if 'skin' in node:
            report['warnings'].append('%s: skinned; drawn rigid at its rest pose' % node.get('name', 'node %d' % i))
        m = g.world(i)
        lin = m[:3, :3]
        flip = np.linalg.det(lin) < 0
        try:
            normal_m = np.linalg.inv(lin).T
        except np.linalg.LinAlgError:
            raise AssetMakeError('%s has a zero scale (a singular transform), so it cannot be converted'
                                 % (node.get('name') or 'node %d' % i)) from None
        name = node.get('name') or 'mesh%d' % i
        for k, prim in enumerate(g.doc['meshes'][node['mesh']]['primitives']):
            pos, nrm, uv, tri = _primitive(g, prim)
            p = (pos @ lin.T + m[:3, 3]) @ amap.T * scale
            n = _normalise(_normalise(nrm @ normal_m.T) @ amap.T)
            if flip:
                tri = tri[:, ::-1]
            diffuse, normal = mats.paths(prim.get('material'))
            chunks = split_triangles(tri, len(p))
            for c, sel in enumerate(chunks):
                used, inv = np.unique(tri[sel], return_inverse=True)
                shape_name = '%s:%d' % (name, k) if len(chunks) == 1 else '%s:%d/%d' % (name, k, c)
                _add_shape(f, root, shape_name, p[used], n[used], uv[used], inv.reshape(-1, 3),
                           _properties(f, diffuse, normal))
                report['shapes'] += 1
            report['vertices'] += len(p)
            report['triangles'] += len(tri)
            lo, hi = np.minimum(lo, p.min(axis=0)), np.maximum(hi, p.max(axis=0))
    if not report['shapes']:
        raise AssetMakeError('%s has no mesh in its scene' % Path(src).name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(f.write())
    report['bounds'] = {'min': [round(float(v), 2) for v in lo], 'max': [round(float(v), 2) for v in hi]}
    report['size'] = [round(float(v), 2) for v in hi - lo]
    report['obnd'] = [int(np.floor(v)) for v in lo] + [int(np.ceil(v)) for v in hi]   # a record's bounds
    report['collision'] = None
    return report
