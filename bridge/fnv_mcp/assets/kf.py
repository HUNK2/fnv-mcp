"""A New Vegas animation (KF) from named node keys (.json) or a glTF animation (.glb), ported from the robot
project's clips.py.

The KF is laid out as vanilla ones are: one NiControllerSequence; per keyed node an NiTransformInterpolator with
NiTransformData (linear keys) driven through an NiTransformController; an NiTextKeyExtraData with "start", the given
keys and "end"; "Bip01" as the accumulation root.

FNV's key lookups (0x00A27490, 0x00A28740) walk from a cached index to the first key at or after the time and divide
by the gap to the key before it, with no check for the last key, so every channel is written so the engine can read
it at any time from 0 to the Stop Time: float32 times, strictly increasing (where a key falls at or before an earlier
one, the later key wins), none past the Stop Time, and a channel of two or more keys held from 0 to the Stop Time.

JSON keys, in the game's axes and units:
    {"name": "Wave", "loop": true, "length": 1.0, "priority": 30, "text_keys": [[0.5, "Hit"]],
     "nodes": {"Bip01 R UpperArm": {"rotation": [[0, [w, x, y, z]], ...], "translation": [[0, [x, y, z]], ...],
                                     "scale": [[0, s], ...]}}}
From a .glb, options.animation picks the animation (default the first); its keys are turned to the game's axes and
units like a mesh (options.up, options.scale). options.skeleton (a NIF) lists node names the KF keys that the
skeleton does not have. Not made: blending from a rest pose for nodes the clip leaves out (they keep what the last
clip set), animation of anything but transforms."""
import json
from pathlib import Path

import numpy as np

from . import gltf, mesh, nif
from .errors import AssetMakeError

ACCUM_ROOT = 'Bip01'
PRIORITY = 30
KNOWN = {'name', 'loop', 'length', 'priority', 'text_keys', 'nodes', 'accum_root'}


def f32(x):
    return float(np.float32(x))


def clean_keys(times, values, stop):
    """[(time, value)] as the engine needs them (module docstring)."""
    stop = f32(stop)
    out = []
    for t, v in zip(times, values):
        t = min(max(f32(t), 0.0), stop)
        while out and out[-1][0] >= t:
            out.pop()
        out.append((t, v))
    if len(out) > 1:
        if out[0][0] > 0.0:
            out.insert(0, (0.0, out[0][1]))
        if out[-1][0] < stop:
            out.append((stop, out[-1][1]))
    return out


def continuous(quats):
    """Quaternions (w, x, y, z), each in the hemisphere of the one before (q and -q are the same rotation)."""
    out = []
    for q in quats:
        q = np.asarray(q, np.float64)
        n = np.linalg.norm(q)
        q = q / n if n > 0 else np.array([1.0, 0, 0, 0])
        if out and float(np.dot(out[-1], q)) < 0:
            q = -q
        out.append(q)
    return [tuple(float(c) for c in q) for q in out]


def _block(f, name, chs, stop, priority):
    """A controlled block for one node. chs: {'translation': [(t, xyz)], 'rotation': [(t, wxyz)],
    'scale': [(t, s)]}."""
    data = {'Rotations': {'type': 0, 'keys': [], 'xyz': None},
            'Translations': {'interpolation': 0, 'keys': []},
            'Scales': {'interpolation': 0, 'keys': []}}
    tr, rot, sc = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0), 1.0
    if chs.get('translation'):
        keys = clean_keys(*zip(*chs['translation']), stop)
        data['Translations'] = {'interpolation': 1, 'keys': [
            {'time': t, 'value': tuple(float(x) for x in v)} for t, v in keys]}
        tr = data['Translations']['keys'][0]['value']
    if chs.get('rotation'):
        keys = clean_keys(*zip(*chs['rotation']), stop)
        quats = continuous([v for _, v in keys])
        data['Rotations'] = {'type': 1, 'keys': [{'time': t, 'value': q} for (t, _), q in zip(keys, quats)],
                             'xyz': None}
        rot = quats[0]
    if chs.get('scale'):
        keys = clean_keys(*zip(*chs['scale']), stop)
        data['Scales'] = {'interpolation': 1, 'keys': [{'time': t, 'value': float(v)} for t, v in keys]}
        sc = float(keys[0][1])
    d = f.add('NiTransformData', **data)
    ip = f.add('NiTransformInterpolator', Translation=tr, Rotation=rot, Scale=sc, Data=d)
    return {'Interpolator': ip, 'Controller': -1, 'Priority': priority, 'Node Name': name,
            'Property Type': None, 'Controller Type': 'NiTransformController', 'Controller ID': None,
            'Interpolator ID': None}


def _number(value, what):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not np.isfinite(value):
        raise AssetMakeError('%s is not a number' % what)
    return float(value)


def _vector(value, width, what):
    if width == 1:
        return _number(value, what)
    if not isinstance(value, (list, tuple)) or len(value) != width:
        raise AssetMakeError('%s is a list of %d numbers' % (what, width))
    return tuple(_number(v, what) for v in value)


def from_json(src):
    """(name, loop, length, priority, text keys, {node: channels}, accum root) from a keys file."""
    try:
        doc = json.loads(Path(src).read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssetMakeError('%s is not a JSON keys file: %s' % (Path(src).name, exc)) from exc
    if not isinstance(doc, dict) or not isinstance(doc.get('nodes'), dict) or not doc['nodes']:
        raise AssetMakeError('a keys file is an object with "nodes": {node name: {"rotation"|"translation"|"scale": '
                             '[[time, value], ...]}}')
    unknown = sorted(set(doc) - KNOWN)
    if unknown:
        raise AssetMakeError('unknown keys-file fields %s; known: %s' % (unknown, sorted(KNOWN)))
    widths = {'rotation': 4, 'translation': 3, 'scale': 1}
    nodes = {}
    for node, chs in doc['nodes'].items():
        if not isinstance(chs, dict) or not chs or set(chs) - set(widths):
            raise AssetMakeError('node %r: channels are rotation [w, x, y, z], translation [x, y, z], scale s' % node)
        nodes[node] = {}
        for path, keys in chs.items():
            if not isinstance(keys, list) or not keys:
                raise AssetMakeError('node %r %s: a list of [time, value]' % (node, path))
            nodes[node][path] = [(_number(k[0], '%s %s time' % (node, path)),
                                  _vector(k[1], widths[path], '%s %s value' % (node, path)))
                                 if isinstance(k, (list, tuple)) and len(k) == 2 else
                                 _bad_key(node, path) for k in keys]
    text = []
    for k in doc.get('text_keys') or []:
        if not (isinstance(k, (list, tuple)) and len(k) == 2 and isinstance(k[1], str)):
            raise AssetMakeError('text_keys is a list of [time, "text"]')
        text.append((_number(k[0], 'text key time'), k[1]))
    length = doc.get('length')
    return (str(doc.get('name') or Path(src).stem), bool(doc.get('loop', False)),
            None if length is None else _number(length, 'length'), int(doc.get('priority', PRIORITY)), text, nodes,
            str(doc.get('accum_root') or ACCUM_ROOT))


def _bad_key(node, path):
    raise AssetMakeError('node %r %s: each key is [time, value]' % (node, path))


def from_glb(src, options):
    """{node name: channels} of one glTF animation, in the game's axes and units."""
    g = mesh.read_glb(src)
    anims = g.doc.get('animations') or []
    if not anims:
        raise AssetMakeError('%s has no animation' % Path(src).name)
    want = options.get('animation')
    anim = anims[0] if not want else next((a for a in anims if a.get('name') == want), None)
    if anim is None:
        raise AssetMakeError('no animation %r; the file has %s' % (want, [a.get('name') for a in anims]))
    amap, scale = mesh.axes(options)
    nodes = {}
    for ch in anim['channels']:
        i = ch['target'].get('node')
        path = ch['target']['path']
        if i is None or path not in ('translation', 'rotation', 'scale'):
            continue
        s = anim['samplers'][ch['sampler']]
        if s.get('interpolation', 'LINEAR') == 'CUBICSPLINE':
            raise AssetMakeError('cubic spline keys are not read; export with linear interpolation')
        times = g.accessor(s['input']).astype(np.float64)
        vals = g.accessor(s['output']).astype(np.float64)
        name = g.nodes[i].get('name') or 'node%d' % i
        if path == 'translation':
            keys = [(t, tuple(amap @ v * scale)) for t, v in zip(times, vals)]
        elif path == 'rotation':
            keys = [(t, (v[3],) + tuple(amap @ v[:3])) for t, v in zip(times, vals)]   # xyzw -> w, then axes
        else:
            keys = [(t, float(np.mean(v))) for t, v in zip(times, vals)]
        nodes.setdefault(name, {})[path] = keys
    if not nodes:
        raise AssetMakeError('animation %r keys no node transforms' % anim.get('name'))
    return anim.get('name') or Path(src).stem, nodes


def skeleton_names(data):
    return {b.fields.get('Name') for b in nif.read(data).blocks if b.fields is not None and b.fields.get('Name')}


def make(src, out, options=None, skeleton_bytes=None):
    """Write the KF `out` from `src` (.json keys or .glb). skeleton_bytes: the NIF options.skeleton names, read by
    the caller (a Data path or a file). Returns a report."""
    options = dict(options or {})
    suffix = Path(src).suffix.lower()
    if suffix == '.json':
        name, loop, length, priority, text, nodes, accum = from_json(src)
    elif suffix == '.glb':
        name, nodes = from_glb(src, options)
        loop, length, priority, text, accum = False, None, PRIORITY, [], ACCUM_ROOT
    else:
        raise AssetMakeError('an animation is made from a keys file (.json) or a glTF binary (.glb)')
    name = options.get('name') or name
    loop = bool(options.get('loop', loop))
    priority = int(options.get('priority', priority))
    if not 0 <= priority <= 255:
        raise AssetMakeError('priority is 0 to 255')
    for t, value in options.get('text_keys') or []:
        text.append((_number(t, 'text key time'), str(value)))
    if options.get('skeleton') and skeleton_bytes is None:
        skeleton_bytes = Path(options['skeleton']).read_bytes()
    end = length if length is not None else max(t for chs in nodes.values() for keys in chs.values()
                                                for t, _ in keys)
    if end <= 0:
        raise AssetMakeError('the animation is 0 seconds long; give "length"')
    end = f32(end)
    f = nif.new_file()
    seq = f.add('NiControllerSequence', Name=name, **{'Cycle Type': 0 if loop else 2, 'Accum Root Name': accum})
    blocks = [_block(f, node, chs, end, priority) for node, chs in sorted(nodes.items())]
    keys = [{'Time': 0.0, 'Value': 'start'}] + [{'Time': min(f32(t), end), 'Value': v} for t, v in text] + \
           [{'Time': end, 'Value': 'end'}]
    keys.sort(key=lambda k: k['Time'])
    tk = f.add('NiTextKeyExtraData', **{'Text Keys': keys})
    f.blocks[seq]['Controlled Blocks'] = blocks
    f.blocks[seq]['Text Keys'] = tk
    f.blocks[seq]['Stop Time'] = end
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(f.write())
    report = {'file': str(out), 'sequence': name, 'nodes': len(blocks), 'length': end, 'loop': loop,
              'priority': priority, 'textKeys': [k['Value'] for k in keys], 'warnings': []}
    if accum not in nodes:
        report['warnings'].append('%s (the accumulation root) is not keyed: fine for an idle or a gesture; a clip '
                                  'that moves the actor must key it' % accum)
    if skeleton_bytes is not None:
        try:
            names = skeleton_names(skeleton_bytes)
        except nif.NifError as exc:
            raise AssetMakeError('options.skeleton is not a NIF this reads: %s' % exc) from exc
        report['notInSkeleton'] = sorted(n for n in nodes if n not in names)
    return report
