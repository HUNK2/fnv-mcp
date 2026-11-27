"""Regression tests for the Gemini audit, area 3 (engine map, oracle, symdb, naming, crash)."""
import unittest
from types import SimpleNamespace

from engine_map import commands, demangle, headers
from engine_map.pe import Image as PeImage
from fnv_mcp import fnv_facts, fnv_naming
from fnv_mcp.fnv_oracle import Purity, _equal

BASE = 0x00500000
OTHER = BASE + 0x100


class FakeImage:
    def __init__(self, blobs):
        self.blobs = blobs

    def read(self, address, size):
        return self.blobs.get(address, b'')[:size]


def check(code_a, code_b):
    engine = SimpleNamespace(functions={BASE: {'size': len(code_a)}, OTHER: {'size': len(code_b)}})
    return Purity(engine, FakeImage({BASE: code_a, OTHER: code_b})).check(BASE)


class OracleTests(unittest.TestCase):
    def test_030_tail_call_merges_pops(self):
        r = check(bytes.fromhex('e9fb000000'), bytes.fromhex('c20400'))
        self.assertTrue(r['pure'])
        self.assertEqual(r['pops'], 4)

    def test_031_conditional_tail_call_into_impure_callee(self):
        r = check(bytes.fromhex('0f84fa000000c3'), bytes.fromhex('8908c3'))  # jz B ; ret  /  mov [eax], ecx ; ret
        self.assertFalse(r['pure'])

    def test_031_conditional_jump_inside_is_fine(self):
        r = check(bytes.fromhex('7401c3c3'), bytes.fromhex('c3'))
        self.assertTrue(r['pure'])

    def test_033_int_compare_is_unsigned_safe(self):
        self.assertTrue(_equal(-1, 0xFFFFFFFF, 'int'))
        self.assertTrue(_equal(0xFF000001, -16777215, 'int'))

    def test_040_skills_has_fourteen_with_unarmed(self):
        self.assertEqual(len(fnv_facts.SKILLS), 14)
        self.assertEqual(fnv_facts.SKILLS[0], 'Barter')
        self.assertEqual(fnv_facts.SKILLS[-1], 'Unarmed')


class NamingTests(unittest.TestCase):
    def test_045_writers(self):
        w = fnv_naming._writes_reg_unseen
        self.assertTrue(w('eax', 'mov', ['al', '1']))
        self.assertTrue(w('edx', 'cdq', ['']))
        self.assertTrue(w('eax', 'call', ['0x401000']))
        self.assertTrue(w('ecx', 'xchg', ['eax', 'ecx']))
        self.assertTrue(w('ecx', 'rep movsd', ['dword ptr es:[edi]', 'dword ptr [esi]']))
        self.assertFalse(w('eax', 'mov', ['eax', '1']))


class EngineMapTests(unittest.TestCase):
    def test_042_stdcall_decoration(self):
        self.assertEqual(demangle.demangle('_Foo@8')[1], '__stdcall Foo(...)')
        self.assertEqual(demangle.demangle('_Foo')[1], '__cdecl Foo(...)')
        self.assertEqual(demangle.demangle('@Foo@8')[1], '__fastcall Foo(...)')

    def test_043_split_args_ignores_strings_and_arrows(self):
        self.assertEqual(commands._split_args('"a,b(", x->y, 3'), ['"a,b("', 'x->y', '3'])

    def test_043_strip_comments_keeps_url_strings(self):
        self.assertEqual(commands._strip_comments('a("http://x"); // c\nb'), 'a("http://x"); \nb')

    def test_044_members(self):
        body = ('/* { */\nint z; // +0x0\nunion { int a; float b; };\nvirtual void F(int a,\n  int b); // slot 0x10\n'
                'int d;\n')
        fields, virtuals = headers._members(body, 1)
        self.assertEqual([f['name'] for f in fields], ['z', 'a', 'b', 'd'])
        self.assertEqual(virtuals[0]['slot'], 16)
        self.assertEqual(virtuals[0]['declaration'], 'virtual void F(int a, int b)')

    def test_044_array_alignment_uses_element_size(self):
        layout = {'fields': [{'type': 'char', 'offset': 0, 'bits': None},
                             {'type': 'char[8]', 'offset': None, 'bits': None},
                             {'type': 'int', 'offset': None, 'bits': None}]}
        headers._infer_offsets(layout)
        self.assertEqual([f['offset'] for f in layout['fields']], [0, 1, 12])

    def test_047_read_zero_pads_bss(self):
        img = PeImage.__new__(PeImage)
        img.data = b'\x00' * 16 + b'\x01\x02'
        img.sections = [{'name': '.data', 'va': 0x1000, 'vsize': 8, 'rawsize': 2, 'raw': 16}]
        self.assertEqual(img.read(0x1000, 6), b'\x01\x02\0\0\0\0')
        self.assertEqual(img.read(0x1006, 10), b'\0\0')


if __name__ == '__main__':
    unittest.main()
