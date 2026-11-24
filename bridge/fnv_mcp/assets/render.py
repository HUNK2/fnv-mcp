"""An offline look at a NIF (ported from the robot project's work/render.py): every NiTriShape placed by its node
chain, drawn front (x-z, looking along +y) and side (y-z, looking along -x), flat shaded, in painter's order, with
the size in game units written under the panels. No textures, no skinning, no animation: a check of shape, size and
place, not of looks (fnv_photo shows the game's own render)."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import nif
from .errors import AssetMakeError

SIZE = 360


def _local(b):
    m = np.eye(4)
    m[:3, :3] = np.array(b['Rotation'], np.float64).reshape(3, 3) * b['Scale']
    m[:3, 3] = b['Translation']
    return m


def triangles(f):
    """World-space triangles (N x 3 x 3) of every NiTriShape (rotation row-major as stored)."""
    parent = {}
    for i, b in enumerate(f.blocks):
        if b.fields and 'Children' in b.fields:
            for c in b['Children']:
                if c >= 0:
                    parent[c] = i
    cache = {}

    def world(i):
        if i not in cache:
            cache[i] = (world(parent[i]) if i in parent else np.eye(4)) @ _local(f.blocks[i])
        return cache[i]

    tris, shapes = [], 0
    for i, b in enumerate(f.blocks):
        if b.type != 'NiTriShape' or b.fields is None or b['Data'] < 0:
            continue
        d = f.blocks[b['Data']]
        if d.fields is None or not d.fields.get('Triangles'):
            continue
        v = np.array(d['Vertices'], np.float64)
        t = np.array([tuple(x) for x in d['Triangles']], np.int64)
        m = world(i)
        tris.append((v @ m[:3, :3].T + m[:3, 3])[t])
        shapes += 1
    return (np.concatenate(tris) if tris else np.zeros((0, 3, 3))), shapes


def preview(path, out, data=None):
    """Draw the NIF at `path` (or its bytes `data`) to the PNG `out`. Returns a report."""
    try:
        f = nif.read(data if data is not None else Path(path).read_bytes())
    except nif.NifError as exc:
        raise AssetMakeError('%s is not a NIF this reads: %s' % (Path(path).name, exc)) from exc
    allt, shapes = triangles(f)
    if not len(allt):
        raise AssetMakeError('%s has no triangle shapes to draw (NiTriStrips and skinned parts are not drawn)'
                             % Path(path).name)
    pts = allt.reshape(-1, 3)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    img = Image.new('RGB', (SIZE * 2, SIZE + 24), (40, 40, 48))
    draw = ImageDraw.Draw(img)
    span = max(float((hi - lo).max()), 1e-6) * 1.1
    centre = (lo + hi) / 2
    light = np.array([0.3, -0.8, 0.5])
    light /= np.linalg.norm(light)
    n = np.cross(allt[:, 1] - allt[:, 0], allt[:, 2] - allt[:, 0])
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-9)
    shade = np.abs(n @ light)
    for panel, (ax, depth, sign) in enumerate([(0, 1, 1), (1, 0, -1)]):
        order = np.argsort(-allt[:, :, depth].mean(axis=1) * sign)
        for k in order:
            tri = allt[k]
            xs = (tri[:, ax] - centre[ax]) * sign / span * SIZE + SIZE / 2 + panel * SIZE
            ys = SIZE / 2 - (tri[:, 2] - centre[2]) / span * SIZE
            c = int(60 + 180 * shade[k])
            draw.polygon(list(zip(xs.tolist(), ys.tolist())), fill=(c, c, int(c * 0.6)))
    size = hi - lo
    draw.text((6, SIZE + 6), 'front (x-z)   size %.1f x %.1f x %.1f units' % tuple(size), fill=(220, 220, 220))
    draw.text((SIZE + 6, SIZE + 6), 'side (y-z)', fill=(220, 220, 220))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return {'file': str(out), 'shapes': shapes, 'triangles': int(len(allt)),
            'bounds': {'min': [round(float(v), 2) for v in lo], 'max': [round(float(v), 2) for v in hi]},
            'size': [round(float(v), 2) for v in size]}
