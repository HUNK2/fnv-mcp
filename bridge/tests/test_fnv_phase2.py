"""Offline tests for the phase 2 tools: reverse references, profile edits, hook-set helpers, world helpers."""
import os
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fnv_mcp import fnv_records
from fnv_mcp.fnv_control import ControlTools, _stack_words
from fnv_mcp.fnv_world import _f32, address_space


def sub(tag, data):
    return tag.encode() + struct.pack('<H', len(data)) + data


def record(tag, form_id, *subs):
    body = b''.join(subs)
    return tag.encode() + struct.pack('<IIIIHH', len(body), 0, form_id, 0, 15, 0) + body


def plugin(records, masters=('FalloutNV.esm',)):
    head = b''.join(sub('MAST', m.encode() + b'\0') + sub('DATA', b'\0' * 8) for m in masters)
    tes4 = b'TES4' + struct.pack('<IIIIHH', len(head), 0, 0, 0, 15, 0) + head
    body = b''.join(records)
    group = b'GRUP' + struct.pack('<I', 24 + len(body)) + b'TEST' + struct.pack('<i', 0) + b'\0' * 8
    return tes4 + group + body


class ReferenceScan(unittest.TestCase):
    def test_form_fields_conditions_and_lists(self):
        ctda = bytearray(28)
        struct.pack_into('<H', ctda, 8, 0x48)          # GetIsID: parameter 1 is a form
        struct.pack_into('<I', ctda, 12, 0x00104E84)
        struct.pack_into('<I', ctda, 16, 0x0000000F)   # parameter 2 is not a form for this function
        ctda_int = bytearray(28)
        struct.pack_into('<H', ctda_int, 8, 0x0E)      # GetActorValue: an actor value index, never a form
        struct.pack_into('<I', ctda_int, 12, 0x0000000F)
        lvlo = struct.pack('<HHIhH', 1, 0, 0x0000000F, 3, 0)
        data = plugin([
            record('REFR', 0x01000800, sub('NAME', struct.pack('<I', 0x00104E84))),
            record('INFO', 0x01000801, sub('CTDA', bytes(ctda)), sub('CTDA', bytes(ctda_int))),
            record('LVLI', 0x01000802, sub('LVLO', lvlo)),
            record('MISC', 0x01000803, sub('DATA', struct.pack('<I', 0x00104E84))),  # DATA is never a form
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'Test.esp'
            path.write_bytes(data)
            known = {0x00104E84, 0x0000000F}
            rows = fnv_records.scan_references(path, lambda raw: raw in known, {0x48: [True, False], 0x0E: [False]})
        found = {(t, tag, to) for _, t, _, tag, to in rows}
        self.assertEqual(found, {('REFR', 'NAME', 0x00104E84), ('INFO', 'CTDA', 0x00104E84), ('LVLI', 'LVLO', 0x0000000F)})

    def test_how_labels(self):
        self.assertEqual(fnv_records._how('FLST', 'LNAM'), 'form list entry')
        self.assertEqual(fnv_records._how('INFO', 'CTDA'), 'dialogue condition')
        self.assertEqual(fnv_records._how('ACHR', 'NAME'), 'placed reference of it')


class ProfileSet(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        profile = root / 'mo2' / 'profiles' / 'Dev'
        profile.mkdir(parents=True)
        (root / 'mo2' / 'mods' / 'ModA').mkdir(parents=True)
        (profile / 'modlist.txt').write_text('# header\n+ModA\n', encoding='utf-8')
        (profile / 'plugins.txt').write_text('# header\nFalloutNV.esm\nModA.esp\n', encoding='utf-8')
        (profile / 'loadorder.txt').write_text('# header\nFalloutNV.esm\nModA.esp\n', encoding='utf-8')
        (root / 'game' / 'Data').mkdir(parents=True)
        self.profile = profile
        project = SimpleNamespace(fnv=SimpleNamespace(mo2_root=str(root / 'mo2'), mo2_profile='Dev'),
                                  state_dir=str(root / 'state'), game_root=str(root / 'game'))
        self.tools = fnv_records.RecordsTools(SimpleNamespace(project=project))

    def tearDown(self):
        self._tmp.cleanup()

    def test_disable_and_enable_with_backup(self):
        out = self.tools.profile_set(mods={'ModA': False}, plugins={'ModA.esp': False})
        self.assertEqual(len(out['changed']), 2)
        self.assertIn('-ModA', (self.profile / 'modlist.txt').read_text(encoding='utf-8').splitlines())
        self.assertNotIn('ModA.esp', (self.profile / 'plugins.txt').read_text(encoding='utf-8').splitlines())
        self.assertTrue(Path(out['backup'], 'modlist.txt').exists())
        again = self.tools.profile_set(plugins={'ModA.esp': True})
        self.assertEqual(again['changed'][0]['now'], 'active')

    def test_dry_run_errors_and_running_game(self):
        out = self.tools.profile_set(mods={'Missing': True}, plugins={'FalloutNV.esm': False}, dry_run=True)
        self.assertEqual(len(out['errors']), 2)
        self.assertIsNone(out['backup'])
        game = SimpleNamespace(process=lambda fresh=False: [1234])
        with self.assertRaises(fnv_records.DataError):
            self.tools.profile_set(mods={'ModA': False}, game=game)


class Helpers(unittest.TestCase):
    def test_stack_words_by_convention(self):
        layout = [{'where': 'stack', 'index': 0, 'kind': 'pointer'}, {'where': 'stack', 'index': 1, 'kind': 'double'}]
        self.assertEqual(_stack_words(layout), 3)
        self.assertIsNone(_stack_words([{'where': None}]))

    def test_tracepoint_conditions(self):
        call = {'this': {'ptr': '0x00001000'}, 'args': [{'value': 5}, {'value': '0x0000ABCD'}], 'caller': {'address': '0x00401000'}}
        self.assertTrue(ControlTools._matches(call, [(2, 5, {'on': 'param0'}), (3, 0xABCD, {'on': 'param1'})]))
        self.assertFalse(ControlTools._matches(call, [(1, 0x2000, {'on': 'this'})]))

    def test_vats_percent_in_single_precision(self):
        self.assertEqual(int(_f32(_f32(0.95) * 100.0)), 95)  # the menu shows 95, not 94
        self.assertEqual(int(_f32(_f32(0.4907) * 100.0)), 49)

    def test_background_mouse_ini(self):
        from fnv_mcp.fnv_world import WorldTools
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / 'profiles' / 'Dev'
            profile.mkdir(parents=True)
            ini = profile / 'Fallout.ini'
            ini.write_text('[Controls]\nbBackground Mouse=0\nbBackground Keyboard=1\n', encoding='utf-8')
            project = SimpleNamespace(fnv=SimpleNamespace(mo2_root=tmp, mo2_profile='Dev'))
            game = SimpleNamespace(process=lambda fresh=False: [])
            world = WorldTools(SimpleNamespace(live=SimpleNamespace(project=project), game=game))
            out = world.window('status', persist=True)
            self.assertTrue(out['ini']['changed'])
            self.assertIn('bBackground Mouse=1', ini.read_text(encoding='utf-8'))
            self.assertTrue(out['state']['iniBackgroundMouse'])
            self.assertFalse(world.window('status', persist=True)['ini']['changed'])
            self.assertTrue((profile / 'Fallout.ini.fnvmcp-backup').exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows processes and structures')
    def test_address_space_of_this_process(self):
        space = address_space(os.getpid())
        self.assertIsNotNone(space)
        self.assertEqual(space['limit'], 2048)
        self.assertGreater(space['free'] + space['used'], 0)


try:
    import capstone  # noqa: F401
    HAVE_CAPSTONE = True
except ImportError:
    HAVE_CAPSTONE = False


@unittest.skipUnless(HAVE_CAPSTONE, 'capstone (the naming extra) is not installed')
class ProbePlanning(unittest.TestCase):
    """The N1 planner, with a tiny hand-built image and engine (no game, no real exe)."""

    class Image:
        def __init__(self, functions):
            self.bytes = {}
            self.path = __file__
            for a, code in functions.items():
                self.bytes[a] = code

        def read(self, address, length):
            for a, code in self.bytes.items():
                if a <= address < a + len(code):
                    o = address - a
                    return (code[o:o + length]).ljust(length, b'\x00')
            return None

    def engine(self, functions, rtti=None):
        from types import SimpleNamespace
        funcs = {a: {'address': '0x%08X' % a, 'name': n, 'status': s, 'size': len(code)}
                 for a, (n, s, code) in functions.items()}
        e = SimpleNamespace(functions=funcs, addresses=sorted(funcs), rtti=rtti or {},
                            vtables={}, callers={}, calls={}, tails={})
        e.resolve = lambda ref: int(ref, 16) if isinstance(ref, str) else ref
        e.summary = lambda a, with_location=True: {'address': '0x%08X' % a, 'name': funcs[a]['name'],
                                                   'status': funcs[a]['status']}
        e.containing = lambda a: next(((x, a - x) for x in funcs if x <= a < x + funcs[x]['size']), None)
        return e

    def test_entry_sites_and_rejections(self):
        from fnv_mcp import fnv_probe
        # 0x1000 normal prologue (entry). 0x1100 a 3-byte getter reached by a direct call from 0x1200 and two vtable
        # slots. 0x1300 has a short jump landing inside its own first 5 bytes, plus a call site from 0x1400 (so it
        # cannot take an entry jump but is still probeable at the site).
        normal = b'\x55\x8b\xec\x83\xec\x08\xc3'          # push ebp; mov ebp,esp; sub esp,8; ret
        getter = b'\x8b\x01\xc3'                          # mov eax,[ecx]; ret   (too short for an entry jump)
        caller = b'\x51\xe8' + struct.pack('<i', 0x1100 - (0x1201 + 5)) + b'\xc3'  # call 0x1100, rel32 at 0x1202
        # clean 6-byte prologue, then a jmp at 0x1306 back to 0x1302 -- inside the moved bytes 0x1300..0x1305
        selfjmp = b'\x55\x8b\xec\x83\xec\x08\xeb\xfa\xc3'
        bcaller = b'\xe8' + struct.pack('<i', 0x1300 - (0x1400 + 5)) + b'\xc3'      # call 0x1300
        functions = {
            0x1000: ('Clean::Method', 'unnamed', normal),
            0x1100: ('Tiny::Get', 'unnamed', getter),
            0x1200: ('Caller::Fn', 'decomp', caller),
            0x1300: ('Branchy::Fn', 'unnamed', selfjmp),
            0x1400: ('BCaller::Fn', 'decomp', bcaller),
        }
        rtti = {'Tiny': {'vtables': [{'address': '0x4000', 'slots': ['0x00001100', '0x00001100']}]}}
        e = self.engine(functions, rtti)
        e.callers = {0x1100: [(0x1200, 'call')], 0x1300: [(0x1400, 'call')]}
        img = self.Image({a: code for a, (_, _, code) in functions.items()})
        p = fnv_probe.ProbePlanner(e, img)
        self.assertEqual(p.analyse(0x1000)['mode'], 'entry')
        tiny = p.analyse(0x1100)
        self.assertEqual(tiny['mode'], 'sites')
        kinds = sorted(k for k, _ in tiny['sites'])
        self.assertEqual(kinds, [fnv_probe.SITE_POINTER, fnv_probe.SITE_POINTER, fnv_probe.SITE_REL32])
        self.assertIn((fnv_probe.SITE_REL32, 0x1202), tiny['sites'])  # the rel32 of the E8 call
        self.assertIn((fnv_probe.SITE_POINTER, 0x4004), tiny['sites'])  # slot 1 of the vtable
        branchy = p.analyse(0x1300)
        self.assertEqual(branchy['mode'], 'sites')
        self.assertIn('lands inside the moved bytes', branchy['entryProblem'])
        self.assertIn('quarantined', fnv_probe.ProbePlanner(e, img, {0x1000}).analyse(0x1000)['reason'])
        summary = fnv_probe.summarise(p.plan([0x1000, 0x1100, 0x1300]), e)
        self.assertEqual((summary['entry'], summary['sites']), (1, 2))
        # with a cache, as ProbeTools.plan uses it (site results carry reason None)
        cache = {}
        first = p.plan([0x1000, 0x1100, 0x1300], cache)
        self.assertEqual(sorted(cache), ['00001000', '00001100', '00001300'])
        self.assertEqual(p.plan([0x1000, 0x1100, 0x1300], cache), first)
        q = fnv_probe.ProbePlanner(e, img, {0x1100})
        q.plan([0x1100], cache_q := {})
        self.assertEqual(cache_q, {})  # quarantine verdicts are not cached

    def test_int3_for_what_nothing_else_reaches(self):
        from fnv_mcp import fnv_probe
        # 0x1500: an empty function (ret) and 0x1600 a 4-byte getter, neither called directly nor in a vtable
        functions = {0x1500: ('Empty::Fn', 'unnamed', bytes([0xC3])),
                     0x1600: ('Getter::Fn', 'unnamed', bytes.fromhex('8b4104c3')),
                     0x1700: ('Ret4::Fn', 'unnamed', bytes.fromhex('c20400'))}
        e = self.engine(functions)
        img = self.Image({a: code for a, (_, _, code) in functions.items()})
        plain = fnv_probe.ProbePlanner(e, img)
        self.assertIsNone(plain.analyse(0x1600)['mode'])
        trap = fnv_probe.ProbePlanner(e, img, int3=True)
        self.assertEqual(trap.analyse(0x1500), dict(trap.analyse(0x1500), mode='int3', stolen=1, relocs=[]))
        self.assertEqual((trap.analyse(0x1600)['mode'], trap.analyse(0x1600)['stolen']), ('int3', 3))
        self.assertEqual(trap.analyse(0x1700)['stolen'], 3)
        cache = {}
        trap.plan([0x1500, 0x1600], cache)
        self.assertEqual(cache, {})  # int3 verdicts depend on the flag: not cached


@unittest.skipUnless(HAVE_CAPSTONE, 'capstone (the naming extra) is not installed')
class CallKinds(unittest.TestCase):
    """The call instruction before a return address: vtable calls only through [reg] or [reg+disp]."""

    def kind(self, call_bytes):
        from fnv_mcp import fnv_probe

        class Img:
            def __init__(self, code):
                self.code = code

            def read(self, a, n):
                return self.code[a - 0x1000:a - 0x1000 + n]
        code = bytes([0x90] * 16) + call_bytes + bytes([0x90] * 8)
        return fnv_probe.call_kind(Img(code), 0x1000 + 16 + len(call_bytes))

    def test_kinds(self):
        self.assertEqual(self.kind(bytes.fromhex('ff5014'))['slotIndex'], 5)          # call [eax+0x14]
        self.assertEqual(self.kind(bytes.fromhex('ff10'))['slotIndex'], 0)            # call [eax]
        self.assertEqual(self.kind(bytes.fromhex('ff55e4'))['kind'], 'pointer')       # call [ebp-0x1c]: a local
        self.assertEqual(self.kind(bytes.fromhex('ff51fc'))['kind'], 'pointer')       # call [ecx-4]
        self.assertEqual(self.kind(bytes.fromhex('ffd0'))['kind'], 'register')        # call eax
        self.assertEqual(self.kind(bytes.fromhex('ff148500104000'))['kind'], 'pointer')  # call [eax*4+table]
        self.assertEqual(self.kind(bytes.fromhex('e8') + (0x2000).to_bytes(4, 'little'))['kind'], 'direct')


class SlotIntroducer(unittest.TestCase):
    """Which class a resolved virtual call's slot belongs to: the primary base chain for the primary vtable, the
    subobject's own class for a secondary vtable (both found wrong by the N8 pilot reviewers)."""

    def test_primary_and_secondary(self):
        from types import SimpleNamespace
        from fnv_mcp.fnv_naming import slot_introducer
        vt = lambda addr, off, n: {'address': hex(addr), 'thisOffset': off, 'slots': ['0x1'] * n}
        rtti = {
            'Base': {'bases': [], 'directBases': [], 'vtables': [vt(0x100, 0, 20)]},
            'Side': {'bases': [], 'directBases': [], 'vtables': [vt(0x200, 0, 30)]},   # a side interface, 30 slots
            'Owner': {'bases': [], 'directBases': [], 'vtables': [vt(0x300, 0, 6)]},   # e.g. ActorValueOwner, 6 slots
            'Mid': {'bases': ['Base'], 'directBases': ['Base'], 'vtables': [vt(0x400, 0, 25)]},
            'Leaf': {'bases': ['Mid', 'Base', 'Side', 'Owner'], 'directBases': ['Mid', 'Side', 'Owner'],
                     'vtables': [vt(0x500, 0, 25), vt(0x510, 0x40, 30), vt(0x520, 0x94, 6)]},
        }
        vtables = {}
        for cls, info in rtti.items():
            for v in info['vtables']:
                vtables[int(v['address'], 16)] = (cls, v)
        e = SimpleNamespace(rtti=rtti, vtables=vtables)
        # primary vtable, slot 18: Base introduces it (Side has 30 slots but is a separate subobject)
        self.assertEqual(slot_introducer(e, ['Leaf'], 18, [0x500]), 'Base')
        self.assertEqual(slot_introducer(e, ['Leaf'], 22, [0x500]), 'Mid')
        # secondary vtable at +0x94 with 6 slots: the Owner subobject
        self.assertEqual(slot_introducer(e, ['Leaf'], 2, [0x520]), 'Owner (subobject at +0x94)')
        # a secondary vtable no ancestor's shape fits: said so, not guessed
        e.vtables[0x530] = ('Leaf', vt(0x530, 0xA8, 9))
        self.assertEqual(slot_introducer(e, ['Leaf'], 1, [0x530]), 'secondary vtable at +0xA8')


class DoseResponse(unittest.TestCase):
    def test_fit_classifies(self):
        from fnv_mcp import fnv_atlas
        per_unit = [(0, 0), (0, 0), (1, 2), (2, 4), (4, 8), (1, 2), (2, 4), (4, 8)]
        fit = fnv_atlas.classify(per_unit)
        self.assertEqual(fit['kind'], 'per_unit')
        self.assertAlmostEqual(fit['per_unit'], 2.0, places=3)
        with_action = [(0, 0), (0, 0), (1, 1), (2, 1), (4, 1)]
        self.assertEqual(fnv_atlas.classify(with_action)['kind'], 'with_action')
        background = [(0, 30), (1, 31), (2, 29), (4, 30)]
        self.assertIsNone(fnv_atlas.classify(background))
        noisy = [(0, 5), (1, 40), (2, 20), (4, 90)]  # high but no clean line
        self.assertIsNone(fnv_atlas.classify(noisy))


if __name__ == '__main__':
    unittest.main()
