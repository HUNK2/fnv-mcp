"""Minimal glTF binary reader (numpy): the document, accessors as arrays, node transforms."""
import json
import struct

import numpy as np

COMPONENT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


class Glb:
    def __init__(self, path):
        with open(path, 'rb') as f:
            data = f.read()
        magic, _version, length = struct.unpack_from('<4sII', data, 0)
        if magic != b'glTF':
            raise ValueError('%s is not a glb' % path)
        off, self.doc, self.bin = 12, None, b''
        while off < length:
            clen, ctype = struct.unpack_from('<II', data, off)
            chunk = data[off + 8:off + 8 + clen]
            if ctype == 0x4E4F534A:
                self.doc = json.loads(chunk)
            elif ctype == 0x004E4942:
                self.bin = chunk
            off += 8 + clen
        self.nodes = self.doc['nodes']
        self.parent = {}
        for i, n in enumerate(self.nodes):
            for c in n.get('children', []):
                self.parent[c] = i
        self._globals = {}
        self.by_name = {n.get('name'): i for i, n in enumerate(self.nodes)}

    def accessor(self, index):
        acc = self.doc['accessors'][index]
        dtype = np.dtype(COMPONENT[acc['componentType']])
        width = WIDTH[acc['type']]
        count = acc['count']
        if 'bufferView' not in acc:
            out = np.zeros((count, width), dtype)
        else:
            view = self.doc['bufferViews'][acc['bufferView']]
            start = view.get('byteOffset', 0) + acc.get('byteOffset', 0)
            stride = view.get('byteStride', 0) or dtype.itemsize * width
            if stride == dtype.itemsize * width:
                out = np.frombuffer(self.bin, dtype, count * width, start).reshape(count, width).copy()
            else:
                out = np.array([np.frombuffer(self.bin, dtype, width, start + i * stride) for i in range(count)], dtype)
        if acc.get('normalized') and dtype.kind in 'iu':
            out = out.astype(np.float32) / np.iinfo(dtype).max
        return out[:, 0] if width == 1 else out

    def scene_roots(self):
        return self.doc['scenes'][self.doc.get('scene', 0)]['nodes']

    def tree(self):
        """[(node, parent node or None)] of every node the scene reaches, depth first in file order."""
        out = []

        def walk(i, parent):
            out.append((i, parent))
            for c in self.nodes[i].get('children', []):
                walk(c, i)

        for r in self.scene_roots():
            walk(r, None)
        return out

    def base(self):
        """The scene root the body stands for (body._add_model hangs its children straight under the NIF parent and
        drops its own transform), or None when the scene has several roots or its root is a mesh: then each root
        is a node of its own."""
        roots = self.scene_roots()
        return roots[0] if len(roots) == 1 and 'mesh' not in self.nodes[roots[0]] else None

    def bones(self):
        """The non-mesh nodes the body NIF holds as NiNodes (the ones clips can key): every node the scene reaches,
        but the base root."""
        base = self.base()
        return [i for i, _ in self.tree() if i != base and 'mesh' not in self.nodes[i]]

    def local(self, i):
        return node_matrix(self.nodes[i])

    def world(self, i):
        if i not in self._globals:
            m = self.local(i)
            self._globals[i] = m if i not in self.parent else self.world(self.parent[i]) @ m
        return self._globals[i]


def quat_to_matrix(x, y, z, w):
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def node_matrix(node):
    """A node's local transform as a 4x4 matrix (column vectors)."""
    if 'matrix' in node:
        return np.array(node['matrix'], np.float64).reshape(4, 4).T
    t = np.array(node.get('translation', [0, 0, 0]), np.float64)
    s = np.array(node.get('scale', [1, 1, 1]), np.float64)
    m = np.eye(4)
    m[:3, :3] = quat_to_matrix(*node.get('rotation', [0, 0, 0, 1])) * s
    m[:3, 3] = t
    return m


def node_trs(node):
    """(translation, 3x3 rotation, scale xyz) of a node."""
    if 'matrix' in node:
        m = node_matrix(node)
        s = np.linalg.norm(m[:3, :3], axis=0)
        return m[:3, 3].copy(), m[:3, :3] / s, s
    t = np.array(node.get('translation', [0, 0, 0]), np.float64)
    s = np.array(node.get('scale', [1, 1, 1]), np.float64)
    return t, quat_to_matrix(*node.get('rotation', [0, 0, 0, 1])), s
