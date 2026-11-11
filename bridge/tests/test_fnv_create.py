"""Plan 2 W4 creation loop: BSA hash map, the record fields fnv_form reload applies, file log decoding, reload of a
model through the loaded cells, placement journal and export, packaging rules. Fakes only; no game."""
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp import fnv_create  # noqa: E402
from geck_mcp.esp.codec import Record, Subrecord  # noqa: E402
from geck_mcp.fnv_create import AssetTools, AuthorTools, FormTools, PlaceTools, bsa_hashes  # noqa: E402
from geck_mcp.fnv_live import Journal, LiveError  # noqa: E402


def write_bsa(path, folders):
    """A minimal BSA v104 with folder names and file names: folders = {folder: [(file, hash)]}."""
    names = b''.join(f.encode() + b'\0' for files in folders.values() for f, _ in files)
    folder_records, body = b'', b''
    for folder, files in folders.items():
        folder_records += struct.pack('<QII', 0, len(files), 0)
        body += bytes([len(folder) + 1]) + folder.encode() + b'\0'
        body += b''.join(struct.pack('<QII', h, 0, 0) for _, h in files)
    total = sum(len(f) for f in folders.values())
    header = b'BSA\0' + struct.pack('<8I', 104, 36, 3, len(folders), total, 0, len(names), 0)
    Path(path).write_bytes(header + folder_records + body + names)


class BsaTests(unittest.TestCase):
    def test_hashes_map_to_paths(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'Test.bsa'
            write_bsa(p, {'meshes\\clutter': [('bottle.nif', 0x1111), ('cup.nif', 0x2222)],
                          'textures\\clutter': [('bottle.dds', 0x3333)]})
            out = bsa_hashes(p)
        self.assertEqual(out[0x1111], ['meshes\\clutter\\bottle.nif'])
        self.assertEqual(out[0x2222], ['meshes\\clutter\\cup.nif'])
        self.assertEqual(out[0x3333], ['textures\\clutter\\bottle.dds'])

    def test_not_a_bsa(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'x.bsa'
            p.write_bytes(b'nope' * 20)
            self.assertEqual(bsa_hashes(p), {})


class FieldOpsTests(unittest.TestCase):
    def test_armour_fields(self):
        rec = Record('ARMO', 0x01000ABC, _subrecords=[
            Subrecord('EDID', b'TestArmor\0'), Subrecord('FULL', b'Test Armor\0'),
            Subrecord('MODL', b'armor\\test\\m.nif\0'), Subrecord('MOD2', b'armor\\test\\g.nif\0'),
            Subrecord('DATA', struct.pack('<iif', 120, 300, 15.0)), Subrecord('DNAM', struct.pack('<hHf', 0, 0, 12.0))])
        ops = {f: (v, t) for f, v, t in FormTools(None)._field_ops('ARMO', rec, '0A000ABC')}
        self.assertEqual(ops['name'][0], 'Test Armor')
        self.assertEqual(ops['model.MODL'][1] % ops['model.MODL'][0], 'SetBipedModelPathEX "armor\\test\\m.nif" 0 0 0A000ABC')
        self.assertEqual(ops['model.MOD2'][1] % ops['model.MOD2'][0], 'SetBipedModelPathEX "armor\\test\\g.nif" 1 0 0A000ABC')
        self.assertEqual(ops['value'][1] % ops['value'][0], 'SetBaseItemValue 0A000ABC 120')
        self.assertEqual(ops['health'][1] % ops['health'][0], 'SetHealth 300 0A000ABC')
        self.assertEqual(ops['weight'][0], 15.0)
        self.assertEqual(ops['damageThreshold'][1] % ops['damageThreshold'][0], 'SetArmorDT 12.0 0A000ABC')

    def test_misc_fields(self):
        rec = Record('MISC', 0x01000001, _subrecords=[Subrecord('MODL', b'clutter\\x.nif\0'),
                                                     Subrecord('DATA', struct.pack('<if', 7, 0.5))])
        ops = {f: (v, t) for f, v, t in FormTools(None)._field_ops('MISC', rec, '0A000001')}
        self.assertEqual(ops['model'][1] % ops['model'][0], 'SetModelPathEX "clutter\\x.nif" 0A000001')
        self.assertEqual(ops['value'][0], 7)
        self.assertEqual(ops['weight'][0], 0.5)


class FakeLink:
    def __init__(self, live):
        self.live = live
        self.sent = []

    def one(self, *op, **kw):
        self.sent.append(op)
        if op[0] == 'hooks':
            return {'ok': True, 'hooks': self.live.hooks}
        if op[0] == 'call':
            return {'ok': True, 'eax': '0x0'}
        if op[0] == 'unhook':
            self.live.hooks = []
        return {'ok': True}

    def batch(self, ops, timeout=None):
        out = []
        for op in ops:
            if op[0] == 'eval':
                text = bytes.fromhex(op[1]).decode()
                out.append(self.live.eval_one(text, int(op[2], 16)))
            else:
                out.append({'ok': True})
        return out

    def eval(self, text, ref=0):
        return self.live.eval_one(text, ref)

    def ping(self):
        return {'frame': 1}


class FakeLive:
    def __init__(self, state):
        self.project = SimpleNamespace(state_dir=state, game_root=str(Path(state) / 'game'))
        self.journal = Journal(Path(state) / 'journal.jsonl')
        self.hooks = []
        self.link = FakeLink(self)
        self.lines = []
        self.mem = {}
        self.refs = {}       # ptr -> {'formId', 'base', 'model', 'pose'}

    def eval_one(self, text, ref):
        r = self.refs.get(ref)
        if text == 'GetBaseObject' and r:
            return {'ok': True, 'result': {'type': 'form', 'formId': r['base']}}
        if text.startswith('GetModelPath '):
            base = text.split()[1]
            model = next((x['model'] for x in self.refs.values() if x['base'] == base), '')
            return {'ok': True, 'result': {'type': 'string', 'value': model}}
        if text == 'GetParentCell':
            return {'ok': True, 'result': {'type': 'form', 'formId': '00001234'}}
        if text.startswith('PlaceAtMe'):
            ptr = 0x9000 + len(self.refs)
            self.refs[ptr] = {'formId': 'FF%06X' % ptr, 'base': text.split()[1], 'model': '',
                              'pose': [0, 0, 0, 0, 0, 0, 1]}
            return {'ok': True, 'result': {'type': 'form', 'formId': 'FF%06X' % ptr, 'ptr': '0x%08X' % ptr}}
        return {'ok': True, 'result': {'type': 'number', 'value': 0}}

    def u32s(self, addresses):
        return [self.mem.get(a, 0) for a in addresses]

    def reads(self, spans):
        return [bytes(n) for _, n in spans]

    def walk_list(self, head, limit=500):
        return list(self.refs) if head == 0x500 + fnv_create.__dict__.get('CELL_REFS', 0xAC) else []

    def form_ids(self, ptrs):
        return [{'formId': self.refs[p]['formId'], 'formType': 47, 'flags': 0} if p in self.refs else None
                for p in ptrs]

    def values(self, exprs, ref=0):
        r = self.refs.get(ref)
        out = []
        for e in exprs:
            if r and e.startswith(('GetPos', 'GetAngle', 'GetScale')):
                idx = ['GetPos X', 'GetPos Y', 'GetPos Z', 'GetAngle X', 'GetAngle Y', 'GetAngle Z',
                       'GetScale'].index(e)
                out.append(r['pose'][idx])
            elif e == 'GetDisabled':
                out.append(0)
            else:
                out.append(0)
        return out

    def console(self, line, ref=0):
        self.lines.append((line, ref))
        r = self.refs.get(ref)
        if r and line.startswith('SetPos '):
            r['pose']['XYZ'.index(line.split()[1])] = float(line.split()[2])
        return {'accepted': True, 'output': []}

    def resolve(self, ref):
        if ref == 'player':
            return {'ptr': 0x14, 'formId': '00000014'}
        for p, r in self.refs.items():
            if r['formId'].upper() == ref.upper():
                return {'ptr': p, 'formId': r['formId']}
        return {'ptr': int(ref, 16), 'formId': ref.upper()}

    def identity(self):
        return {'pid': 5}

    def ready(self):
        return {}

    def in_game(self):
        return {}

    def changing(self, reason):
        return None


class AssetTests(unittest.TestCase):
    def test_reload_disables_purges_and_enables_every_user_of_the_model(self):
        with tempfile.TemporaryDirectory() as d:
            live = FakeLive(d)
            live.refs = {0x100: {'formId': '00000100', 'base': '0003404A', 'model': 'Clutter\\Food\\Wine.NIF'},
                         0x200: {'formId': '00000200', 'base': '00000999', 'model': 'Other.nif'}}
            live.mem[fnv_create.MODEL_LOADER] = 0x7000
            from geck_mcp import fnv_eyes
            live.mem[fnv_eyes.G_TES] = 0x400
            live.reads = lambda spans: [struct.pack('<16I', *([0] * 13 + [0x500, 0, 0])) if a == 0x400 else bytes(n)
                                        for a, n in spans]
            assets = AssetTools(live, sleep=lambda s: None, clock=iter(range(1000)).__next__)
            live.u32s = lambda addrs: [{fnv_create.MODEL_LOADER: 0x7000, fnv_eyes.G_TES: 0x400}.get(a, 1)
                                       for a in addrs]
            out = assets.reload('meshes/clutter/food/wine.nif')
        self.assertEqual(out['refs'], 1)
        self.assertEqual([l for l, r in live.lines], ['Disable', 'Enable'])
        purge = [s for s in live.link.sent if s[0] == 'call'][0]
        self.assertEqual(int(purge[1], 16), fnv_create.CLEAR_UNUSED_MODELS)
        self.assertEqual(purge[2], 'vg')                       # guarded: the function is not proven yet
        self.assertEqual(int(purge[3], 16), 0x7000)

    def test_file_log_needs_start(self):
        with tempfile.TemporaryDirectory() as d:
            assets = AssetTools(FakeLive(d))
            with self.assertRaises(LiveError) as cm:
                assets.file_log('read')
            self.assertEqual(cm.exception.code, 'NOT_RECORDING')

    def test_file_log_decodes_lookup_names_and_archive_hashes(self):
        with tempfile.TemporaryDirectory() as d:
            live = FakeLive(d)
            live.hooks = [{'label': 'file_log', 'stolen': 6, 'id': 1}, {'label': 'file_log_bsa', 'stolen': 6, 'id': 2}]
            name = b'meshes\\missing.nif\0'.ljust(96, b'\0')
            lookup = [1, 2, 9, 0, 0, 0x401000, 5, 0, 0] + [0] * 15 + list(struct.unpack('<24I', name))
            entry = struct.pack('<QII', 0xABCD, 0, 0).ljust(96, b'\0')
            archive = [1, 2, 9, 0, 0, 0x402000, 3, 0, 0x1234] + [0] * 15 + list(struct.unpack('<24I', entry))
            live.deep = SimpleNamespace(records=lambda h, since=0: (1, [lookup] if h['label'] == 'file_log'
                                                                    else [archive], 0))
            live.engine = lambda: SimpleNamespace(summary=lambda a: {})
            assets = AssetTools(live)
            assets._hashes = {0xABCD: ['meshes\\clutter\\bottle.nif']}
            out = assets.file_log('read')
            self.assertEqual(out['totals'], {'opened': 1, 'missing': 1})
            self.assertEqual([e['path'] for e in out['entries']], ['meshes\\clutter\\bottle.nif', 'meshes\\missing.nif'])
            self.assertEqual(assets.file_log('read', missing=True)['entries'][0]['found'], False)


class PlaceAndExportTests(unittest.TestCase):
    def test_place_is_journaled_listed_and_exported(self):
        with tempfile.TemporaryDirectory() as d:
            live = FakeLive(d)
            place = PlaceTools(live)
            out = place.at('0003404A', position=[10, 20, 30], rotation=[0, 0, 90])
            self.assertTrue(out['ref'].startswith('FF'))
            self.assertIn(('SetPos X 10.000', live.resolve(out['ref'])['ptr']), live.lines)
            listed = place.list()['placed']
            self.assertEqual(len(listed), 1)
            author = AuthorTools(live, place=place)
            author._editor_id = lambda form_id: 'Wine' if form_id == '0003404A' else None
            exported = author.export(name='t')
            spec = json.loads(Path(exported['path']).read_text())
            obj = spec['cells']['LiveSessionCell']['objects'][0]
            self.assertEqual(obj['base'], '@Wine')
            self.assertEqual(obj['position'], [10, 20, 30])

    def test_remove_marks_the_placement_undone(self):
        with tempfile.TemporaryDirectory() as d:
            live = FakeLive(d)
            place = PlaceTools(live)
            ref = place.at('0003404A', position=[0, 0, 0])['ref']
            self.assertEqual(place.remove()['removed'], [ref])
            self.assertEqual(place.list()['placed'], [])
            self.assertIn('MarkForDelete', [l for l, _ in live.lines])


class FormTests(unittest.TestCase):
    def test_refused_types(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                FormTools(FakeLive(d)).create('QUST')
            with self.assertRaises(ValueError):
                FormTools(FakeLive(d)).create('ZZZZ')

    def test_reload_of_a_missing_rebuilt_file_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            live = FakeLive(d)
            live.in_game = lambda: {}
            with self.assertRaises(LiveError) as cm:
                FormTools(live).reload(str(Path(d) / 'build' / 'Mine.esp'))
            self.assertEqual(cm.exception.code, 'PLUGIN_NOT_FOUND')

    def test_bytes_and_delete_need_a_form(self):
        with tempfile.TemporaryDirectory() as d:
            for action in ('bytes', 'delete'):
                live = FakeLive(d)
                live.in_game = lambda: {}
                with self.assertRaises(ValueError):
                    FormTools(live).run(action)

    def test_delete_refuses_a_form_this_session_did_not_create(self):
        with tempfile.TemporaryDirectory() as d:
            live = FakeLive(d)
            live.in_game = lambda: {}
            live.resolve = lambda ref: {'ptr': 0x30000000, 'formId': '0000000F'}
            with self.assertRaises(LiveError) as cm:
                FormTools(live).delete('Caps001')
            self.assertEqual(cm.exception.code, 'NOT_CREATED_HERE')


class ScaffoldTests(unittest.TestCase):
    def test_item_scaffold_files(self):
        with tempfile.TemporaryDirectory() as d:
            out = AuthorTools(FakeLive(d)).scaffold('item', 'MyThing')
            self.assertEqual(out['files'], ['MyThing.spec.json', 'MyThing.test.json', 'README.md'])
            spec = json.loads((Path(out['folder']) / 'MyThing.spec.json').read_text())
            self.assertEqual(spec['records']['MyThingItem']['type'], 'MISC')

    def test_bad_names_refused(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                AuthorTools(FakeLive(d)).scaffold('item', '1bad name')
            with self.assertRaises(ValueError):
                AuthorTools(FakeLive(d)).scaffold('castle', 'Good')


if __name__ == '__main__':
    unittest.main()
