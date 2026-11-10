"""Tests for engine_map/codeview.py. The debug sections are built by hand; no compiler is needed except in the
one test that is skipped unless FNV_MSVC9_ROOT and FNV_DECOMP are set."""
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from engine_map import codeview
from engine_map.codeview import ObjectFile, parse_object
from engine_map.symdb_schema import LOCAL_KINDS, TYPE_KINDS

pack = struct.pack


# --- builders -------------------------------------------------------------------------------------------------

def cstr(text):
    return text.encode('latin-1') + b'\0'


def pad4(data, fill=b'\0'):
    return data + fill * (-len(data) % 4)


def sym_record(kind, body):
    body = pad4(body)
    return pack('<HH', len(body) + 2, kind) + body


def proc_body(name, length, type_index, stored=0, flags=0, dbg=(0, 0)):
    return pack('<IIIIIIIIHB', 0, 0, 0, length, dbg[0], dbg[1], type_index, stored, 0, flags) + cstr(name)


def block_body(length, stored, name=''):
    return pack('<IIIIH', 0, 0, length, stored, 0) + cstr(name)


def bprel(name, offset, type_index):
    return sym_record(0x110B, pack('<iI', offset, type_index) + cstr(name))


def regrel(name, offset, type_index, reg):
    return sym_record(0x1111, pack('<iIH', offset, type_index, reg) + cstr(name))


def register(name, type_index, reg):
    return sym_record(0x1106, pack('<IH', type_index, reg) + cstr(name))


def data_record(kind, name, type_index, stored=0):
    return sym_record(kind, pack('<IIH', type_index, stored, 0) + cstr(name))


def frameproc(frame_bytes, flags):
    return sym_record(0x1012, pack('<IIIIIHI', frame_bytes, 0, 0, 0, 0, 0, flags))


END = sym_record(0x0006, b'')


class DebugS:
    """A .debug$S section being built. `sub` returns the offset of the payload inside the section."""

    def __init__(self):
        self.data = bytearray(pack('<I', 4))
        self.relocs = []

    def sub(self, kind, payload, relocs=()):
        base = len(self.data) + 8
        self.data += pack('<II', kind, len(payload)) + pad4(payload)
        for off, sym, rkind in relocs:
            self.relocs.append((base + off, sym, rkind))
        return base

    def symbols(self, records):
        """records: bytes, or (bytes, [(offset in record body, symbol index, kind)])."""
        payload = bytearray()
        relocs = []
        for rec in records:
            rec, rel = rec if isinstance(rec, tuple) else (rec, ())
            for off, sym, kind in rel:
                relocs.append((len(payload) + 4 + off, sym, kind))
            payload += rec
        return self.sub(0xF1, bytes(payload), relocs)

    def section(self):
        return {'name': '.debug$S', 'data': bytes(self.data), 'flags': 0x42100040, 'relocs': list(self.relocs)}


def code_relocs(sym, field=28):
    """The SECREL + SECTION relocation pair next to an offset/segment field."""
    return [(field, sym, 0x0B), (field + 4, sym, 0x0A)]


def proc_record(kind, name, length, type_index, sym, stored=0, flags=0):
    return sym_record(kind, proc_body(name, length, type_index, stored, flags)), code_relocs(sym)


def strings_and_files(names):
    """(F3 payload, F4 payload, {name: file offset in F4}) for the given file names."""
    f3 = b'\0'
    f4 = b''
    offsets = {}
    for name in names:
        offsets[name] = len(f4)
        f4 += pack('<IBB', len(f3), 16, 1) + bytes(range(16))
        f4 = pad4(f4)
        f3 += cstr(name)
    return f3, f4, offsets


def lines_payload(blocks, sym_flags=0, length=100, columns=False):
    """blocks: [(file offset, [(offset, line)])]. Relocated field is offCon (offset 0 of the payload)."""
    out = pack('<IHHI', 0, 0, 1 if columns else 0, length)
    for fo, rows in blocks:
        body = b''.join(pack('<II', o, ln | 0x80000000) for o, ln in rows)
        if columns:
            body += b''.join(pack('<HH', 1, 9) for _ in rows)
        out += pack('<III', fo, len(rows), 12 + len(body)) + body
    return out


def sym(name, section, value=0, type_=0x20, storage=2):
    return {'name': name, 'value': value, 'section': section, 'type': type_, 'storage': storage}


class Fake:
    """Stands in for ObjectFile: sections, symbols, by_name."""

    def __init__(self, sections, symbols):
        self.sections = [None] + list(sections)
        self.symbols = dict(enumerate(symbols))
        self.by_name = {}
        for s in symbols:
            self.by_name.setdefault(s['name'], []).append(s)


def text_section(size=0x100):
    return {'name': '.text', 'data': bytes(size), 'flags': 0x60500020, 'relocs': []}


# type records ------------------------------------------------------------------------------------------------

def num(v, leaf=None):
    if leaf is None:
        assert 0 <= v < 0x8000
        return pack('<H', v)
    fmt = {0x8000: 'b', 0x8001: 'h', 0x8002: 'H', 0x8003: 'i', 0x8004: 'I', 0x8009: 'q', 0x800A: 'Q'}[leaf]
    return pack('<H' + fmt, leaf, v)


def type_pad(data):
    k = -len(data) % 4
    return data + bytes(0xF0 | n for n in range(k, 0, -1))


class Types:
    def __init__(self, filler=0):
        self.data = bytearray(pack('<I', 4))
        self.count = 0
        for _ in range(filler):
            self.add(0x1002, pack('<II', 0x0074, 0x800a))   # pointers to int that nothing uses

    def add(self, leaf, body):
        body = type_pad(body)
        self.data += pack('<HH', len(body) + 2, leaf) + body
        self.count += 1
        return 0x1000 + self.count - 1

    def section(self):
        return {'name': '.debug$T', 'data': bytes(self.data), 'flags': 0x42100040, 'relocs': []}

    def fieldlist(self, *subs):
        return self.add(0x1203, b''.join(type_pad(s) if i < len(subs) - 1 else s for i, s in enumerate(subs)))

    def struct(self, name, size, fieldlist=0, prop=0, leaf=0x1505, vshape=0):
        return self.add(leaf, pack('<HHIII', 0, prop, fieldlist, 0, vshape) + num(size) + cstr(name))

    def pointer(self, target, mode=0, attr=0):
        return self.add(0x1002, pack('<II', target, 0x800a | (mode << 5) | attr))

    def mfunction(self, ret, cls, this, args, call=11):
        al = self.arglist(args)
        return self.add(0x1009, pack('<IIIBBHIi', ret, cls, this, call, 0, len(args), al, 0))

    def arglist(self, args):
        return self.add(0x1201, pack('<I', len(args)) + b''.join(pack('<I', a) for a in args))


def member(name, type_index, offset, attr=3, offset_leaf=None):
    return pack('<HHI', 0x150D, attr, type_index) + num(offset, offset_leaf) + cstr(name)


def type_hashes(result):
    """Every hash that appears anywhere in a parse_object result."""
    out = set()
    for f in result['functions']:
        out.add(f['type'])
        out.update(l['type'] for l in f['locals'])
    out.update(g['type'] for g in result['globals'])
    for t in result['types'].values():
        out.add(t['target'])
        out.update(m['type'] for m in t['members'])
        out.update(b['type'] for b in t['bases'])
        out.update(m['type'] for m in t['methods'])
        out.update(t['args'])
        out.update(v for k, v in t['attrs'].items() if k in ('class', 'this'))
    out.discard(None)
    return out


# --- COFF assembly --------------------------------------------------------------------------------------------

def build_coff(sections, symbols):
    """sections: [(name, data, flags, relocs, overflow)]; symbols: [(name, value, section, type, storage, aux)].
    Returns the bytes of an i386 object."""
    strings = b''
    n = len(sections)
    offset = 20 + 40 * n
    headers = []
    blobs = []
    for name, data, flags, relocs, overflow in sections:
        rawptr = offset if data else 0
        offset += len(data)
        relptr = 0
        table = b''
        count = len(relocs)
        if overflow:
            table = pack('<IIH', count + 1, 0, 0)
            flags |= 0x01000000
            count = 0xFFFF
        table += b''.join(pack('<IIH', *r) for r in relocs)
        if table:
            relptr = offset
            offset += len(table)
        headers.append(pack('<8sIIIIIIHHI', name.encode(), 0, 0, len(data), rawptr, relptr, 0, count, 0, flags))
        blobs.append(data + table)
    symtab = b''
    count = 0
    for name, value, section, type_, storage, aux in symbols:
        raw = name.encode()
        if len(raw) > 8:
            raw = pack('<II', 0, 4 + len(strings))
            strings += name.encode() + b'\0'
        symtab += pack('<8sIhHBB', raw.ljust(8, b'\0'), value, section, type_, storage, aux) + bytes(18 * aux)
        count += 1 + aux
    header = pack('<HHIIIHH', 0x14c, n, 0, offset, count, 0, 0)
    return header + b''.join(headers) + b''.join(blobs) + symtab + pack('<I', 4 + len(strings)) + strings


# --- tests ----------------------------------------------------------------------------------------------------

class ObjectFileTests(unittest.TestCase):
    def assemble(self, many=False):
        relocs = [(i * 4, 0, 0x14) for i in range(70000)] if many else [(4, 1, 0x14), (9, 0, 6)]
        sections = [('.text', bytes(range(32)), 0x60500020, [(2, 1, 6)], False),
                    ('.bss', b'', 0xC0300080, [], False),
                    ('.debug$S', b'\4\0\0\0abc', 0x42100040, relocs, many)]
        symbols = [('.text', 0, 1, 0, 3, 1), ('_short', 8, 1, 0x20, 2, 0),
                   ('?a_much_longer_symbol_name@@YAXXZ', 16, 1, 0x20, 2, 0), ('_extern', 0, 0, 0, 2, 0)]
        return build_coff(sections, symbols)

    def test_read_minimal_object(self):
        obj = ObjectFile(self.assemble())
        self.assertIsNone(obj.sections[0])
        self.assertEqual([s['name'] for s in obj.sections[1:]], ['.text', '.bss', '.debug$S'])
        self.assertEqual(obj.sections[1]['data'], bytes(range(32)))
        self.assertEqual(obj.sections[2]['data'], b'')
        self.assertEqual(obj.sections[1]['relocs'], [(2, 1, 6)])
        self.assertEqual(obj.sections[3]['relocs'], [(4, 1, 0x14), (9, 0, 6)])
        # the section symbol has one auxiliary record: the next symbol has table index 2
        self.assertEqual(sorted(obj.symbols), [0, 2, 3, 4])
        self.assertEqual(obj.symbols[3]['name'], '?a_much_longer_symbol_name@@YAXXZ')
        self.assertEqual(obj.symbols[3]['value'], 16)
        self.assertEqual(obj.by_name['_short'], [obj.symbols[2]])
        self.assertEqual(obj.named('_short')['section'], 1)

    def test_path_and_bytes(self):
        data = self.assemble()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.obj'
            path.write_bytes(data)
            self.assertEqual(ObjectFile(str(path)).symbols, ObjectFile(data).symbols)
            self.assertEqual(ObjectFile(path).sections[1]['data'], ObjectFile(data).sections[1]['data'])

    def test_more_than_65535_relocations(self):
        obj = ObjectFile(self.assemble(many=True))
        relocs = obj.sections[3]['relocs']
        self.assertEqual(len(relocs), 70000)
        self.assertEqual(relocs[0], (0, 0, 0x14))
        self.assertEqual(relocs[-1], (69999 * 4, 0, 0x14))

    def test_not_an_object(self):
        with self.assertRaises(ValueError):
            ObjectFile(b'MZ' + bytes(100))


class SymbolTests(unittest.TestCase):
    def make_function_object(self):
        """.text holds f (0x00) and g (0x40); symbol 0 is f, 1 is g, 2 the section symbol, 3 static data."""
        symbols = [sym('?f@@YAXH@Z', 1, 0), sym('?g@@YAXXZ', 1, 0x40), sym('.text', 1, 0, 0, 3),
                   sym('?counter@?1??f@@YAXH@Z@4HA', 2, 8, 0, 3), sym('.data', 2, 0, 0, 3)]
        s = DebugS()
        s.symbols([
            sym_record(0x1101, pack('<I', 0) + cstr('a.obj')),
            sym_record(0x1116, pack('<IHHHHHHH', 0, 0x14c, 15, 0, 30729, 15, 0, 30729) + cstr('Test Compiler')),
            proc_record(0x1110, 'ns::f', 0x60, 0x0003, 0, flags=0x80),
            frameproc(12, 0x4000),
            bprel('this', -4, 0x0403),
            bprel('count', 8, 0x0074),
            regrel('spill', 12, 0x0075, 23),
            (sym_record(0x1103, block_body(0x20, 0x10)), [(12, 0, 0x0B), (16, 0, 0x0A)]),
            bprel('inner', -8, 0x0070),
            register('hot', 0x0074, 18),
            (sym_record(0x1103, block_body(0x08, 0x14)), [(12, 0, 0x0B), (16, 0, 0x0A)]),
            bprel('deep', -12, 0x0074),
            END,
            END,
            (data_record(0x110C, 'counter', 0x0074), [(4, 3, 0x0B), (8, 3, 0x0A)]),
            END,
            proc_record(0x110F, 'g', 0x10, 0x0003, 1),
            END,
            (data_record(0x110D, 'g_value', 0x0074), [(4, 3, 0x0B), (8, 3, 0x0A)]),
            data_record(0x110C, 'ls_value', 0x0472),
            sym_record(0x1108, pack('<I', 0x0074) + cstr('Td')),
        ])
        return Fake([text_section(), {'name': '.data', 'data': bytes(16), 'flags': 0xC0300040, 'relocs': []},
                     s.section()], symbols), symbols

    def test_procedure_locals_and_globals(self):
        obj, symbols = self.make_function_object()
        r = parse_object(obj)
        self.assertEqual(r['compiler'], 'Test Compiler')
        f, g = r['functions']
        self.assertEqual((f['symbol'], f['name'], f['isGlobal']), ('?f@@YAXH@Z', 'ns::f', True))
        self.assertEqual((f['length'], f['procFlags'], f['frameBytes'], f['frameFlags']), (0x60, 0x80, 12, 0x4000))
        self.assertEqual((g['symbol'], g['name'], g['isGlobal'], g['length']), ('?g@@YAXXZ', 'g', False, 0x10))
        self.assertEqual(g['locals'], [])
        by = {l['name']: l for l in f['locals']}
        self.assertEqual([l['name'] for l in f['locals']],
                         ['this', 'count', 'spill', 'inner', 'hot', 'deep', 'counter'])
        self.assertEqual((by['this']['kind'], by['this']['offset'], by['this']['reg']), ('bprel', -4, None))
        self.assertEqual((by['count']['kind'], by['count']['offset']), ('bprel', 8))
        self.assertEqual((by['spill']['kind'], by['spill']['reg'], by['spill']['offset']), ('regrel', 23, 12))
        self.assertEqual((by['hot']['kind'], by['hot']['reg'], by['hot']['offset']), ('register', 18, None))
        self.assertEqual(by['count']['type'], 'prim:0074')
        self.assertEqual(r['types'][by['this']['type']]['kind'], 'ptr')
        self.assertEqual(r['types'][by['this']['type']]['target'], 'prim:0003')
        # scopes: whole function, outer block, nested block
        self.assertIsNone(by['this']['scope'])
        self.assertIsNone(by['count']['scope'])
        self.assertEqual(by['inner']['scope'], [0x10, 0x30])
        self.assertEqual(by['hot']['scope'], [0x10, 0x30])
        self.assertEqual(by['deep']['scope'], [0x14, 0x1C])
        # static local: the data symbol named by the relocation
        st = by['counter']
        self.assertEqual((st['kind'], st['symbol'], st['offset'], st['reg']),
                         ('static', '?counter@?1??f@@YAXH@Z@4HA', None, None))
        # globals outside procedures
        self.assertEqual([x['name'] for x in r['globals']], ['g_value', 'ls_value'])
        self.assertEqual(r['globals'][0]['isGlobal'], True)
        self.assertEqual(r['globals'][1]['isGlobal'], False)
        self.assertEqual(r['types'][r['globals'][1]['type']]['kind'], 'ptr')
        self.assertEqual(r['globals'][0]['symbol'], '?counter@?1??f@@YAXH@Z@4HA')
        self.assertIsNone(r['globals'][1]['symbol'])
        self.assertEqual(r['warnings'], [])
        for l in f['locals']:
            self.assertIn(l['kind'], LOCAL_KINDS)

    def test_static_local_by_section_symbol(self):
        # relocation names the .data section symbol (index 4); the stored offset 8 selects symbol 3
        obj, symbols = self.make_function_object()
        sec = obj.sections[3]
        sec['relocs'] = [(o, 4 if s == 3 else s, k) for o, s, k in sec['relocs']]
        data = bytearray(sec['data'])
        for o, s, k in sec['relocs']:
            if s == 4 and k == 0x0B:
                data[o:o + 4] = pack('<I', 8)
        sec['data'] = bytes(data)
        r = parse_object(obj)
        counter = [l for l in r['functions'][0]['locals'] if l['kind'] == 'static'][0]
        self.assertEqual(counter['symbol'], '?counter@?1??f@@YAXH@Z@4HA')
        self.assertEqual(r['globals'][0]['symbol'], '?counter@?1??f@@YAXH@Z@4HA')

    def test_unknown_and_truncated_records(self):
        s = DebugS()
        s.symbols([sym_record(0x7777, b'abcd'), sym_record(0x7777, b'abcd'), sym_record(0x7778, b'')])
        # a record that claims more bytes than the subsection holds
        s.sub(0xF1, pack('<HH', 200, 0x110B) + b'\0' * 8)
        s.sub(0x9999, b'\0' * 8)
        r = parse_object(Fake([text_section(), s.section()], []))
        self.assertEqual(sum('0x7777' in w for w in r['warnings']), 1)
        self.assertEqual(sum('0x7778' in w for w in r['warnings']), 1)
        self.assertTrue(any('truncated symbol record' in w for w in r['warnings']))
        self.assertTrue(any('0x9999' in w for w in r['warnings']))
        self.assertEqual(r['functions'], [])

    def test_truncated_record_body_does_not_raise(self):
        s = DebugS()
        s.symbols([(sym_record(0x1110, b'\0' * 12), code_relocs(0)), bprel('x', 4, 0x74)])
        r = parse_object(Fake([text_section(), s.section()], [sym('f', 1)]))
        self.assertTrue(any('truncated' in w for w in r['warnings']))

    def test_unresolved_relocation(self):
        s = DebugS()
        s.symbols([sym_record(0x1110, proc_body('orphan', 4, 0x0003)), END])
        r = parse_object(Fake([text_section(), s.section()], []))
        self.assertIsNone(r['functions'][0]['symbol'])
        self.assertTrue(any('unresolved' in w for w in r['warnings']))


class LineTests(unittest.TestCase):
    def test_two_files_columns_and_no_source(self):
        f3, f4, fo = strings_and_files(['c:\\src\\a.cpp', 'c:\\inc\\b.h'])
        s = DebugS()
        s.sub(0xF3, f3)
        s.sub(0xF4, f4)
        s.symbols([proc_record(0x1110, 'f', 0x40, 0x0003, 0), END])
        blocks = [(fo['c:\\src\\a.cpp'], [(0, 10), (8, 11), (0x30, 0xFEEFEE)]),
                  (fo['c:\\inc\\b.h'], [(4, 200), (0x20, 0xF00F00), (0x24, 201)])]
        s.sub(0xF2, lines_payload(blocks, columns=True), [(0, 0, 0x0B), (4, 0, 0x0A)])
        r = parse_object(Fake([text_section(), s.section()], [sym('f', 1)]))
        self.assertEqual(r['files'], ['c:\\src\\a.cpp', 'c:\\inc\\b.h'])
        self.assertEqual(r['functions'][0]['lines'], [[0, 0, 10], [4, 1, 200], [8, 0, 11], [0x24, 1, 201]])
        self.assertEqual(r['warnings'], [])

    def test_no_columns(self):
        f3, f4, fo = strings_and_files(['a.cpp'])
        s = DebugS()
        s.sub(0xF3, f3)
        s.sub(0xF4, f4)
        s.symbols([proc_record(0x1110, 'f', 0x40, 0x0003, 0), END])
        s.sub(0xF2, lines_payload([(0, [(0, 5), (3, 6)]), (0, [(9, 7)])]), [(0, 0, 0x0B), (4, 0, 0x0A)])
        r = parse_object(Fake([text_section(), s.section()], [sym('f', 1)]))
        self.assertEqual(r['functions'][0]['lines'], [[0, 0, 5], [3, 0, 6], [9, 0, 7]])

    def test_gy_layout(self):
        """Each function in its own COMDAT section and its own .debug$S; strings and files only in the main one."""
        f3, f4, fo = strings_and_files(['a.cpp'])
        main = DebugS()
        main.symbols([sym_record(0x1116, pack('<IHHHHHHH', 0, 0x14c, 15, 0, 1, 15, 0, 1) + cstr('Compiler'))])
        main.sub(0xF3, f3)
        main.sub(0xF4, f4)
        parts = []
        for n, name in enumerate(['?one@@YAXXZ', '?two@@YAHH@Z']):
            d = DebugS()
            d.symbols([proc_record(0x1110, name[1:4], 0x20, 0x0003, n), bprel('p', 8, 0x0074), END])
            d.sub(0xF2, lines_payload([(0, [(0, 10 + n), (6, 20 + n)])]), [(0, n, 0x0B), (4, n, 0x0A)])
            parts.append(d.section())
        texts = [text_section(0x20), text_section(0x20)]
        symbols = [sym('?one@@YAXXZ', 2), sym('?two@@YAHH@Z', 3)]
        obj = Fake([main.section()] + texts + parts, symbols)
        # sections: 1 main debug, 2-3 code, 4-5 per-function debug; symbols point at the code sections 2 and 3
        r = parse_object(obj)
        self.assertEqual([f['symbol'] for f in r['functions']], ['?one@@YAXXZ', '?two@@YAHH@Z'])
        self.assertEqual(r['functions'][0]['lines'], [[0, 0, 10], [6, 0, 20]])
        self.assertEqual(r['functions'][1]['lines'], [[0, 0, 11], [6, 0, 21]])
        self.assertEqual(r['functions'][1]['locals'][0]['name'], 'p')
        self.assertEqual(r['compiler'], 'Compiler')
        self.assertEqual(r['files'], ['a.cpp'])
        self.assertEqual(r['warnings'], [])

    def test_resolution_through_function_and_section_symbols(self):
        f3, f4, fo = strings_and_files(['a.cpp'])
        s = DebugS()
        s.sub(0xF3, f3)
        s.sub(0xF4, f4)
        # fa: relocated against its function symbol (0). fb: against the section symbol (2), stored offset 0x30.
        fb = (sym_record(0x1110, proc_body('fb', 0x20, 0x0003, stored=0x30)), code_relocs(2))
        s.symbols([proc_record(0x1110, 'fa', 0x30, 0x0003, 0), END, fb,
                   (sym_record(0x1103, block_body(8, 0x34)), [(12, 2, 0x0B), (16, 2, 0x0A)]), bprel('v', -4, 0x74),
                   END, END])
        s.sub(0xF2, lines_payload([(0, [(0, 1), (0x10, 2)])]), [(0, 0, 0x0B), (4, 0, 0x0A)])
        payload = lines_payload([(0, [(0, 50), (6, 51)])])
        s.sub(0xF2, payload[:0] + pack('<I', 0x30) + payload[4:], [(0, 2, 0x0B), (4, 2, 0x0A)])
        symbols = [sym('?fa@@YAXXZ', 1, 0), sym('?fb@@YAXXZ', 1, 0x30), sym('.text', 1, 0, 0, 3)]
        r = parse_object(Fake([text_section(), s.section()], symbols))
        fa, fb = r['functions']
        self.assertEqual(fa['symbol'], '?fa@@YAXXZ')
        self.assertEqual(fb['symbol'], '?fb@@YAXXZ')
        self.assertEqual(fa['lines'], [[0, 0, 1], [0x10, 0, 2]])
        self.assertEqual(fb['lines'], [[0, 0, 50], [6, 0, 51]])
        self.assertEqual(fb['locals'][0]['scope'], [4, 12])
        self.assertEqual(r['warnings'], [])

    def test_lines_without_symbols_are_reported(self):
        f3, f4, fo = strings_and_files(['a.cpp'])
        s = DebugS()
        s.sub(0xF3, f3)
        s.sub(0xF4, f4)
        s.sub(0xF2, lines_payload([(0, [(0, 1)])]), [(0, 0, 0x0B)])
        r = parse_object(Fake([text_section(), s.section()], [sym('f', 1)]))
        self.assertTrue(r['warnings'])


class TypeTests(unittest.TestCase):
    def build(self, filler=0):
        """A forward-declared and defined class Foo with a base, bit field, methods and an unnamed member type."""
        t = Types(filler)
        ix = {}
        ix['int_ptr'] = t.pointer(0x0074)
        ix['base_fields'] = t.fieldlist(member('x', 0x0074, 0))
        ix['Base'] = t.struct('Base', 4, ix['base_fields'])
        ix['Foo_fwd'] = t.struct('Foo', 0, 0, prop=0x80)
        ix['Foo_ptr'] = t.pointer(ix['Foo_fwd'])
        ix['unnamed_fields'] = t.fieldlist(member('lo', 0x0021, 0), member('hi', 0x0021, 2))
        ix['unnamed'] = t.struct('Foo::<unnamed-type-pair>', 4, ix['unnamed_fields'], prop=8)
        ix['bits'] = t.add(0x1205, pack('<IBB', 0x0075, 3, 2))
        ix['mf_plain'] = t.mfunction(0x0003, ix['Foo_fwd'], ix['Foo_ptr'], [0x0074, ix['Foo_ptr']])
        ix['mf_static'] = t.mfunction(0x0074, ix['Foo_fwd'], 0, [], call=0)
        ix['mlist'] = t.add(0x1206, pack('<HHI', 3, 0, ix['mf_plain'])
                            + pack('<HHI', (2 << 2) | 3, 0, ix['mf_static']))
        ix['indexed'] = t.fieldlist(member('tail', 0x0074, 28, offset_leaf=0x8003))
        ix['Foo_fields'] = t.fieldlist(
            pack('<HHI', 0x1400, 3, ix['Base']) + num(0),
            pack('<HHI', 0x1409, 0, 0x1000),
            member('a', 0x0074, 4),
            member('f', ix['bits'], 8),
            member('p', ix['Foo_ptr'], 12),
            member('pair', ix['unnamed'], 16),
            pack('<HHI', 0x1511, (4 << 2) | 3, ix['mf_plain']) + pack('<I', 8) + cstr('Run'),
            pack('<HHI', 0x150F, 2, ix['mlist']) + cstr('Bar'),
            pack('<HHI', 0x1404, 0, ix['indexed']))
        ix['Foo'] = t.struct('Foo', 32, ix['Foo_fields'])
        ix['proc_args'] = t.arglist([0x0074, ix['Foo_ptr']])
        ix['proc'] = t.add(0x1008, pack('<IBBHI', 0x0003, 0, 0, 2, ix['proc_args']))
        ix['const_ptr'] = t.add(0x1001, pack('<IH', ix['int_ptr'], 1))
        ix['ref'] = t.pointer(ix['Foo'], mode=1)
        ix['array'] = t.add(0x1503, pack('<II', 0x0074, 0x0074) + num(16) + cstr(''))
        ix['enum_fields'] = t.fieldlist(
            *[pack('<HH', 0x1502, 3) + num(v, leaf) + cstr('E%d' % i) for i, (v, leaf) in enumerate([
                (5, None), (-3, 0x8000), (-300, 0x8001), (40000, 0x8002), (-100000, 0x8003), (3000000000, 0x8004),
                (-(2 ** 40), 0x8009), (2 ** 63 + 5, 0x800A)])])
        ix['Color'] = t.add(0x1507, pack('<HHII', 8, 0, 0x0074, ix['enum_fields']) + cstr('Color'))
        ix['memptr'] = t.add(0x1002, pack('<IIIH', 0x0074, 0x800a | (2 << 5), ix['Foo'], 0))
        return t, ix

    def object_for(self, filler=0):
        t, ix = self.build(filler)
        s = DebugS()
        s.symbols([
            proc_record(0x1110, 'Use', 0x20, ix['proc'], 0),
            bprel('foo', -4, ix['Foo_fwd']),         # forward reference: resolved to the definition in the object
            bprel('base', -8, ix['Base']),
            bprel('p', 8, ix['Foo_ptr']),
            bprel('c', 12, ix['const_ptr']),
            bprel('prim_ptr', 16, 0x0474),
            bprel('a', 20, ix['array']),
            bprel('e', 24, ix['Color']),
            bprel('r', 28, ix['ref']),
            END,
            data_record(0x110C, 'g_unnamed', ix['unnamed']),
            data_record(0x110C, 'g_mp', ix['memptr']),
        ])
        return Fake([text_section(), s.section(), t.section()], [sym('?Use@@YAXXZ', 1)]), ix

    def test_class_layout(self):
        obj, ix = self.object_for()
        r = parse_object(obj)
        T = r['types']
        self.assertEqual(r['warnings'], [])
        foo = [t for t in T.values() if t['kind'] == 'struct' and t['name'] == 'Foo'][0]
        self.assertEqual(foo['size'], 32)
        self.assertEqual(foo['attrs'], {'vtable': True})
        self.assertEqual([m['name'] for m in foo['members']], ['a', 'f', 'p', 'pair', 'tail'])
        by = {m['name']: m for m in foo['members']}
        self.assertEqual((by['a']['offset'], by['a']['type'], by['a']['bitPos']), (4, 'prim:0074', None))
        # bit field flattened: underlying type, position and length
        self.assertEqual((by['f']['type'], by['f']['bitPos'], by['f']['bitLen'], by['f']['offset']),
                         ('prim:0075', 2, 3, 8))
        # pointer to the class goes through udtref
        p = T[by['p']['type']]
        self.assertEqual((p['kind'], p['target'], p['size']), ('ptr', 'udtref:Foo', 4))
        self.assertEqual((T['udtref:Foo']['kind'], T['udtref:Foo']['name']), ('udtref', 'Foo'))
        # LF_INDEX continuation
        self.assertEqual(by['tail']['offset'], 28)
        # base class by udtref
        self.assertEqual(foo['bases'], [{'type': 'udtref:Base', 'offset': 0, 'virtual': False}])
        # unnamed member type: referenced directly and hashed by content
        pair = T[by['pair']['type']]
        self.assertEqual(pair['kind'], 'struct')
        self.assertEqual([m['name'] for m in pair['members']], ['lo', 'hi'])
        self.assertFalse(any(k.startswith('udtref:Foo::<unnamed') for k in T))
        # methods: LF_ONEMETHOD introducing a virtual; LF_METHOD with a list of two
        run = [m for m in foo['methods'] if m['name'] == 'Run'][0]
        self.assertEqual((run['virtual'], run['static'], run['vtOffset']), (True, False, 8))
        bars = [m for m in foo['methods'] if m['name'] == 'Bar']
        self.assertEqual([(m['virtual'], m['static'], m['vtOffset']) for m in bars],
                         [(False, False, None), (False, True, None)])
        mf = T[run['type']]
        self.assertEqual(mf['kind'], 'mfunc')
        self.assertEqual(mf['target'], 'prim:0003')
        self.assertEqual(mf['attrs']['call'], 'thiscall')
        self.assertEqual(mf['attrs']['class'], 'udtref:Foo')
        self.assertEqual(T[mf['attrs']['this']]['target'], 'udtref:Foo')
        self.assertEqual(mf['args'][0], 'prim:0074')
        self.assertEqual(T[mf['args'][1]]['target'], 'udtref:Foo')
        static = T[bars[1]['type']]
        self.assertIsNone(static['attrs']['this'])
        self.assertEqual(static['args'], [])
        # base class definition exists and is complete
        base = [t for t in T.values() if t['name'] == 'Base' and t['kind'] == 'struct'][0]
        self.assertEqual(base['members'][0]['name'], 'x')

    def test_locals_and_references(self):
        obj, ix = self.object_for()
        r = parse_object(obj)
        T = r['types']
        f = r['functions'][0]
        loc = {l['name']: T[l['type']] if l['type'] in T else None for l in f['locals']}
        raw = {l['name']: l['type'] for l in f['locals']}
        # a local of a forward-declared type refers to the definition the object has
        self.assertEqual(loc['foo']['kind'], 'struct')
        self.assertEqual(loc['foo']['size'], 32)
        base = [h for h, t in T.items() if t['name'] == 'Base' and t['kind'] == 'struct'][0]
        self.assertEqual(raw['base'], base)
        self.assertEqual((loc['p']['kind'], loc['p']['target']), ('ptr', 'udtref:Foo'))
        self.assertEqual(loc['c']['kind'], 'modifier')
        self.assertEqual(loc['c']['attrs'], {'const': True})
        self.assertEqual(loc['c']['size'], 4)
        self.assertEqual(T[loc['c']['target']]['kind'], 'ptr')
        self.assertEqual((loc['prim_ptr']['kind'], loc['prim_ptr']['target']), ('ptr', 'prim:0074'))
        self.assertEqual(T['prim:0074']['name'], 'int')
        self.assertEqual(T['prim:0074']['attrs'], {'cv': 0x74})
        self.assertEqual((loc['a']['kind'], loc['a']['size'], loc['a']['attrs']['count'], loc['a']['target']),
                         ('array', 16, 4, 'prim:0074'))
        self.assertEqual((loc['r']['kind'], loc['r']['target']), ('ref', 'udtref:Foo'))
        proc = T[f['type']]
        self.assertEqual((proc['kind'], proc['target'], proc['attrs']), ('proc', 'prim:0003', {'call': 'cdecl'}))
        self.assertEqual(proc['args'][0], 'prim:0074')
        self.assertEqual(T[proc['args'][1]]['target'], 'udtref:Foo')
        # globals: unnamed struct referenced directly, member pointer
        g = {x['name']: T[x['type']] for x in r['globals']}
        self.assertEqual(g['g_unnamed']['kind'], 'struct')
        self.assertEqual(g['g_mp']['kind'], 'memptr')
        self.assertEqual((g['g_mp']['attrs']['mode'], g['g_mp']['attrs']['class']), ('data', 'udtref:Foo'))

    def test_enum_and_numeric_leaves(self):
        obj, ix = self.object_for()
        T = parse_object(obj)['types']
        color = [t for t in T.values() if t['kind'] == 'enum'][0]
        self.assertEqual((color['name'], color['size'], color['target']), ('Color', 4, 'prim:0074'))
        self.assertEqual(color['enumerators'], [
            ['E0', 5], ['E1', -3], ['E2', -300], ['E3', 40000], ['E4', -100000], ['E5', 3000000000],
            ['E6', -(2 ** 40)], ['E7', 2 ** 63 + 5]])

    def test_hash_stability_and_closure(self):
        a = parse_object(self.object_for(0)[0])
        b = parse_object(self.object_for(7)[0])
        ha = {t['name']: h for h, t in a['types'].items() if t['kind'] in ('struct', 'enum') and t['name']}
        hb = {t['name']: h for h, t in b['types'].items() if t['kind'] in ('struct', 'enum') and t['name']}
        self.assertEqual(ha, hb)
        self.assertEqual(set(a['types']), set(b['types']))
        self.assertTrue(all(len(h) == 20 for h in ha.values()))
        for r in (a, b):
            self.assertTrue(type_hashes(r) <= set(r['types']), type_hashes(r) - set(r['types']))
            for h, t in r['types'].items():
                self.assertIn(t['kind'], TYPE_KINDS)
                if t['kind'] == 'prim':
                    self.assertEqual(h, 'prim:%04x' % t['attrs']['cv'])
                elif t['kind'] == 'udtref':
                    self.assertEqual(h, 'udtref:' + t['name'])
                else:
                    self.assertEqual(len(h), 20)

    def test_forward_only_gives_udtref(self):
        t = Types()
        fwd = t.struct('Opaque', 0, 0, prop=0x80)
        s = DebugS()
        s.symbols([proc_record(0x1110, 'f', 4, 0x0003, 0), bprel('o', -4, fwd), END])
        r = parse_object(Fake([text_section(), s.section(), t.section()], [sym('f', 1)]))
        self.assertEqual(r['functions'][0]['locals'][0]['type'], 'udtref:Opaque')
        self.assertEqual(r['types']['udtref:Opaque']['kind'], 'udtref')
        self.assertNotIn('struct', [x['kind'] for x in r['types'].values()])

    def test_complete_named_types_are_included_without_use(self):
        t = Types()
        fields = t.fieldlist(member('v', 0x0074, 0))
        t.struct('Lonely', 4, fields)
        r = parse_object(Fake([text_section(), t.section()], []))
        self.assertEqual([x['name'] for x in r['types'].values() if x['kind'] == 'struct'], ['Lonely'])

    def test_unknown_leaf_and_deep_chain(self):
        t = Types()
        weird = t.add(0x1ABC, b'\0' * 8)
        chain = 0x0074
        for _ in range(400):
            chain = t.pointer(chain)
        s = DebugS()
        s.symbols([proc_record(0x1110, 'f', 4, 0x0003, 0), bprel('w', -4, weird), bprel('deep', -8, chain), END,
                   bprel('x', 0, weird)])
        r = parse_object(Fake([text_section(), s.section(), t.section()], [sym('f', 1)]))
        loc = {l['name']: l for l in r['functions'][0]['locals']}
        self.assertEqual(r['types'][loc['w']['type']]['kind'], 'other')
        self.assertEqual(r['types'][loc['w']['type']]['attrs'], {'leaf': 0x1ABC})
        self.assertEqual(sum('0x1abc' in w for w in r['warnings']), 1)
        self.assertIn(loc['deep']['type'], r['types'])
        self.assertTrue(type_hashes(r) <= set(r['types']))

    def test_truncated_type_record(self):
        t = Types()
        t.add(0x1002, b'\1\0')      # pointer record cut short
        t.data += pack('<HH', 400, 0x1505) + b'\0' * 10
        s = DebugS()
        s.symbols([proc_record(0x1110, 'f', 4, 0x0003, 0), bprel('v', -4, 0x1000), END])
        r = parse_object(Fake([text_section(), s.section(), t.section()], [sym('f', 1)]))
        self.assertTrue(any('truncated' in w or 'malformed' in w for w in r['warnings']))
        self.assertEqual(r['types'][r['functions'][0]['locals'][0]['type']]['kind'], 'other')

    def test_padding_bytes_in_field_lists(self):
        t = Types()
        # names of 1, 2 and 3 characters give every amount of padding
        fields = t.fieldlist(member('a', 0x0074, 0), member('bb', 0x0074, 4), member('ccc', 0x0074, 8),
                             member('dddd', 0x0074, 12))
        t.struct('P', 16, fields)
        r = parse_object(Fake([text_section(), t.section()], []))
        p = [x for x in r['types'].values() if x['name'] == 'P'][0]
        self.assertEqual([(m['name'], m['offset']) for m in p['members']],
                         [('a', 0), ('bb', 4), ('ccc', 8), ('dddd', 12)])
        self.assertEqual([w for w in r['warnings'] if 'debug$S' not in w], [])


@unittest.skipUnless(os.environ.get('FNV_MSVC9_ROOT') and os.environ.get('FNV_DECOMP'),
                     'needs FNV_MSVC9_ROOT and FNV_DECOMP')
class RealCompilerTests(unittest.TestCase):
    SOURCE = '''struct Point {
    int x;
    int y;
    float weight;
    unsigned flags : 3;
    virtual int Sum() const;
};

int Point::Sum() const
{
    return x + y;
}

int AddScaled(int a, const Point* p)
{
    int total = a + p->x;
    total += p->y;
    return total;
}
'''

    def compile(self, tmp, flags):
        source = Path(tmp) / 'sample.cpp'
        source.write_text(self.SOURCE)
        obj = Path(tmp) / ('sample%d.obj' % len(flags))
        cmd = [sys.executable, str(Path(os.environ['FNV_DECOMP']) / 'tools' / 'msvc.py'), '--compiler', 'sp1',
               '/c', '/nologo', '/Z7'] + flags + [str(source), '/Fo' + str(obj)]
        done = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return parse_object(ObjectFile(obj))

    def check(self, r, optimised):
        funcs = {f['name']: f for f in r['functions']}
        self.assertEqual(sorted(funcs), ['AddScaled', 'Point::Sum'])
        self.assertEqual(funcs['AddScaled']['symbol'], '?AddScaled@@YAHHPBUPoint@@@Z')
        self.assertEqual(funcs['Point::Sum']['symbol'], '?Sum@Point@@UBEHXZ')
        add = funcs['AddScaled']
        params = {l['name']: l for l in add['locals']}
        self.assertEqual(params['a']['kind'], 'bprel')
        self.assertEqual(params['a']['offset'], 4 if optimised else 8)
        self.assertEqual(params['p']['offset'], 8 if optimised else 12)
        self.assertEqual(r['types'][params['p']['type']]['kind'], 'ptr')
        lines = {line for _, _, line in add['lines']}
        self.assertTrue({15, 19} <= lines, lines)
        self.assertTrue(lines <= set(range(15, 20)), lines)
        self.assertEqual([x[0] for x in add['lines']], sorted(x[0] for x in add['lines']))
        self.assertEqual({line for _, _, line in funcs['Point::Sum']['lines']} - {10, 11, 12}, set())
        self.assertTrue(r['files'][0].lower().endswith('sample.cpp'))
        point = [t for t in r['types'].values() if t['name'] == 'Point' and t['kind'] == 'struct'][0]
        self.assertEqual(point['size'], 20)
        self.assertEqual(point['attrs'], {'vtable': True})
        self.assertEqual([(m['name'], m['offset']) for m in point['members']],
                         [('x', 4), ('y', 8), ('weight', 12), ('flags', 16)])
        self.assertEqual(point['members'][3]['bitLen'], 3)
        self.assertTrue(type_hashes(r) <= set(r['types']))

    def test_od_and_o2_gy(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.check(self.compile(tmp, ['/Od']), False)
            self.check(self.compile(tmp, ['/O2', '/Gy']), True)


if __name__ == '__main__':
    unittest.main()
