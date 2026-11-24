"""Little-endian reader and writer of the NIF primitive types."""
import struct

from ...esp import codec as _codec  # noqa: F401 - registers the 'cp1252-bytes' error handler

FORMATS = {
    'byte': 'B', 'bool': 'B', 'ushort': 'H', 'short': 'h', 'uint': 'I', 'int': 'i', 'float': 'f',
    'ref': 'i', 'ptr': 'i', 'string': 'i',
    'vec3': '3f', 'vec4': '4f', 'quat': '4f', 'mat33': '9f', 'color3': '3f', 'color4': '4f', 'texcoord': '2f',
    'byte4': '4B', 'triangle': '3H', 'matchgroup': None,
}
SIZES = {k: struct.calcsize('<' + v) for k, v in FORMATS.items() if v}
MULTI = {k for k, v in FORMATS.items() if v and len(v) > 1}


class Reader:
    def __init__(self, data, pos=0, end=None):
        self.data = data
        self.pos = pos
        self.end = len(data) if end is None else end

    def get(self, kind):
        fmt = FORMATS[kind]
        size = SIZES[kind]
        if self.pos + size > self.end:
            raise EOFError('read past the end of the block (%s at %d)' % (kind, self.pos))
        value = struct.unpack_from('<' + fmt, self.data, self.pos)
        self.pos += size
        return tuple(value) if kind in MULTI else value[0]

    def array(self, kind, count):
        if kind in MULTI or kind not in FORMATS:
            return [self.get(kind) for _ in range(count)]
        size = SIZES[kind] * count
        if self.pos + size > self.end:
            raise EOFError('read past the end of the block (%d x %s at %d)' % (count, kind, self.pos))
        value = list(struct.unpack_from('<%d%s' % (count, FORMATS[kind]), self.data, self.pos))
        self.pos += size
        return value

    def sized_string(self):
        n = self.get('uint')
        if self.pos + n > self.end:
            raise EOFError('string past the end of the block')
        s = self.data[self.pos:self.pos + n]
        self.pos += n
        return s.decode('cp1252', 'cp1252-bytes')

    def bytes(self, n):
        s = self.data[self.pos:self.pos + n]
        self.pos += n
        return bytes(s)


class Writer:
    def __init__(self):
        self.parts = []

    def put(self, kind, value):
        fmt = FORMATS[kind]
        if kind in MULTI:
            self.parts.append(struct.pack('<' + fmt, *value))
        else:
            if value is True or value is False:   # a vanilla 'bool' byte can hold 2 (unset): kept as read
                value = int(value)
            self.parts.append(struct.pack('<' + fmt, value))

    def array(self, kind, values):
        if kind in MULTI:
            for v in values:
                self.put(kind, v)
        else:
            self.parts.append(struct.pack('<%d%s' % (len(values), FORMATS[kind]), *values))

    def sized_string(self, s):
        b = s.encode('cp1252', 'cp1252-bytes')
        self.parts.append(struct.pack('<I', len(b)) + b)

    def raw(self, b):
        self.parts.append(bytes(b))

    def value(self):
        return b''.join(self.parts)
