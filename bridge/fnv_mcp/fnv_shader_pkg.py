"""Shader packages (.sdp), naming, replacement compatibility check and HLSL templates for fnv_shader.

A package is the engine's shaderpackageNNN.sdp: header u32 100, u32 count, u32 payload size, then per shader
char name[256], u32 size, bytecode. A replacement must be a drop-in for the original: the engine binds the same
constants, samplers and inputs, so the check compares the compiled bytecode of both.
"""
import re
import struct
from pathlib import Path

from . import fnv_shader_hlsl as H
from .fnv_conv import ConvError

SDP_VERSION = 100
_NAME_LEN = 256
_FNV_OFFSET = 0x811C9DC5
_FNV_PRIME = 0x01000193
_REG_LETTER = {'float4': 'c', 'int4': 'i', 'bool': 'b', 'sampler': 's'}
_REG_DECL = {'float4': 'float4', 'int4': 'int4', 'bool': 'bool', 'sampler': 'sampler2D'}


def fnv1a32(data):
    h = _FNV_OFFSET
    for b in data:
        h = ((h ^ b) * _FNV_PRIME) & 0xFFFFFFFF
    return h


def read_sdp(path):
    """The shaders of a package as a list of (name, bytecode)."""
    raw = Path(path).read_bytes()
    if len(raw) < 12:
        raise ConvError('BAD_PACKAGE', '%s is too short to be a shader package.' % Path(path).name)
    version, count, _payload = struct.unpack_from('<III', raw, 0)
    if version != SDP_VERSION:
        raise ConvError('BAD_PACKAGE', '%s has version %d, expected %d.' % (Path(path).name, version, SDP_VERSION))
    out, pos = [], 12
    for _ in range(count):
        if pos + _NAME_LEN + 4 > len(raw):
            raise ConvError('BAD_PACKAGE', '%s is truncated.' % Path(path).name)
        name = raw[pos:pos + _NAME_LEN].split(b'\0', 1)[0].decode('latin-1')
        size = struct.unpack_from('<I', raw, pos + _NAME_LEN)[0]
        pos += _NAME_LEN + 4
        if pos + size > len(raw):
            raise ConvError('BAD_PACKAGE', '%s is truncated.' % Path(path).name)
        out.append((name, raw[pos:pos + size]))
        pos += size
    return out


def write_sdp(path, entries):
    parts = []
    for name, code in entries:
        raw_name = name.encode('latin-1')
        if len(raw_name) >= _NAME_LEN:
            raise ConvError('BAD_PACKAGE', 'Shader name %r is too long (max %d).' % (name, _NAME_LEN - 1))
        parts.append(raw_name.ljust(_NAME_LEN, b'\0') + struct.pack('<I', len(code)) + bytes(code))
    payload = b''.join(parts)
    Path(path).write_bytes(struct.pack('<III', SDP_VERSION, len(entries), len(payload)) + payload)


class Package:
    """A loaded shader package: shaders by name, with lookup by code hash and sharing."""

    def __init__(self, path):
        self.path = Path(path)
        self._entries = read_sdp(self.path)
        self._code = {}
        for name, code in self._entries:
            self._code.setdefault(name, code)
        self._hash = {name: fnv1a32(code) for name, code in self._code.items()}

    def names(self):
        return [name for name, _ in self._entries]

    def code(self, name):
        try:
            return self._code[name]
        except KeyError:
            raise ConvError('NO_SHADER', 'Shader %r is not in %s.' % (name, self.path.name))

    def hash_of(self, name):
        self.code(name)
        return self._hash[name]

    def names_for_hash(self, h):
        return [name for name in self._code if self._hash[name] == h]

    def shared_with(self, name):
        h = self.hash_of(name)
        return [n for n in self._code if n != name and self._hash[n] == h]


def package_index(gpu_class, f94a4, f941e, f941f):
    """Which shaderpackageNNN.sdp the engine loads (a port of 0x00B7FAF0)."""
    if gpu_class == 1:
        return 1
    if gpu_class == 2:
        return 2
    if gpu_class == 3:
        return 5
    if gpu_class == 4:
        return 8
    if gpu_class == 5:
        if f94a4:
            return (13 if f941f else 12) if f941e else 4
        return (11 if f941f else 10) if f941e else 3
    if gpu_class == 6:
        if f94a4:
            return (17 if f941f else 16) if f941e else 7
        return (15 if f941f else 14) if f941e else 6
    if gpu_class == 7:
        return (19 if f941f else 18) if f941e else 9
    return 0


def package_path(data_dir, index):
    return Path(data_dir) / 'Shaders' / ('shaderpackage%03d.sdp' % index)


def profile_for(original):
    """The compile profile that matches a shader's version token."""
    v = H.version_of(original)
    table = {0xFFFF0201: 'ps_2_a', 0xFFFF0200: 'ps_2_0', 0xFFFF0300: 'ps_3_0'}
    if v in table:
        return table[v]
    if v >> 8 == 0xFFFE03:
        return 'vs_3_0'
    if v >> 8 == 0xFFFE02:
        return 'vs_2_0'
    raise ConvError('BAD_BYTECODE', 'Unknown shader version token 0x%08X.' % v)


def _reg(regset, index):
    return '%s%d' % (_REG_LETTER.get(regset, '?'), index)


def _used(constants):
    """Map (regset, register) -> constant name for every register the constants cover."""
    out = {}
    for c in constants:
        for i in range(c.index, c.index + max(c.count, 1)):
            out.setdefault((c.regset, i), c.name)
    return out


def _is_vs(version):
    return version >> 16 == 0xFFFE


def _major(version):
    return (version >> 8) & 0xFF


def _usage_key(entry):
    """'v1 texcoord' -> 'texcoord0': the usage of an input entry with a missing index read as 0."""
    usage = entry.split()[-1]
    return usage if usage[-1].isdigit() else usage + '0'


def check_replacement(original, replacement):
    """Problems that stop a replacement from being a drop-in for the original: a list of {code, text}.

    Soft problems carry 'warning': True. Codes: KIND, MODEL, REGISTER, SAMPLER, INPUT, OUTPUT, NAME.
    """
    vo, vr = H.version_of(original), H.version_of(replacement)
    if _is_vs(vo) != _is_vs(vr):
        return [{'code': 'KIND', 'text': 'The original is a %s shader but the replacement is a %s shader.'
                 % ('vertex' if _is_vs(vo) else 'pixel', 'vertex' if _is_vs(vr) else 'pixel')}]
    problems = []
    if _major(vo) != _major(vr):
        problems.append({'code': 'MODEL', 'text': 'The original is shader model %d.x (%s) but the replacement is '
                         '%d.x; compile it with profile %s.' % (_major(vo), profile_for(original), _major(vr),
                                                                profile_for(original))})
    orig, repl = _used(H.parse_ctab(original)), _used(H.parse_ctab(replacement))
    for (regset, i), name in sorted(repl.items()):
        if (regset, i) in orig:
            if orig[(regset, i)] != name:
                problems.append({'code': 'NAME', 'warning': True,
                                 'text': '%s is %r in the original but %r in the replacement.'
                                         % (_reg(regset, i), orig[(regset, i)], name)})
            continue
        if regset == 'sampler':
            problems.append({'code': 'SAMPLER', 'text': 'The replacement uses sampler %s (%s), which the original '
                             'does not have.' % (_reg(regset, i), name)})
        else:
            problems.append({'code': 'REGISTER', 'text': 'The replacement uses register %s (%s), which the '
                             'original does not have.' % (_reg(regset, i), name)})
    d_orig, d_repl = H.disassemble(original), H.disassemble(replacement)
    if _is_vs(vo):
        o_out, r_out = H.outputs_of(d_orig), H.outputs_of(d_repl)
        if sorted(o_out) != sorted(r_out):
            problems.append({'code': 'OUTPUT', 'text': 'Vertex shader outputs differ: original [%s], replacement '
                             '[%s].' % (', '.join(o_out), ', '.join(r_out))})
    soft = not _is_vs(vo) and _major(vo) == 2
    o_in, r_in = H.inputs_of(d_orig), H.inputs_of(d_repl)
    if soft or _major(vr) == 2:
        # ps_2_x text carries no usage: compare by register only.
        o_in = {s.split()[0] for s in o_in}
        r_in = [s.split()[0] for s in r_in]
    else:
        # Version 3 inputs link to the previous stage by semantic; the register number only reflects the
        # shader's own input set, so compare the usage ('color' means color0).
        o_in = {_usage_key(s) for s in o_in}
        r_in = [_usage_key(s) for s in r_in]
    for s in r_in:
        if s not in o_in:
            problem = {'code': 'INPUT', 'text': 'The replacement reads input %s, which the original does not '
                       'declare.' % s}
            if soft:
                problem['warning'] = True
            problems.append(problem)
    return problems


def _semantic(usage):
    m = re.match(r'^([a-z_]+?)(\d*)$', usage)
    base, idx = m.group(1), m.group(2)
    if base in ('color', 'texcoord'):
        return '%s%s' % (base.upper(), idx or '0')
    return '%s%s' % (base.upper(), idx)


def _input_params(disasm):
    """[(register, semantic)] for the declared inputs; ps_2_x entries have no usage (v = colour, t = texcoord)."""
    out = []
    for entry in H.inputs_of(disasm):
        parts = entry.split()
        reg = parts[0]
        if len(parts) > 1:
            out.append((reg, _semantic(parts[1])))
        else:
            out.append((reg, '%s%s' % ('COLOR' if reg[0] == 'v' else 'TEXCOORD', reg[1:])))
    return out


def replacement_template(name, original):
    """HLSL skeleton for replacing `name`: same constants, samplers and inputs, a trivial body. No vanilla code."""
    profile = profile_for(original)
    version = H.version_of(original)
    disasm = H.disassemble(original)
    lines = ['// Replacement for %s (profile %s).' % (name, profile),
             '// Constants, samplers and inputs below match the original; keep their registers.', '']
    first_sampler = None
    for c in H.parse_ctab(original):
        reg = _reg(c.regset, c.index)
        if c.regset == 'sampler':
            first_sampler = first_sampler or c.name
            lines.append('sampler2D %s : register(%s);' % (c.name, reg))
        elif c.count > 1:
            lines.append('%s %s[%d] : register(%s);' % (_REG_DECL[c.regset], c.name, c.count, reg))
        else:
            lines.append('%s %s : register(%s);' % (_REG_DECL[c.regset], c.name, reg))
    lines.append('')
    params = _input_params(disasm)
    if _is_vs(version):
        outs = []
        for entry in sorted(H.outputs_of(disasm), key=lambda s: int(s.split()[0][1:])):
            reg, usage = entry.split()
            outs.append('    float4 %s : %s;' % (reg, _semantic(usage)))
        lines += ['struct VSOut {'] + outs + ['};', '']
        args = ', '.join('float4 %s : %s' % p for p in params)
        lines += ['VSOut main(%s)' % args, '{', '    VSOut o = (VSOut)0;', '    return o;', '}']
    else:
        texcoord = next((p for p in params if p[1].startswith('TEXCOORD')), None)
        colour = next((p for p in params if p[1].startswith('COLOR')), None)
        args = ', '.join('float4 %s : %s' % p for p in params)
        if first_sampler and texcoord:
            body = 'return tex2D(%s, %s.xy);' % (first_sampler, texcoord[0])
        elif colour:
            body = 'return %s;' % colour[0]
        else:
            body = 'return float4(1,1,1,1);'
        lines += ['float4 main(%s) : COLOR0' % args, '{', '    ' + body, '}']
    return '\n'.join(lines) + '\n'


def effect_template():
    """HLSL skeleton for a screen effect: the s0/s1/c0/c1 contract, one example param at c2, passthrough body."""
    return '''// Screen effect (profile ps_3_0). The engine binds:
//   s0 = the scene, s1 = the HDR scene
//   c0 = (time in seconds, frame delta in seconds, width, height)
//   c1 = (1/width, 1/height, frame mod 65536, 0)
// Your own parameters start at c2 (set them with effect_set params).
sampler2D scene : register(s0);
sampler2D hdrScene : register(s1);
float4 timeInfo : register(c0);
float4 pixelInfo : register(c1);
float4 tint : register(c2) = float4(1, 1, 1, 1);   // example param: scales the scene colour, (1,1,1,1) = unchanged

float4 main(float2 uv : TEXCOORD0) : COLOR0
{
    return tex2D(scene, uv) * tint;
}
'''


def patch_sdp(src_bytes, replacements):
    """A package's raw bytes with the code of the named entries replaced ({name: bytecode}). Every other byte is
    copied as it is (the vanilla name fields carry non-zero padding after the NUL, which write_sdp would zero); a
    replaced entry keeps its raw name field and gets the new size and code. The header's payload size is refreshed."""
    version, count, _payload = struct.unpack_from('<III', src_bytes, 0)
    if version != SDP_VERSION:
        raise ConvError('BAD_PACKAGE', 'package has version %d, expected %d.' % (version, SDP_VERSION))
    out, pos = [src_bytes[:12]], 12
    for _ in range(count):
        field = src_bytes[pos:pos + _NAME_LEN]
        size = struct.unpack_from('<I', src_bytes, pos + _NAME_LEN)[0]
        body = pos + _NAME_LEN + 4
        name = field.split(b'\0', 1)[0].decode('latin-1')
        if name in replacements:
            code = bytes(replacements[name])
            out.append(field + struct.pack('<I', len(code)) + code)
        else:
            out.append(src_bytes[pos:body + size])
        pos = body + size
    out.append(src_bytes[pos:])
    raw = b''.join(out)
    return raw[:8] + struct.pack('<I', len(raw) - 12) + raw[12:]
