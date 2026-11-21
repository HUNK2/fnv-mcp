"""Predicate compiler (Plan 2, W1): predicate text to the stack program of the engine companion.

The byte format is section 2 of docs/FNV-COMPANION-V2.md. Text form:

  numbers    12, 0x1F (32-bit; above 2147483647 the bit pattern), 1.5 (float), -3
  memory     [ADDR]:TYPE reads one value. ADDR is a number, a [..] pointer to follow, and + or - numbers:
               [0x011F6394+0x0C]:f32      one read at 0x011F6394 + 0x0C
               [[0x011DEA0C]+0x07]:u8     read the pointer at 0x011DEA0C, then the byte at it + 7
  hook bases this.[..] arg0.[..] reg.eax.[..]   the same, relative to this, a stack argument or a register:
               this.[0x08]:u32  arg0.[0x04]:i32  reg.eax.[0]:u32
  TYPE       u8 i8 u16 i16 u32 i32 f32 (required)
  frame      the frame counter;  main  true on the game's main thread
  operators  == != < <= > >=   and or not   + - & << >>   ( )
  Precedence, lowest first: or, and, not, comparisons (one per term), &, shifts, + and -.
  Comparisons are SIGNED 32-bit in the companion: a u32 pointer at or above 0x80000000 (common with the 4 GB patch)
  is negative, so `x:u32 > 0x10000000` is false for it. compile_predicate reports such a comparison in `warnings`.

Errors name the column (1-based) and say what to do next. Field paths such as player.parentCell are not
supported yet: read the memory with [address]:type.
"""
import json
import re
import struct
import threading
from pathlib import Path

from .fnv_conv import ConvError
from .fnv_state import GameIdentity, StateError, read_json, update_json

MAX_OPS = 64
MAX_DEPTH = 16
TYPES = {'u8': 0, 'i8': 1, 'u16': 2, 'i16': 3, 'u32': 4, 'i32': 5, 'f32': 6}
REGISTERS = {'eax': 0, 'ecx': 1, 'edx': 2, 'ebx': 3, 'esp': 4, 'ebp': 5, 'esi': 6, 'edi': 7}
COMPARE = {'==': 0x20, '!=': 0x21, '<': 0x22, '<=': 0x23, '>': 0x24, '>=': 0x25}
KEYWORDS = ('and', 'or', 'not')
BASE_THIS, BASE_ARG, BASE_REG = 3, 2, 1
BASE_ARG_SLOT = 4               # argN:type reads the argument's own value (its stack slot)
ARG = re.compile(r'arg(\d{1,3})')
TOKEN = re.compile(r'\s+|(?P<float>\d+\.\d+)|(?P<int>0[xX][0-9A-Fa-f]+|\d+)|(?P<name>[A-Za-z_]\w*)'
                   r'|(?P<op>==|!=|<=|>=|<<|>>|[<>+\-&()\[\]:.])')
I32_MIN, I32_MAX = -2 ** 31, 2 ** 31 - 1


# Three valid forms, shown in the predicate parameters' descriptions and in every BAD_PREDICATE (part 3).
PREDICATE_EXAMPLES = ('frame >= 500', '[0x011DEA0C]:u8 == 1', 'this.[0x08]:u32 == 0x1F and arg1:i32 > 3')
# fnv_wait_for runs on the frame, not in a hook: no this, argN or reg
WAIT_EXAMPLES = ('frame >= 500', '[0x011DEA0C]:u8 == 1', '[[0x011DEA3C]+0x24]:f32 > 0 and frame >= 100')
NAMES = ('frame', 'main', 'this', 'reg', 'arg0', 'arg1', 'arg2', 'and', 'or', 'not')


def _fail(text, pos, message):
    column = pos + 1
    end = '' if message.endswith(('.', '?')) else '.'
    return ConvError('BAD_PREDICATE', 'Bad predicate at column %d: %s%s Valid forms: %s. Next: fix the text at that '
                     'column and call again.' % (column, message, end, '; '.join(PREDICATE_EXAMPLES)),
                     {'column': column, 'text': text, 'examples': list(PREDICATE_EXAMPLES)})


def _guess(word, choices):
    from .fnv_conv import nearest
    hits = nearest(word, choices, 1)
    return ". Did you mean '%s'?" % hits[0] if hits else ''



def _tokenize(text):
    tokens, pos = [], 0
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise _fail(text, pos, 'unexpected character %r' % text[pos])
        kind = m.lastgroup
        if kind:
            value = m.group(kind)
            if kind == 'int':
                value = int(value, 16) if value[:2] in ('0x', '0X') else int(value)
            elif kind == 'float':
                value = float(value)
            tokens.append((kind, value, pos))
        pos = m.end()
    tokens.append(('end', None, len(text)))
    return tokens


def _check_i32(value, text, pos):
    if not I32_MIN <= value <= I32_MAX:
        raise _fail(text, pos, 'offset %d does not fit in 32 bits' % value)
    return value


class _Code:
    """The emitted program and its size: ops count and the deepest stack."""

    def __init__(self):
        self.data = bytearray()
        self.ops = 0
        self.depth = 0
        self.max_depth = 0
        self.reads = []         # (absolute address, size) of the first read of each absolute load: for a pre-check
        self.u32_loads = 0      # u32 loads so far (a signed ordering compare of one is suspect)
        self.warnings = []

    def _push(self, data):
        self.data += data
        self.ops += 1
        self.depth += 1
        self.max_depth = max(self.max_depth, self.depth)

    def int_(self, value):
        self._push(b'\x01' + (struct.pack('<i', value) if value < 2 ** 31 else struct.pack('<I', value)))

    def float_(self, value):
        self._push(b'\x02' + struct.pack('<f', value))

    def load(self, base, value, offsets, kind):
        if base == 0:
            size = 4 if len(offsets) > 1 else {'u8': 1, 'i8': 1, 'u16': 2, 'i16': 2}.get(kind, 4)   # a pointer first
            self.reads.append(((value + offsets[0]) & 0xFFFFFFFF, size))
        if kind == 'u32':
            self.u32_loads += 1
        body = b''.join(struct.pack('<i', o) for o in offsets)
        self._push(b'\x10' + bytes([base]) + struct.pack('<I', value) + bytes([len(offsets)]) + body
                   + bytes([TYPES[kind]]))

    def simple(self, code):
        """frame (0x50) or main (0x51): push one value."""
        self._push(bytes([code]))

    def binary(self, code):
        self.data.append(code)
        self.ops += 1
        self.depth -= 1

    def unary(self, code):
        self.data.append(code)
        self.ops += 1


class _Parser:
    def __init__(self, text):
        self.text = text
        self.toks = _tokenize(text)
        self.k = 0
        self.code = _Code()

    # ------------------------------------------------------------ token helpers
    def peek(self):
        return self.toks[self.k]

    def take(self):
        tok = self.toks[self.k]
        if tok[0] != 'end':
            self.k += 1
        return tok

    def is_(self, kind, value=None):
        tok = self.peek()
        return tok[0] == kind and (value is None or tok[1] == value)

    def fail(self, message, pos=None):
        return _fail(self.text, self.peek()[2] if pos is None else pos, message)

    def found(self):
        tok = self.peek()
        return 'the end of the text' if tok[0] == 'end' else "'%s'" % tok[1]

    def expect(self, kind, value, what):
        if not self.is_(kind, value):
            raise self.fail('expected %s but found %s' % (what, self.found()))
        return self.take()

    # ------------------------------------------------------------ grammar
    def parse(self):
        if self.is_('end'):
            raise self.fail('the predicate is empty')
        self.or_expr()
        if not self.is_('end'):
            raise self.fail('unexpected %s' % self.found())
        return self.code

    def or_expr(self):
        self.and_expr()
        while self.is_('name', 'or'):
            self.take()
            self.and_expr()
            self.code.binary(0x31)

    def and_expr(self):
        self.not_expr()
        while self.is_('name', 'and'):
            self.take()
            self.not_expr()
            self.code.binary(0x30)

    def not_expr(self):
        if self.is_('name', 'not'):
            self.take()
            self.not_expr()
            self.code.unary(0x32)
        else:
            self.comparison()

    def comparison(self):
        loads_before = self.code.u32_loads
        self.bit_and()
        tok = self.peek()
        if tok[0] == 'op' and tok[1] in COMPARE:
            self.take()
            self.bit_and()
            self.code.binary(COMPARE[tok[1]])
            if tok[1] not in ('==', '!=') and self.code.u32_loads > loads_before:
                self.code.warnings.append('the comparison %s on a u32 value is signed: values at or above 0x80000000 '
                                          'count as negative (use i32, or compare with == / != / &)' % tok[1])
            nxt = self.peek()
            if nxt[0] == 'op' and nxt[1] in COMPARE:
                raise self.fail('comparisons cannot be chained: join them with and')

    def bit_and(self):
        self.shift()
        while self.is_('op', '&'):
            self.take()
            self.shift()
            self.code.binary(0x42)

    def shift(self):
        self.additive()
        while self.is_('op', '<<') or self.is_('op', '>>'):
            op = self.take()[1]
            self.additive()
            self.code.binary(0x44 if op == '<<' else 0x43)

    def additive(self):
        self.term()
        while self.is_('op', '+') or self.is_('op', '-'):
            op = self.take()[1]
            self.term()
            self.code.binary(0x40 if op == '+' else 0x41)

    def term(self):
        tok = self.peek()
        if tok[0] == 'op' and tok[1] == '-' and self.toks[self.k + 1][0] in ('int', 'float'):
            self.take()
            num = self.take()
            if num[0] == 'int':
                self.push_int(-num[1], num[2])
            else:
                try:
                    self.code.float_(-num[1])
                except OverflowError:
                    raise self.fail('the float -%s is too large for 32 bits' % num[1], num[2])
            return
        if tok[0] == 'int':
            self.take()
            self.push_int(tok[1], tok[2])
        elif tok[0] == 'float':
            self.take()
            try:
                self.code.float_(tok[1])
            except OverflowError:
                raise self.fail('the float %s is too large for 32 bits' % tok[1], tok[2])
        elif tok[0] == 'op' and tok[1] == '(':
            self.take()
            self.or_expr()
            self.expect('op', ')', "')'")
        elif tok[0] == 'op' and tok[1] == '[':
            self.read(None)
        elif tok[0] == 'name' and tok[1] == 'frame':
            self.take()
            self.code.simple(0x50)
        elif tok[0] == 'name' and tok[1] == 'main':
            self.take()
            self.code.simple(0x51)
        elif tok[0] == 'name' and (tok[1] in ('this', 'reg') or ARG.fullmatch(tok[1])):
            self.based()
        elif tok[0] == 'name' and tok[1] in KEYWORDS:
            raise self.fail("unexpected '%s'" % tok[1])
        elif tok[0] == 'name':
            raise self.fail("unknown name '%s' (values are numbers, [address]:type, this./argN./reg.X. reads, frame "
                            "and main)%s" % (tok[1], _guess(tok[1], NAMES)))
        else:
            raise self.fail('expected a value (a number, [address]:type, frame, main or "(") but found %s'
                            % self.found())

    def push_int(self, value, pos):
        if not I32_MIN <= value <= 2 ** 32 - 1:
            raise self.fail('the number %d does not fit in 32 bits' % value, pos)
        self.code.int_(value)

    def based(self):
        tok = self.take()
        name = tok[1]
        if name == 'this':
            root = (BASE_THIS, 0)
        elif name == 'reg':
            self.expect('op', '.', "'.' and a register name after reg")
            reg = self.expect('name', None, 'a register name (eax ecx edx ebx esp ebp esi edi)')
            if reg[1] not in REGISTERS:
                raise _fail(self.text, reg[2], "unknown register '%s' (eax ecx edx ebx esp ebp esi edi)" % reg[1])
            root = (BASE_REG, REGISTERS[reg[1]])
        else:
            index = int(name[3:])
            if index > 255:
                raise _fail(self.text, tok[2], 'the argument index must be 0 to 255')
            root = (BASE_ARG, index)
            if self.is_('op', ':'):   # argN:type, the argument's own value
                self.take()
                kind = self.peek()
                if kind[0] != 'name' or kind[1] not in TYPES:
                    raise self.fail('unknown type %s (use u8 i8 u16 i16 u32 i32 f32)' % self.found())
                self.take()
                self.code.load(BASE_ARG_SLOT, index, [0], kind[1])
                return
        self.expect('op', '.', "'.' then [offset] after %s, or ':' and a type for its value" % name)
        self.read(root)

    def bracket(self, root):
        self.expect('op', '[', "'['")
        chain = self.address(root)
        self.expect('op', ']', "']'")
        return chain

    def address(self, root):
        """(base, value, offsets) for an address. Relative to root (a hook base) or absolute."""
        absolute = False
        if self.is_('int'):
            tok = self.take()
            absolute = root is None
            if root is None:
                if not 0 <= tok[1] <= 0xFFFFFFFF:
                    raise _fail(self.text, tok[2], 'an address must be 0 to FFFFFFFF')
                chain = (0, tok[1], [0])
            else:
                chain = (root[0], root[1], [_check_i32(tok[1], self.text, tok[2])])
        elif self.is_('op', '['):
            inner = self.bracket(root)
            chain = (inner[0], inner[1], inner[2] + [0])
        else:
            raise self.fail('expected an address: a number, or [ for a pointer to follow, but found %s' % self.found())
        while self.is_('op', '+') or self.is_('op', '-'):
            sign = 1 if self.take()[1] == '+' else -1
            if not self.is_('int'):
                raise self.fail("expected a number after '+' or '-' but found %s" % self.found())
            tok = self.take()
            base, value, offsets = chain
            total = offsets[-1] + sign * tok[1]
            if absolute and not I32_MIN <= total <= I32_MAX and 0 <= value + total <= 0xFFFFFFFF:
                value, total = value + total, 0     # an absolute address: the sum is the address, not an offset
            offsets[-1] = _check_i32(total, self.text, tok[2])
            chain = (base, value, offsets)
        return chain

    def read(self, root):
        base, value, offsets = self.bracket(root)
        self.expect('op', ':', "':' and a type (u8 i8 u16 i16 u32 i32 f32)")
        tok = self.peek()
        if tok[0] != 'name' or tok[1] not in TYPES:
            raise self.fail('unknown type %s (use u8 i8 u16 i16 u32 i32 f32)%s'
                            % (self.found(), _guess(str(tok[1]), TYPES) if tok[0] == 'name' else ''))
        self.take()
        self.code.load(base, value, offsets, tok[1])


def compile_predicate(text):
    """Compile predicate text. Returns {'hex', 'ops', 'depth', 'bytes'}; raises ConvError with the column."""
    if not isinstance(text, str):
        raise ConvError('BAD_PREDICATE', 'The predicate must be text. Next: pass a string such as "frame >= 100".')
    code = _Parser(text).parse()
    if code.ops > MAX_OPS:
        raise ConvError('PREDICATE_TOO_LONG', 'The predicate needs %d ops; the limit is %d. Next: split it into two '
                        'predicates or drop terms.' % (code.ops, MAX_OPS), {'ops': code.ops, 'limit': MAX_OPS})
    if code.max_depth > MAX_DEPTH:
        raise ConvError('PREDICATE_TOO_DEEP', 'The predicate needs a stack of %d; the limit is %d. Next: use fewer '
                        'nested parentheses.' % (code.max_depth, MAX_DEPTH),
                        {'depth': code.max_depth, 'limit': MAX_DEPTH})
    out = {'hex': bytes(code.data).hex(), 'ops': code.ops, 'depth': code.max_depth, 'bytes': len(code.data),
           'reads': list(code.reads)}
    if code.warnings:
        out['warnings'] = code.warnings
    return out


# ===================================================================== state kept across server restarts
_STATE_LOCK = threading.RLock()


class ProcState:
    """Small JSON file in the project's state dir for what only lives in server memory (clock pause, script freeze,
    thread freeze, break bookkeeping): a supervisor restarts the server on every code change, and the game keeps
    what the old server set. Each section is stored with the game's pid and start time; a record whose pid or start
    does not match the running game belongs to a game that is gone and is ignored (and dropped). Best effort: a file
    that cannot be read or written means no persistence, never an error."""

    FILE = 'debug_state.json'

    def __init__(self, live, name=None):
        self.live = live
        self.name = name or self.FILE

    def _path(self):
        folder = getattr(getattr(self.live, 'project', None), 'state_dir', None)
        return Path(folder) / self.name if folder else None

    @staticmethod
    def _ident(rec):
        return GameIdentity.parse(rec) if isinstance(rec, dict) else None

    def load(self, section, pid, start):
        """The stored value of a section for this game process, or None. A start time that could not be read on
        either side still matches the pid (R20)."""
        path = self._path()
        if path is None or not pid:
            return None
        with _STATE_LOCK:
            rec = read_json(path, {}).get(section)
        ident = self._ident(rec)
        if not ident or not ident.same_game(GameIdentity.parse((pid, start))):
            return None
        return rec.get('value')

    def save(self, section, pid, start, value):
        """Store a section (None, an empty list or an empty dict removes it). Records of other game processes are
        dropped on the way; records of this one are kept even when one side's start time is unknown (R20)."""
        path = self._path()
        if path is None or not pid:
            return
        me = GameIdentity.parse((pid, start))
        if me is None:
            return

        def change(data):
            kept = {}
            for k, v in data.items():
                ident = self._ident(v)
                if ident and ident.same_game(me):
                    kept[k] = v
            if value is None or (isinstance(value, (list, dict)) and not value):
                kept.pop(section, None)
            else:
                old = self._ident(kept.get(section))
                known = start if start is not None else (old.start if old else None)
                kept[section] = {'pid': me.pid, 'start': known, 'value': value}
            return kept
        with _STATE_LOCK:
            try:
                update_json(path, change, {})
            except (OSError, StateError):   # best effort: no persistence, never an error
                pass
