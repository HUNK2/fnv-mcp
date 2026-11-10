"""Reader for COFF objects and parser for their CodeView C13 debug sections (.debug$S, .debug$T).

`ObjectFile` has the shape of `Coff` in the decomp's tools/compare.py (sections, symbols, by_name) and also
accepts sections with more than 65,535 relocations. `parse_object` turns an object into plain dicts and lists:
functions with lines and locals, globals, and a closed dict of types. See docs/FNV-SYMDB.md sections 3-5.
Nothing here keeps code bytes; the objects come from your own compiler run and must not be committed.
"""
import hashlib
import json
import struct

_unpack = struct.unpack_from
_iter_unpack = struct.iter_unpack
_BAD = (struct.error, IndexError, ValueError)
MAX_DEPTH = 200


class ObjectFile:
    """i386 COFF object. `ObjectFile(path)` or `ObjectFile(bytes)`."""

    def __init__(self, source):
        if isinstance(source, (bytes, bytearray, memoryview)):
            self.data = bytes(source)
        else:
            with open(source, 'rb') as f:
                self.data = f.read()
        data = self.data
        if len(data) < 20:
            raise ValueError('not a COFF object')
        machine, count, _, symptr, symcount, opts, _ = struct.unpack_from('<HHIIIHH', data)
        if machine != 0x14c or opts:
            raise ValueError('expected i386 COFF object, not an executable or bigobj')
        strptr = symptr + symcount * 18
        strings = b''
        if symcount:
            strlen = struct.unpack_from('<I', data, strptr)[0]
            strings = data[strptr:strptr + strlen]

        def string_at(offset):
            return strings[offset:].split(b'\0', 1)[0].decode('latin-1')

        self.sections = [None]
        for i in range(count):
            row = struct.unpack_from('<8sIIIIIIHHI', data, 20 + i * 40)
            name, _, _, length, raw, relptr, _, relcount, _, flags = row
            if name[:1] == b'/' and name[1:].rstrip(b'\0').isdigit():
                name = string_at(int(name[1:].rstrip(b'\0'))).encode('latin-1')
            skip = 0
            if flags & 0x01000000:
                # More than 65,535 relocations: the real count is stored in the first one, which is skipped.
                if relptr + 10 > len(data):
                    raise ValueError('truncated relocation table')
                relcount = max(struct.unpack_from('<I', data, relptr)[0] - 1, 0)
                skip = 10
            start = relptr + skip
            if relcount and start + relcount * 10 > len(data):
                raise ValueError('truncated relocation table')
            relocs = list(_iter_unpack('<IIH', data[start:start + relcount * 10])) if relcount else []
            self.sections.append(dict(
                name=name.rstrip(b'\0').decode('latin-1'),
                data=bytes(length) if raw == 0 and flags & 0x80 else data[raw:raw + length],
                flags=flags, relocs=relocs))
        self.symbols = {}
        self.by_name = {}
        i = 0
        while i < symcount:
            rawname, value, section, typ, storage, aux = struct.unpack_from('<8sIhHBB', data, symptr + i * 18)
            if rawname[:4] == b'\0\0\0\0':
                name = string_at(struct.unpack_from('<I', rawname, 4)[0])
            else:
                name = rawname.rstrip(b'\0').decode('latin-1')
            symbol = dict(name=name, value=value, section=section, type=typ, storage=storage)
            self.symbols[i] = symbol
            self.by_name.setdefault(name, []).append(symbol)
            i += 1 + aux

    def named(self, name):
        found = [s for s in self.by_name.get(name, []) if s['section'] > 0]
        if len(found) != 1:
            raise ValueError('expected one defined symbol: ' + name)
        return found[0]


# --- numeric leaves and strings -------------------------------------------------------------------------------

_NUMERIC = {0x8000: '<b', 0x8001: '<h', 0x8002: '<H', 0x8003: '<i', 0x8004: '<I', 0x8005: '<f', 0x8006: '<d',
            0x8009: '<q', 0x800A: '<Q'}
_NUMERIC_SKIP = {0x8007: 10, 0x8008: 16, 0x800B: 6, 0x800C: 8, 0x800D: 16, 0x800E: 20, 0x800F: 32}


def _num(d, o):
    """Numeric leaf at o: (value, next offset)."""
    v = _unpack('<H', d, o)[0]
    o += 2
    if v < 0x8000:
        return v, o
    fmt = _NUMERIC.get(v)
    if fmt:
        return _unpack(fmt, d, o)[0], o + struct.calcsize(fmt)
    if v in _NUMERIC_SKIP:
        return 0, o + _NUMERIC_SKIP[v]
    raise ValueError('numeric leaf 0x%04x' % v)


def _cstr(d, o, end):
    """NUL-terminated string at o, bounded by end: (text, offset after the NUL)."""
    z = d.find(b'\0', o, end)
    if z < 0:
        raise ValueError('unterminated string')
    return d[o:z].decode('latin-1'), z + 1


class _Warnings:
    def __init__(self):
        self.items = []
        self.seen = set()

    def __call__(self, text):
        if text not in self.seen:
            self.seen.add(text)
            self.items.append(text)


# --- types ----------------------------------------------------------------------------------------------------

_PRIMS = {
    0x00: ('notype', 0), 0x03: ('void', 0), 0x08: ('HRESULT', 4),
    0x10: ('signed char', 1), 0x11: ('short', 2), 0x12: ('long', 4), 0x13: ('__int64', 8), 0x14: ('__int128', 16),
    0x20: ('unsigned char', 1), 0x21: ('unsigned short', 2), 0x22: ('unsigned long', 4),
    0x23: ('unsigned __int64', 8), 0x24: ('unsigned __int128', 16),
    0x30: ('bool', 1), 0x31: ('bool16', 2), 0x32: ('bool32', 4), 0x33: ('bool64', 8),
    0x40: ('float', 4), 0x41: ('double', 8), 0x42: ('long double', 10), 0x43: ('__float128', 16),
    0x44: ('float48', 6),
    0x68: ('__int8', 1), 0x69: ('unsigned __int8', 1), 0x70: ('char', 1), 0x71: ('wchar_t', 2),
    0x72: ('short', 2), 0x73: ('unsigned short', 2), 0x74: ('int', 4), 0x75: ('unsigned int', 4),
    0x76: ('__int64', 8), 0x77: ('unsigned __int64', 8),
}
_PTR_SIZES = {1: 2, 2: 4, 3: 4, 4: 4, 5: 6, 6: 8}
_UDT = {0x1504: 'class', 0x1505: 'struct', 0x1506: 'union', 0x1507: 'enum'}
_CALLS = {0: 'cdecl', 1: 'cdecl', 2: 'pascal', 3: 'pascal', 4: 'fastcall', 5: 'fastcall', 7: 'stdcall',
          8: 'stdcall', 9: 'syscall', 10: 'syscall', 11: 'thiscall', 22: 'clrcall'}
_BIT_CHECK = (('const', 1), ('volatile', 2), ('unaligned', 4))


def _unnamed(name):
    return not name or '<unnamed' in name or '__unnamed' in name


class _Types:
    """Type records of one object, converted on demand to hashed type dicts (docs/FNV-SYMDB.md section 5)."""

    def __init__(self, data, warn):
        self.data = data
        self.warn = warn
        self.recs = []        # (leaf, body start, body end); type index = 0x1000 + position
        self.out = {}         # hash -> Type
        self.defs = {}        # name -> position of the first complete definition
        self.unnamed_defs = {}  # unnamed type name -> positions of its complete definitions
        self.cache = {}
        self.defined = {}
        self.busy = set()
        self.headers = {}
        self.depth = 0
        if len(data) >= 4:
            if _unpack('<I', data, 0)[0] != 4:
                warn('unsupported .debug$T signature')
            else:
                self._scan()

    def _scan(self):
        d = self.data
        p = 4
        n = len(d)
        recs = self.recs
        while p + 4 <= n:
            length, leaf = _unpack('<HH', d, p)
            end = p + 2 + length
            if length < 2 or end > n:
                self.warn('truncated type record at 0x%x' % p)
                break
            recs.append((leaf, p + 4, end))
            p = end
        for i, (leaf, _, _) in enumerate(recs):
            if leaf in _UDT:
                try:
                    h = self.header(i)
                except _BAD:
                    self.warn('malformed type record, leaf 0x%04x' % leaf)
                    continue
                if h['fwd']:
                    continue
                if _unnamed(h['name']):
                    self.unnamed_defs.setdefault(h['name'], []).append(i)
                else:
                    self.defs.setdefault(h['name'], i)

    def body(self, i):
        _, s, e = self.recs[i]
        return self.data[s:e]

    def header(self, i):
        h = self.headers.get(i)
        if h is not None:
            return h
        leaf = self.recs[i][0]
        d = self.body(i)
        under = 0
        if leaf in (0x1504, 0x1505):
            _, prop, fl = _unpack('<HHI', d, 0)
            size, o = _num(d, 16)
        elif leaf == 0x1506:
            _, prop, fl = _unpack('<HHI', d, 0)
            size, o = _num(d, 8)
        else:
            _, prop, under, fl = _unpack('<HHII', d, 0)
            size, o = None, 12
        name = _cstr(d, o, len(d))[0]
        h = {'leaf': leaf, 'prop': prop, 'fl': fl, 'size': size, 'name': name, 'under': under,
             'fwd': bool(prop & 0x80)}
        self.headers[i] = h
        return h

    # -- emitting --

    def emit(self, kind, name=None, size=None, target=None, attrs=None, members=(), bases=(), methods=(),
             enumerators=(), args=()):
        t = {'kind': kind, 'name': name, 'size': size, 'target': target, 'attrs': attrs or {},
             'members': list(members), 'bases': list(bases), 'methods': list(methods),
             'enumerators': [list(x) for x in enumerators], 'args': list(args)}
        h = hashlib.sha1(json.dumps(t, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:20]
        self.out.setdefault(h, t)
        return h

    def other(self, leaf):
        return self.emit('other', attrs={'leaf': leaf})

    def udtref(self, name):
        h = 'udtref:' + name
        if h not in self.out:
            self.out[h] = {'kind': 'udtref', 'name': name, 'size': None, 'target': None, 'attrs': {},
                           'members': [], 'bases': [], 'methods': [], 'enumerators': [], 'args': []}
        return h

    def prim(self, index):
        base = index & 0xF0FF
        h = 'prim:%04x' % base
        if h not in self.out:
            info = _PRIMS.get(base)
            if info is None:
                self.warn('unknown primitive type 0x%04x' % index)
                info = ('prim_%04x' % base, None)
            self.out[h] = {'kind': 'prim', 'name': info[0], 'size': info[1], 'target': None,
                           'attrs': {'cv': base}, 'members': [], 'bases': [], 'methods': [], 'enumerators': [],
                           'args': []}
        mode = (index >> 8) & 7
        if mode:
            attrs = {} if mode == 4 else {'cvMode': mode}
            return self.emit('ptr', size=_PTR_SIZES.get(mode, 4), target=h, attrs=attrs)
        return h

    def size_of(self, h):
        """Size in bytes of a converted type, None when unknown."""
        if h is None:
            return None
        if h.startswith('udtref:'):
            j = self.defs.get(h[7:])
            return self.header(j)['size'] if j is not None else None
        t = self.out.get(h)
        return t['size'] if t else None

    # -- references --

    def ref(self, index, direct=False):
        """Hash of type `index`. A named class/struct/union/enum is its udtref unless `direct`."""
        if index == 0:
            return None
        if index < 0x1000:
            return self.prim(index)
        key = (index, direct)
        h = self.cache.get(key)
        if h is None:
            h = self.cache[key] = self._guard(index - 0x1000, direct)
        return h

    def _guard(self, i, direct):
        if i >= len(self.recs):
            self.warn('type index 0x%x out of range' % (i + 0x1000))
            return self.other(-1)
        leaf = self.recs[i][0]
        if self.depth > MAX_DEPTH:
            self.warn('type chain deeper than %d, cut' % MAX_DEPTH)
            return self.other(leaf)
        self.depth += 1
        try:
            return self._ref(i, leaf, direct)
        except _BAD:
            self.warn('malformed type record, leaf 0x%04x' % leaf)
            return self.other(leaf)
        finally:
            self.depth -= 1

    def _ref(self, i, leaf, direct):
        if leaf in _UDT:
            h = self.header(i)
            name = h['name']
            if _unnamed(name):
                if h['fwd']:
                    # a forward reference to an unnamed nested type: its definition when the name is unique
                    found = self.unnamed_defs.get(name, ())
                    return self.define(found[0]) if len(found) == 1 else self.other(leaf)
                return self.define(i)
            if not direct:
                return self.udtref(name)
            j = i
            if h['fwd']:
                j = self.defs.get(name)
                if j is None:
                    return self.udtref(name)
            return self.define(j)
        return self.convert(i, leaf)

    def define(self, i):
        h = self.defined.get(i)
        if h is not None:
            return h
        if i in self.busy:
            self.warn('type cycle through an unnamed type')
            return self.other(self.recs[i][0])
        self.busy.add(i)
        try:
            h = self.defined[i] = self._define(i)
        except _BAD:
            self.warn('malformed type record, leaf 0x%04x' % self.recs[i][0])
            h = self.defined[i] = self.other(self.recs[i][0])
        finally:
            self.busy.discard(i)
        return h

    def _define(self, i):
        hd = self.header(i)
        leaf = hd['leaf']
        kind = _UDT[leaf]
        name = hd['name'] or None
        if kind == 'enum':
            fields = self.fields(hd['fl'])
            target = self.ref(hd['under'])
            return self.emit('enum', name, self.size_of(target), target, enumerators=fields['enums'])
        fields = self.fields(hd['fl'])
        return self.emit(kind, name, hd['size'], None, {'vtable': True} if fields['vtable'] else {},
                         fields['members'], fields['bases'], fields['methods'])

    # -- field lists --

    def fields(self, fl):
        members, bases, methods, enums = [], [], [], []
        vtable = False
        seen = set()
        while fl >= 0x1000 and fl not in seen:
            seen.add(fl)
            i = fl - 0x1000
            if i >= len(self.recs) or self.recs[i][0] != 0x1203:
                self.warn('field list index 0x%x is not a field list' % fl)
                break
            d = self.body(i)
            e = len(d)
            o = 0
            fl = 0
            while o < e:
                c = d[o]
                if c >= 0xF0:
                    o += max(c & 0x0F, 1)
                    continue
                sub = _unpack('<H', d, o)[0]
                o += 2
                if sub == 0x150D:
                    attr, t = _unpack('<HI', d, o)
                    off, o = _num(d, o + 6)
                    name, o = _cstr(d, o, e)
                    members.append(self._member(name, off, t))
                elif sub == 0x1400:
                    attr, t = _unpack('<HI', d, o)
                    off, o = _num(d, o + 6)
                    bases.append({'type': self.ref(t), 'offset': off, 'virtual': False})
                elif sub in (0x1401, 0x1402):
                    attr, t, vbp = _unpack('<HII', d, o)
                    _, o = _num(d, o + 10)
                    _, o = _num(d, o)
                    bases.append({'type': self.ref(t), 'offset': None, 'virtual': True})
                elif sub == 0x1404:
                    fl = _unpack('<HI', d, o)[1]
                    o += 6
                elif sub == 0x1409:
                    vtable = True
                    o += 6
                elif sub == 0x1502:
                    value, o = _num(d, o + 2)
                    name, o = _cstr(d, o, e)
                    enums.append([name, value])
                elif sub == 0x150E:
                    name, o = _cstr(d, o + 6, e)
                elif sub == 0x1510:
                    name, o = _cstr(d, o + 6, e)
                elif sub == 0x150F:
                    count, ml = _unpack('<HI', d, o)
                    name, o = _cstr(d, o + 6, e)
                    self._methodlist(methods, name, ml, count)
                elif sub == 0x1511:
                    attr, t = _unpack('<HI', d, o)
                    o += 6
                    vt = None
                    if (attr >> 2) & 7 in (4, 6):
                        vt = _unpack('<I', d, o)[0]
                        o += 4
                    name, o = _cstr(d, o, e)
                    methods.append(self._method(name, attr, t, vt))
                else:
                    self.warn('unknown field list leaf 0x%04x' % sub)
                    break
        return {'members': members, 'bases': bases, 'methods': methods, 'enums': enums, 'vtable': vtable}

    def _member(self, name, off, t):
        pos = length = None
        if 0x1000 <= t < 0x1000 + len(self.recs) and self.recs[t - 0x1000][0] == 0x1205:
            under, length, pos = _unpack('<IBB', self.body(t - 0x1000), 0)
            t = under
        return {'name': name, 'offset': off, 'type': self.ref(t), 'bitPos': pos, 'bitLen': length}

    def _method(self, name, attr, t, vt):
        kind = (attr >> 2) & 7
        return {'name': name, 'type': self.ref(t), 'virtual': kind in (1, 4, 5, 6), 'static': kind == 2,
                'vtOffset': vt}

    def _methodlist(self, methods, name, ml, count):
        i = ml - 0x1000
        if i < 0 or i >= len(self.recs) or self.recs[i][0] != 0x1206:
            self.warn('method list index 0x%x is not a method list' % ml)
            return
        d = self.body(i)
        o, e = 0, len(d)
        for _ in range(count):
            if o + 8 > e:
                break
            attr, _, t = _unpack('<HHI', d, o)
            o += 8
            vt = None
            if (attr >> 2) & 7 in (4, 6):
                vt = _unpack('<I', d, o)[0]
                o += 4
            methods.append(self._method(name, attr, t, vt))

    def arglist(self, index):
        i = index - 0x1000
        if i < 0 or i >= len(self.recs) or self.recs[i][0] != 0x1201:
            return []
        d = self.body(i)
        count = _unpack('<I', d, 0)[0]
        count = min(count, (len(d) - 4) // 4)
        return [self.ref(t) for t in _unpack('<%dI' % count, d, 4)] if count else []

    # -- other leaves --

    def convert(self, i, leaf):
        d = self.body(i)
        s, e = 0, len(d)
        if leaf == 0x1002:
            t, attr = _unpack('<II', d, s)
            mode = (attr >> 5) & 7
            size = (attr >> 13) & 0x3F or 4
            flags = {}
            if attr & 0x400:
                flags['const'] = True
            if attr & 0x200:
                flags['volatile'] = True
            if mode in (2, 3):
                cls, _fmt = _unpack('<IH', d, s + 8)
                flags['class'] = self.ref(cls)
                flags['mode'] = 'data' if mode == 2 else 'function'
                return self.emit('memptr', None, size, self.ref(t), flags)
            return self.emit('ref' if mode == 1 else 'ptr', None, size, self.ref(t), flags)
        if leaf == 0x1001:
            t, attr = _unpack('<IH', d, s)
            target = self.ref(t)
            attrs = {name: True for name, bit in _BIT_CHECK if attr & bit}
            return self.emit('modifier', None, None if target and target.startswith('udtref:')
                             else self.size_of(target), target, attrs)
        if leaf == 0x1008:
            ret, call, _attr, _count, al = _unpack('<IBBHI', d, s)
            return self.emit('proc', None, None, self.ref(ret), {'call': _CALLS.get(call, 'call%d' % call)},
                             args=self.arglist(al))
        if leaf == 0x1009:
            ret, cls, this, call, _attr, _count, al, adjust = _unpack('<IIIBBHIi', d, s)
            attrs = {'call': _CALLS.get(call, 'call%d' % call), 'class': self.ref(cls),
                     'this': self.ref(this), 'thisAdjust': adjust}
            return self.emit('mfunc', None, None, self.ref(ret), attrs, args=self.arglist(al))
        if leaf == 0x1503:
            elem, _index = _unpack('<II', d, s)
            size, o = _num(d, s + 8)
            name = _cstr(d, o, e)[0]
            target = self.ref(elem)
            esize = self.size_of(target)
            count = size // esize if esize and size % esize == 0 else None
            return self.emit('array', name or None, size, target, {'count': count})
        if leaf == 0x1205:
            t, length, pos = _unpack('<IBB', d, s)
            return self.emit('bitfield', None, None, self.ref(t), {'bitLen': length, 'bitPos': pos})
        self.warn('unknown type leaf 0x%04x' % leaf)
        return self.other(leaf)


# --- debug$S --------------------------------------------------------------------------------------------------

_NO_SOURCE = (0xFEEFEE, 0xF00F00)
_PROC = (0x1110, 0x110F)
_DATA = {0x110C: False, 0x110D: True, 0x1112: False, 0x1113: True}
_KNOWN_SYMBOLS = {0x0001, 0x1101, 0x1105, 0x1107, 0x1108, 0x1124, 0x1005, 0x1019, 0x1125, 0x1126, 0x1127,
                  0x112C, 0x1136, 0x1137, 0x113D, 0x1116, 0x113C, 0x1012, 0x1139, 0x113A}


class _Debug:
    """One pass over the .debug$S sections of an object."""

    def __init__(self, obj, types, warn):
        self.obj = obj
        self.types = types
        self.warn = warn
        self.symbols = obj.symbols
        self.sections = obj.sections
        self.compiler = None
        self.functions = []
        self.globals = []
        self.files = []
        self.by_key = {}        # (section, address) -> Function
        self.start = {}         # (section, address) -> address of the procedure record's code start
        self.func_at = {}
        self.data_at = {}
        self.rel = {}
        self.pieces = []        # (section index, data, [(kind, body start, body length)])
        self.strings = {}
        self.file_index = {}
        self.stack = []
        self.func = None
        self.pstart = None      # (section, address) of the open procedure
        self.pstored = 0
        self.blocks = []
        self._index_symbols()

    def _index_symbols(self):
        for sym in self.symbols.values():
            sec = sym['section']
            if sec <= 0 or sec >= len(self.sections):
                continue
            if self._section_symbol(sym):
                continue
            if sym['type'] & 0x20:
                self.func_at.setdefault((sec, sym['value']), sym['name'])
            else:
                self.data_at.setdefault((sec, sym['value']), sym['name'])

    def _section_symbol(self, sym):
        sec = self.sections[sym['section']] if 0 < sym['section'] < len(self.sections) else None
        return sec is not None and sym['name'] == sec['name'] and sym['storage'] == 3

    # -- relocations --

    def relocs(self, si):
        r = self.rel.get(si)
        if r is None:
            r = self.rel[si] = {}
            for off, idx, kind in self.sections[si]['relocs']:
                if off not in r or kind == 0x0B:
                    r[off] = (idx, kind)
        return r

    def code_at(self, si, d, field):
        """(function symbol or None, key, address) of the code named by the relocation at `field`."""
        r = self.relocs(si).get(field)
        sym = self.symbols.get(r[0]) if r else None
        if sym is None or sym['section'] <= 0:
            return None, None, None
        stored = _unpack('<I', d, field)[0]
        address = (sym['section'], sym['value'] + stored)
        if sym['type'] & 0x20 and not self._section_symbol(sym):
            return sym['name'], (sym['section'], sym['value']), address
        return self.func_at.get(address), address, address

    def data_symbol(self, si, d, field):
        r = self.relocs(si).get(field)
        sym = self.symbols.get(r[0]) if r else None
        if sym is None:
            return None
        if sym['section'] > 0 and self._section_symbol(sym):
            return self.data_at.get((sym['section'], _unpack('<I', d, field)[0]))
        return sym['name']

    # -- subsections --

    def run(self):
        for si, sec in enumerate(self.sections):
            if sec is None or sec['name'] != '.debug$S':
                continue
            d = sec['data']
            if len(d) < 4 or _unpack('<I', d, 0)[0] != 4:
                self.warn('unsupported .debug$S signature in section %d' % si)
                continue
            subs = []
            p = 4
            while p + 8 <= len(d):
                kind, length = _unpack('<II', d, p)
                kind &= 0x7FFFFFFF
                if p + 8 + length > len(d):
                    self.warn('truncated debug subsection 0x%x' % kind)
                    break
                subs.append((kind, p + 8, length))
                p += 8 + ((length + 3) & ~3)
            self.pieces.append((si, d, subs))
        table = {}
        for si, d, subs in self.pieces:
            for kind, p, n in subs:
                if kind in (0xF3, 0xF4):
                    table.setdefault(kind, (d, p, n))
        if 0xF3 in table:
            d, p, n = table[0xF3]
            self.strings = d[p:p + n]
        if 0xF4 in table:
            self._checksums(*table[0xF4])
        for si, d, subs in self.pieces:
            for kind, p, n in subs:
                if kind == 0xF1:
                    self.symbols_in(si, d, p, p + n)
                elif kind not in (0xF2, 0xF3, 0xF4, 0xF5):
                    self.warn('unknown debug subsection 0x%x' % kind)
        for si, d, subs in self.pieces:
            for kind, p, n in subs:
                if kind == 0xF2:
                    try:
                        self.lines_in(si, d, p, p + n)
                    except _BAD:
                        self.warn('truncated lines subsection')
        for f in self.functions:
            f['lines'].sort(key=lambda x: x[0])

    def string(self, off):
        z = self.strings.find(b'\0', off)
        return self.strings[off:z if z >= 0 else len(self.strings)].decode('latin-1')

    def _checksums(self, d, p, n):
        names = {}
        q = 0
        while q + 6 <= n:
            name_off, cb, _kind = _unpack('<IBB', d, p + q)
            name = self.string(name_off)
            if name not in names:
                names[name] = len(self.files)
                self.files.append(name)
            self.file_index[q] = names[name]
            q += (6 + cb + 3) & ~3

    def symbols_in(self, si, d, q, end):
        w = self.warn
        types = self.types
        while q + 4 <= end:
            rl, rt = _unpack('<HH', d, q)
            rend = q + 2 + rl
            if rl < 2 or rend > end:
                w('truncated symbol record kind 0x%04x' % rt)
                return
            b = q + 4
            q = rend
            try:
                if rt == 0x110B:
                    if self.func is not None:
                        off, t = _unpack('<iI', d, b)
                        self._local('bprel', None, off, t, _cstr(d, b + 8, rend)[0])
                elif rt == 0x0006:
                    self._end()
                elif rt == 0x1111:
                    if self.func is not None:
                        off, t, reg = _unpack('<iIH', d, b)
                        self._local('regrel', reg, off, t, _cstr(d, b + 10, rend)[0])
                elif rt == 0x1106:
                    if self.func is not None:
                        t, reg = _unpack('<IH', d, b)
                        self._local('register', reg, None, t, _cstr(d, b + 6, rend)[0])
                elif rt in _PROC:
                    self._proc(si, d, b, rend, rt == 0x1110)
                elif rt == 0x1012:
                    if self.func is not None:
                        self.func['frameBytes'] = _unpack('<I', d, b)[0]
                        if rend - b >= 26:
                            self.func['frameFlags'] = _unpack('<I', d, b + 22)[0]
                elif rt in (0x1103, 0x1104):
                    self._block(si, d, b, rend)
                elif rt in _DATA:
                    t, _off, _seg = _unpack('<IIH', d, b)
                    name = _cstr(d, b + 10, rend)[0]
                    symbol = self.data_symbol(si, d, b + 4)
                    if self.func is not None:
                        if not name:
                            continue    # compiler data inside a function (jump tables)
                        self.func['locals'].append({'name': name, 'kind': 'static', 'reg': None, 'offset': None,
                                                    'type': types.ref(t, True), 'scope': self._scope(),
                                                    'symbol': symbol})
                    elif not self.stack:
                        self.globals.append({'name': name, 'symbol': symbol, 'type': types.ref(t, True),
                                             'isGlobal': _DATA[rt]})
                elif rt == 0x1102:
                    self.stack.append('thunk')
                elif rt in (0x1116, 0x113C):
                    if self.compiler is None:
                        self.compiler = _cstr(d, b + (18 if rt == 0x1116 else 22), rend)[0]
                elif rt not in _KNOWN_SYMBOLS:
                    w('unknown symbol record kind 0x%04x' % rt)
            except _BAD:
                w('truncated symbol record kind 0x%04x' % rt)
                return

    def _scope(self):
        return list(self.blocks[-1]) if self.blocks else None

    def _local(self, kind, reg, off, t, name):
        self.func['locals'].append({'name': name, 'kind': kind, 'reg': reg, 'offset': off,
                                    'type': self.types.ref(t, True), 'scope': self._scope(), 'symbol': None})

    def _end(self):
        if not self.stack:
            self.warn('S_END without an open scope')
            return
        kind = self.stack.pop()
        if kind == 'proc':
            self.func = None
            self.blocks = []
        elif kind == 'block' and self.blocks:
            self.blocks.pop()

    def _proc(self, si, d, b, rend, is_global):
        (_parent, _end, _next, length, dstart, dend, t, stored, _seg, flags) = _unpack('<IIIIIIIIHB', d, b)
        name = _cstr(d, b + 35, rend)[0]
        symbol, key, address = self.code_at(si, d, b + 28)
        if key is None:
            self.warn('unresolved relocation for procedure %s' % name)
        f = {'symbol': symbol, 'name': name, 'isGlobal': is_global, 'length': length, 'debugStart': dstart,
             'debugEnd': dend, 'procFlags': flags, 'type': self.types.ref(t, True), 'frameBytes': None,
             'frameFlags': None, 'locals': [], 'lines': []}
        self.functions.append(f)
        if key is not None:
            if symbol is None:
                self.warn('no function symbol at the code of procedure %s' % name)
            self.by_key.setdefault(key, f)
            self.start.setdefault(key, address)
        self.func = f
        self.pstart = address
        self.pstored = stored
        self.blocks = []
        self.stack.append('proc')

    def _block(self, si, d, b, rend):
        length, stored = _unpack('<II', d, b + 8)
        lo = stored - self.pstored
        if self.func is not None:
            _symbol, _key, address = self.code_at(si, d, b + 12)
            if address is not None and self.pstart is not None and address[0] == self.pstart[0]:
                lo = address[1] - self.pstart[1]
            self.blocks.append((lo, lo + length))
        self.stack.append('block')

    def lines_in(self, si, d, p, end):
        off_con, _seg, flags, _cb = _unpack('<IHHI', d, p)
        symbol, key, address = self.code_at(si, d, p)
        f = self.by_key.get(key) if key is not None else None
        if f is None:
            self.warn('lines subsection for code without a procedure record')
            return
        base = address[1] - self.start[key][1]
        out = f['lines']
        q = p + 12
        while q + 12 <= end:
            fo, n, cb = _unpack('<III', d, q)
            if cb < 12 or q + cb > end or 12 + n * 8 > cb:
                self.warn('truncated line block')
                return
            fi = self.file_index.get(fo)
            if fi is None:
                self.warn('line block refers to an unknown file offset')
            else:
                for off, v in _iter_unpack('<II', d[q + 12:q + 12 + n * 8]):
                    line = v & 0xFFFFFF
                    if line not in _NO_SOURCE:
                        out.append([base + off, fi, line])
            q += cb


def parse_object(obj):
    """Parse the CodeView sections of an object (ObjectFile or Coff) into plain dicts and lists."""
    warn = _Warnings()
    tdata = b''
    for sec in obj.sections:
        if sec is not None and sec['name'] == '.debug$T':
            tdata = sec['data']
            break
    types = _Types(tdata, warn)
    dbg = _Debug(obj, types, warn)
    dbg.run()
    if not dbg.pieces:
        warn('object has no .debug$S section')
    # every complete named class, struct, union and enum of the object
    for i, (leaf, _, _) in enumerate(types.recs):
        if leaf in _UDT:
            try:
                h = types.header(i)
            except _BAD:
                continue
            if not h['fwd'] and not _unnamed(h['name']):
                types.ref(0x1000 + i, True)
    return {'compiler': dbg.compiler, 'files': dbg.files, 'functions': dbg.functions, 'globals': dbg.globals,
            'types': types.out, 'warnings': warn.items}
