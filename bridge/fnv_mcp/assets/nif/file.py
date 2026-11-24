"""The NIF container: header (block types, sizes, string table), blocks, footer (roots)."""
import struct

from ...esp import codec as _codec  # noqa: F401 - registers the 'cp1252-bytes' error handler

from . import schema
from .stream import Reader, Writer

HEADER_LINE = b'Gamebryo File Format, Version 20.2.0.7\n'
VERSION = 0x14020007
USER_VERSION = 11
BS_VERSION = 34


class NifError(ValueError):
    pass


class Block:
    """A block: `type`, and `fields` (parsed) or `raw` (bytes of a type the schema does not know)."""

    def __init__(self, kind, fields=None, raw=None):
        self.type = kind
        self.fields = fields
        self.raw = raw

    def __getitem__(self, key):
        return self.fields[key]

    def __setitem__(self, key, value):
        self.fields[key] = value

    def __repr__(self):
        return '<%s %s>' % (self.type, (self.fields or {}).get('Name', '') if self.fields else 'raw')


class _Ctx:
    """String table conversion while reading or writing blocks."""

    def __init__(self, strings, index=None, bs_version=34):
        self.strings = strings
        self.index = index
        self.bs_version = bs_version

    def read_value(self, r, kind):
        if kind == 'string':
            i = r.get('int')
            if i == -1:
                return None
            if not 0 <= i < len(self.strings):
                raise EOFError('string index %d outside the table (%d strings)' % (i, len(self.strings)))
            return self.strings[i]
        if kind == 'sized':
            return r.sized_string()
        if kind in schema.COMPOSITE:
            return {name: self.read_value(r, k) for name, k in schema.COMPOSITE[kind]}
        return r.get(kind)

    def write_value(self, w, kind, value):
        if kind == 'string':
            w.put('int', -1 if value is None else self.index(value))
        elif kind == 'sized':
            w.sized_string(value)
        elif kind in schema.COMPOSITE:
            for name, k in schema.COMPOSITE[kind]:
                self.write_value(w, k, value[name])
        else:
            w.put(kind, value)


def _parse_block(kind, data, start, end, ctx):
    r = Reader(data, start, end)
    fields = {'__bs': ctx.bs_version}       # conditions may test the file's Bethesda version
    for op in schema.TYPES[kind]:
        if op.when is not None and not op.when(fields):
            continue
        op.read(r, fields, ctx)
    del fields['__bs']
    if r.pos != end:
        raise EOFError('read %d of %d bytes' % (r.pos - start, end - start))
    return fields


def _write_block(block, ctx):
    if block.fields is None:
        return block.raw
    fields = dict(block.fields)
    fields['__bs'] = ctx.bs_version
    schema.counts_in_step(block.type, fields)
    w = Writer()
    for op in schema.TYPES[block.type]:
        if op.when is not None and not op.when(fields):
            continue
        op.write(w, fields, ctx)
    del fields['__bs']
    block.fields.update(fields)
    return w.value()


class NifFile:
    def __init__(self):
        self.version_line = HEADER_LINE
        self.user_version = USER_VERSION
        self.bs_version = BS_VERSION
        self.export_info = [b'', b'', b'']     # author, process script, export script (short strings with their nul)
        self.blocks = []
        self.strings = []                      # the header's string table, in order
        self.max_string_length = None          # kept as read when unchanged
        self.groups = []
        self.roots = [0]

    # ---------------------------------------------------------------- editing
    def add(self, kind, **values):
        """Append a new block of a schema type; fields not given take their defaults. Returns its index."""
        if kind not in schema.TYPES:
            raise NifError('no layout for block type %s' % kind)
        fields = {'__bs': self.bs_version}
        names = set()
        for op in schema.TYPES[kind]:
            names.add(op.name)
            if op.name in values:
                fields[op.name] = values[op.name]
            else:
                op.make_default(fields)       # in order, so conditions see the values given before them
        del fields['__bs']
        for k in values:
            if k not in names:
                raise NifError('%s has no field %r' % (kind, k))
        self.blocks.append(Block(kind, fields))
        return len(self.blocks) - 1

    def find(self, name, kind=None):
        """Index of the first block with this Name (and type), or -1."""
        for i, b in enumerate(self.blocks):
            if b.fields is not None and b.fields.get('Name') == name and (kind is None or b.type == kind):
                return i
        return -1

    def children(self, i):
        return [c for c in self.blocks[i].fields.get('Children', []) if c >= 0]

    # ---------------------------------------------------------------- writing
    def write(self):
        strings = list(self.strings)
        lookup = {}
        for i, s in enumerate(strings):
            lookup.setdefault(s, i)

        def index(s):
            if s not in lookup:
                lookup[s] = len(strings)
                strings.append(s)
            return lookup[s]

        ctx = _Ctx(strings, index, self.bs_version)
        bodies = [_write_block(b, ctx) for b in self.blocks]
        used = {b.type for b in self.blocks}
        types = [t for t in getattr(self, 'type_order', []) if t in used]
        type_index = []
        for b in self.blocks:
            if b.type not in types:
                types.append(b.type)
            type_index.append(types.index(b.type))
        w = Writer()
        w.raw(self.version_line)
        w.raw(struct.pack('<IBII', VERSION, 1, self.user_version, len(self.blocks)))
        w.raw(struct.pack('<I', self.bs_version))
        for s in self.export_info:
            w.raw(struct.pack('<B', len(s)) + s)
        w.raw(struct.pack('<H', len(types)))
        for t in types:
            w.sized_string(t)
        w.raw(struct.pack('<%dH' % len(type_index), *type_index))
        w.raw(struct.pack('<%dI' % len(bodies), *[len(b) for b in bodies]))
        max_len = max([len(s.encode('cp1252', 'cp1252-bytes')) for s in strings] or [0])
        if self.max_string_length is not None and strings == self.strings:
            max_len = self.max_string_length
        w.raw(struct.pack('<II', len(strings), max_len))
        for s in strings:
            w.sized_string(s)
        w.raw(struct.pack('<I', len(self.groups)))
        w.raw(struct.pack('<%dI' % len(self.groups), *self.groups))
        for body in bodies:
            w.raw(body)
        w.raw(struct.pack('<I', len(self.roots)))
        w.raw(struct.pack('<%di' % len(self.roots), *self.roots))
        return w.value()


def new_file():
    return NifFile()


def read(data, strict=False):
    """Parse a NIF/KF. Unknown block types are kept raw, or raise NifError when strict."""
    nl = data.find(b'\n')
    if nl < 0 or not data.startswith(b'Gamebryo File Format'):
        raise NifError('not a Gamebryo 20.x file')
    f = NifFile()
    f.version_line = data[:nl + 1]
    p = nl + 1
    version, endian, uv, nblocks = struct.unpack_from('<IBII', data, p)
    p += 13
    if version != VERSION:
        raise NifError('version %08X: only 20.2.0.7 is supported' % version)
    f.user_version = uv
    f.bs_version = struct.unpack_from('<I', data, p)[0]
    p += 4
    f.export_info = []
    for _ in range(3):
        n = data[p]
        f.export_info.append(data[p + 1:p + 1 + n])
        p += 1 + n
    ntypes = struct.unpack_from('<H', data, p)[0]
    p += 2
    types = []
    for _ in range(ntypes):
        n = struct.unpack_from('<I', data, p)[0]
        types.append(data[p + 4:p + 4 + n].decode('ascii'))
        p += 4 + n
    f.type_order = types
    type_index = struct.unpack_from('<%dH' % nblocks, data, p)
    p += 2 * nblocks
    sizes = struct.unpack_from('<%dI' % nblocks, data, p)
    p += 4 * nblocks
    nstrings, f.max_string_length = struct.unpack_from('<II', data, p)
    p += 8
    for _ in range(nstrings):
        n = struct.unpack_from('<I', data, p)[0]
        f.strings.append(data[p + 4:p + 4 + n].decode('cp1252', 'cp1252-bytes'))
        p += 4 + n
    ngroups = struct.unpack_from('<I', data, p)[0]
    p += 4
    f.groups = list(struct.unpack_from('<%dI' % ngroups, data, p))
    p += 4 * ngroups
    ctx = _Ctx(f.strings, bs_version=f.bs_version)
    for i, (ti, size) in enumerate(zip(type_index, sizes)):
        kind = types[ti & 0x7FFF]
        if kind in schema.TYPES:
            try:
                fields = _parse_block(kind, data, p, p + size, ctx)
            except EOFError as exc:
                raise NifError('block %d (%s): %s' % (i, kind, exc))
            f.blocks.append(Block(kind, fields))
        elif strict:
            raise NifError('block %d: no layout for block type %s' % (i, kind))
        else:
            f.blocks.append(Block(kind, raw=data[p:p + size]))
        p += size
    nroots = struct.unpack_from('<I', data, p)[0]
    f.roots = list(struct.unpack_from('<%di' % nroots, data, p + 4))
    p += 4 + 4 * nroots
    if p != len(data):
        raise NifError('%d bytes after the footer' % (len(data) - p))
    return f
