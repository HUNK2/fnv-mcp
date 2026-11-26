"""fnv_code (Plan 2, W5): swap an engine function for one compiled from edited decomp source, in the running game.

The decomp rebuilds each function to the game's exact bytes, and build/units.json binds every name a unit uses to an
original address. So a changed function is compiled by the game's own compiler (the unit's recipe), its code section
is taken out of the object file, every relocation is bound to the original address of what it names, the blob is
placed in executable memory, and the original entry jumps to it.

    checkout function   copy the function's unit into state/fnv/code/<name>/ (the decomp checkout is never edited);
                        the copy's path and the function's line range
    edit find replace   change the copy (exact text, inside the function's lines)
    build               compile the copy with the unit's compiler and flags, link the function: size, relocations,
                        names it could not bind (then it cannot be swapped)
    swap / unswap       place the linked code and redirect the original entry to it / put the entry back
    compare args        call the original and the new code side by side (before a swap) with the same arguments;
                        mode shadow: record the game's own calls of the original for some seconds (arguments and
                        result) and replay each on the new code: mismatches with their arguments (pure functions)
    list                active swaps with their source diffs
    export              a local patch set (target, expected original bytes, code, relocations) and a loader module in
                        C that applies it at start (load it hot with fnv_native, or build it as a plugin with
                        fnv_plugin_build)

New code can call the original: declare `extern "C" <type> <convention> fnv_orig_<8 hex digits>(...)` and call it;
the linker binds it to a trampoline (the original's first instructions, then a jump back), so the call works while
the entry is redirected. Functions shorter than five bytes are swapped at their call sites (E8 calls found through
the engine map's call table) and their vtable slots (pointers in the exe's read-only data) instead of their entry.

Limits told in results: functions with exception handling (.text$x / .xdata$x) and functions that need new writable
statics are refused; a function folded with others changes for all of them; callers that inlined it keep the old
body (most game logic is /Od); class layouts cannot change. Compiled code and objects stay in the bridge state
folder: local use only, never in git.
"""
import difflib
import hashlib
import importlib.util
import json
import re
import time
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from .fnv_conv import ConvError, verdict
from .fnv_link import word
from .fnv_native import c_name
from .fnv_state import GameIdentity, read_json, update_json

REL32, DIR32, DIR32NB = 0x14, 0x06, 0x07
WRITABLE = 0x80000000
ORIG_RE = re.compile(r'^(?:_|\?)?fnv_orig_([0-9A-Fa-f]{8})(?:@.*)?$')   # C name, or C++ mangled (block scope)
EXE_RDATA = (0x01000000, 0x01200000)       # read-only data of FalloutNV.exe (vtables); scanned for short swaps
SHADOW_ARGS = 8


class CodeTools:
    def __init__(self, live):
        self.live = live
        self._swaps = {}           # address -> {function, blob, old, size[, sites]}; see swaps
        self._swaps_game = False   # the game process the swap list belongs to (False: not read yet)
        self.placed = {}           # address -> blob address (placed for compare, not redirected)
        self._units = None
        self._names = None
        self._coff = None

    # ------------------------------------------------------------------ swap list
    @property
    def swaps(self):
        """Active swaps of the running game. Kept in the bridge state (swaps.json) with the game's pid and start
        time, so a restarted MCP server can still list and undo the swaps it made; a new game process starts empty
        (its code is unpatched)."""
        game = self._game_id()
        if game != self._swaps_game:
            self._swaps_game = game
            self._swaps = {}
            data = read_json(self.work / 'swaps.json', {})
            stored = GameIdentity.parse(data.get('game'))
            if game and stored and stored.same_game(GameIdentity.parse(game)):
                try:
                    self._swaps = {int(k, 16): v for k, v in (data.get('swaps') or {}).items()}
                except (TypeError, ValueError):
                    self._swaps = {}
        return self._swaps

    def _save_swaps(self, address=None):
        """Store this server's record for one address (or its removal; None: every address it holds) in swaps.json,
        under the file's lock and on top of what another server process stored for the same game (S2.4); the merged
        list becomes ours."""
        mine = GameIdentity.parse(self._swaps_game)
        if mine is None:   # no game known: writing would erase another server's list for the live game (review M8)
            return

        def merge(stored):
            theirs = GameIdentity.parse(stored.get('game'))
            swaps = dict(stored.get('swaps') or {}) if theirs and mine and theirs.same_game(mine) else {}
            for a in ([address] if address is not None else list(self._swaps)):
                if a in self._swaps:
                    swaps['0x%08X' % a] = self._swaps[a]
                else:
                    swaps.pop('0x%08X' % a, None)
            return {'game': self._swaps_game, 'swaps': swaps}
        merged = update_json(self.work / 'swaps.json', merge, {}, indent=1)
        try:
            self._swaps = {int(k, 16): v for k, v in merged['swaps'].items()}
        except (TypeError, ValueError):
            pass

    def _game_id(self):
        try:
            proc = self.live.game.process()
        except Exception:  # noqa: BLE001 - no game (or several): nothing is swapped in it
            return None
        return GameIdentity(*proc).key() if proc else None

    # ------------------------------------------------------------------ decomp access
    @property
    def decomp(self):
        root = getattr(self.live.project.fnv, 'decomp_root', None)
        if not root or not (Path(root) / 'build' / 'units.json').is_file():
            raise ConvError('NO_DECOMP', 'fnv.decomp_root does not point at a decomp checkout with build/units.json.')
        return Path(root)

    @property
    def work(self):
        return Path(self.live.project.state_dir) / 'fnv' / 'code'

    def units(self):
        if self._units is None:
            data = json.loads((self.decomp / 'build' / 'units.json').read_text(encoding='utf-8'))
            self._units = data['units'] if isinstance(data, dict) else data
        return self._units

    def names(self):
        """Every name any unit binds, with the addresses it is bound to."""
        if self._names is None:
            names = {}
            for u in self.units():
                for k, v in (u.get('symbols') or {}).items():
                    names.setdefault(k, set()).add(int(v, 16) if isinstance(v, str) else int(v))
                for f in u['functions']:
                    a = int(f['address'], 16)
                    for n in [f['symbol']] + f.get('fold_aliases', []):
                        names.setdefault(n, set()).add(a)
            self._names = names
        return self._names

    def coff_class(self):
        if self._coff is None:
            tools = str(self.decomp / 'tools')
            if tools not in sys.path:
                sys.path.append(tools)          # compare.py imports its sibling function_spans
            spec = importlib.util.spec_from_file_location('fnv_decomp_compare', self.decomp / 'tools' / 'compare.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self._coff = module.Coff
        return self._coff

    def _target(self, function):
        engine = self.live.engine()
        try:
            address = engine.resolve(function)
        except Exception as e:  # noqa: BLE001
            raise ConvError('NOT_FOUND', 'No function %r in the engine map (%s).' % (function, e))
        for u in self.units():
            for f in u['functions']:
                if int(f['address'], 16) == address:
                    return address, u, f
        raise ConvError('NO_UNIT', 'No decomp unit builds 0x%08X.' % address)

    def _dir(self, address):
        return self.work / ('%08X' % address)

    def _meta(self, address):
        path = self._dir(address) / 'meta.json'
        if not path.is_file():
            raise ConvError('NOT_CHECKED_OUT', 'Check the function out first (fnv_code checkout).')
        return json.loads(path.read_text(encoding='utf-8'))

    # ------------------------------------------------------------------ checkout / edit
    def checkout(self, function):
        address, unit, spec = self._target(function)
        folder = self._dir(address)
        folder.mkdir(parents=True, exist_ok=True)
        original = self.decomp / unit['source']
        copy = folder / Path(unit['source']).name
        if not copy.exists():
            shutil.copyfile(original, copy)
        start, end = self._range(copy.read_text(encoding='utf-8', errors='replace'), address)
        meta = {'function': function, 'address': '0x%08X' % address, 'unit': unit['source'], 'symbol': spec['symbol'],
                'size': spec['size'], 'compiler': unit['compiler'], 'flags': unit['flags'], 'copy': str(copy),
                'original': str(original), 'lines': [start, end]}
        (folder / 'meta.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
        text = copy.read_text(encoding='utf-8', errors='replace').splitlines()
        return verdict('Checked out %s (0x%08X) from %s' % (function, address, unit['source']),
                       'Edit the copy (fnv_code edit, or the file itself), then build. The decomp checkout is not '
                       'touched.', copy=str(copy), lines=[start, end],
                       source='\n'.join(text[start - 1:end]) if start else None, compiler=unit['compiler'],
                       flags=unit['flags'], unitFunctions=len(unit['functions']),
                       foldAliases=spec.get('fold_aliases') or None)

    @staticmethod
    def _range(text, address):
        lines = text.splitlines()
        tag = '@fnv 0x%08x' % address
        start = next((i for i, l in enumerate(lines) if tag in l.lower()), None)
        if start is None:
            return None, None
        end = next((i for i in range(start + 1, len(lines)) if '@fnv 0x' in lines[i].lower()), len(lines))
        while end > start + 1 and not lines[end - 1].strip().startswith('}'):
            end -= 1
        return start + 1, end

    def edit(self, function, find, replace):
        address, _, _ = self._target(function)
        meta = self._meta(address)
        copy = Path(meta['copy'])
        text = copy.read_text(encoding='utf-8', errors='replace')
        start, end = self._range(text, address)
        if start is None:
            raise ConvError('NOT_IN_COPY', 'The copy has no "@fnv 0x%08x" tag for %s, so its lines are unknown.'
                            % (address, function))
        lines = text.splitlines(keepends=True)
        body = ''.join(lines[start - 1:end])
        if not find or body.count(find) != 1:
            raise ConvError('EDIT_NOT_FOUND', 'find must occur exactly once inside the function (found %d times).'
                            % (body.count(find) if find else 0))
        body = body.replace(find, replace, 1)
        copy.write_text(''.join(lines[:start - 1]) + body + ''.join(lines[end:]), encoding='utf-8')
        return verdict('Edited the copy of %s' % function, 'Next: fnv_code build.', diff=self._diff(meta))

    def _diff(self, meta):
        a = Path(meta['original']).read_text(encoding='utf-8', errors='replace').splitlines()
        b = Path(meta['copy']).read_text(encoding='utf-8', errors='replace').splitlines()
        return '\n'.join(difflib.unified_diff(a, b, 'decomp/' + meta['unit'], 'copy', lineterm='', n=2))[:6000]

    # ------------------------------------------------------------------ build / link
    def build(self, function):
        address, unit, spec = self._target(function)
        meta = self._meta(address)
        folder = self._dir(address)
        obj = folder / 'unit.obj'
        if obj.exists():
            obj.unlink()
        from .locate import compiler_problem
        problem = compiler_problem(unit['compiler'])
        if problem:
            raise ConvError('COMPILER_MISSING', 'fnv_code build needs the compiler kit: %s.' % problem)
        cmd = self._compile_cmd(self.decomp, unit, meta, obj)
        result = subprocess.run(cmd, cwd=str(self.decomp), capture_output=True, text=True, errors='replace',
                                timeout=300)
        if result.returncode or not obj.exists():
            raise ConvError('COMPILE_FAILED', 'The unit did not compile:\n%s' % (result.stdout + result.stderr)[-5000:],
                            {'output': (result.stdout + result.stderr)[-5000:]})
        link = self._link(obj, unit, spec)
        (folder / 'link.json').write_text(json.dumps(link, indent=1), encoding='utf-8')
        # A blob placed for compare came from the previous build: compare and swap must place this one.
        self.placed.pop(address, None)
        ok = not link['unresolved'] and not link['refused']
        return verdict('Built %s: %d bytes (original %d), %d relocations%s' % (
            function, link['size'], spec['size'], len(link['relocs']),
            '' if ok else ', cannot be swapped'),
            'Next: fnv_code compare (side by side) or swap.' if ok else 'Fix what is listed, then build again.',
            size=link['size'], originalSize=spec['size'], relocations=len(link['relocs']),
            copiedSections=link['copied'], unresolved=link['unresolved'] or None, refused=link['refused'] or None,
            diff=self._diff(meta))

    @staticmethod
    def _compile_cmd(decomp, unit, meta, obj):
        """The compiler call for the copy. The copy lives in the bridge state, not next to its unit, so the unit's own
        folder comes first: a quoted #include "Actor.h" must find src/.../Actor.h before include/Actor.h, a different
        header of the same name (bug found 2026-11-18 on playercharacter.cpp)."""
        return [sys.executable, str(decomp / 'tools' / 'msvc.py'), '--compiler', unit['compiler'], '/c', '/nologo'] \
            + list(unit['flags']) + ['/I' + str(Path(meta['original']).parent), '/I' + str(decomp / 'include'),
                                     meta['copy'], '/Fo' + str(obj)]

    def _link(self, obj, unit, spec):
        coff = self.coff_class()(obj)
        mappings = {k: (int(v, 16) if isinstance(v, str) else int(v)) for k, v in (unit.get('symbols') or {}).items()}
        for f in unit['functions']:
            for n in [f['symbol']] + f.get('fold_aliases', []):
                mappings[n] = int(f['address'], 16)
        sym = coff.named(spec['symbol'])
        sec_index = sym['section']
        section = coff.sections[sec_index]
        start = sym['value']
        others = [s['value'] for s in coff.symbols.values() if s['section'] == sec_index and s['type'] & 0x20
                  and s['value'] > start and not s['name'].startswith('__catch$')]
        end = min(others) if others else len(section['data'])
        code = bytearray(section['data'][start:end])
        relocs, unresolved, refused, copied = [], [], [], []
        extra = {}            # section index -> offset of its copy in the blob
        names = self.names()

        def place_section(index):
            if index in extra:
                return extra[index]
            sec = coff.sections[index]
            if sec['flags'] & WRITABLE:
                refused.append('needs a new writable static in %s (a fresh copy would split the game\'s state)'
                               % sec['name'])
                return None
            if sec['name'].startswith(('.xdata', '.text$x')):
                refused.append('exception handling (%s) is not supported yet' % sec['name'])
                return None
            if sec['relocs']:
                refused.append('copied section %s has relocations of its own (not supported yet)' % sec['name'])
                return None
            while len(code) % 16:
                code.append(0xCC)
            extra[index] = len(code)
            code.extend(sec['data'])
            copied.append({'section': sec['name'], 'bytes': len(sec['data'])})
            return extra[index]

        for offset, index, kind in section['relocs']:
            if not start <= offset < end:
                continue
            at = offset - start
            target = coff.symbols[index]
            name = target['name']
            raw = struct.unpack_from('<i' if kind == REL32 else '<I', code, at)[0]
            entry = {'offset': at, 'kind': kind, 'name': name}
            orig = ORIG_RE.match(name)
            if orig:
                entry['orig'] = int(orig.group(1), 16)
                entry['addend'] = raw
            elif name in mappings:
                entry['absolute'] = (mappings[name] + raw) & 0xFFFFFFFF
            elif target['section'] == sec_index and start <= target['value'] < end:
                entry['blob'] = target['value'] - start + raw
            elif target['section'] > 0:
                if name.startswith('__ehhandler$') or name.startswith('__unwindfunclet$'):
                    refused.append('exception handling (%s) is not supported yet' % name)
                    continue
                base = place_section(target['section'])
                if base is None:
                    continue
                entry['blob'] = base + target['value'] + raw
            elif target['section'] == -1:
                entry['absolute'] = (target['value'] + raw) & 0xFFFFFFFF
            elif name in names and len(names[name]) == 1:
                entry['absolute'] = (next(iter(names[name])) + raw) & 0xFFFFFFFF
            else:
                unresolved.append(name + (' (bound to %d addresses in the recipe)' % len(names[name])
                                          if name in names else ''))
                continue
            if kind not in (REL32, DIR32):
                refused.append('relocation kind %d at +0x%x is not supported' % (kind, at))
                continue
            relocs.append(entry)
        return {'code': code.hex(), 'size': len(code), 'relocs': relocs, 'unresolved': unresolved,
                'refused': refused, 'copied': copied, 'symbol': spec['symbol']}

    def _linked(self, address):
        path = self._dir(address) / 'link.json'
        if not path.is_file():
            raise ConvError('NOT_BUILT', 'Build it first (fnv_code build).')
        link = json.loads(path.read_text(encoding='utf-8'))
        if link['unresolved'] or link['refused']:
            raise ConvError('NOT_SWAPPABLE', 'The build cannot be placed: %s'
                            % '; '.join(link['refused'] + ['unresolved ' + u for u in link['unresolved']]))
        return link

    @staticmethod
    def _final(link, base, trampolines=None):
        code = bytearray.fromhex(link['code'])
        for r in link['relocs']:
            if 'orig' in r:
                target = (trampolines or {})[r['orig']] + r.get('addend', 0)
            else:
                target = r['absolute'] if 'absolute' in r else base + r['blob']
            if r['kind'] == REL32:
                struct.pack_into('<i', code, r['offset'], (target - (base + r['offset'] + 4) + 2 ** 31) % 2 ** 32 - 2 ** 31)
            else:
                struct.pack_into('<I', code, r['offset'], target & 0xFFFFFFFF)
        return bytes(code)

    # ------------------------------------------------------------------ placing
    def _trampoline(self, target):
        """Executable copy of a function's first whole instructions plus a jump back: calls the original even while
        its entry is redirected (fnv_orig_<address>)."""
        from .fnv_deep import decode_prologue
        cached = getattr(self, '_trampolines', {}).get(target)
        if cached:
            return cached
        raw = self.live.reads([(target, 24)])[0]
        if address_swapped := self.swaps.get(target):
            raw = bytes.fromhex(address_swapped['old']) + raw[5:]
        stolen, relocs = decode_prologue(raw)
        out = self.live.link.one('code', 'alloc', word(stolen + 16))
        if not out.get('ok'):
            raise ConvError('ALLOC_FAILED', 'No code memory for the trampoline (%s).' % out.get('error'))
        tramp = int(out['address'], 16)
        body = bytearray(raw[:stolen])
        for at in relocs:                          # rel32 operands: same target from the new place
            rel = struct.unpack_from('<i', body, at)[0]
            dest = target + at + 4 + rel
            struct.pack_into('<i', body, at, dest - (tramp + at + 4))
        body += b'\xE9' + struct.pack('<i', (target + stolen) - (tramp + stolen + 5))
        self.live.link.one('code', 'patch', word(tramp), bytes(body).hex())
        self.__dict__.setdefault('_trampolines', {})[target] = tramp
        return tramp

    def _place(self, address, link):
        out = self.live.link.one('code', 'alloc', word(link['size'] + 16))
        if not out.get('ok'):
            raise ConvError('ALLOC_FAILED', 'The companion could not allocate code memory (%s).' % out.get('error'))
        base = int(out['address'], 16)
        trampolines = {r['orig']: self._trampoline(r['orig']) for r in link['relocs'] if 'orig' in r}
        code = self._final(link, base, trampolines)
        wrote = self.live.link.one('code', 'patch', word(base), code.hex())
        if not wrote.get('ok'):
            raise ConvError('WRITE_FAILED', 'Could not write the code (%s).' % wrote.get('error'))
        return base

    def swap(self, function):
        self.live.ready()
        address, unit, spec = self._target(function)
        if address in self.swaps:
            raise ConvError('ALREADY_SWAPPED', '%s is swapped already. Next: unswap, then swap the new build.' % function)
        link = self._linked(address)
        self.live.changing('code swap')
        base = self.placed.pop(address, None) or self._place(address, link)
        meta = self._meta(address)
        if int(spec['size']) < 5:
            sites = self._redirect_sites(address, base)
            if not sites:
                raise ConvError('NO_SITES', 'The function is shorter than a jump (5 bytes) and no call site or vtable '
                                'slot of it was found to redirect.')
            self.swaps[address] = {'function': function, 'blob': base, 'old': None, 'size': link['size'],
                                   'sites': sites}
            self._save_swaps(address)
            self.live.journal.add({'kind': 'code_swap', 'function': function, 'address': '0x%08X' % address,
                                   'undo': None})
            return verdict('Swapped %s at %d call sites and vtable slots (the function is %d bytes, too short for a '
                           'jump)' % (function, len(sites), int(spec['size'])),
                           'Calls through those sites run the new code; a call through a pointer made at run time '
                           'still reaches the original.', blob='0x%08X' % base, sites=[
                               {'at': '0x%08X' % x['at'], 'kind': x['kind']} for x in sites][:50],
                           notes=self._notes(spec), diff=self._diff(meta))
        jump = b'\xE9' + struct.pack('<i', base - (address + 5))
        out = self.live.link.one('code', 'patch', word(address), jump.hex())
        if not out.get('ok'):
            raise ConvError('WRITE_FAILED', 'Could not redirect the entry (%s).' % out.get('error'))
        self.swaps[address] = {'function': function, 'blob': base, 'old': out['old'], 'size': link['size']}
        self._save_swaps(address)
        self.live.journal.add({'kind': 'code_swap', 'function': function, 'address': '0x%08X' % address,
                               'undo': None})
        return verdict('Swapped %s: 0x%08X now runs the new code at 0x%08X' % (function, address, base),
                       'Every call goes to the new code. unswap puts the entry back.', blob='0x%08X' % base,
                       size=link['size'], notes=self._notes(spec), diff=self._diff(meta))

    def _redirect_sites(self, address, base):
        """Point every direct call (E8 rel32 in the callers the engine map lists) and every vtable slot (an aligned
        pointer in the exe's read-only data) of address at base. Returns the patched sites with their old bytes."""
        engine = self.live.engine()
        callers = [a for a, targets in engine.calls.items() if address in targets]
        sizes = {}
        for a in callers:
            try:
                sizes[a] = int((engine.summary(a, with_location=False) or {}).get('size') or 0)
            except Exception:  # noqa: BLE001
                sizes[a] = 0
        sites = []
        try:
            self._patch_sites(address, base, callers, sizes, sites)
        except Exception:
            # Put back what was patched before the failure, so nothing stays redirected without a record.
            for site in reversed(sites):
                try:
                    self.live.link.one('code', 'patch', word(site['at']), site['old'])
                except Exception:  # noqa: BLE001 - best effort; the original error is what the caller needs
                    pass
            raise
        return sites

    def _patch_sites(self, address, base, callers, sizes, sites):
        for a in callers:
            size = sizes.get(a) or 0x400
            code = self.live.reads([(a, size)])[0] or b''
            for i in range(len(code) - 4):
                if code[i] == 0xE8 and a + i + 5 + struct.unpack_from('<i', code, i + 1)[0] == address:
                    rel = struct.pack('<i', base - (a + i + 5))
                    out = self.live.link.one('code', 'patch', word(a + i + 1), rel.hex())
                    if out.get('ok'):
                        sites.append({'at': a + i + 1, 'kind': 'call', 'old': out['old']})
        lo, hi = EXE_RDATA
        needle = struct.pack('<I', address)
        for start in range(lo, hi, 0x10000):
            data = self.live.reads([(start, 0x10000)])[0]
            if not data:
                continue
            at = data.find(needle)
            while at >= 0:
                if at % 4 == 0:
                    out = self.live.link.one('code', 'patch', word(start + at), struct.pack('<I', base).hex())
                    if out.get('ok'):
                        sites.append({'at': start + at, 'kind': 'vtable', 'old': out['old']})
                at = data.find(needle, at + 1)

    @staticmethod
    def _notes(spec):
        notes = ['Callers that inlined the old body keep it (most game logic is /Od, where nothing is inlined).',
                 'Class layouts cannot change: the rest of the game uses the old ones.']
        if spec.get('fold_aliases'):
            notes.insert(0, 'Folded with %s: they change too.' % ', '.join(spec['fold_aliases']))
        return notes

    def unswap(self, function=None):
        self.live.ready()
        targets = [a for a in self.swaps if function is None or a == self._target(function)[0]]
        if not targets:
            raise ConvError('NOT_SWAPPED', 'Nothing is swapped%s.' % ('' if function is None else ' for ' + function))
        done, failed = [], []
        for address in targets:
            s = self.swaps[address]
            left = []
            for site in s.get('sites') or []:
                try:
                    out = self.live.link.one('code', 'patch', word(site['at']), site['old'])
                except Exception as e:  # noqa: BLE001 - keep the site in the record, report it
                    out = {'ok': False, 'error': str(e)}
                if not out.get('ok'):
                    left.append(dict(site, error=out.get('error')))
            entry_error = None
            if s.get('old'):
                try:
                    out = self.live.link.one('code', 'patch', word(address), s['old'])
                except Exception as e:  # noqa: BLE001
                    out = {'ok': False, 'error': str(e)}
                if not out.get('ok'):
                    entry_error = out.get('error') or 'write failed'
            if left or entry_error:
                # Only what is back is forgotten: the record keeps every site (and the entry) still redirected, so
                # unswap can be called again and list still shows it.
                s['sites'] = left or None
                if not entry_error:
                    s['old'] = None
                failed.append({'function': s['function'], 'entry': entry_error, 'sites': len(left)})
            else:
                self.swaps.pop(address)
                done.append(s['function'])
            self._save_swaps(address)
        if failed:
            raise ConvError('WRITE_FAILED', 'Could not restore everything: %s. What was restored is forgotten; the '
                            'rest stays in the swap list. Next: call unswap again (a thread may have been inside '
                            'the bytes: "busy").' % failed, {'restored': done, 'failed': failed})
        return verdict('Restored %s' % ', '.join(done), 'The original entries run again (the new code stays '
                       'allocated, in case a thread is still inside it).', restored=done)

    def compare(self, function, args=None, this=None, returns='float', mode='call', seconds=5.0, nargs=None):
        """Call the original function and the new code with the same arguments (before a swap); mode shadow replays
        the game's own calls."""
        self.live.ready()
        address, unit, spec = self._target(function)
        link = self._linked(address)
        if mode == 'shadow':
            return self._shadow(function, address, link, returns, seconds, nargs)
        if address in self.swaps:
            raise ConvError('SWAPPED', 'Compare runs before a swap (both entries now lead to the new code). '
                            'Next: unswap, compare, swap.')
        base = self.placed.get(address)
        if base is None:
            base = self._place(address, link)
            self.placed[address] = base
        old = self.live.engine_call('0x%08X' % address, this, args, returns, None, True)
        new = self.live.engine_call('0x%08X' % base, this, args, returns, self._convention(old), True)
        same = old.get('result') == new.get('result')
        return verdict('Original %s, new %s%s' % (old.get('result'), new.get('result'), '' if same else ' (differ)'),
                       'Both ran guarded on the main thread with the same arguments; the new code is placed, not '
                       'swapped.', original=old.get('result'), new=new.get('result'), same=same,
                       blob='0x%08X' % base)

    def _shadow(self, function, address, link, returns, seconds, nargs=None, limit=200):
        """Record the game's calls of the original (arguments, this, result) for some seconds, then run the new code
        with each set of arguments and compare. Meant for pure functions (no side effects, no hidden state)."""
        from .fnv_flow import hook_at
        from .fnv_deep import HF_RECORD
        if address in self.swaps:
            raise ConvError('SWAPPED', 'shadow runs before a swap. Next: unswap, compare mode=shadow, swap.')
        n = int(nargs if nargs is not None else SHADOW_ARGS)
        hook = hook_at(self.live, address, 'code.shadow', n, flags=HF_RECORD | 1)     # 1 = also on exit
        try:
            time.sleep(float(seconds))
            h = next((x for x in self.live.deep.hooks() if x.get('label') == 'code.shadow'), None)
            _, recs, _ = self.live.deep.records(h, 0, limit=4096) if h else (0, [], 0)
        finally:
            self.live.link.one('unhook', word(hook['id']))
        base = self.placed.get(address)
        if base is None:
            base = self._place(address, link)
            self.placed[address] = base
        calls = [r for r in recs if r[1] == 2][:limit]          # exit records carry the result
        # The record hook always keeps ecx; it is `this` only for a thiscall function (a cdecl one would get it as
        # an extra first argument). Same rule as engine_call: the decomp signature.
        try:
            _, record, signature, _ = self.live._signature('0x%08X' % address)
        except Exception:  # noqa: BLE001
            record, signature = {}, ''
        conv = next((c for c in ('thiscall', 'fastcall', 'stdcall', 'cdecl') if '__' + c in signature), None)             or ('thiscall' if '::' in (record.get('name') or '') else 'cdecl')
        uses_this = conv in ('thiscall', 'fastcall')
        rows, same, seen = [], 0, set()
        for r in calls:
            if conv == 'fastcall':
                # ecx and edx are the first two arguments; the stack words are the rest (engine_call maps a
                # fastcall without `this` as regs = args, so this order lands them in ecx, edx, stack).
                args = [r[3], r[4]] + list(r[12:12 + max(0, n - 2)])
                this = None
            else:
                args = list(r[12:12 + n])
                this = ('0x%08X' % r[3] if r[3] else None) if uses_this else None
            key = (r[3] if uses_this else None, tuple(args))
            if key in seen:
                continue
            seen.add(key)
            typed = [{'type': 'int', 'value': a} for a in args]
            new = self.live.engine_call('0x%08X' % base, this, typed, returns, conv, True)
            old_value = r[8] if returns != 'float' else None
            new_value = new.get('result')
            if returns == 'float':
                old = self.live.engine_call('0x%08X' % address, this, typed, returns, conv, True)
                old_value = old.get('result')
            match = old_value == new_value if returns == 'float' else (int(new_value or 0) & 0xFFFFFFFF) == old_value
            same += bool(match)
            if not match:
                rows.append({'args': ['0x%08X' % a for a in args], 'this': this,
                             'original': old_value, 'new': new_value})
        return verdict('%d distinct live calls replayed on the new code: %d agree, %d differ' % (
            len(seen), same, len(seen) - same),
            'A record hook on the original caught the game\'s own calls for %s s (arguments, this, and the result '
            'at exit); each was run again on the new code (and, for float results, on the original) with the same '
            'arguments. Only meaningful for pure functions.' % seconds, function=function, calls=len(calls),
            distinct=len(seen), agree=same, convention=conv, mismatches=rows[:50], blob='0x%08X' % base)

    @staticmethod
    def _convention(call_result):
        return call_result.get('convention')

    def list(self):
        rows = []
        for address, s in self.swaps.items():
            try:
                diff = self._diff(self._meta(address))
            except ConvError:
                diff = None
            rows.append({'function': s['function'], 'address': '0x%08X' % address, 'blob': '0x%08X' % s['blob'],
                         'size': s['size'], 'diff': diff})
        return verdict('%d swapped functions' % len(rows), None, swaps=rows)

    def export(self, function):
        self.live.ready()
        address, unit, spec = self._target(function)
        if int(spec['size']) < 5:
            raise ConvError('TOO_SHORT', '%s is %d bytes, shorter than the 5-byte jump the exported loader writes (it '
                            'would overwrite the next function). Use fnv_code swap in the running game (it redirects '
                            'the call sites instead), or write the change as a hook in a native module.'
                            % (function, int(spec['size'])))
        link = self._linked(address)
        original = self.live.reads([(address, int(spec['size']))])[0]
        if address in self.swaps and original is not None:
            original = bytes.fromhex(self.swaps[address]['old']) + original[5:]
        patch = {'target': '0x%08X' % address, 'function': function, 'gameBuild': '1.4.0.525',
                 'expectedSha256': hashlib.sha256(original or b'').hexdigest(), 'expectedSize': int(spec['size']),
                 'code': link['code'], 'relocs': link['relocs'],
                 'note': 'Local use only: the code is recompiled game code. To publish a change, write it as a hook '
                         'in a native module (fnv_native), which holds only your own code.'}
        out = self._dir(address) / 'patch.json'
        out.write_text(json.dumps(patch, indent=1), encoding='utf-8')
        loader = self._dir(address) / ('fnvmcp_patch_%08x.c' % address)
        loader.write_text(self._loader_source(address, original or b'', link), encoding='utf-8')
        return verdict('Patch set written to %s, with a loader module %s' % (out, loader.name), patch['note'],
                       path=str(out), loader=str(loader),
                       next='fnv_native build source=<loader path> name=%s, then load: applies the patch now; '
                            'fnv_plugin_build with the same source applies it at every start (local only).'
                            % loader.stem)

    @staticmethod
    def _loader_source(address, original, link):
        """C source of a module that checks the expected original bytes, places the code, binds its relocations and
        jumps the entry to it (with the fnvmcp module API: alloc, write; the exe has no NX, so heap code runs)."""
        code = bytes.fromhex(link['code'])
        rows = []
        for r in link['relocs']:
            if 'orig' in r:
                raise ConvError('NOT_EXPORTABLE', 'The code calls fnv_orig_%08X: a loader for a trampoline is not '
                                'generated. Export a version without it.' % r['orig'])
            kind = 1 if r['kind'] == REL32 else 0
            if 'absolute' in r:
                rows.append('{%d, %d, 0x%08Xu, 0}' % (r['offset'], kind, r['absolute']))
            else:
                rows.append('{%d, %d, %du, 1}' % (r['offset'], kind, r['blob']))
        expect = (original or b'')[:16]

        def carray(data):
            return ', '.join('0x%02x' % b for b in data) or '0'
        return """/* Loader for a fnv_code patch set (local use only: the code is recompiled game code). Generated. */
#include "fnvmcp_module.h"
#include <string.h>

static const unsigned char code[] = {%s};
static const unsigned char expect[] = {%s};
typedef struct { unsigned offset, rel32; unsigned value, in_blob; } Reloc;
static const Reloc relocs[] = {%s};
#define TARGET 0x%08Xu

FNVMCP_EXPORT int fnvmcp_module_init(const FnvmcpApi *api) {
    unsigned char now[sizeof(expect)];
    unsigned char *blob;
    unsigned char jump[5];
    if (!api->read(TARGET, now, sizeof(now)) || memcmp(now, expect, sizeof(expect))) {
        api->log("patch 0x%%08X: the game's bytes differ from the expected ones; not applied", TARGET);
        return 1;
    }
    blob = (unsigned char *)api->alloc(sizeof(code));
    if (!blob) return 1;
    memcpy(blob, code, sizeof(code));
    for (unsigned i = 0; i < sizeof(relocs) / sizeof(relocs[0]); i++) {
        const Reloc *r = &relocs[i];
        unsigned target = r->in_blob ? (unsigned)(unsigned long)blob + r->value : r->value;
        unsigned field = r->rel32 ? target - ((unsigned)(unsigned long)blob + r->offset + 4) : target;
        memcpy(blob + r->offset, &field, 4);
    }
    jump[0] = 0xE9;
    {
        unsigned rel = (unsigned)(unsigned long)blob - (TARGET + 5);
        memcpy(jump + 1, &rel, 4);
    }
    if (!api->write(TARGET, jump, 5)) return 1;
    api->log("patch 0x%%08X applied (%%u bytes)", TARGET, (unsigned)sizeof(code));
    return 0;
}
""" % (carray(code), carray(expect), ', '.join(rows) or '{0, 0, 0, 0}', address)

    def run(self, action, **a):
        f = a.get('function')
        if action != 'list' and action != 'unswap' and not f:
            raise ConvError('BAD_ARGUMENTS', '%s needs function.' % action)
        if action == 'checkout':
            return self.checkout(f)
        if action == 'edit':
            return self.edit(f, a.get('find'), a.get('replace') or '')
        if action == 'build':
            return self.build(f)
        if action == 'swap':
            return self.swap(f)
        if action == 'unswap':
            return self.unswap(f)
        if action == 'compare':
            return self.compare(f, a.get('args'), a.get('this'), a.get('returns') or 'float', a.get('mode') or 'call',
                                a.get('seconds') or 5.0, a.get('nargs'))
        if action == 'list':
            return self.list()
        if action == 'export':
            return self.export(f)
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: checkout, edit, build, swap, unswap, compare, '
                        'list, export.' % action)
