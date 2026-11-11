"""fnv_pred: the predicate compiler, byte for byte, and its errors (no game needed)."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geck_mcp.fnv_conv import ConvError  # noqa: E402
from geck_mcp.fnv_pred import compile_predicate  # noqa: E402


def load(base, value, offsets, type_code):
    """One load op of contract section 2, built here so the expected bytes are visible in the test."""
    return (bytes([0x10, base]) + struct.pack('<I', value) + bytes([len(offsets)])
            + b''.join(struct.pack('<i', o) for o in offsets) + bytes([type_code]))


def push(value):
    return b'\x01' + struct.pack('<i', value)


def hexof(*parts):
    return b''.join(parts).hex()


class CompileBytes(unittest.TestCase):
    def test_numbers(self):
        self.assertEqual(compile_predicate('12')['hex'], hexof(push(12)))
        self.assertEqual(compile_predicate('0x1F')['hex'], hexof(push(0x1F)))
        self.assertEqual(compile_predicate('-3')['hex'], hexof(push(-3)))
        self.assertEqual(compile_predicate('1.5')['hex'], hexof(b'\x02' + struct.pack('<f', 1.5)))
        # above 2147483647 the 32-bit pattern is stored
        self.assertEqual(compile_predicate('0xFFFFFFFF')['hex'], hexof(b'\x01' + struct.pack('<I', 0xFFFFFFFF)))

    def test_frame_and_compare(self):
        out = compile_predicate('frame >= 100')
        self.assertEqual(out['hex'], hexof(b'\x50', push(100), b'\x25'))
        self.assertEqual(out['hex'], '50016400000025')
        self.assertEqual(out['ops'], 3)
        self.assertEqual(out['depth'], 2)
        self.assertEqual(compile_predicate('main')['hex'], '51')

    def test_absolute_read_with_offset_and_float_compare(self):
        out = compile_predicate('[0x011F6394+0x0C]:f32 > 0')
        expected = hexof(load(0, 0x011F6394, [0x0C], 6), push(0), b'\x24')
        self.assertEqual(out['hex'], expected)
        self.assertEqual(out['hex'], '100094631f01010c00000006010000000024')

    def test_pointer_chain_reads_the_pointer_first(self):
        out = compile_predicate('[[0x011DEA0C]+0x07]:u8')
        # one pointer read at 0x011DEA0C (offset 0), then the byte at pointer + 7
        self.assertEqual(out['hex'], hexof(load(0, 0x011DEA0C, [0, 0x07], 0)))
        self.assertEqual(out['hex'], '10000cea1d0102000000000700000000')

    def test_nested_chain_of_two_pointers(self):
        out = compile_predicate('[[[0x01000000]+0x08]+0x10]:i32')
        self.assertEqual(out['hex'], hexof(load(0, 0x01000000, [0, 0x08, 0x10], 5)))

    def test_hook_bases(self):
        out = compile_predicate('this.[0x08]:u32 == arg0.[0x04]:i32')
        expected = hexof(load(3, 0, [0x08], 4), load(2, 0, [0x04], 5), b'\x20')
        self.assertEqual(out['hex'], expected)
        self.assertEqual(out['ops'], 3)
        self.assertEqual(compile_predicate('arg2.[0]:u8')['hex'], hexof(load(2, 2, [0], 0)))
        self.assertEqual(compile_predicate('reg.eax.[0]:u32')['hex'], hexof(load(1, 0, [0], 4)))
        self.assertEqual(compile_predicate('reg.edi.[0x10]:f32')['hex'], hexof(load(1, 7, [0x10], 6)))
        # a pointer inside a hook base: this, then the pointer at this + 8, then + 0x10
        self.assertEqual(compile_predicate('this.[[0x08]+0x10]:u8')['hex'], hexof(load(3, 0, [0x08, 0x10], 0)))

    def test_precedence_shift_and_mask_before_compare(self):
        out = compile_predicate('reg.eax.[0]:u32 & 0xFF >> 2 == 3')
        expected = hexof(load(1, 0, [0], 4), push(0xFF), push(2), b'\x43', b'\x42', push(3), b'\x20')
        self.assertEqual(out['hex'], expected)
        self.assertEqual(out['depth'], 3)

    def test_logic_and_not(self):
        self.assertEqual(compile_predicate('not frame == 5 or main')['hex'],
                         hexof(b'\x50', push(5), b'\x20', b'\x32', b'\x51', b'\x31'))
        self.assertEqual(compile_predicate('frame > 1 and frame < 9')['hex'],
                         hexof(b'\x50', push(1), b'\x24', b'\x50', push(9), b'\x22', b'\x30'))

    def test_parentheses_and_shifts(self):
        self.assertEqual(compile_predicate('(1 + 2) << 1')['hex'],
                         hexof(push(1), push(2), b'\x40', push(1), b'\x44'))
        self.assertEqual(compile_predicate('1 + 2 - 3')['hex'], hexof(push(1), push(2), b'\x40', push(3), b'\x41'))

    def test_depth_and_ops_reported(self):
        out = compile_predicate('frame >= 1 and frame <= 2')
        self.assertEqual(out['bytes'], len(bytes.fromhex(out['hex'])))
        self.assertEqual(out['ops'], 7)
        self.assertEqual(out['depth'], 3)


class CompileErrors(unittest.TestCase):
    def error(self, text):
        with self.assertRaises(ConvError) as cm:
            compile_predicate(text)
        return cm.exception

    def test_empty(self):
        e = self.error('   ')
        self.assertEqual(e.code, 'BAD_PREDICATE')
        self.assertIn('column 4', str(e))   # the end of the text after the spaces
        self.assertIn('empty', str(e))

    def test_missing_operand_names_the_end(self):
        e = self.error('frame ==')
        self.assertIn('the end of the text', str(e))
        self.assertIn('column 9', str(e))

    def test_missing_type(self):
        e = self.error('[0x10]')
        self.assertIn("expected ':'", str(e))
        self.assertIn('column 7', str(e))

    def test_unknown_type(self):
        e = self.error('[0x10]:u7')
        self.assertIn("unknown type 'u7'", str(e))
        self.assertIn('column 8', str(e))

    def test_field_path_is_not_supported_yet(self):
        e = self.error('player.x')
        self.assertIn("unknown name 'player'", str(e))
        self.assertIn('column 1', str(e))

    def test_base_needs_a_bracket(self):
        e = self.error('this.0x08')
        self.assertIn("expected '['", str(e))
        self.assertIn('column 6', str(e))

    def test_unknown_register(self):
        e = self.error('reg.foo.[0]:u8')
        self.assertIn("unknown register 'foo'", str(e))
        self.assertIn('column 5', str(e))

    def test_stray_token(self):
        e = self.error('frame 3')
        self.assertIn("unexpected '3'", str(e))
        self.assertIn('column 7', str(e))

    def test_chained_comparison(self):
        e = self.error('1 == 2 == 3')
        self.assertIn('cannot be chained', str(e))
        self.assertIn('column 8', str(e))

    def test_unknown_character(self):
        e = self.error('[1]:u8 @')
        self.assertIn("unexpected character '@'", str(e))
        self.assertIn('column 8', str(e))

    def test_unbalanced_parenthesis(self):
        e = self.error('(frame >= 1')
        self.assertIn("expected ')'", str(e))

    def test_number_too_large(self):
        e = self.error('0x1FFFFFFFF')
        self.assertIn('does not fit in 32 bits', str(e))

    def test_offset_too_large(self):
        e = self.error('this.[0x7FFFFFFF+0x7FFFFFFF]:u8')
        self.assertIn('does not fit', str(e))

    def test_program_too_long(self):
        text = ' + '.join(['1'] * 40)     # 79 ops
        e = self.error(text)
        self.assertEqual(e.code, 'PREDICATE_TOO_LONG')
        self.assertIn('79 ops', str(e))
        self.assertIn('limit is 64', str(e))

    def test_stack_too_deep(self):
        text = '1'
        for _ in range(17):
            text = '1 + (' + text + ')'
        e = self.error(text)
        self.assertEqual(e.code, 'PREDICATE_TOO_DEEP')
        self.assertIn('limit is 16', str(e))

    def test_not_text(self):
        with self.assertRaises(ConvError):
            compile_predicate(None)


if __name__ == '__main__':
    unittest.main()
