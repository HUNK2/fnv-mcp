"""Symbol database builder tests: store step, path mapping, selection, double-compile check, driver.
No compiler and no real parser are needed: worker results are fabricated."""
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from engine_map import symdb, symdb_schema


def unit(source='src/bscore/bsrandom.cpp', functions=None, symbols=None, compiler='sp1'):
    return {'source': source, 'compiler': compiler, 'flags': ['/MT', '/Od'],
            'functions': functions if functions is not None else [
                {'address': '0x00400000', 'symbol': '?A@@YAXXZ', 'size': 16},
                {'address': '0x00400020', 'symbol': '?B@@YAXXZ', 'size': 8}],
            'symbols': symbols or {}}


def prim(name, cv, size):
    return {'kind': 'prim', 'name': name, 'size': size, 'attrs': {'cv': cv}}


def make_types():
    return {
        'prim:0074': prim('int', 0x74, 4),
        'prim:0003': prim('void', 3, 0),
        'udtref:Foo': {'kind': 'udtref', 'name': 'Foo'},
        'ptrfoo': {'kind': 'ptr', 'size': 4, 'target': 'udtref:Foo', 'attrs': {'const': True}},
        'foo': {'kind': 'struct', 'name': 'Foo', 'size': 8, 'attrs': {'vtable': True},
                'members': [{'name': 'x', 'offset': 0, 'type': 'prim:0074', 'bitPos': None, 'bitLen': None},
                            {'name': 'f', 'offset': 4, 'type': 'prim:0074', 'bitPos': 3, 'bitLen': 2}],
                'bases': [{'type': 'udtref:Foo', 'offset': 0, 'virtual': True}],
                'methods': [{'name': 'Run', 'type': 'proc', 'virtual': True, 'static': False, 'vtOffset': 8},
                            {'name': 'Make', 'type': None, 'virtual': False, 'static': True, 'vtOffset': None}]},
        'color': {'kind': 'enum', 'name': 'Color', 'size': 4, 'target': 'prim:0074',
                  'enumerators': [['Red', 0], ['Blue', 5]]},
        'proc': {'kind': 'proc', 'target': 'prim:0003', 'attrs': {'call': 'cdecl'}, 'args': ['ptrfoo', 'prim:0074']},
    }


def make_parsed(files=None):
    return {
        'compiler': 'x', 'warnings': [],
        'files': files or ['c:\\work\\x\\src\\bscore\\bsrandom.cpp', 'c:\\sdk\\include\\stdio.h'],
        'types': make_types(),
        'functions': [
            {'symbol': '?A@@YAXXZ', 'name': 'A', 'isGlobal': True, 'length': 16, 'debugStart': 3, 'debugEnd': 12,
             'procFlags': 0, 'type': 'proc', 'frameBytes': 20, 'frameFlags': 5,
             'locals': [{'name': 'p', 'kind': 'bprel', 'reg': None, 'offset': 8, 'type': 'ptrfoo', 'scope': None,
                         'symbol': None},
                        {'name': 'i', 'kind': 'bprel', 'reg': None, 'offset': -4, 'type': 'prim:0074',
                         'scope': [2, 10], 'symbol': None}],
             'lines': [[0, 0, 10], [4, 0, 11], [4, 1, 99], [15, 0, 12], [16, 0, 13], [40, 0, 14], [6, 5, 7]]},
            {'symbol': '?B@@YAXXZ', 'name': 'B', 'isGlobal': False, 'length': 8, 'debugStart': 0, 'debugEnd': 8,
             'procFlags': 1, 'type': 'proc', 'frameBytes': None, 'frameFlags': None, 'locals': [
                 {'name': 'q', 'kind': 'register', 'reg': 17, 'offset': None, 'type': 'prim:0074', 'scope': None}],
             'lines': [[0, 0, 20]]}],
        'globals': [{'name': 'g1', 'symbol': '?g1@@3HA', 'type': 'prim:0074', 'isGlobal': True},
                    {'name': 'g2', 'symbol': '?g2@@3HA', 'type': 'foo', 'isGlobal': False},
                    {'name': 'g3', 'symbol': None, 'type': None, 'isGlobal': True}],
    }


def make_result(u, verified, parsed=None, status='ok', error=None):
    return {'source': u['source'], 'compiler': u['compiler'], 'flags': ' '.join(u['flags']), 'status': status,
            'error': error, 'seconds': 1.5, 'mode': 'image', 'verified': verified,
            'parsed': parsed}


def make_tree(root):
    for rel in ('src/bscore/BSRandom.cpp', 'include/Foo.h'):
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x')


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        make_tree(self.root)
        self.db = sqlite3.connect(':memory:')
        symdb_schema.create(self.db)
        self.store = symdb.Store(self.db, symdb.PathMapper(self.root))
        self.lower = str(self.root).replace('\\', '/').lower()

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def q(self, sql, *args):
        return self.db.execute(sql, args).fetchall()

    def files_for(self):
        return [self.lower.replace('/', '\\') + '\\src\\bscore\\bsrandom.cpp', 'c:\\sdk\\include\\stdio.h']

    def add_main(self):
        u = unit(symbols={'?g1@@3HA': '0x00700010'})
        r = make_result(u, [0x400000, 0x400020], make_parsed(self.files_for()))
        return u, self.store.add_unit(u, r)


class PathTests(StoreCase):
    def test_mapping(self):
        m = symdb.PathMapper(self.root)
        self.assertEqual(m.map(self.lower + '/src/bscore/bsrandom.cpp'), 'src/bscore/BSRandom.cpp')
        self.assertEqual(m.map(self.lower.replace('/', '\\') + '\\include\\foo.h'), 'include/Foo.h')
        self.assertEqual(m.map('C:\\SDK\\Include\\stdio.h'), 'C:/SDK/Include/stdio.h')
        self.assertEqual(m.map(self.lower + '/src/missing.h'), 'src/missing.h')


class StoreTests(StoreCase):
    def test_functions_lines_locals(self):
        u, unit_id = self.add_main()
        self.assertEqual(self.q('SELECT status, functions, verified, seconds, flags FROM units'),
                         [('ok', 2, 2, 1.5, '/MT /Od')])
        rows = self.q('SELECT addr, size, name, decorated, verified, has_debug, proc_flags, frame_bytes, '
                      'frame_flags, debug_start, debug_end FROM functions ORDER BY addr')
        self.assertEqual(rows[0], (0x400000, 16, 'A', '?A@@YAXXZ', 1, 1, 0, 20, 5, 3, 12))
        self.assertEqual(rows[1][:2], (0x400020, 8))
        # offsets 16 and 40 are at or beyond the size and dropped; offset 4 has one row (the last record);
        # file index 5 does not exist
        lines = self.q('SELECT l.offset, f.path, l.line FROM lines l JOIN files f ON f.id = l.file_id '
                       'WHERE func_addr = ? ORDER BY offset', 0x400000)
        self.assertEqual(lines, [(0, 'src/bscore/BSRandom.cpp', 10), (4, 'c:/sdk/include/stdio.h', 99),
                                 (15, 'src/bscore/BSRandom.cpp', 12)])
        self.assertEqual(self.q('SELECT path FROM files ORDER BY id'),
                         [('src/bscore/BSRandom.cpp',), ('c:/sdk/include/stdio.h',)])
        loc = self.q('SELECT seq, name, kind, reg, offset, scope_lo, scope_hi, type_id FROM locals '
                     'WHERE func_addr = ? ORDER BY seq', 0x400000)
        self.assertEqual([r[:7] for r in loc], [(0, 'p', 'bprel', None, 8, None, None),
                                                (1, 'i', 'bprel', None, -4, 2, 10)])

    def test_unverified_function_has_no_lines_or_locals(self):
        u = unit()
        self.store.add_unit(u, make_result(u, [0x400020], make_parsed(self.files_for()), status='partial'))
        self.assertEqual(self.q('SELECT addr, verified, has_debug, name FROM functions ORDER BY addr'),
                         [(0x400000, 0, 1, 'A'), (0x400020, 1, 1, 'B')])
        self.assertEqual(self.q('SELECT DISTINCT func_addr FROM lines'), [(0x400020,)])
        self.assertEqual(self.q('SELECT DISTINCT func_addr FROM locals'), [(0x400020,)])
        self.assertEqual(self.q('SELECT verified FROM units'), [(1,)])

    def test_failed_unit_still_lists_functions(self):
        u = unit(compiler='vc8')
        self.store.add_unit(u, make_result(u, [], None, status='no-compiler', error='FNV_MSVC8_ROOT is not set'))
        self.assertEqual(self.q('SELECT status, error, verified FROM units'),
                         [('no-compiler', 'FNV_MSVC8_ROOT is not set', 0)])
        self.assertEqual(self.q('SELECT COUNT(*), SUM(has_debug), SUM(verified) FROM functions'), [(2, 0, 0)])

    def test_unit_without_debug_for_a_function(self):
        u = unit()
        parsed = make_parsed(self.files_for())
        parsed['functions'].pop(1)
        self.store.add_unit(u, make_result(u, [0x400000, 0x400020], parsed))
        self.assertEqual(self.q('SELECT has_debug, name, type_id FROM functions WHERE addr = ?', 0x400020),
                         [(0, None, None)])

    def test_fold_alias_finds_the_procedure(self):
        u = unit(functions=[{'address': '0x00400000', 'symbol': '?Z@@YAXXZ', 'size': 16,
                             'fold_aliases': ['?A@@YAXXZ']}])
        self.store.add_unit(u, make_result(u, [0x400000], make_parsed(self.files_for())))
        self.assertEqual(self.q('SELECT name, decorated FROM functions'), [('A', '?Z@@YAXXZ')])

    def test_duplicate_address_first_verified_wins(self):
        a = unit('src/a.cpp', [{'address': '0x00400000', 'symbol': '?A@@YAXXZ', 'size': 16}])
        b = unit('src/b.cpp', [{'address': '0x00400000', 'symbol': '?A@@YAXXZ', 'size': 16}])
        c = unit('src/c.cpp', [{'address': '0x00400000', 'symbol': '?A@@YAXXZ', 'size': 16}])
        parsed = lambda: make_parsed(self.files_for())  # noqa: E731
        ida = self.store.add_unit(a, make_result(a, [], parsed(), 'partial'), order=0)
        idb = self.store.add_unit(b, make_result(b, [0x400000], parsed()), order=1)
        self.assertEqual(self.q('SELECT unit_id, verified FROM functions'), [(idb, 1)])
        self.assertEqual(self.q('SELECT COUNT(*) FROM lines'), [(3,)])
        self.store.add_unit(c, make_result(c, [0x400000], parsed()), order=2)   # also verified, but later
        self.assertEqual(self.q('SELECT unit_id FROM functions'), [(idb,)])
        self.assertEqual(self.q('SELECT COUNT(*) FROM lines'), [(3,)])
        self.assertEqual(self.q('SELECT COUNT(*) FROM locals'), [(2,)])
        self.assertNotEqual(ida, idb)
        # a verified unit that arrives first is also kept against an earlier-numbered unverified one
        self.assertEqual(self.q('SELECT COUNT(*) FROM units'), [(3,)])

    def test_duplicate_source_gets_a_suffix(self):
        u = unit()
        self.store.add_unit(u, make_result(u, [], None, 'compile-failed'))
        v = unit(functions=[{'address': '0x00500000', 'symbol': '?C@@YAXXZ', 'size': 4}])
        self.store.add_unit(v, make_result(v, [], None, 'compile-failed'))
        self.assertEqual(self.q('SELECT source FROM units ORDER BY id'),
                         [('src/bscore/bsrandom.cpp',), ('src/bscore/bsrandom.cpp#2',)])
        self.assertEqual(symdb.unit_keys([u, u, v]), ['src/bscore/bsrandom.cpp', 'src/bscore/bsrandom.cpp#2',
                                                      'src/bscore/bsrandom.cpp#3'])


class TypeTests(StoreCase):
    def test_types_children_and_ids(self):
        self.add_main()
        ids = {h: i for i, h in self.q('SELECT id, hash FROM types')}
        self.assertEqual(set(ids), set(make_types()))
        row = self.q('SELECT kind, name, size, target_id, attrs FROM types WHERE hash = ?', 'ptrfoo')[0]
        self.assertEqual(row, ('ptr', None, 4, ids['udtref:Foo'], '{"const":true}'))
        self.assertIsNone(self.q('SELECT attrs FROM types WHERE hash = ?', 'color')[0][0])
        self.assertEqual(self.q('SELECT kind, name FROM types WHERE hash = ?', 'udtref:Foo'), [('udtref', 'Foo')])
        foo = ids['foo']
        self.assertEqual(self.q('SELECT seq, name, offset, type_id2, bit_pos, bit_len FROM members WHERE type_id = ?',
                                foo), [(0, 'x', 0, ids['prim:0074'], None, None),
                                       (1, 'f', 4, ids['prim:0074'], 3, 2)])
        self.assertEqual(self.q('SELECT seq, base_id, offset, is_virtual FROM bases WHERE type_id = ?', foo),
                         [(0, ids['udtref:Foo'], 0, 1)])
        self.assertEqual(self.q('SELECT seq, name, type_id2, is_virtual, is_static, vt_offset FROM methods '
                                'WHERE type_id = ? ORDER BY seq', foo),
                         [(0, 'Run', ids['proc'], 1, 0, 8), (1, 'Make', None, 0, 1, None)])
        self.assertEqual(self.q('SELECT seq, name, value FROM enums WHERE type_id = ?', ids['color']),
                         [(0, 'Red', 0), (1, 'Blue', 5)])
        self.assertEqual(self.q('SELECT seq, type_id2 FROM args WHERE type_id = ? ORDER BY seq', ids['proc']),
                         [(0, ids['ptrfoo']), (1, ids['prim:0074'])])
        self.assertEqual(self.q('SELECT target_id FROM types WHERE hash = ?', 'proc'), [(ids['prim:0003'],)])
        self.assertEqual(self.q('SELECT type_id FROM functions WHERE addr = ?', 0x400000), [(ids['proc'],)])
        self.assertEqual(self.q('SELECT type_id FROM locals WHERE func_addr = ? AND seq = 0', 0x400000),
                         [(ids['ptrfoo'],)])

    def test_seen_counts_units(self):
        self.add_main()
        u = unit('src/other.cpp', [{'address': '0x00500000', 'symbol': '?A@@YAXXZ', 'size': 16}])
        types = make_types()
        types['extra'] = prim('char', 0x70, 1)
        parsed = make_parsed(self.files_for())
        parsed['types'] = types
        self.store.add_unit(u, make_result(u, [0x500000], parsed))
        seen = dict(self.q('SELECT hash, seen FROM types'))
        self.assertEqual(seen['foo'], 2)
        self.assertEqual(seen['udtref:Foo'], 2)
        self.assertEqual(seen['extra'], 1)
        self.assertEqual(self.q('SELECT COUNT(*) FROM types'), [(len(types),)])
        self.assertEqual(self.q('SELECT COUNT(*) FROM members'), [(2,)])     # children written once


class GlobalsAndMetaTests(StoreCase):
    def test_globals(self):
        _, unit_id = self.add_main()
        rows = self.q('SELECT name, decorated, addr, type_id IS NULL, unit_id, is_global FROM globals ORDER BY id')
        self.assertEqual(rows, [('g1', '?g1@@3HA', 0x700010, 0, unit_id, 1),
                                ('g2', '?g2@@3HA', None, 0, unit_id, 0),
                                ('g3', None, None, 1, unit_id, 1)])

    def test_meta_keys(self):
        self.add_main()
        counts = self.store.finish({'decompPath': 'x', 'decompCommit': None, 'builtAt': 'now', 'mode': 'image',
                                    'imageSha256': 'ab', 'seconds': 2, 'unitsDoubleCompile': 0})
        meta = dict(self.q('SELECT key, value FROM meta'))
        self.assertEqual(set(meta), set(symdb_schema.META_KEYS) - {'decompCommit'})
        self.assertEqual(meta['units'], '1')
        self.assertEqual(meta['unitsOk'], '1')
        self.assertEqual(meta['unitsFailed'], '0')
        self.assertEqual(meta['functions'], '2')
        self.assertEqual(meta['functionsVerified'], '2')
        self.assertEqual(meta['functionsWithLines'], '2')
        self.assertEqual(meta['types'], str(len(make_types())))
        self.assertEqual(counts['types'], len(make_types()))
        self.assertEqual(meta['schema'], '1')


class Coff:
    """A fake object: one .text section; functions are (name, offset, size)."""

    def __init__(self, data, relocs, functions):
        self.sections = [None, {'name': '.text', 'data': data, 'relocs': relocs, 'flags': 0}]
        self.symbols = {i: {'name': n, 'value': v, 'section': 1, 'type': 0x20, 'storage': 2}
                        for i, (n, v, _) in enumerate(functions)}

    def named(self, name):
        found = [s for s in self.symbols.values() if s['name'] == name]
        if len(found) != 1:
            raise ValueError('expected one defined symbol: ' + name)
        return found[0]


class SameCodeTests(unittest.TestCase):
    FUNCS = [{'address': '0x00401000', 'symbol': 'f', 'size': 12}, {'address': '0x00401010', 'symbol': 'g', 'size': 8}]

    def coff(self, addend, body=b'\x90', tail=b'\x33\xc0\xc3\xcc'):
        f = b'\xe8' + addend.to_bytes(4, 'little') + body * 7
        g = b'\xa1' + (0).to_bytes(4, 'little') + tail[:3]
        data = f + b'\xcc' * 4 + g + b'\xcc' * 4
        return Coff(data, [(1, 0, 0x14), (17, 0, 6)], [('f', 0, 12), ('g', 16, 8)])

    def test_equal_with_other_addends(self):
        self.assertEqual(symdb.same_code(self.coff(0), self.coff(0x1234), self.FUNCS), {0x401000, 0x401010})

    def test_one_changed_byte(self):
        got = symdb.same_code(self.coff(0), self.coff(5, body=b'\x91'), self.FUNCS)
        self.assertEqual(got, {0x401010})
        other = self.coff(0, tail=b'\x33\xc0\xc2')
        self.assertEqual(symdb.same_code(self.coff(0), other, self.FUNCS), {0x401000})

    def test_different_relocation_kind(self):
        b = self.coff(0)
        b.sections[1]['relocs'] = [(1, 0, 6), (17, 0, 6)]
        self.assertEqual(symdb.same_code(self.coff(0), b, self.FUNCS), {0x401010})

    def test_missing_symbol(self):
        funcs = self.FUNCS + [{'address': '0x00402000', 'symbol': 'nope', 'size': 4}]
        self.assertEqual(symdb.same_code(self.coff(0), self.coff(1), funcs), {0x401000, 0x401010})


class SelectionTests(unittest.TestCase):
    UNITS = [unit('src/bscore/bsrandom.cpp'), unit('src/fallout/misc/main.cpp'), unit('src/NiRenderedTexture.cpp'),
             unit('src/bscore/other.cpp')]

    def test_only(self):
        got = symdb.select_units(self.UNITS, ['src/bscore/bsrandom.cpp', 'NIRENDERED'])
        self.assertEqual([u['source'] for u in got], ['src/bscore/bsrandom.cpp', 'src/NiRenderedTexture.cpp'])
        self.assertEqual(len(symdb.select_units(self.UNITS, ['bscore'])), 2)
        self.assertEqual(symdb.select_units(self.UNITS, ['nothing']), [])

    def test_limit_after_only(self):
        self.assertEqual(len(symdb.select_units(self.UNITS)), 4)
        self.assertEqual([u['source'] for u in symdb.select_units(self.UNITS, ['bscore'], 1)],
                         ['src/bscore/bsrandom.cpp'])
        self.assertEqual(len(symdb.select_units(self.UNITS, limit=3)), 3)


class WorkerTests(unittest.TestCase):
    def test_no_compiler(self):
        u = unit(compiler='vc8')
        r = symdb.build_unit(u, 'C:/nowhere', env={})
        self.assertEqual(r['status'], 'no-compiler')
        self.assertIn('FNV_MSVC8_ROOT', r['error'])
        self.assertIsNone(r['parsed'])

    def test_rtm_root_falls_back(self):
        with mock.patch.dict(os.environ, {'FNVCHECK_RTM_ROOT': 'R'}, clear=False):
            os.environ.pop('FNV_MSVC9_RTM_ROOT', None)
            self.assertEqual(symdb.compiler_env()['FNV_MSVC9_RTM_ROOT'], 'R')
        with mock.patch.dict(os.environ, {'FNVCHECK_RTM_ROOT': 'R', 'FNV_MSVC9_RTM_ROOT': 'Q'}):
            self.assertEqual(symdb.compiler_env()['FNV_MSVC9_RTM_ROOT'], 'Q')

    def test_compile_failed_and_cleanup(self):
        made = []
        real = tempfile.mkdtemp

        def mk(*a, **k):
            made.append(real(*a, **k))
            return made[-1]
        with mock.patch.object(symdb.tempfile, 'mkdtemp', mk), \
                mock.patch.object(symdb, 'compile_object', return_value=(False, 'error C2065')):
            r = symdb.build_unit(unit(), 'C:/nowhere', env={'FNV_MSVC9_ROOT': 'x'})
        self.assertEqual((r['status'], r['error']), ('compile-failed', 'error C2065'))
        self.assertTrue(made and not os.path.exists(made[0]))
        self.assertTrue(os.path.basename(made[0]).startswith('fnvsymdb-'))

    def fake_parser(self, parsed=None, fail=False):
        parser = mock.Mock()
        parser.ObjectFile.side_effect = lambda p: p
        parser.parse_object.return_value = parsed or {'files': [], 'functions': [], 'globals': [], 'types': {}}
        if fail:
            parser.parse_object.side_effect = ValueError('bad')
        return parser

    def run_worker(self, verified, parser, pe=None, mode='image'):
        with mock.patch.object(symdb, 'compile_object', return_value=(True, '')), \
                mock.patch.object(symdb, '_parser', return_value=parser), \
                mock.patch.object(symdb, 'verify_image', return_value=verified), \
                mock.patch.dict('sys.modules', {'compare': mock.Mock(Coff=lambda p: None)}):
            return symdb.build_unit(unit(), 'C:/nowhere', pe=pe, env={'FNV_MSVC9_ROOT': 'x'})

    def test_ok_partial_parse_error(self):
        r = self.run_worker({0x400000, 0x400020}, self.fake_parser(), pe=object())
        self.assertEqual((r['status'], r['mode'], r['verified']), ('ok', 'image', [0x400000, 0x400020]))
        r = self.run_worker({0x400000}, self.fake_parser(), pe=object())
        self.assertEqual(r['status'], 'partial')
        self.assertIn('1 of 2', r['error'])
        r = self.run_worker({0x400000}, self.fake_parser(fail=True), pe=object())
        self.assertEqual(r['status'], 'parse-error')
        self.assertEqual(r['verified'], [0x400000])

    def recording_parser(self):
        parser = mock.Mock()
        parser.calls = []

        def object_file(path):
            parser.calls.append(os.path.basename(path))
            return 'obj:' + os.path.basename(path)
        parser.ObjectFile = object_file
        parser.parse_object.return_value = {'files': [], 'functions': [], 'globals': [], 'types': {}}
        return parser

    def test_image_check_uses_object_file(self):
        parser = self.recording_parser()
        seen = []
        with mock.patch.object(symdb, 'compile_object', return_value=(True, '')), \
                mock.patch.object(symdb, '_parser', return_value=parser), \
                mock.patch.object(symdb, 'verify_image', side_effect=lambda u, o, pe: seen.append(o) or {0x400000}):
            r = symdb.build_unit(unit(), 'C:/nowhere', pe=object(), env={'FNV_MSVC9_ROOT': 'x'})
        self.assertEqual(seen, ['obj:unit.obj'])
        self.assertEqual((r['mode'], r['fallback']), ('image', None))

    def test_double_compile_uses_object_file_for_both(self):
        parser = self.recording_parser()
        got = []
        with mock.patch.object(symdb, 'compile_object', return_value=(True, '')), \
                mock.patch.object(symdb, '_parser', return_value=parser), \
                mock.patch.object(symdb, 'same_code', side_effect=lambda a, b, f: got.append((a, b)) or {0x400000}):
            r = symdb.build_unit(unit(), 'C:/nowhere', pe=None, env={'FNV_MSVC9_ROOT': 'x'})
        self.assertEqual(got, [('obj:unit.obj', 'obj:plain.obj')])
        self.assertEqual((r['mode'], r['status'], r['fallback']), ('double-compile', 'partial', None))

    def test_image_error_falls_back_to_double_compile(self):
        parser = self.recording_parser()
        with mock.patch.object(symdb, 'compile_object', return_value=(True, '')), \
                mock.patch.object(symdb, '_parser', return_value=parser), \
                mock.patch.object(symdb, 'verify_image', side_effect=ValueError('too many relocations')), \
                mock.patch.object(symdb, 'same_code', return_value={0x400000, 0x400020}):
            r = symdb.build_unit(unit(), 'C:/nowhere', pe=object(), env={'FNV_MSVC9_ROOT': 'x'})
        self.assertEqual((r['mode'], r['status']), ('double-compile', 'ok'))
        self.assertIn('too many relocations', r['fallback'])

    def test_no_check_gives_mode_none(self):
        parser = self.recording_parser()
        with mock.patch.object(symdb, 'compile_object', side_effect=[(True, ''), (False, 'boom')]), \
                mock.patch.object(symdb, '_parser', return_value=parser):
            r = symdb.build_unit(unit(), 'C:/nowhere', pe=None, env={'FNV_MSVC9_ROOT': 'x'})
        self.assertIsNone(r['mode'])
        self.assertEqual(r['status'], 'partial')


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.decomp = self.root / 'decomp'
        make_tree(self.decomp)
        (self.decomp / 'build').mkdir()
        self.units = [unit('src/u%d.cpp' % n, [{'address': '0x%08x' % (0x400000 + n * 0x100), 'symbol': '?A@@YAXXZ',
                                                'size': 16}]) for n in range(5)]
        self.out = self.root / 'out' / 'symdb.sqlite'

    def tearDown(self):
        self.tmp.cleanup()

    def worker(self, u):
        parsed = make_parsed([str(self.decomp).replace('\\', '/').lower() + '/src/bscore/bsrandom.cpp'])
        if u['source'].endswith('u3.cpp'):
            return symdb.failed_result(u, 'boom ' * 200)
        n = int(u['functions'][0]['address'], 16)
        return make_result(u, [n], parsed)

    def run_build(self, **kw):
        stderr = mock.Mock()
        with mock.patch.object(symdb, 'log', stderr), \
                mock.patch('engine_map.generate.git_head', return_value='abc123'):
            summary = symdb.build(self.decomp, self.out, units=self.units, worker=self.worker, jobs=2,
                                  make_pool=lambda j, i, a: ThreadPoolExecutor(j), progress=2, **kw)
        return summary, stderr

    def test_build_and_atomic_replace(self):
        self.out.parent.mkdir()
        self.out.write_text('old database')
        building = Path(str(self.out) + '.building')
        summary, stderr = self.run_build()
        self.assertFalse(building.exists())
        con = sqlite3.connect(str(self.out))
        try:
            meta = dict(con.execute('SELECT key, value FROM meta'))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM units').fetchone()[0], 5)
            self.assertEqual(con.execute("SELECT status FROM units WHERE source = 'src/u3.cpp'").fetchone()[0],
                             'compile-failed')
        finally:
            con.close()
        self.assertEqual(meta['decompCommit'], 'abc123')
        self.assertEqual(meta['mode'], 'double-compile')
        self.assertNotIn('imageSha256', meta)
        self.assertEqual((meta['units'], meta['unitsOk'], meta['unitsFailed']), ('5', '4', '1'))
        for key in symdb_schema.META_KEYS:
            self.assertIn(key, summary)
        self.assertEqual(summary['out'], str(self.out))
        self.assertEqual(len(summary['failed']), 1)
        self.assertEqual(summary['failed'][0]['unit'], 'src/u3.cpp')
        self.assertLessEqual(len(summary['failed'][0]['error']), 300)
        json.dumps(summary)
        progress = [c.args[0] for c in stderr.call_args_list]
        self.assertEqual(len(progress), 3)       # progress every 2 units, plus the last
        self.assertTrue(progress[-1].startswith('5/5 units, 1 failed'))

    def test_failed_build_keeps_old_file(self):
        self.out.parent.mkdir()
        self.out.write_text('old database')
        with mock.patch.object(symdb, 'log'), mock.patch.object(symdb.Store, 'add_unit', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                symdb.build(self.decomp, self.out, units=self.units, worker=self.worker, jobs=1,
                            make_pool=lambda j, i, a: ThreadPoolExecutor(j))
        self.assertEqual(self.out.read_text(), 'old database')
        self.assertFalse(Path(str(self.out) + '.building').exists())

    def test_worker_exception_is_a_failed_unit(self):
        def broken(u):
            raise RuntimeError('pool died')
        with mock.patch.object(symdb, 'log'), mock.patch('engine_map.generate.git_head', return_value=None):
            summary = symdb.build(self.decomp, self.out, units=self.units[:2], worker=broken, jobs=1,
                                  make_pool=lambda j, i, a: ThreadPoolExecutor(j))
        self.assertEqual(summary['unitsFailed'], 2)
        self.assertIn('pool died', summary['failed'][0]['error'])

    def build_with(self, worker, units=None, make_pool=None, jobs=1, **kw):
        with mock.patch.object(symdb, 'log') as stderr, mock.patch('engine_map.generate.git_head', return_value=None):
            summary = symdb.build(self.decomp, self.out, units=units or self.units, worker=worker, jobs=jobs,
                                  make_pool=make_pool or (lambda j, i, a: ThreadPoolExecutor(j)), **kw)
        return summary, stderr

    def test_checks_fallbacks_partial_and_failed(self):
        def worker(u):
            n = int(u['source'][5:-4])
            r = make_result(u, [0x400000 + n * 0x100], None)
            r['mode'] = ('image', 'double-compile', 'double-compile', None, None)[n]
            if n == 1:
                r['fallback'] = 'ValueError: too many relocations'
            if n == 2:
                r['fallback'] = 'ValueError: other'
            if n == 3:
                r.update(status='partial', verified=[], error='1 of 1 functions not verified')
            if n == 4:
                r.update(status='compile-failed', error='C2065', verified=[])
            return r
        summary, stderr = self.build_with(worker)
        self.assertEqual(summary['checks'], {'image': 1, 'double-compile': 2, 'none': 2})
        self.assertEqual(summary['fallbacks'], 2)
        self.assertEqual([f['unit'] for f in summary['fallbackUnits']], ['src/u1.cpp', 'src/u2.cpp'])
        self.assertIn('relocations', summary['fallbackUnits'][0]['error'])
        self.assertEqual((summary['unitsOk'], summary['unitsFailed'], summary['unitsPartial']), (3, 1, 1))
        self.assertEqual(summary['unitsDoubleCompile'], 2)
        self.assertEqual([(f['unit'], f['status']) for f in summary['failed']],
                         [('src/u3.cpp', 'partial'), ('src/u4.cpp', 'compile-failed')])
        con = sqlite3.connect(str(self.out))
        try:
            meta = dict(con.execute('SELECT key, value FROM meta'))
        finally:
            con.close()
        self.assertEqual((meta['unitsFailed'], meta['unitsPartial'], meta['unitsDoubleCompile']), ('1', '1', '2'))
        for key in symdb_schema.META_KEYS:
            self.assertIn(key, summary)
        self.assertTrue(stderr.call_args_list[-1].args[0].startswith('5/5 units, 1 failed, 1 partial'))

    def test_broken_pool_fails_that_unit_and_goes_on(self):
        from concurrent.futures.process import BrokenProcessPool
        pools = []

        class Pool(ThreadPoolExecutor):
            def __init__(self, n, first):
                super().__init__(n)
                self.first, self.index = first, len(pools)
                pools.append(self)

            def submit(self, fn, *args):
                if self.first:
                    self.first = False
                    future = super().submit(lambda: None)
                    future.result()
                    failed = type(future)()
                    failed.set_exception(BrokenProcessPool('a worker died'))
                    return failed
                return super().submit(fn, *args)

        summary, _ = self.build_with(self.worker, make_pool=lambda j, i, a: Pool(j, len(pools) == 0))
        self.assertGreaterEqual(len(pools), 2)
        self.assertEqual(summary['units'], 5)
        failed = {f['unit']: f for f in summary['failed']}
        self.assertEqual(failed['src/u0.cpp']['status'], 'compile-failed')
        self.assertIn('a worker died', failed['src/u0.cpp']['error'])
        self.assertNotIn('src/u4.cpp', failed)      # built by the new pool
        self.assertGreaterEqual(summary['unitsOk'], 1)

    def test_submit_on_broken_pool_creates_new_pool(self):
        from concurrent.futures.process import BrokenProcessPool
        pools = []

        class Pool(ThreadPoolExecutor):
            def __init__(self, n, bad):
                super().__init__(n)
                self.bad = bad
                pools.append(self)

            def submit(self, fn, *args):
                if self.bad:
                    raise BrokenProcessPool('pool is broken')
                return super().submit(fn, *args)

        summary, _ = self.build_with(self.worker, make_pool=lambda j, i, a: Pool(j, not pools))
        self.assertEqual(len(pools), 2)
        failed = {f['unit'] for f in summary['failed']}
        self.assertEqual(failed, {'src/u0.cpp', 'src/u3.cpp'})
        self.assertEqual(summary['unitsOk'], 3)

    def test_commit_cadence(self):
        units = [unit('src/u%d.cpp' % n, [{'address': '0x%08x' % (0x400000 + n * 0x10), 'symbol': 'f', 'size': 4}])
                 for n in range(450)]
        commits = []
        real = symdb.open_building

        class Spy:
            def __init__(self, db):
                self.db = db

            def execute(self, sql, *args):
                if sql == 'COMMIT':
                    commits.append(sql)
                return self.db.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self.db, name)

        def worker(u):
            return make_result(u, [], None, 'compile-failed')
        with mock.patch.object(symdb, 'open_building', lambda p: Spy(real(p))):
            summary, _ = self.build_with(worker, units=units)
        self.assertEqual(summary['units'], 450)
        self.assertEqual(len(commits), 2)       # after 200 and after 400

    def test_commit_when_done_jumps_over_a_multiple(self):
        """wait() can return several futures at once; the commit must not be skipped."""
        units = [unit('src/u%d.cpp' % n, [{'address': '0x%08x' % (0x400000 + n * 0x10), 'symbol': 'f', 'size': 4}])
                 for n in range(300)]
        commits = []
        real = symdb.open_building

        class Spy:
            def __init__(self, db):
                self.db = db

            def execute(self, sql, *args):
                if sql == 'COMMIT':
                    commits.append(sql)
                return self.db.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self.db, name)

        def fake_wait(futures, return_when=None):
            return set(list(futures)[:7]), set()     # seven results at a time: done goes 7, 14, ... 203
        with mock.patch.object(symdb, 'open_building', lambda p: Spy(real(p))), \
                mock.patch.object(symdb, 'wait', fake_wait):
            self.build_with(lambda u: make_result(u, [], None, 'compile-failed'), units=units, jobs=3)
        self.assertEqual(len(commits), 1)

    def test_swap_retries_then_works(self):
        calls = []
        real = os.replace

        def flaky(a, b):
            calls.append(1)
            if len(calls) < 3:
                raise PermissionError(5, 'Access is denied')
            return real(a, b)
        with mock.patch.object(symdb.os, 'replace', flaky), mock.patch.object(symdb, 'REPLACE_STEP', 0.001):
            summary, _ = self.build_with(self.worker)
        self.assertEqual(len(calls), 3)
        self.assertTrue(summary['replaced'])
        self.assertEqual(summary['out'], str(self.out))
        self.assertTrue(self.out.exists())
        self.assertFalse(Path(str(self.out) + '.building').exists())

    def test_swap_always_fails_keeps_building(self):
        def denied(a, b):
            raise PermissionError(5, 'Access is denied')
        building = Path(str(self.out) + '.building')
        with mock.patch.object(symdb.os, 'replace', denied), mock.patch.object(symdb, 'REPLACE_STEP', 0.001),                 mock.patch.object(symdb, 'REPLACE_WAIT', 0.02):
            summary, _ = self.build_with(self.worker)
        self.assertFalse(summary['replaced'])
        self.assertEqual(summary['out'], str(building))
        self.assertTrue(building.exists())
        self.assertFalse(self.out.exists())
        self.assertIn('in use', summary['error'])
        self.assertIn(str(building), summary['error'])
        self.assertIn(str(self.out), summary['error'])

    def test_main_exit_3_when_swap_fails(self):
        (self.decomp / 'build' / 'units.json').write_text(json.dumps({'units': self.units}))

        def denied(a, b):
            raise PermissionError(5, 'Access is denied')
        real_build = symdb.build

        def build(*args, **kw):
            return real_build(*args, worker=self.worker, make_pool=lambda j, i, a: ThreadPoolExecutor(j), **kw)
        with mock.patch.object(symdb.os, 'replace', denied), mock.patch.object(symdb, 'REPLACE_WAIT', 0),                 mock.patch.object(symdb, 'REPLACE_STEP', 0), mock.patch.object(symdb, 'build', build),                 mock.patch.object(symdb, 'log') as stderr, mock.patch('builtins.print') as out,                 mock.patch('engine_map.generate.git_head', return_value=None):
            code = symdb.main(['--decomp', str(self.decomp), '--out', str(self.out), '--jobs', '1'])
        self.assertEqual(code, 3)
        self.assertTrue(Path(str(self.out) + '.building').exists())
        self.assertIn('in use', stderr.call_args_list[-1].args[0])
        self.assertIn('"replaced": false', out.call_args.args[0])

    def test_only_and_limit(self):
        summary, _ = self.run_build(only=['u1', 'u2', 'u4'], limit=2)
        self.assertEqual(summary['units'], 2)
        con = sqlite3.connect(str(self.out))
        try:
            self.assertEqual(sorted(r[0] for r in con.execute('SELECT source FROM units')),
                             ['src/u1.cpp', 'src/u2.cpp'])
        finally:
            con.close()

    def test_main_rejects_missing_inputs(self):
        with mock.patch.object(symdb, 'log'):
            self.assertEqual(symdb.main(['--decomp', str(self.decomp), '--out', str(self.out)]), 2)


if __name__ == '__main__':
    unittest.main()
