"""Symbol database reader and the tools that use it (fnv_symbols, symbolize, crash and hang doctors, class layout),
on a tiny database built here with the schema module. No game, no real decomp data."""
import json
import os
import sqlite3
import struct
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine_map import symdb_schema  # noqa: E402
from geck_mcp import fnv_crash, fnv_deep, fnv_engine, fnv_symdb  # noqa: E402
from geck_mcp.fnv_tools import FnvService  # noqa: E402
import test_fnv  # noqa: E402

COMMIT = 'a' * 40


def build_db(path, decomp=None, commit=COMMIT):
    """Two functions: ApplyDamage (0x401000, verified, lines + locals) and ApplyLimbDamage (0x401100, no lines).
    Types: char, const char *, TESObjectREFR *, float [3], two different Actor definitions, an enum, a cycle."""
    db = sqlite3.connect(str(path))
    symdb_schema.create(db)
    meta = {'decompPath': str(decomp or ''), 'decompCommit': commit, 'builtAt': '2026-11-10T00:00:00Z',
            'mode': 'image', 'units': '3', 'unitsOk': '1', 'unitsFailed': '2', 'functions': '2',
            'functionsWithLines': '1', 'functionsVerified': '1', 'types': '17'}
    db.executemany('INSERT INTO meta VALUES(?, ?)', list(meta.items()))
    db.executemany('INSERT INTO files(id, path) VALUES(?, ?)', [(1, 'src/actor.cpp'), (2, 'src/Other/actor.cpp'),
                                                                 (3, 'include/inline.h')])
    db.executemany('INSERT INTO units(id, source, compiler, flags, status, error, functions, verified, seconds) '
                   'VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)',
                   [(1, 'src/actor.cpp', 'cl', '/O2', 'ok', None, 2, 1, 1.0),
                    (2, 'src/bad.cpp', 'cl', '/O2', 'compile-failed', 'C2065 undeclared', 0, 0, 0.1),
                    (3, 'src/half.cpp', 'cl', '/O2', 'partial', None, 5, 2, 0.5)])
    db.executemany('INSERT INTO functions(addr, size, unit_id, name, decorated, verified, has_debug, type_id, '
                   'proc_flags, frame_bytes, frame_flags, debug_start, debug_end) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   [(0x401000, 0x40, 1, 'Actor::ApplyDamage', '?ApplyDamage@Actor@@QAEXM@Z', 1, 1, 14, 0, 0x20, 0, 3,
                     0x3C),
                    (0x401100, 0x20, 1, 'Actor::ApplyLimbDamage', '?ApplyLimbDamage@Actor@@QAEXXZ', 0, 0, None, None,
                     None, None, None, None)])
    db.executemany('INSERT INTO lines(func_addr, offset, file_id, line) VALUES(?, ?, ?, ?)',
                   [(0x401000, 0, 1, 5), (0x401000, 0x6, 1, 7), (0x401000, 0x10, 1, 9), (0x401000, 0x20, 3, 3),
                    (0x401000, 0x30, 1, 12)])
    #                      seq name        kind      reg  offset type scope_lo scope_hi
    db.executemany('INSERT INTO locals(func_addr, seq, name, kind, reg, offset, type_id, scope_lo, scope_hi) '
                   'VALUES(?,?,?,?,?,?,?,?,?)',
                   [(0x401000, 0, 'this', 'register', 18, None, 12, None, None),
                    (0x401000, 1, 'afAmount', 'bprel', None, 8, 7, None, None),
                    (0x401000, 2, 'count', 'bprel', None, -0x14, 1, 0x10, 0x30),
                    (0x401000, 3, 'owner', 'bprel', None, -4, 6, None, None),
                    (0x401000, 4, 'pos', 'bprel', None, -0x20, 8, None, None),
                    (0x401000, 5, 'cached', 'register', 23, None, 6, 0x10, 0x30),
                    (0x401000, 6, 'slot', 'regrel', 22, 0x18, 1, None, None)])
    types = [  # id, hash, kind, name, size, target, attrs, seen
        (1, 'prim:0074', 'prim', 'int', 4, None, None, 1),
        (2, 'prim:0070', 'prim', 'char', 1, None, None, 1),
        (3, 'm1', 'modifier', None, None, 2, '{"const": true}', 1),
        (4, 'p1', 'ptr', None, None, 3, None, 1),
        (5, 'udtref:TESObjectREFR', 'udtref', 'TESObjectREFR', None, None, None, 1),
        (6, 'p2', 'ptr', None, None, 5, None, 1),
        (7, 'prim:0040', 'prim', 'float', 4, None, None, 1),
        (8, 'a1', 'array', None, 12, 7, '{"count": 3}', 1),
        (9, 'd1', 'class', 'Actor', 0x20, None, '{"vtable": true}', 5),
        (10, 'd2', 'class', 'Actor', 0x1C, None, None, 1),
        (11, 'udtref:Actor', 'udtref', 'Actor', None, None, None, 1),
        (12, 'p3', 'ptr', None, None, 11, None, 1),
        (13, 'e1', 'enum', 'Color', 4, None, None, 1),
        (14, 'f1', 'proc', None, None, 1, '{"call": "cdecl"}', 1),
        (15, 'p4', 'ptr', None, None, 14, None, 1),
        (16, 'c1', 'modifier', None, None, 17, '{"const": true}', 1),      # 16 -> 17 -> 16: a cycle
        (17, 'c2', 'ptr', None, None, 16, None, 1),
        (18, 'prim:0075', 'prim', 'unsigned int', 4, None, None, 1),
        (19, 'udtref:TESObjectREFR2', 'udtref', 'Nowhere', None, None, None, 1),
    ]
    db.executemany('INSERT INTO types(id, hash, kind, name, size, target_id, attrs, seen) VALUES(?,?,?,?,?,?,?,?)',
                   types)
    db.executemany('INSERT INTO members(type_id, seq, name, offset, type_id2, bit_pos, bit_len) VALUES(?,?,?,?,?,?,?)',
                   [(9, 0, 'm_uiFlags', 0xC, 18, None, None), (9, 1, 'm_fHealth', 0x10, 7, None, None),
                    (9, 2, 'm_bDead', 0x14, 18, 3, 1), (9, 3, 'm_pos', 0x18, 8, None, None),
                    (10, 0, 'm_fHealth', 0x10, 7, None, None)])
    db.execute('INSERT INTO bases(type_id, seq, base_id, offset, is_virtual) VALUES(9, 0, 5, 0, 0)')
    db.executemany('INSERT INTO methods(type_id, seq, name, type_id2, is_virtual, is_static, vt_offset) '
                   'VALUES(?,?,?,?,?,?,?)',
                   [(9, 0, '~Actor', 14, 1, 0, 0), (9, 1, 'ApplyDamage', 14, 1, 0, 4), (9, 2, 'Make', 14, 0, 1, None)])
    db.executemany('INSERT INTO args(type_id, seq, type_id2) VALUES(?, ?, ?)', [(14, 0, 4), (14, 1, 7)])
    db.executemany('INSERT INTO enums(type_id, seq, name, value) VALUES(?, ?, ?, ?)',
                   [(13, 0, 'Red', 0), (13, 1, 'Green', 1), (13, 2, 'Blue', 2)])
    db.commit()
    db.close()


class SymdbBase(test_fnv.FnvTestBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.db_path = Path(cls.project.state_dir) / 'symdb.sqlite'
        cls.git = Path(cls.tmp.name) / 'checkout'
        (cls.git / '.git').mkdir(parents=True)
        (cls.git / '.git' / 'HEAD').write_text(COMMIT + '\n')
        build_db(cls.db_path, cls.git)

    @classmethod
    def tearDownClass(cls):
        fnv_symdb.clear_cache()
        super().tearDownClass()

    def setUp(self):
        self._env = mock.patch.dict(os.environ)
        self._env.start()
        os.environ.pop('FNV_SYMDB', None)
        self.addCleanup(self._env.stop)


class ReaderTests(SymdbBase):
    def setUp(self):
        super().setUp()
        self.db = fnv_symdb.load(self.db_path)

    def test_load_and_default_path(self):
        self.assertIsNone(fnv_symdb.load(Path(self.tmp.name) / 'nothing.sqlite'))
        self.assertIs(fnv_symdb.load(self.db_path), self.db)   # cached while the file is unchanged
        self.assertEqual(fnv_symdb.default_path(self.project), self.db_path)
        os.environ['FNV_SYMDB'] = 'from_env.sqlite'
        self.assertEqual(fnv_symdb.default_path(self.project), Path('from_env.sqlite'))
        fnv = self.project.fnv.model_copy(update={'symdb': Path('from_setting.sqlite')})
        self.assertEqual(fnv_symdb.default_path(self.project.model_copy(update={'fnv': fnv})),
                         Path('from_setting.sqlite'))
        bad = Path(self.tmp.name) / 'bad.sqlite'
        bad.write_bytes(b'this is not a database' * 20)
        with self.assertRaises(fnv_symdb.SymbolDbError):
            fnv_symdb.load(bad)

    def test_read_only(self):
        with self.db._session() as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('DELETE FROM meta')

    def test_file_not_held_open(self):
        # an open connection makes os.replace over the file fail on Windows, so nothing may stay open
        path = Path(self.tmp.name) / 'swap.sqlite'
        build_db(path, commit='c' * 40)
        fnv_symdb.clear_cache()
        db = fnv_symdb.load(path)
        self.assertEqual(db.line_at(0x401012)['line'], 9)
        self.assertEqual(db.locals_of(0x401000, 0x12)[0]['name'], 'this')
        self.assertEqual(db.layout('Actor')['size'], 0x20)
        newer = Path(self.tmp.name) / 'swap.building'
        build_db(newer, commit='d' * 40)
        os.replace(str(newer), str(path))
        self.assertEqual(fnv_symdb.load(path).meta['decompCommit'], 'd' * 40)
        fnv_symdb.clear_cache()
        self.db = fnv_symdb.load(self.db_path)

    def test_threads(self):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as pool:
            results = list(pool.map(lambda i: self.db.line_at(0x401000 + i % 0x30), range(200)))
        self.assertTrue(all(r and r['file'] for r in results))

    def test_identity_and_stale(self):
        ident = self.db.identity()
        self.assertEqual((ident['decompCommit'], ident['mode'], ident['schema']), (COMMIT, 'image', '1'))
        self.assertEqual(ident['counts']['functions'], 2)
        self.assertIs(ident['stale'], False)
        (self.git / '.git' / 'HEAD').write_text('b' * 40 + '\n')
        try:
            self.assertIs(self.db.identity()['stale'], True)
        finally:
            (self.git / '.git' / 'HEAD').write_text(COMMIT + '\n')
        other = Path(self.tmp.name) / 'other.sqlite'
        build_db(other, decomp=Path(self.tmp.name) / 'no-such-checkout')
        self.assertIsNone(fnv_symdb.SymbolDb(other).identity()['stale'])

    def test_function_and_lines(self):
        self.assertEqual(self.db.function(0x401000)['name'], 'Actor::ApplyDamage')
        self.assertIsNone(self.db.function(0x401001))
        self.assertEqual(self.db.lines_of(0x401000)[:2], [(0, 'src/actor.cpp', 5), (6, 'src/actor.cpp', 7)])
        self.assertEqual(self.db.lines_of(0x401100), [])
        hit = self.db.line_at(0x401012)
        self.assertEqual((hit['functionAddress'], hit['offset'], hit['file'], hit['line'], hit['lineOffset'],
                          hit['verified']), (0x401000, 0x12, 'src/actor.cpp', 9, 0x10, True))
        self.assertEqual(self.db.line_at(0x401024)['file'], 'include/inline.h')
        self.assertIsNone(self.db.line_at(0x401105))     # function without lines
        self.assertIsNone(self.db.line_at(0x500000))     # not in a function
        self.assertEqual(self.db.resolve('Actor::ApplyLimbDamage')['addr'], 0x401100)
        self.assertEqual(self.db.resolve('0x401010')['addr'], 0x401000)
        with self.assertRaises(fnv_symdb.SymbolDbError):
            self.db.resolve('Nothing::Here')

    def test_addresses_of(self):
        self.assertEqual(self.db.addresses_of('src/actor.cpp', 9), [(0x401010, 0x401000)])
        self.assertEqual(self.db.addresses_of('SRC\\ACTOR.CPP', 12), [(0x401030, 0x401000)])
        self.assertEqual(self.db.addresses_of('inline.h', 3), [(0x401020, 0x401000)])
        self.assertEqual(self.db.addresses_of('actor.cpp', 7), [(0x401006, 0x401000)])
        self.assertEqual(self.db.addresses_of('actor.cpp', 999), [])
        self.assertEqual(self.db.files_matching('actor.cpp'), ['src/Other/actor.cpp', 'src/actor.cpp'])
        self.assertEqual(self.db.files_matching('tor.cpp'), [])      # whole path components only

    def test_type_text(self):
        t = self.db.type_text
        self.assertEqual(t(4), 'const char *')
        self.assertEqual(t(6), 'TESObjectREFR *')
        self.assertEqual(t(8), 'float [3]')
        self.assertEqual(t(12), 'Actor *')
        self.assertEqual(t(13), 'Color')
        self.assertEqual(t(14), 'int (__cdecl)(const char *, float)')
        self.assertEqual(t(15), 'int (__cdecl *)(const char *, float)')
        self.assertEqual(t(None), 'void')
        self.assertEqual(t(999), '?')
        self.assertIsInstance(t(16), str)    # cycle: must come back
        self.assertIsInstance(t(17), str)

    def test_locals(self):
        found = {l['name']: l for l in self.db.locals_of(0x401000)}
        self.assertEqual(found['afAmount']['location'], 'ebp+8')
        self.assertEqual((found['afAmount']['type'], found['afAmount']['size'], found['afAmount']['param']),
                         ('float', 4, True))
        self.assertEqual((found['count']['location'], found['count']['param']), ('ebp-0x14', False))
        self.assertEqual((found['owner']['location'], found['owner']['size'], found['owner']['type']),
                         ('ebp-4', 4, 'TESObjectREFR *'))
        self.assertEqual((found['pos']['size'], found['pos']['type']), (12, 'float [3]'))
        self.assertEqual((found['this']['location'], found['this']['kind'], found['this']['param']),
                         ('ecx', 'register', False))
        self.assertEqual(found['cached']['location'], 'esi')
        self.assertEqual(found['slot']['location'], 'ebp+0x18')
        self.assertEqual(found['slot']['kind'], 'regrel')
        self.assertFalse(found['slot']['param'])
        self.assertEqual(self.db.locals_of(0x401000)[0]['name'], 'this')        # record order
        # scope: count and cached live in offsets 0x10..0x30 only
        early = {l['name'] for l in self.db.locals_of(0x401000, 0x4)}
        late = {l['name'] for l in self.db.locals_of(0x401000, 0x20)}
        self.assertNotIn('count', early)
        self.assertIn('count', late)
        self.assertIn('afAmount', early)
        self.assertEqual(self.db.locals_of(0x401100), [])

    def test_sizes(self):
        self.assertEqual(self.db.type_size(12), 4)       # pointer
        self.assertEqual(self.db.type_size(11), 0x20)    # udtref -> best Actor definition
        self.assertEqual(self.db.type_size(3), 1)        # const char: size from target
        self.assertEqual(self.db.type_size(8), 12)
        self.assertIsNone(self.db.type_size(5))          # TESObjectREFR has no definition here
        self.assertIsNone(self.db.type_size(19))

    def test_layout(self):
        layout = self.db.layout('Actor')
        self.assertEqual((layout['size'], layout['kind'], layout['variants'], layout['conflicts']),
                         (0x20, 'class', 2, [0x1C]))
        names = [(m['name'], m['offset'], m['type'], m['size']) for m in layout['members']]
        self.assertEqual(names[:2], [('m_uiFlags', 0xC, 'unsigned int', 4), ('m_fHealth', 0x10, 'float', 4)])
        bits = layout['members'][2]
        self.assertEqual((bits['bitPos'], bits['bitLen']), (3, 1))
        self.assertEqual(layout['members'][3]['size'], 12)
        self.assertEqual(layout['bases'], [{'name': 'TESObjectREFR', 'offset': 0, 'virtual': False}])
        self.assertEqual([(m['name'], m['virtual'], m['vtableSlot']) for m in layout['methods']],
                         [('~Actor', True, 0), ('ApplyDamage', True, 1), ('Make', False, None)])
        self.assertTrue(layout['methods'][2]['static'])
        self.assertEqual(self.db.layout('actor')['size'], 0x20)       # any case
        self.assertIsNone(self.db.layout('NoSuchClass'))
        color = self.db.layout('Color')
        self.assertEqual([(e['name'], e['value']) for e in color['enumerators']],
                         [('Red', 0), ('Green', 1), ('Blue', 2)])

    def test_coverage(self):
        cov = self.db.coverage()
        self.assertEqual((cov['functions'], cov['functionsWithLines'], cov['functionsVerified'], cov['units']),
                         (2, 1, 1, 3))
        self.assertEqual({u['source'] for u in cov['unitsNotOkList']}, {'src/bad.cpp', 'src/half.cpp'})


class SymbolizeTests(SymdbBase):
    def test_without_database(self):
        engine = fnv_engine.EngineMap(self.map_path)
        engine.symdb_path = Path(self.tmp.name) / 'missing.sqlite'
        s = engine.symbolize(0x401105)
        self.assertEqual(s['name'], 'Actor::ApplyLimbDamage')
        for key in ('source', 'sourceLine', 'lineOffset'):
            self.assertNotIn(key, s)

    def test_with_database(self):
        engine = fnv_engine.load(*fnv_engine.default_paths(self.project))
        s = engine.symbolize(0x401012)
        self.assertEqual(s['name'], 'Actor::ApplyDamage')
        self.assertEqual(s['source'], 'src/actor.cpp:9')
        self.assertEqual(s['lineOffset'], '0x10')
        self.assertEqual(s['sourceLine'], 'FUN_00401100(this);')
        self.assertEqual(s['offset'], '0x12')
        self.assertNotIn('source', engine.symbolize(0x401105))     # function has no lines
        # a line whose file is not in the checkout: still reports the position
        s = engine.symbolize(0x401024)
        self.assertEqual(s['source'], 'include/inline.h:3')
        self.assertNotIn('sourceLine', s)

    def test_without_source_opens_no_database(self):
        engine = fnv_engine.load(*fnv_engine.default_paths(self.project))
        with mock.patch.object(fnv_symdb, 'load', wraps=fnv_symdb.load) as load:
            s = engine.symbolize(0x401012, source=False)
        self.assertEqual((s['name'], s['offset']), ('Actor::ApplyDamage', '0x12'))
        self.assertNotIn('source', s)
        self.assertEqual(load.call_count, 0)

    def test_patch_map_names_patched_functions_without_source_lines(self):
        """fnv_patch_map names ~400 patched spots; a source line each (a database session and a decomp file read)
        was 0.4 s of its 0.5 s, and the tool never shows them."""
        engine = fnv_engine.load(*fnv_engine.default_paths(self.project))
        nops = SimpleNamespace(read=lambda address, n: b'\x90' * n)
        with mock.patch.object(fnv_symdb, 'load', wraps=fnv_symdb.load) as load:
            out = fnv_deep.DeepTools._classify(SimpleNamespace(), nops, nops, [], engine, 0x401012, 0x401014, {}, False)
        self.assertEqual(out['function']['name'], 'Actor::ApplyDamage')
        self.assertEqual(load.call_count, 0)

    def test_service(self):
        r = FnvService(self.project).symbolize(['0x00401012', '0x00401105'])
        self.assertEqual(r['data']['results'][0]['source'], 'src/actor.cpp:9')
        self.assertNotIn('source', r['data']['results'][1])

    def test_crash_doctor_carries_source(self):
        engine = fnv_engine.load(*fnv_engine.default_paths(self.project))
        e = fnv_crash.explain(fnv_crash.parse_text(test_fnv.LOG5), engine)
        frame = e['frames'][2]          # 0x00401010 is a return address: the call is at 0x0040100F, line 7
        self.assertEqual(frame['function']['name'], 'Actor::ApplyDamage')
        self.assertEqual(frame['offset'], '0x10')
        self.assertEqual(frame['source'], 'src/actor.cpp:7')
        self.assertNotIn('source', e['frames'][0])      # 0x00401105: no lines


class ReturnAddressTests(SymdbBase):
    def test_symbolize(self):
        engine = fnv_engine.load(*fnv_engine.default_paths(self.project))
        plain = engine.symbolize(0x401010)
        self.assertEqual(plain['source'], 'src/actor.cpp:9')
        ret = engine.symbolize(0x401010, return_address=True)
        self.assertEqual((ret['address'], ret['offset']), ('0x00401010', '0x10'))
        self.assertEqual(ret['source'], 'src/actor.cpp:7')
        # the instruction after the last byte of a function still belongs to that function
        end = engine.symbolize(0x401040, return_address=True)
        self.assertEqual(end['name'], 'Actor::ApplyDamage')
        self.assertEqual(end['source'], 'src/actor.cpp:12')
        self.assertNotEqual(engine.symbolize(0x401040).get('name'), 'Actor::ApplyDamage')

    def test_crash_frame_zero_is_not_a_return_address(self):
        engine = fnv_engine.load(*fnv_engine.default_paths(self.project))
        log = test_fnv.LOG5.replace('FalloutNV (0x00401105)', 'FalloutNV (0x00401010)')
        e = fnv_crash.explain(fnv_crash.parse_text(log), engine)
        self.assertEqual(e['frames'][0]['source'], 'src/actor.cpp:9')     # fault address: exact
        self.assertEqual(e['frames'][2]['source'], 'src/actor.cpp:7')     # caller: the call before it


class FakeProcess:
    """Memory of a stopped game: a dict of address -> bytes."""
    memory = {}

    def __init__(self, pid):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def modules(self):
        return [{'name': 'FalloutNV.exe', 'path': 'x', 'base': 0x400000, 'size': 0xF00000}]

    def threads(self):
        return [1]

    def main_thread(self, threads=None):
        return 1

    def thread_context(self, tid):
        return {'eip': 0, 'esp': 0, 'ebp': 0}

    def thread_cpu_ms(self, tid):
        return 0.0

    def read(self, address, length):
        for start, data in self.memory.items():
            if start <= address and address + length <= start + len(data):
                return data[address - start:address - start + length]
        return None


class HangDoctorTests(SymdbBase):
    EBP = 0x0019F000

    def run_doctor(self, frames):
        FakeProcess.memory = {
            self.EBP + 8: struct.pack('<f', 2.5),
            self.EBP - 0x14: struct.pack('<i', -7),
            self.EBP - 4: struct.pack('<I', 0x12345678),
            self.EBP - 0x20: b'\0' * 12,
        }
        engine = fnv_engine.load(*fnv_engine.default_paths(self.project))
        game = SimpleNamespace(process=lambda: (4242, 'FalloutNV.exe'),
                               ping=lambda timeout=2.0: {'mainThreadId': 1, 'msSinceFrame': 10, 'frame': 5})
        live = SimpleNamespace(game=game, engine=lambda: engine, link=None)
        with mock.patch.object(fnv_deep.fnv_procmem, 'GameProcess', FakeProcess), \
                mock.patch.object(fnv_deep.fnv_procmem, 'walk_stack', return_value=(frames, [])):
            return fnv_deep.DeepTools(live).hang_doctor(samples=1, interval_ms=50)

    def test_locals_and_source(self):
        frames = [{'address': 0x401012, 'how': 'eip', 'ebp': self.EBP},
                  {'address': 0x401105, 'how': 'frame', 'ebp': 0x0019F100},     # no debug info: nothing to read
                  {'address': 0x10001234, 'how': 'frame', 'ebp': 0}]            # not game code
        out = self.run_doctor(frames)
        named = out['threads'][0]['samples'][0]['frames']
        self.assertEqual(named[0]['source'], 'src/actor.cpp:9')
        self.assertNotIn('frameNotReady', named[0])
        values = {l['name']: l for l in named[0]['locals']}
        self.assertEqual(values['afAmount']['value'], 2.5)
        self.assertEqual(values['count']['value'], -7)           # in scope at offset 0x12
        self.assertEqual(values['owner']['value'], '0x12345678')
        self.assertIn('not read', values['pos']['note'])         # 12 bytes: too big
        self.assertNotIn('this', values)                         # register locals are not on the stack
        self.assertNotIn('locals', named[1])
        self.assertNotIn('source', named[1])

    def test_return_addresses(self):
        # 0x401030 as a caller frame: the call is at offset 0x2F, so line 12's neighbour (line 3 at 0x20) and the
        # scope of `count` (0x10 to 0x30, end exclusive) are judged at 0x2F. As eip the same address is out of scope.
        frames = [{'address': 0x401030, 'how': 'eip', 'ebp': self.EBP},
                  {'address': 0x401030, 'how': 'frame', 'ebp': self.EBP}]
        out = self.run_doctor(frames)
        named = out['threads'][0]['samples'][0]['frames']
        self.assertEqual(named[0]['source'], 'src/actor.cpp:12')
        self.assertEqual(named[1]['source'], 'include/inline.h:3')
        self.assertNotIn('count', [l['name'] for l in named[0]['locals']])
        self.assertIn('count', [l['name'] for l in named[1]['locals']])

    def test_stack_scan_uses_return_addresses(self):
        FakeProcess.memory = {}
        engine = fnv_engine.load(*fnv_engine.default_paths(self.project))
        game = SimpleNamespace(process=lambda: (4242, 'FalloutNV.exe'),
                               ping=lambda timeout=2.0: {'mainThreadId': 1, 'msSinceFrame': 10, 'frame': 5})
        live = SimpleNamespace(game=game, engine=lambda: engine, link=None)
        scan = [{'address': 0x401010, 'how': 'stack_scan', 'stackOffset': '0x4'}]
        with mock.patch.object(fnv_deep.fnv_procmem, 'GameProcess', FakeProcess),                 mock.patch.object(fnv_deep.fnv_procmem, 'walk_stack',
                                  return_value=([{'address': 0x401105, 'how': 'eip', 'ebp': 0}], scan)):
            out = fnv_deep.DeepTools(live).hang_doctor(samples=1, interval_ms=50)
        self.assertEqual(out['threads'][0]['samples'][0]['stackScan'][0]['source'], 'src/actor.cpp:7')

    def test_unreadable_local_does_not_fail(self):
        frames = [{'address': 0x401012, 'how': 'eip', 'ebp': 0x00700000}]
        named = self.run_doctor(frames)['threads'][0]['samples'][0]['frames']
        self.assertTrue(all('error' in l for l in named[0]['locals']
                            if 'note' not in l))

    def test_frame_not_ready(self):
        named = self.run_doctor([{'address': 0x401001, 'how': 'eip', 'ebp': self.EBP}]
                                )['threads'][0]['samples'][0]['frames']
        self.assertIs(named[0]['frameNotReady'], True)
        self.assertNotIn('locals', named[0])
        # a caller frame inside the same range is a return address, never "not ready"
        named = self.run_doctor([{'address': 0x401200, 'how': 'eip', 'ebp': 0},
                                 {'address': 0x401001, 'how': 'frame', 'ebp': self.EBP}]
                                )['threads'][0]['samples'][0]['frames']
        self.assertNotIn('frameNotReady', named[1])

    def test_only_first_six_game_frames(self):
        frames = [{'address': 0x401012, 'how': 'frame' if i else 'eip', 'ebp': self.EBP} for i in range(8)]
        named = self.run_doctor(frames)['threads'][0]['samples'][0]['frames']
        self.assertEqual([('locals' in n) for n in named], [True] * 6 + [False] * 2)

    def test_walk_stack_carries_ebp(self):
        from geck_mcp import fnv_procmem
        stack = struct.pack('<II', 0x0019F020, 0x00401012) + b'\0' * 0x20
        process = SimpleNamespace(u32=lambda a: None, read=lambda a, n: None)
        regs = {'eip': 0x401100, 'esp': 0x0019F000, 'ebp': 0x0019F000, 'stack': stack}
        frames, _ = fnv_procmem.walk_stack(process, regs,
                                           [{'name': 'FalloutNV.exe', 'base': 0x400000, 'size': 0xF00000}])
        self.assertEqual([(f['how'], f['ebp']) for f in frames], [('eip', 0x0019F000), ('frame', 0x0019F020)])


class ToolTests(SymdbBase):
    def setUp(self):
        super().setUp()
        self.service = FnvService(self.project)

    def test_status(self):
        r = self.service.symbols('status')
        self.assertTrue(r['ok'], r)
        d = r['data']
        self.assertTrue(d['available'])
        self.assertIn('2 functions', d['verdict'])
        self.assertEqual(d['coverage']['functionsWithLines'], 1)
        self.assertEqual(len(d['coverage']['unitsNotOkList']), 2)
        self.assertIs(d['stale'], False)
        self.assertEqual(d['identity']['decompCommit'], COMMIT)

    def test_status_without_database(self):
        project = self.project.model_copy(update={'state_dir': Path(self.tmp.name) / 'nowhere'})
        r = FnvService(project).symbols('status')
        self.assertTrue(r['ok'])
        self.assertIs(r['data']['available'], False)
        self.assertIn('python -m engine_map.symdb --decomp', r['data']['next'])
        self.assertIn('--image <your unpacked FalloutNV.exe> --out', r['data']['next'])
        r = FnvService(project).symbols('lines', function='0x00401000')
        self.assertFalse(r['ok'])
        self.assertEqual(r['error']['code'], 'SYMDB_UNAVAILABLE')
        self.assertIn('engine_map.symdb', r['data']['next'])

    def test_lines(self):
        r = self.service.symbols('lines', function='0x00401000')
        self.assertTrue(r['ok'], r)
        d = r['data']
        self.assertIn('Actor::ApplyDamage', d['verdict'])
        self.assertIsNone(d['file'])        # one row is in include/inline.h
        self.assertEqual(d['total'], 5)
        self.assertEqual(d['rows'][2], {'address': '0x00401010', 'offset': '0x10', 'line': 9,
                                        'text': 'FUN_00401100(this);', 'file': 'src/actor.cpp'})
        page = self.service.symbols('lines', function='Actor::ApplyDamage', limit=2, offset=3)['data']
        self.assertEqual([row['line'] for row in page['rows']], [3, 12])
        none = self.service.symbols('lines', function='0x00401100')['data']
        self.assertEqual(none['total'], 0)
        self.assertIn('no line table', none['verdict'])
        self.assertFalse(self.service.symbols('lines')['ok'])
        self.assertFalse(self.service.symbols('lines', function='0x00500000')['ok'])

    def test_locals(self):
        d = self.service.symbols('locals', function='Actor::ApplyDamage')['data']
        self.assertEqual(len(d['locals']), 7)
        self.assertIn('2 parameters'.replace('2', '1'), d['verdict'])
        self.assertEqual(d['function']['debugStart'], 3)
        early = self.service.symbols('locals', function='0x00401000', address='0x00401004')['data']
        self.assertEqual(early['at'], {'address': '0x00401004', 'offset': '0x4'})
        self.assertNotIn('count', [l['name'] for l in early['locals']])
        only_address = self.service.symbols('locals', address='0x00401020')['data']
        self.assertIn('count', [l['name'] for l in only_address['locals']])
        r = self.service.symbols('locals', function='0x00401000', address='0x00402000')
        self.assertFalse(r['ok'])

    def test_where(self):
        d = self.service.symbols('where', file='src/actor.cpp', line=9)['data']
        self.assertEqual(d['matches'], [{'file': 'src/actor.cpp', 'address': '0x00401010',
                                         'function': '0x00401000', 'functionName': 'Actor::ApplyDamage',
                                         'offset': '0x10'}])
        self.assertIn('is code at 1 address', d['verdict'])
        self.assertEqual(d['text'], 'FUN_00401100(this);')
        several = self.service.symbols('where', file='actor.cpp', line=9)['data']
        self.assertEqual(several['candidates'], ['src/Other/actor.cpp', 'src/actor.cpp'])
        self.assertEqual(several['matchCount'], 1)
        nothing = self.service.symbols('where', file='actor.cpp', line=1000)['data']
        self.assertEqual(nothing['matchCount'], 0)
        self.assertIn('No code', nothing['verdict'])
        self.assertIn('No file', self.service.symbols('where', file='zzz.cpp', line=1)['data']['verdict'])
        self.assertFalse(self.service.symbols('where', file='actor.cpp')['ok'])

    def test_type(self):
        d = self.service.symbols('type', name='Actor', limit=2)['data']
        self.assertTrue(d['verdict'].startswith('Actor is a class of size 0x20'))
        self.assertIn('2 definitions', d['verdict'])
        self.assertEqual(d['conflicts'], ['0x1C'])
        self.assertEqual(d['total'], 4)
        self.assertEqual(len(d['members']), 2)
        self.assertEqual(d['members'][0]['offset'], '0xC')
        self.assertTrue(d['hasVtable'])
        more = self.service.symbols('type', name='Actor', limit=2, offset=2)['data']
        self.assertEqual([m['name'] for m in more['members']], ['m_bDead', 'm_pos'])
        self.assertEqual(more['methodsTotal'], 3)
        e = self.service.symbols('type', name='Color')['data']
        self.assertEqual([x['name'] for x in e['enumerators']], ['Red', 'Green', 'Blue'])
        r = self.service.symbols('type', name='Nope')
        self.assertEqual(r['error']['code'], 'NOT_FOUND')
        self.assertFalse(self.service.symbols('type')['ok'])
        self.assertFalse(self.service.symbols('bogus')['ok'])

    def test_class_layout_with_database(self):
        d = self.service.class_layout('Actor')['data']
        self.assertEqual(d['compilerLayout'], {'size': '0x20', 'members': 4, 'variants': 2, 'conflicts': ['0x1C']})
        self.assertNotIn('memberList', d['compilerLayout'])
        d = self.service.class_layout('Actor', compiler=True)['data']
        self.assertEqual(d['compilerLayout']['memberList'][1],
                         {'offset': '0x10', 'name': 'm_fHealth', 'type': 'float', 'size': 4})
        self.assertEqual(d['compilerLayout']['memberList'][2]['bitLen'], 1)
        self.assertEqual(d['size'], '0x20')       # the engine-map answer is unchanged

    def test_class_layout_without_database(self):
        engine = fnv_engine.load(*fnv_engine.default_paths(self.project))
        saved = engine.symdb_path
        try:
            engine.symdb_path = Path(self.tmp.name) / 'missing.sqlite'
            with mock.patch.object(fnv_engine, 'default_paths',
                                   return_value=(self.map_path, None, Path(self.tmp.name) / 'missing.sqlite')):
                d = self.service.class_layout('Actor', compiler=True)['data']
            self.assertNotIn('compilerLayout', d)
        finally:
            engine.symdb_path = saved


if __name__ == '__main__':
    unittest.main()
