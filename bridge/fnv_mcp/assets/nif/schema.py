"""Block layouts for New Vegas (20.2.0.7, Bethesda version 34): each type is a list of field operations.

F(name, kind)            one value
A(name, kind, count)     an array; `count` names the field holding its length (kept in step on write)
when=lambda f: ...       the field exists only when the condition on the fields read so far holds
Custom ops read and write the key groups and other composite fields.

Kinds are the primitive types in stream.FORMATS, plus 'string' (an index into the header's string table, held as a
Python str or None), 'sized' (a length-prefixed string inside the block) and the composite kinds below.
Layouts were written from the Gamebryo 20.2 format as Bethesda used it and are checked against vanilla files
(tests/test_nif_roundtrip.py: every block reads to exactly its stored size and writes back byte for byte).
"""

_DEFAULTS = {
    'byte': 0, 'bool': 0, 'ushort': 0, 'short': 0, 'uint': 0, 'int': 0, 'float': 0.0, 'ref': -1, 'ptr': -1,
    'string': None, 'sized': '', 'vec3': (0.0, 0.0, 0.0), 'vec4': (0.0, 0.0, 0.0, 0.0), 'quat': (1.0, 0.0, 0.0, 0.0),
    'mat33': (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0), 'color3': (0.0, 0.0, 0.0), 'color4': (0.0, 0.0, 0.0, 0.0),
    'texcoord': (0.0, 0.0), 'byte4': (0, 0, 0, 0), 'triangle': (0, 0, 0),
}


class F:
    def __init__(self, name, kind, default=None, when=None):
        self.name, self.kind, self.when = name, kind, when
        self.default = _DEFAULTS.get(kind) if default is None else default

    def read(self, r, f, ctx):
        f[self.name] = ctx.read_value(r, self.kind)

    def write(self, w, f, ctx):
        ctx.write_value(w, self.kind, f[self.name])

    def make_default(self, f):
        if self.when is None or self.when(f):
            f[self.name] = self.default


class A:
    def __init__(self, name, kind, count, when=None, sync=True):
        self.name, self.kind, self.count, self.when, self.sync = name, kind, count, when, sync

    def n(self, f):
        return self.count(f) if callable(self.count) else f[self.count]

    def read(self, r, f, ctx):
        n = self.n(f)
        if n < 0 or n > 10_000_000:
            raise EOFError('array %s has a bad length %d' % (self.name, n))
        if self.kind in ('string', 'sized') or self.kind in COMPOSITE:
            f[self.name] = [ctx.read_value(r, self.kind) for _ in range(n)]
        else:
            f[self.name] = r.array(self.kind, n)

    def write(self, w, f, ctx):
        values = f[self.name]
        if self.kind in ('string', 'sized') or self.kind in COMPOSITE:
            for v in values:
                ctx.write_value(w, self.kind, v)
        else:
            w.array(self.kind, list(values))

    def make_default(self, f):
        if self.when is None or self.when(f):
            f[self.name] = []


class KeyGroup:
    """Num keys (uint); when there are keys, the interpolation (uint: 1 linear, 2 quadratic, 3 TBC, 5 constant);
    each key: time, value, and for quadratic the forward and backward tangents, for TBC three floats."""

    def __init__(self, name, kind, when=None):
        self.name, self.kind, self.when = name, kind, when

    def read(self, r, f, ctx):
        n = r.get('uint')
        group = {'interpolation': 0, 'keys': []}
        if n:
            group['interpolation'] = r.get('uint')
            for _ in range(n):
                key = {'time': r.get('float'), 'value': ctx.read_value(r, self.kind)}
                if group['interpolation'] == 2:
                    key['forward'] = ctx.read_value(r, self.kind)
                    key['backward'] = ctx.read_value(r, self.kind)
                elif group['interpolation'] == 3:
                    key['tbc'] = (r.get('float'), r.get('float'), r.get('float'))
                group['keys'].append(key)
        f[self.name] = group

    def write(self, w, f, ctx):
        group = f[self.name]
        keys = group['keys']
        w.put('uint', len(keys))
        if keys:
            w.put('uint', group['interpolation'])
            for key in keys:
                w.put('float', key['time'])
                ctx.write_value(w, self.kind, key['value'])
                if group['interpolation'] == 2:
                    ctx.write_value(w, self.kind, key['forward'])
                    ctx.write_value(w, self.kind, key['backward'])
                elif group['interpolation'] == 3:
                    for v in key['tbc']:
                        w.put('float', v)

    def make_default(self, f):
        f[self.name] = {'interpolation': 0, 'keys': []}


class RotationKeys:
    """NiKeyframeData rotations: num keys (uint); when there are keys, the type (uint); types 1-3 store each key's
    time and quaternion (w, x, y, z), plus three floats for TBC (3); type 4 (XYZ) stores three float key groups
    instead (and the count is 1)."""
    name = 'Rotations'
    when = None

    def read(self, r, f, ctx):
        n = r.get('uint')
        rot = {'type': 0, 'keys': [], 'xyz': None}
        if n:
            rot['type'] = r.get('uint')
            if rot['type'] == 4:
                rot['count'] = n
                axes = []
                for _ in range(3):
                    g = {}
                    KeyGroup('g', 'float').read(r, g, ctx)
                    axes.append(g['g'])
                rot['xyz'] = axes
            else:
                for _ in range(n):
                    key = {'time': r.get('float'), 'value': r.get('quat')}
                    if rot['type'] == 3:
                        key['tbc'] = (r.get('float'), r.get('float'), r.get('float'))
                    rot['keys'].append(key)
        f['Rotations'] = rot

    def write(self, w, f, ctx):
        rot = f['Rotations']
        if rot['type'] == 4:
            w.put('uint', rot.get('count', 1))
            w.put('uint', 4)
            for g in rot['xyz']:
                KeyGroup('g', 'float').write(w, {'g': g}, ctx)
            return
        w.put('uint', len(rot['keys']))
        if rot['keys']:
            w.put('uint', rot['type'])
            for key in rot['keys']:
                w.put('float', key['time'])
                w.put('quat', key['value'])
                if rot['type'] == 3:
                    for v in key['tbc']:
                        w.put('float', v)

    def make_default(self, f):
        f['Rotations'] = {'type': 0, 'keys': [], 'xyz': None}


class MatchGroups:
    name = 'Match Groups'

    def __init__(self, when=None):
        self.when = when

    def read(self, r, f, ctx):
        f[self.name] = [r.array('ushort', r.get('ushort')) for _ in range(f['Num Match Groups'])]

    def write(self, w, f, ctx):
        for g in f[self.name]:
            w.put('ushort', len(g))
            w.array('ushort', g)

    def make_default(self, f):
        f[self.name] = []


# Composite kinds: tuples of (field name, kind)
COMPOSITE = {
    'controlledblock': [('Interpolator', 'ref'), ('Controller', 'ref'), ('Priority', 'byte'), ('Node Name', 'string'),
                        ('Property Type', 'string'), ('Controller Type', 'string'), ('Controller ID', 'string'),
                        ('Interpolator ID', 'string')],
    'textkey': [('Time', 'float'), ('Value', 'string')],
    'avobject': [('Name', 'sized'), ('AV Object', 'ptr')],
}


def _count(name):
    return F(name, 'uint')


NiObjectNET = [F('Name', 'string'), _count('Num Extra Data List'), A('Extra Data List', 'ref', 'Num Extra Data List'),
               F('Controller', 'ref')]
NiAVObject = NiObjectNET + [F('Flags', 'uint', 14), F('Translation', 'vec3'), F('Rotation', 'mat33'),
                            F('Scale', 'float', 1.0), _count('Num Properties'),
                            A('Properties', 'ref', 'Num Properties'), F('Collision Object', 'ref')]
NiNode = NiAVObject + [_count('Num Children'), A('Children', 'ref', 'Num Children'), _count('Num Effects'),
                       A('Effects', 'ref', 'Num Effects')]
NiGeometry = NiAVObject + [F('Data', 'ref'), F('Skin Instance', 'ref'), _count('Num Materials'),
                           A('Material Name', 'string', 'Num Materials'),
                           A('Material Extra Data', 'int', 'Num Materials', sync=False),
                           F('Active Material', 'int', -1), F('Material Needs Update', 'byte')]


def _has_tangents(f):
    return f['Has Normals'] and f['BS Data Flags'] & 0x1000


NiGeometryData = [
    F('Group ID', 'int'), F('Num Vertices', 'ushort'), F('Keep Flags', 'byte'), F('Compress Flags', 'byte'),
    F('Has Vertices', 'bool', 1), A('Vertices', 'vec3', 'Num Vertices', when=lambda f: f['Has Vertices']),
    F('BS Data Flags', 'ushort'), F('Has Normals', 'bool'),
    A('Normals', 'vec3', 'Num Vertices', when=lambda f: f['Has Normals'], sync=False),
    A('Tangents', 'vec3', 'Num Vertices', when=_has_tangents, sync=False),
    A('Bitangents', 'vec3', 'Num Vertices', when=_has_tangents, sync=False),
    F('Center', 'vec3'), F('Radius', 'float'), F('Has Vertex Colors', 'bool'),
    A('Vertex Colors', 'color4', 'Num Vertices', when=lambda f: f['Has Vertex Colors'], sync=False),
    A('UV Sets', 'texcoord', 'Num Vertices', when=lambda f: f['BS Data Flags'] & 1, sync=False),
    F('Consistency Flags', 'ushort', 0x4000), F('Additional Data', 'ref')]
NiTriShapeData = NiGeometryData + [
    F('Num Triangles', 'ushort'), F('Num Triangle Points', 'uint'), F('Has Triangles', 'bool', 1),
    A('Triangles', 'triangle', 'Num Triangles', when=lambda f: f['Has Triangles']),
    F('Num Match Groups', 'ushort'), MatchGroups()]

NiExtraData = [F('Name', 'string')]
NiTimeController = [F('Next Controller', 'ref'), F('Flags', 'ushort', 0x48), F('Frequency', 'float', 1.0),
                    F('Phase', 'float'), F('Start Time', 'float'), F('Stop Time', 'float'), F('Target', 'ptr')]
NiSingleInterpController = NiTimeController + [F('Interpolator', 'ref')]
NiShadeFlags = NiObjectNET + [F('Flags', 'ushort')]
BSShaderProperty = NiShadeFlags + [F('Shader Type', 'uint'), F('Shader Flags', 'uint'), F('Shader Flags 2', 'uint'),
                                   F('Environment Map Scale', 'float', 1.0)]
BSShaderLightingProperty = BSShaderProperty + [F('Texture Clamp Mode', 'uint', 3)]

TYPES = {
    'NiNode': NiNode,
    'BSFadeNode': NiNode,
    'NiTriShape': NiGeometry,
    'NiTriStrips': NiGeometry,
    'NiTriShapeData': NiTriShapeData,
    'NiStringExtraData': NiExtraData + [F('String Data', 'string')],
    'NiIntegerExtraData': NiExtraData + [F('Integer Data', 'uint')],
    'BSXFlags': NiExtraData + [F('Integer Data', 'uint')],
    'NiFloatExtraData': NiExtraData + [F('Float Data', 'float')],
    'BSBound': NiExtraData + [F('Center', 'vec3'), F('Dimensions', 'vec3')],
    'NiTextKeyExtraData': NiExtraData + [_count('Num Text Keys'), A('Text Keys', 'textkey', 'Num Text Keys')],
    'NiTransformController': NiSingleInterpController,
    'NiVisController': NiSingleInterpController,
    'NiFloatExtraDataController': NiSingleInterpController + [F('Extra Data Name', 'string')],
    'NiControllerManager': NiTimeController + [
        F('Cumulative', 'bool'), _count('Num Controller Sequences'),
        A('Controller Sequences', 'ref', 'Num Controller Sequences'), F('Object Palette', 'ref')],
    'NiDefaultAVObjectPalette': [F('Scene', 'ptr'), _count('Num Objs'), A('Objs', 'avobject', 'Num Objs')],
    'NiControllerSequence': [
        F('Name', 'string'), _count('Num Controlled Blocks'), F('Array Grow By', 'uint', 1),
        A('Controlled Blocks', 'controlledblock', 'Num Controlled Blocks'), F('Weight', 'float', 1.0),
        F('Text Keys', 'ref'), F('Cycle Type', 'uint'), F('Frequency', 'float', 1.0), F('Start Time', 'float'),
        F('Stop Time', 'float'), F('Manager', 'ptr'), F('Accum Root Name', 'string'),
        # Bethesda version 24-28 (older Fallout 3 clips): one link; above 28: a counted array
        F('Anim Notes', 'ref', when=lambda f: 24 <= f['__bs'] <= 28),
        F('Num Anim Note Arrays', 'ushort', when=lambda f: f['__bs'] > 28),
        A('Anim Note Arrays', 'ref', 'Num Anim Note Arrays', when=lambda f: f['__bs'] > 28)],
    'NiTransformInterpolator': [F('Translation', 'vec3'), F('Rotation', 'quat'), F('Scale', 'float', 1.0),
                                F('Data', 'ref')],
    'NiFloatInterpolator': [F('Value', 'float'), F('Data', 'ref')],
    'NiBoolInterpolator': [F('Value', 'bool'), F('Data', 'ref')],
    'NiPoint3Interpolator': [F('Value', 'vec3'), F('Data', 'ref')],
    'NiTransformData': [RotationKeys(), KeyGroup('Translations', 'vec3'), KeyGroup('Scales', 'float')],
    'NiFloatData': [KeyGroup('Data', 'float')],
    'NiBoolData': [KeyGroup('Data', 'byte')],
    'NiPosData': [KeyGroup('Data', 'vec3')],
    'NiMaterialProperty': NiObjectNET + [F('Specular Color', 'color3'), F('Emissive Color', 'color3'),
                                         F('Glossiness', 'float', 10.0), F('Alpha', 'float', 1.0),
                                         F('Emissive Mult', 'float', 1.0)],
    'NiAlphaProperty': NiShadeFlags + [F('Threshold', 'byte')],
    # 20.1.0.3+: the stencil state packed in Flags (draw mode bits 10-11: 3 = both sides)
    'NiStencilProperty': NiShadeFlags + [F('Stencil Ref', 'uint'), F('Stencil Mask', 'uint', 0xFFFFFFFF)],
    'BSShaderPPLightingProperty': BSShaderLightingProperty + [
        F('Texture Set', 'ref'), F('Refraction Strength', 'float'), F('Refraction Fire Period', 'int'),
        F('Parallax Max Passes', 'float', 4.0), F('Parallax Scale', 'float', 1.0)],
    'BSShaderNoLightingProperty': BSShaderLightingProperty + [
        F('File Name', 'sized'), F('Falloff Start Angle', 'float'), F('Falloff Stop Angle', 'float'),
        F('Falloff Start Opacity', 'float'), F('Falloff Stop Opacity', 'float')],
    'BSShaderTextureSet': [F('Num Textures', 'int'), A('Textures', 'sized', 'Num Textures')],
}
TYPES['BSAnimGroupSequence'] = TYPES['NiControllerSequence']


def counts_in_step(kind, fields):
    """Before writing: each count field takes the length of its array, and derived values are recomputed."""
    for op in TYPES[kind]:
        if isinstance(op, A) and op.sync and isinstance(op.count, str) and (op.when is None or op.when(fields)):
            fields[op.count] = len(fields[op.name])
    if kind == 'NiTriShapeData':
        fields['Num Triangle Points'] = 3 * fields['Num Triangles']
        fields['Num Match Groups'] = len(fields['Match Groups'])
    if kind == 'BSShaderTextureSet':
        fields['Num Textures'] = len(fields['Textures'])
