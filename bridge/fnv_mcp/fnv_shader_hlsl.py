"""HLSL compile and shader bytecode reading for fnv_shader (d3dcompiler_47 through ctypes).

Compiles shader model 2/3 HLSL to bytecode, reads the constant table (CTAB) and the disassembly.
"""
import ctypes
import re
import struct
from ctypes import POINTER, byref, c_char_p, c_size_t, c_uint, c_void_p
from typing import NamedTuple

from .fnv_conv import ConvError

PROFILES = ('vs_2_0', 'vs_3_0', 'ps_2_0', 'ps_2_a', 'ps_2_b', 'ps_3_0')
SOURCE_NAME = b'effect.hlsl'
_REGSETS = {0: 'bool', 1: 'int4', 2: 'float4', 3: 'sampler'}
_INCLUDE_RE = re.compile(r'^\s*#\s*include\b', re.M)
_ERR_RE = re.compile(r'effect\.hlsl\((\d+)(?:,(\d+)(?:-\d+)?)?\):\s*(error|warning)\s+([A-Za-z]\d+):\s*(.*)')
_DLL = None


class Constant(NamedTuple):
    name: str
    regset: str
    index: int
    count: int


def _dll():
    global _DLL
    if _DLL is None:
        try:
            dll = ctypes.WinDLL('d3dcompiler_47.dll')
        except (OSError, AttributeError) as e:
            raise ConvError('NO_COMPILER', 'd3dcompiler_47.dll could not be loaded (%s).' % e)
        dll.D3DCompile.argtypes = [c_char_p, c_size_t, c_char_p, c_void_p, c_void_p, c_char_p, c_char_p,
                                   c_uint, c_uint, POINTER(c_void_p), POINTER(c_void_p)]
        dll.D3DCompile.restype = ctypes.c_long
        dll.D3DDisassemble.argtypes = [c_char_p, c_size_t, c_uint, c_char_p, POINTER(c_void_p)]
        dll.D3DDisassemble.restype = ctypes.c_long
        _DLL = dll
    return _DLL


def _slot(obj, index, restype):
    vtable = ctypes.cast(obj, POINTER(c_void_p))[0]
    fn = ctypes.cast(vtable, POINTER(c_void_p))[index]
    return ctypes.WINFUNCTYPE(restype, c_void_p)(fn)(obj)


def _blob_bytes(blob):
    """Copy a D3D blob's bytes and release it (vtable: 2 Release, 3 GetBufferPointer, 4 GetBufferSize)."""
    if not blob or not blob.value:
        return b''
    try:
        return ctypes.string_at(_slot(blob, 3, c_void_p), _slot(blob, 4, c_size_t))
    finally:
        _slot(blob, 2, c_uint)


def _clean(text):
    return re.sub(r'(?m)^.*?effect\.hlsl\(', 'effect.hlsl(', text)


def _messages(text):
    errors, warnings = [], []
    for line in _clean(text).splitlines():
        m = _ERR_RE.search(line)
        if not m:
            continue
        item = {'line': int(m.group(1)), 'col': int(m.group(2) or 0), 'code': m.group(4),
                'text': m.group(5).strip()}
        (errors if m.group(3) == 'error' else warnings).append(item)
    return errors, warnings


def version_of(bytecode):
    """The version token, e.g. 0xFFFF0300 for ps_3_0."""
    return struct.unpack_from('<I', bytecode, 0)[0]


def _ctab_block(b):
    i = 4
    while i + 8 <= len(b):
        tok = struct.unpack_from('<I', b, i)[0]
        if tok & 0xFFFF != 0xFFFE:
            return None
        n = (tok >> 16) & 0x7FFF
        if b[i + 4:i + 8] == b'CTAB':
            return b[i + 8:i + 4 + n * 4]
        i += 4 + n * 4
    return None


def parse_ctab(bytecode):
    """The constant table of compiled bytecode as a list of Constant (empty when there is none)."""
    c = _ctab_block(bytecode)
    if c is None or len(c) < 28:
        return []
    _size, _creator, _ver, nconst, cinfo, _flags, _target = struct.unpack_from('<7I', c, 0)
    out = []
    for k in range(nconst):
        nm, rs, ri, rc, _res, _ti, _dv = struct.unpack_from('<IHHHHII', c, cinfo + k * 20)
        name = c[nm:c.index(b'\0', nm)].decode('latin-1')
        out.append(Constant(name, _REGSETS.get(rs, str(rs)), ri, rc))
    return out


def disassemble(bytecode):
    """The D3D disassembly text of compiled bytecode."""
    blob = c_void_p()
    hr = _dll().D3DDisassemble(bytecode, len(bytecode), 0, None, byref(blob))
    if hr != 0:
        raise ConvError('BAD_BYTECODE', 'D3DDisassemble failed (0x%08X).' % (hr & 0xFFFFFFFF))
    return _blob_bytes(blob).decode('latin-1')


def inputs_of(disasm):
    """Input declarations such as 'v0 color' or 't0 texcoord0' (usage omitted in ps_2_x text: 'v0')."""
    out = []
    for line in disasm.splitlines():
        m = re.match(r'^\s*dcl(?:_(\w+))?\s+([vt]\d+)(?:\.\w+)?\s*$', line.split('//')[0])
        if m:
            out.append(('%s %s' % (m.group(2), m.group(1))) if m.group(1) else m.group(2))
    return out


def outputs_of(disasm):
    """vs_3_0 output declarations such as 'o0 position' and 'o1 texcoord0'."""
    out = []
    for line in disasm.splitlines():
        m = re.match(r'^\s*dcl_(\w+)\s+(o\d+)(?:\.\w+)?\s*$', line.split('//')[0])
        if m:
            out.append('%s %s' % (m.group(2), m.group(1)))
    return out


def slots_of(disasm):
    m = re.search(r'approximately (\d+) instruction slots? used', disasm)
    return int(m.group(1)) if m else None


def compile_hlsl(source, profile='ps_3_0', entry='main'):
    """Compile HLSL source. Returns ok, bytecode, errors, warnings, version, slots, constants, inputs."""
    if profile not in PROFILES:
        raise ConvError('BAD_PROFILE', 'Profile %r is not supported. Valid profiles: %s.'
                        % (profile, ', '.join(PROFILES)), {'profiles': list(PROFILES)})
    if _INCLUDE_RE.search(source):
        raise ConvError('INCLUDE_REFUSED', '#include is not allowed in shader source; paste the code in.')
    data = source.encode('utf-8')
    code, errs = c_void_p(), c_void_p()
    hr = _dll().D3DCompile(data, len(data), SOURCE_NAME, None, None, entry.encode('ascii'),
                           profile.encode('ascii'), 0, 0, byref(code), byref(errs))
    err_text = _blob_bytes(errs).decode('utf-8', 'replace')
    bytecode = _blob_bytes(code) if hr >= 0 else b''
    errors, warnings = _messages(err_text)
    if hr < 0 or not bytecode:
        if not errors:
            errors = [{'line': 0, 'col': 0, 'code': 'X0000', 'text': _clean(err_text).strip()
                       or 'compile failed (0x%08X)' % (hr & 0xFFFFFFFF)}]
        return {'ok': False, 'bytecode': None, 'errors': errors, 'warnings': warnings, 'version': 0,
                'slots': None, 'constants': [], 'inputs': []}
    disasm = disassemble(bytecode)
    return {'ok': True, 'bytecode': bytecode, 'errors': errors, 'warnings': warnings,
            'version': version_of(bytecode), 'slots': slots_of(disasm), 'constants': parse_ctab(bytecode),
            'inputs': inputs_of(disasm)}
