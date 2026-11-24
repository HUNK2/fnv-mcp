"""Y7: the purity checker's string-write prefix `movs` also matched movsx, movss and movsd (SSE)."""
import unittest
from types import SimpleNamespace

from fnv_mcp.fnv_oracle import Purity

BASE = 0x00500000


class Image:
    def __init__(self, code):
        self.code = code

    def read(self, address, size):
        return self.code[:size]


def check(code):
    engine = SimpleNamespace(functions={BASE: {'size': len(code)}})
    return Purity(engine, Image(code)).check(BASE)


class MovsPurityTests(unittest.TestCase):
    def test_y7_movsx_movss_movsd_sse_are_not_string_writes(self):
        for name, code in (('movsx', bytes.fromhex('0fbe4110c3')),
                           ('movss', bytes.fromhex('f30f104110c3')),
                           ('movsd xmm', bytes.fromhex('f20f104110c3'))):
            self.assertTrue(check(code)['pure'], name)

    def test_y7_real_string_moves_still_refused(self):
        for name, code in (('movsb', bytes.fromhex('a4c3')), ('movsd string', bytes.fromhex('a5c3')),
                           ('rep movsd', bytes.fromhex('f3a5c3')), ('stosd', bytes.fromhex('abc3'))):
            self.assertFalse(check(code)['pure'], name)


if __name__ == '__main__':
    unittest.main()
