"""PLAN-3 S6 batch B: records, parsers, saves (R9/P1, R16, P2-P10)."""
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fnv_mcp import fnv_records  # noqa: E402
from fnv_mcp.fnv_data import decode_subrecord  # noqa: E402


def game_data():
    """The game's Data folder from the project file, or None (tests that need it are skipped)."""
    try:
        from fnv_mcp.config import Project
        from fnv_mcp import locate
        root = locate.project_file(Path(__file__).resolve().parents[1] / 'projects')
        data = Project.load(str(root)).game_root / 'Data'
        return data if (data / 'FalloutNV.esm').exists() else None
    except Exception:  # noqa: BLE001
        return None


DATA = game_data()


def sub(tag, payload):
    return tag + struct.pack('<H', len(payload)) + payload


def record(tag, form_id, body, flags=0):
    return tag + struct.pack('<IIIII', len(body), flags, form_id, 0, 0) + body


def plugin(*records, masters=('FalloutNV.esm',)):
    head = sub(b'HEDR', struct.pack('<fiI', 1.34, len(records), 0x800))
    for m in masters:
        head += sub(b'MAST', m.encode() + b'\0') + sub(b'DATA', b'\0' * 8)
    return record(b'TES4', 0, head) + b''.join(records)


def write(tmp, name, data):
    path = Path(tmp) / name
    path.write_bytes(data)
    return path


class FormIdTextTests(unittest.TestCase):
    """R9/P1: a 4-byte field whose bytes look like text (printable, last byte 0) was skipped as text."""

    @unittest.skipUnless(DATA, 'needs the game Data folder')
    def test_formid_with_printable_bytes_is_reference(self):
        esm = DATA / 'FalloutNV.esm'
        data = esm.read_bytes()
        ids, pos = set(), 24 + struct.unpack_from('<I', data, 4)[0]
        while pos + 24 <= len(data):
            tag, size, _, fid = struct.unpack_from('<4sIII', data, pos)
            if tag == b'GRUP':
                pos += 24
                continue
            ids.add(fid)
            pos += 24 + size
        refs = fnv_records.scan_references(esm, lambda v: v in ids)
        races = {r[4] for r in refs if r[1] == 'NPC_' and r[3] == 'RNAM'}
        self.assertTrue({0x000987DC, 0x000987DD, 0x000987DE, 0x000987DF} <= races, sorted('%08X' % r for r in races))

    def test_elijah_rnam_reference(self):
        # NVDLC01Elijah (01001300 in the DLC) names its race with RNAM; a race id with printable low bytes and a 0 top
        # byte reads as text ("ab\x09\0" style). Crafted here with such an id that exists in the master.
        race = 0x000A6261                      # bytes 61 62 0A 00: printable, ends in 0
        npc = record(b'NPC_', 0x01001300, sub(b'EDID', b'NVDLC01Elijah\0') + sub(b'RNAM', struct.pack('<I', race)))
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, 'DeadMoney.esm', plugin(npc))
            refs = fnv_records.scan_references(path, lambda v: v == race)
        self.assertIn((0x01001300, 'NPC_', race), [(r[0], r[1], r[4]) for r in refs])

    def test_text_that_is_not_a_record_stays_text(self):
        npc = record(b'NPC_', 0x01001300, sub(b'RNAM', b'ab\n\0'))
        with tempfile.TemporaryDirectory() as tmp:
            refs = fnv_records.scan_references(write(tmp, 'x.esp', plugin(npc)), lambda v: False)
        self.assertEqual(refs, [])

    def test_decode_subrecord_shows_a_form_not_text(self):
        race = 0x000A6261
        self.assertEqual(decode_subrecord('NPC_', 'RNAM', struct.pack('<I', race)), 'ab\n')
        self.assertIsNone(decode_subrecord('NPC_', 'RNAM', struct.pack('<I', race), is_form=lambda v: v == race))

    def test_refs_version_bumped(self):
        self.assertGreaterEqual(fnv_records.REFS_VERSION, 4)


class Cp1252Tests(unittest.TestCase):
    """R16: strict cp1252 decodes raised on the bytes cp1252 leaves undefined (0x81, 0x8D, 0x8F, 0x90, 0x9D)."""

    def test_undefined_cp1252_byte_in_full_modl_mast(self):
        from fnv_mcp.esp import codec
        for raw in (b'Caf\x81', b'meshes\\x\x8d.nif', b'Mod\x9d.esp'):
            text = codec.text(raw)
            self.assertEqual(text.encode('cp1252', 'cp1252-bytes'), raw, 'reads back to the same bytes')

    def test_script_variable_with_an_undefined_byte(self):
        from fnv_mcp.esp import scripts
        slsd = struct.pack('<I', 1) + b'\0' * 12 + b'\0' + b'\0' * 7
        layout = scripts.semantic_layout([('SLSD', slsd.hex()), ('SCVR', b'var\x81\0'.hex())])
        self.assertEqual(layout[0][0], 'var\x81')

    def test_no_strict_cp1252_decode_left(self):
        import re
        root = Path(__file__).resolve().parents[1] / 'fnv_mcp'
        strict = re.compile(r"""\.decode\(\s*['"]cp1252['"]\s*\)""")
        found = ['%s:%d' % (path.relative_to(root), n) for path in root.rglob('*.py')
                 for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1) if strict.search(line)]
        self.assertEqual(found, [])


def find_record(data, form_id, tag):
    pos = 24 + struct.unpack_from('<I', data, 4)[0]
    while pos + 24 <= len(data):
        rtag, size, flags, fid = struct.unpack_from('<4sIII', data, pos)
        if rtag == b'GRUP':
            pos += 24
            continue
        if fid == form_id and rtag == tag:
            return pos, size, flags
        pos += 24 + size
    return None


class BadChecksumTests(unittest.TestCase):
    """P2: FalloutNV.esm LAND 00150FC0 has a valid deflate stream with a wrong Adler-32."""

    def bad_payload(self):
        import zlib
        data = sub(b'DATA', b'land bytes ' * 40) * 10
        stream = bytearray(zlib.compress(data))
        stream[-1] ^= 0xFF                                  # break the Adler-32
        return data, struct.pack('<I', len(data)) + bytes(stream)

    def test_bad_checksum_keeps_the_bytes_with_a_warning(self):
        from fnv_mcp.esp import codec
        data, payload = self.bad_payload()
        warnings = []
        self.assertEqual(codec.decompress(payload, warnings), data)
        self.assertEqual(len(warnings), 1)
        self.assertIn('checksum', warnings[0])

    def test_broken_stream_is_format_error(self):
        from fnv_mcp.esp import codec
        with self.assertRaises(codec.FormatError):
            codec.decompress(struct.pack('<I', 10) + b'\x78\x9c not deflate')

    def test_record_carries_the_warning(self):
        from fnv_mcp.esp import codec
        data, payload = self.bad_payload()
        rec = codec.Record('LAND', 0x150FC0, codec.COMPRESSED)
        rec._compressed = payload
        rec.subrecords
        self.assertTrue(rec.warnings)

    @unittest.skipUnless(DATA, 'needs the game Data folder')
    def test_vanilla_land_150fc0_parses(self):
        from fnv_mcp.esp import codec
        data = (DATA / 'FalloutNV.esm').read_bytes()
        found = find_record(data, 0x00150FC0, b'LAND')
        self.assertIsNotNone(found)
        pos, size, flags = found
        rec, _ = codec._parse_record(data, pos, len(data), lazy=False)
        self.assertTrue(rec.subrecords)
        self.assertTrue(rec.warnings, 'the bad checksum is reported')


class FuzzTests(unittest.TestCase):
    """P3: corrupt plugins raised UnicodeDecodeError, zlib.error and struct.error instead of FormatError."""

    def seed_plugin(self):
        import zlib
        from fnv_mcp.esp import codec
        body = sub(b'EDID', b'Thing\0') + sub(b'FULL', b'A thing\0') + sub(b'DATA', b'\x01' * 12)
        packed = struct.pack('<I', len(body)) + zlib.compress(body)
        recs = record(b'MISC', 0x01000800, body) + record(b'MISC', 0x01000801, packed, flags=codec.COMPRESSED)
        group = b'GRUP' + struct.pack('<I', 24 + len(recs)) + b'MISC' + struct.pack('<iII', 0, 0, 0) + recs
        return plugin() + group

    def mutations(self, data, count=400):
        import random
        rng = random.Random(20261113)
        for _ in range(count):
            raw = bytearray(data)
            kind = rng.randrange(3)
            if kind == 0:                                   # flip bytes
                for _ in range(rng.randint(1, 6)):
                    raw[rng.randrange(len(raw))] = rng.randrange(256)
            elif kind == 1:                                 # truncate
                raw = raw[:rng.randrange(1, len(raw))]
            else:                                           # non-ASCII tags and labels
                for at in rng.sample(range(len(raw)), 4):
                    raw[at] = rng.randrange(0x80, 0x100)
            yield bytes(raw)

    def test_fuzzed_plugin_raises_format_error_only(self):
        from fnv_mcp.esp import codec, validate
        seed = self.seed_plugin()
        codec.parse(seed, lazy=False)                       # the seed itself is valid
        wrong = []
        for n, data in enumerate(self.mutations(seed)):
            for name, fn in (('parse', lambda d: codec.parse(d, lazy=False)),
                             ('iter_records', lambda d: [r.subrecords for r, _ in codec.iter_records(d)]),
                             ('labels', lambda d: [repr(g) for g in codec.parse(d).groups]),
                             ('validate', lambda d: validate.validate(d))):
                try:
                    fn(data)
                except codec.FormatError:
                    pass
                except Exception as e:  # noqa: BLE001 - what this test looks for
                    wrong.append((n, name, type(e).__name__, str(e)[:60]))
        self.assertEqual(wrong[:10], [], '%d raw exceptions' % len(wrong))


class XxxxTests(unittest.TestCase):
    """P4: a subrecord read with XXXX but small enough without it was written back without the XXXX."""

    def test_xxxx_round_trip_exact(self):
        from fnv_mcp.esp import codec
        small = b'abcdefghij'
        body = (sub(b'EDID', b'Big\0') + b'XXXX' + struct.pack('<HI', 4, len(small)) + b'DATA' + struct.pack('<H', 0)
                + small)
        data = plugin(record(b'MISC', 0x01000800, body))
        self.assertEqual(codec.write(codec.parse(data, lazy=False)), data)

    def test_large_subrecord_still_gets_xxxx(self):
        from fnv_mcp.esp import codec
        rec = codec.Record('MISC', 0x01000800)
        rec.subrecords = [codec.Subrecord('DATA', b'x' * 0x10000)]
        self.assertIn(b'XXXX', codec.encode_subrecords(rec.subrecords))

    def test_xxxx_flag_does_not_change_equality(self):
        from fnv_mcp.esp import codec
        self.assertEqual(codec.Subrecord('DATA', b'1'), codec.Subrecord('DATA', b'1', xxxx=True))


class RecordFindWinnerTests(unittest.TestCase):
    """P5: record_find showed the name of the highest file id, not of the load-order winner."""

    def test_record_find_uses_load_order_winner(self):
        import sqlite3
        from types import SimpleNamespace
        from fnv_mcp.fnv_data import DataTools
        db = sqlite3.connect(':memory:')
        db.execute('create table records(file integer, owner text, local integer, type text, edid text, full text, '
                   'offset integer)')
        # FalloutNV.esm was re-indexed after DeadMoney.esm, so it has the higher file id (2) but loads first
        db.execute("insert into records values (2, 'falloutnv.esm', 0x15EDAC, 'INFO', 'VDialogueVeronicaAbeMsgRefuse', "
                   "'Base text', 0)")
        db.execute("insert into records values (1, 'falloutnv.esm', 0x15EDAC, 'INFO', 'VDialogueVeronicaAbeMsgRefuse', "
                   "'It''s better you don''t...', 0)")
        entries = [{'name': 'FalloutNV.esm', 'index': 0, 'fileId': 2}, {'name': 'DeadMoney.esm', 'index': 1, 'fileId': 1}]
        tools = DataTools.__new__(DataTools)
        tools._index = SimpleNamespace(db=db)
        tools._active = lambda: (entries, None)
        out = tools.record_find('0015EDAC')['results'][0]
        self.assertEqual(out['winner'], 'DeadMoney.esm')
        self.assertEqual(out['name'], "It's better you don't...")


class FormIndexTests(unittest.TestCase):
    """P6: a top byte one past the plugin's own index resolved to the plugin itself."""

    def test_form_index_bounds(self):
        import sqlite3
        from types import SimpleNamespace
        from fnv_mcp.fnv_data import DataTools
        db = sqlite3.connect(':memory:')
        db.execute('create table records(file integer, owner text, local integer, type text, edid text, full text)')
        db.execute("insert into records values (1, 'mymod.esp', 0x800, 'MISC', 'Thing', null)")
        tools = DataTools.__new__(DataTools)
        tools._index = SimpleNamespace(db=db)
        # file ids as _active gives them: _form reads only the active plugins' rows (Z2)
        entries = [{'name': 'FalloutNV.esm', 'index': 0, 'fileId': 0}, {'name': 'MyMod.esp', 'index': 1, 'fileId': 1}]
        plugin_entry = {'name': 'MyMod.esp', 'masters': ['FalloutNV.esm']}
        self.assertEqual(tools._form(entries, plugin_entry, 0x01000800)['editorId'], 'Thing')   # index 1: itself
        # past it: the plugin itself too, the record index's rule (S6 review B M7: P6 was not a bug)
        self.assertEqual(tools._form(entries, plugin_entry, 0x02000800)['editorId'], 'Thing')


class IndexGapTests(unittest.TestCase):
    """P7: placed grenades/mines and navmeshes were not indexed by EditorID; PlayerREF unknown; offset/limit unclamped."""

    def test_pgre_edid_indexed(self):
        from fnv_mcp.fnv_data import RecordIndex
        recs = [record(b'PGRE', 0x01000800, sub(b'EDID', b'FiendMineRef\0')),
                record(b'PMIS', 0x01000801, sub(b'EDID', b'SomeMissileRef\0')),
                record(b'NAVM', 0x01000802, sub(b'EDID', b'NVDLC04ElevNav02REF\0'))]
        with tempfile.TemporaryDirectory() as tmp:
            path = write(tmp, 'MyMod.esp', plugin(*recs))
            index = RecordIndex(tmp)
            file_id = index.ensure(str(path))
            names = {r[0] for r in index.db.execute('select edid from records where file = ?', (file_id,))}
            index.db.close()
        self.assertTrue({'FiendMineRef', 'SomeMissileRef', 'NVDLC04ElevNav02REF'} <= names, names)

    def test_playerref_is_known(self):
        import sqlite3
        from types import SimpleNamespace
        from fnv_mcp.fnv_data import DataTools
        db = sqlite3.connect(':memory:')
        db.execute('create table records(file integer, owner text, local integer, type text, edid text, full text)')
        tools = DataTools.__new__(DataTools)
        tools._index = SimpleNamespace(db=db)
        self.assertEqual(tools._edid_lookup('PlayerREF', []), {'ACHR'})

    def test_huge_offset_clamped(self):
        import sqlite3
        from types import SimpleNamespace
        from fnv_mcp.fnv_data import DataTools
        db = sqlite3.connect(':memory:')
        db.execute('create table records(file integer, owner text, local integer, type text, edid text, full text)')
        for k in range(5):
            db.execute("insert into records values (1, 'a.esp', ?, 'MISC', ?, null)", (k, 'Thing%d' % k))
        tools = DataTools.__new__(DataTools)
        tools._index = SimpleNamespace(db=db)
        tools._active = lambda: ([{'name': 'A.esp', 'index': 0, 'fileId': 1}], None)
        self.assertEqual(tools.record_find('Thing', offset=10 ** 30)['count'], 0)
        self.assertEqual(tools.record_find('Thing', limit=-1)['count'], 1, 'limit is at least 1, not unlimited')
        self.assertEqual(tools.record_find('Thing', limit=10 ** 9)['count'], 5)


class LintVanillaTests(unittest.TestCase):
    """P8: lint false positives on the vanilla scripts (Ref. Cmd, PlayerREF, ShowMessage, zero-parameter commands)."""

    @staticmethod
    def engine():
        from fnv_mcp import fnv_engine
        from fnv_mcp.config import Project
        from fnv_mcp import locate
        root = locate.project_file(Path(__file__).resolve().parents[1] / 'projects')
        if root is None or not root.exists():       # a local project file (CI has none)
            raise unittest.SkipTest('needs the local project file and the engine map')
        paths = fnv_engine.default_paths(Project.load(str(root)))
        if not paths[0].exists():
            raise unittest.SkipTest('needs the engine map')
        return fnv_engine.load(*paths)

    @unittest.skipUnless(DATA, 'needs the game Data folder')
    def test_vanilla_scripts_lint_clean(self):
        from collections import Counter
        from fnv_mcp.esp import codec
        from fnv_mcp.fnv_data import lint_script
        engine = self.engine()
        data = (DATA / 'FalloutNV.esm').read_bytes()
        edids = {}
        for rec, _ in codec.iter_records(data):
            if rec.type != 'LAND' and rec.editor_id:
                edids.setdefault(rec.editor_id.lower(), set()).add(rec.type)
        lookup = lambda name: edids.get(name.lower(), set()) | ({'ACHR'} if name.lower() == 'playerref' else set())
        codes, scripts = Counter(), 0
        for rec, _ in codec.iter_records(data, types=['SCPT']):
            source = rec.get('SCTX')
            if source:
                scripts += 1
                for p in lint_script(codec.text(source), engine, lookup)['problems']:
                    codes[(p['severity'], p['code'])] += 1
        errors = {k: v for k, v in codes.items() if k[0] == 'error'}
        self.assertEqual(errors, {})
        self.assertNotIn(('warning', 'TOO_MANY_ARGUMENTS'), codes)
        self.assertNotIn(('warning', 'UNKNOWN_REFERENCE'), codes)
        # 2,576 scripts with source; 151 warnings recorded on 2026-11-13 (declarations in blocks, stray endif/else,
        # duplicate variables, decoration lines, variables named like commands, one scn with extra words)
        self.assertGreaterEqual(scripts, 2500)
        warnings = sum(v for k, v in codes.items() if k[0] == 'warning')
        self.assertLessEqual(warnings, 151, codes)
        # 132 infos (EXTRA_TOKENS_IGNORED after zero-parameter commands, S6 review B M4)
        self.assertLessEqual(codes.get(('info', 'EXTRA_TOKENS_IGNORED'), 0), 132)

    def test_ref_space_cmd_showmessage_and_zero_param_extras(self):
        from fnv_mcp.fnv_data import lint_script
        engine = self.engine()
        source = ('scn T\nref r\nBegin GameMode\n  r. StopLook\n  ShowMessage SomeMsg rank\n  StopCombat player\n'
                  '  PlayerREF.Disable\nEnd\n')
        problems = lint_script(source, engine, lambda name: {'MESG'} if name == 'SomeMsg' else set())['problems']
        self.assertEqual([p for p in problems if p['severity'] == 'error'], [])
        self.assertEqual([p['code'] for p in problems if p['code'] in ('TOO_MANY_ARGUMENTS', 'UNKNOWN_REFERENCE')],
                         [])


class SpecCheckTests(unittest.TestCase):
    """P9: `$` matched before a trailing newline; model_path and text() took control characters."""

    def test_edid_trailing_newline_refused(self):
        from fnv_mcp.esp import records
        with self.assertRaises(records.SpecError):
            records.check_edid('Thing\n', 'records.Thing')
        records.check_edid('Thing', 'records.Thing')

    def test_spec_names_with_trailing_newline_refused(self):
        from fnv_mcp.esp import build, records
        base = {'schema': build.SCHEMA, 'project_id': 'p', 'plugin': 'x.esp', 'masters': ['FalloutNV.esm']}
        for field, value in (('plugin', 'x.esp\n'), ('project_id', 'p\n')):
            with self.assertRaises(records.SpecError, msg=field):
                build.check_spec(dict(base, **{field: value}))
        self.assertTrue(build.REF_ID_RE.match('ref1'))
        self.assertIsNone(build.REF_ID_RE.match('ref1\n'))

    def test_model_path_refuses_control_and_device_names(self):
        from fnv_mcp.esp import records
        for bad in ('a\0.nif', 'a\n.nif', 'a\t.nif', 'a*.nif', 'con.nif', 'dir\\NUL.nif', 'x?.nif'):
            with self.assertRaises(records.SpecError, msg=repr(bad)):
                records.model_path(bad, 'records.X.model')
        records.model_path('clutter\\cup.nif', 'records.X.model')

    def test_text_refuses_nul(self):
        from fnv_mcp.esp import records
        with self.assertRaises(records.SpecError):
            records.text('a\0b', 'records.X.name')
        records.text('two\nlines', 'records.X.name')


class SaveRotationTests(unittest.TestCase):
    """P10: the FLT_MAX rotation sentinel (bytes ff ff 7f 7f) showed as rotation.x = 3.4e38."""

    def initial(self, rotation):
        from types import SimpleNamespace
        from fnv_mcp.fnv_save import Save
        block = b'\x01\x00\x00' + struct.pack('<6f', 1.0, 2.0, 3.0, *rotation)
        save = Save.__new__(Save)
        return save._initial(4, block, SimpleNamespace(resolve=lambda raw: 0x0000003C))

    def test_flt_max_rotation_flagged(self):
        flt_max = struct.unpack('<f', b'\xff\xff\x7f\x7f')[0]
        info = self.initial((flt_max, 0.0, 0.0))
        self.assertEqual(info['rotation'], {'x': None, 'y': 0.0, 'z': 0.0})      # per axis (S6 review B I1)
        self.assertEqual(info['rotationUnset'], ['x'])
        self.assertEqual(info['position'], {'x': 1.0, 'y': 2.0, 'z': 3.0})

    def test_real_rotation_kept(self):
        info = self.initial((0.5, 0.0, 1.25))
        self.assertEqual(info['rotation'], {'x': 0.5, 'y': 0.0, 'z': 1.25})
        self.assertNotIn('rotationUnset', info)


if __name__ == '__main__':
    unittest.main()
