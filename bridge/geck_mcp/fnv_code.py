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
    compare args        call the original and the new code side by side (before a swap) with the same arguments
    list                active swaps with their source diffs
    export              a local patch set (target, hash of the expected original bytes, code, relocations)

Limits told in results: functions with exception handling (.text$x / .xdata$x) and functions that need new writable
statics are refused; a function folded with others changes for all of them; callers that inlined it keep the old
body (most game logic is /Od); class layouts cannot change. Compiled code and objects stay in the bridge state
folder: local use only, never in git.
"""
import difflib
import hashlib
import importlib.util
import json
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from .fnv_conv import ConvError, verdict
from .fnv_link import word
from .fnv_native import c_name

REL32, DIR32, DIR32NB = 0x14, 0x06, 0x07
WRITABLE = 0x80000000


class CodeTools:
    def __init__(self, live):
        self.live = live
        self.swaps = {}            # address -> {name, blob, old, size}
        self.placed = {}           # address -> blob address (placed for compare, not redirected)
        self._units = None
        self._names = None
        self._coff = None

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
        cmd = [sys.executable, str(self.decomp / 'tools' / 'msvc.py'), '--compiler', unit['compiler'], '/c', '/nologo'] \
            + list(unit['flags']) + ['/I' + str(self.decomp / 'include'),
                                     '/I' + str(Path(meta['original']).parent), meta['copy'], '/Fo' + str(obj)]
        result = subprocess.run(cmd, cwd=str(self.decomp), capture_output=True, text=True, errors='replace',
                                timeout=300)
        if result.returncode or not obj.exists():
            raise ConvError('COMPILE_FAILED', 'The unit did not compile:\n%s' % (result.stdout + result.stderr)[-5000:],
                            {'output': (result.stdout + result.stderr)[-5000:]})
        link = self._link(obj, unit, spec)
        (folder / 'link.json').write_text(json.dumps(link, indent=1), encoding='utf-8')
        ok = not link['unresolved'] and not link['refused']
        return verdict('Built %s: %d bytes (original %d), %d relocations%s' % (
            function, link['size'], spec['size'], len(link['relocs']),
            '' if ok else ', cannot be swapped'),
            'Next: fnv_code compare (side by side) or swap.' if ok else 'Fix what is listed, then build again.',
            size=link['size'], originalSize=spec['size'], relocations=len(link['relocs']),
            copiedSections=link['copied'], unresolved=link['unresolved'] or None, refused=link['refused'] or None,
            diff=self._diff(meta))

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
            if name in mappings:
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
    def _final(link, base):
        code = bytearray.fromhex(link['code'])
        for r in link['relocs']:
            target = r['absolute'] if 'absolute' in r else base + r['blob']
            if r['kind'] == REL32:
                struct.pack_into('<i', code, r['offset'], (target - (base + r['offset'] + 4) + 2 ** 31) % 2 ** 32 - 2 ** 31)
            else:
                struct.pack_into('<I', code, r['offset'], target & 0xFFFFFFFF)
        return bytes(code)

    # ------------------------------------------------------------------ placing
    def _place(self, address, link):
        out = self.live.link.one('code', 'alloc', word(link['size'] + 16))
        if not out.get('ok'):
            raise ConvError('ALLOC_FAILED', 'The companion could not allocate code memory (%s).' % out.get('error'))
        base = int(out['address'], 16)
        code = self._final(link, base)
        wrote = self.live.link.one('code', 'patch', word(base), code.hex())
        if not wrote.get('ok'):
            raise ConvError('WRITE_FAILED', 'Could not write the code (%s).' % wrote.get('error'))
        return base

    def swap(self, function):
        self.live.ready()
        address, unit, spec = self._target(function)
        if address in self.swaps:
            raise ConvError('ALREADY_SWAPPED', '%s is swapped already. Next: unswap, then swap the new build.' % function)
        if int(spec['size']) < 5:
            raise ConvError('TOO_SHORT', 'The function is shorter than a jump (5 bytes); call sites would need '
                            'redirecting, which is not built yet.')
        link = self._linked(address)
        self.live.changing('code swap')
        base = self.placed.pop(address, None) or self._place(address, link)
        jump = b'\xE9' + struct.pack('<i', base - (address + 5))
        out = self.live.link.one('code', 'patch', word(address), jump.hex())
        if not out.get('ok'):
            raise ConvError('WRITE_FAILED', 'Could not redirect the entry (%s).' % out.get('error'))
        meta = self._meta(address)
        self.swaps[address] = {'function': function, 'blob': base, 'old': out['old'], 'size': link['size']}
        self.live.journal.add({'kind': 'code_swap', 'function': function, 'address': '0x%08X' % address,
                               'undo': None})
        return verdict('Swapped %s: 0x%08X now runs the new code at 0x%08X' % (function, address, base),
                       'Every call goes to the new code. unswap puts the entry back.', blob='0x%08X' % base,
                       size=link['size'], notes=self._notes(spec), diff=self._diff(meta))

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
        done = []
        for address in targets:
            s = self.swaps.pop(address)
            out = self.live.link.one('code', 'patch', word(address), s['old'])
            if not out.get('ok'):
                raise ConvError('WRITE_FAILED', 'Could not restore 0x%08X (%s).' % (address, out.get('error')))
            done.append(s['function'])
        return verdict('Restored %s' % ', '.join(done), 'The original entries run again (the new code stays '
                       'allocated, in case a thread is still inside it).', restored=done)

    def compare(self, function, args=None, this=None, returns='float'):
        """Call the original function and the new code with the same arguments (before a swap)."""
        self.live.ready()
        address, unit, spec = self._target(function)
        link = self._linked(address)
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
        return verdict('Patch set written to %s' % out, patch['note'], path=str(out))

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
            return self.compare(f, a.get('args'), a.get('this'), a.get('returns') or 'float')
        if action == 'list':
            return self.list()
        if action == 'export':
            return self.export(f)
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: checkout, edit, build, swap, unswap, compare, '
                        'list, export.' % action)
