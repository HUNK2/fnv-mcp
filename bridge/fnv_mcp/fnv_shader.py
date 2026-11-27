"""fnv_shader: HLSL shaders for the running game. This half: screen effects (SHADER-PLAN Task 4).

An effect is a ps_3_0 pixel shader drawn over the finished frame, under the HUD, by the bundled module
fnvmcp_shaderfx. The bridge compiles it, uploads the bytecode through the module's buffer, and keeps the source,
bytecode, params and order in `<state>/fnv/shaders/live/manifest.json`, stamped with the game's pid and start time.
So a later bridge process can rebuild what it knows from the manifest plus the module's own state, and a game
restart (the module died with the game) leaves the sources listed as saved ones, never as live effects.

Every change is journaled with an undo op (registered in fnv_live.py, next to the others) that refers to the manifest
by name or entry id, never carrying the source inline.
"""
import re
import json
import struct
import difflib
import fnmatch
import math
import shutil
from pathlib import Path

from .fnv_conv import ConvError, verdict
from .fnv_link import enable_mod
from .fnv_live import LiveError
from .fnv_layout import ini_value
from .fnv_shader_hlsl import compile_hlsl, disassemble, parse_ctab, version_of
from .fnv_shader_pkg import (Package, check_replacement, effect_template, package_index, profile_for,
                             read_sdp, replacement_template, write_sdp)
from .fnv_state import GameIdentity, locked, read_json, write_json

MODULE = 'fnvmcp_shaderfx'
MAX_EFFECTS = 8
FIRST_PARAM_REG = 2
CALL_SITE = '00B55AD6'
NAME_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,39}$')
PS_3_0 = 0xFFFF0300
FLAG_ADDRESSES = [0x011F91C0, 0x011F94A4, 0x011F941E, 0x011F941F]   # gpu class, then three package flags
ROW = struct.Struct('<5I')                                          # obj, kind (0 ps, 1 vs), d3d, size, hash
READ_CHUNK = 65536 // ROW.size * ROW.size                          # whole rows, at most the companion's read limit
MIN_FOUND = 0.95
LIST_LIMIT = 300
REG_LETTER = {'float4': 'c', 'int4': 'i', 'bool': 'b', 'sampler': 's'}
EXPORT_HOOKS = ['0x0086BA10']
SHADERFX_SOURCE = Path(__file__).resolve().parents[1] / 'native' / 'modules' / 'fnvmcp_shaderfx.c'
MENU_NOTE = 'pictures need the game out of menus (effects do not draw in menus)'


def _rel(path, base):
    """A path as text for the answer: relative to the state dir, else just the file name (never a user folder)."""
    p = Path(path)
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        return p.name


class ShaderTools:
    def __init__(self, live, native, plugins=None):
        self.live = live
        self.native = native
        self.plugins = plugins      # PluginTools, made on the first export that builds a plugin
        self._verified = None       # the game key whose manifest effects were checked against the module
        self._pkg = None            # (key, mtime, Package): the package in use for one game session
        self._scanned = None        # (game key, rows): the engine shader objects of one game session

    @classmethod
    def of(cls, live):
        tools = getattr(live, 'shader_tools', None)
        if tools is None:
            from .fnv_native import NativeTools
            tools = cls(live, NativeTools(live))
            live.shader_tools = tools
        return tools

    # ------------------------------------------------------------------ saved state
    @property
    def base(self):
        return Path(self.live.project.state_dir)

    @property
    def folder(self):
        return self.base / 'fnv' / 'shaders' / 'live'

    @property
    def manifest_path(self):
        return self.folder / 'manifest.json'

    def _read(self, folder=None):
        m = read_json((Path(folder) if folder else self.folder) / 'manifest.json', {})
        for key, empty in (('effects', {}), ('removed', {}), ('saved', {}), ('replacements', {}), ('restored', {}),
                           ('savedReplacements', {})):
            if not isinstance(m.get(key), dict):
                m[key] = empty
        m.setdefault('game', None)
        m['counter'] = int(m.get('counter') or 0)
        return m

    def _write(self, m):
        self.folder.mkdir(parents=True, exist_ok=True)
        write_json(self.manifest_path, m, indent=1)

    def _identity(self):
        return GameIdentity.parse(self.live.game_identity())

    def _c(self, export, *args):
        """One module export; its integer result."""
        return int(self.native.call(MODULE, export, list(args))['value'])

    def state(self):
        """The rebuilt state: the manifest, reconciled with the running game and the module. Effects of another game
        (or ones the module no longer holds) move to `saved`; the manifest is rewritten when that changed it."""
        with locked(self.manifest_path):
            m = self._read()
            here = self._identity()
            if here is None:
                return m, False
            was = GameIdentity.parse(m['game'])
            same = was is not None and was.same_game(here)
            changed = False
            if not same:
                m['saved'].update(m['effects'])
                changed = bool(m['effects']) or bool(m['replacements']) or m['game'] != here.as_dict()
                m['effects'] = {}
                m['savedReplacements'].update(m['replacements'])   # the swaps died with that game; export keeps them
                m['replacements'] = {}
                m['game'] = here.as_dict()
            elif (m['effects'] or m['replacements']) and self._verified != here.key():
                loaded = self.native.is_loaded(MODULE)
                if m['replacements'] and not loaded:
                    m['savedReplacements'].update(m['replacements'])
                    m['replacements'] = {}
                    changed = True
                for name in list(m['effects']):
                    if not loaded or not self._c('sfx_effect_state', m['effects'][name]['slot']) & 1:
                        m['saved'][name] = m['effects'].pop(name)
                        changed = True
            self._verified = here.key()
            if changed:
                self._write(m)
            return m, True

    # ------------------------------------------------------------------ params
    @staticmethod
    def _constants(compiled):
        return [[c.name, c.index, c.count, list(c.default) if c.default else None] for c in compiled['constants']
                if c.regset == 'float4' and c.index >= FIRST_PARAM_REG]

    @staticmethod
    def _valid(constants):
        return ', '.join('%s (c%d%s)' % (n, r, '-c%d' % (r + k - 1) if k > 1 else '') for n, r, k, _d in constants) \
            or 'none (the shader declares no float4 constants at c2 or above)'

    def _check_params(self, constants, params):
        """{name: flat list of 4 * count floats}, or BAD_PARAM naming the valid names and registers. Nothing has been
        sent to the game yet."""
        out = {}
        by_name = {n: (r, k) for n, r, k, _d in constants}
        for name, value in (params or {}).items():
            if name not in by_name:
                raise ConvError('BAD_PARAM', 'Unknown param %r. Valid params: %s.' % (name, self._valid(constants)),
                                {'valid': [{'name': n, 'register': 'c%d' % r, 'count': k} for n, r, k, _d in constants]})
            reg, count = by_name[name]
            nums = value if isinstance(value, (list, tuple)) else [value]
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in nums):
                raise ConvError('BAD_PARAM', 'Param %s (c%d) takes numbers, got %r.' % (name, reg, value))
            if len(nums) > 4 * count:
                raise ConvError('BAD_PARAM', 'Param %s (c%d) takes at most %d numbers, got %d. Valid params: %s.'
                                % (name, reg, 4 * count, len(nums), self._valid(constants)))
            flat = [float(x) for x in nums]
            out[name] = flat + [0.0] * (4 * count - len(flat))
        return out

    def _send_params(self, slot, constants, params, only=None):
        """Every float4 param: the stored value, else the shader's CTAB default, else zeros."""
        for name, reg, count, default in constants:
            if only is not None and name not in only:
                continue
            flat = params.get(name) or default or [0.0] * (4 * count)
            for i in range(count):
                self._c('sfx_effect_param', slot, reg + i, *[float(x) for x in flat[4 * i:4 * i + 4]])

    # ------------------------------------------------------------------ module and pushing an effect
    def _module(self):
        """Load the module when no earlier process did (is_loaded is the truth: nothing loads twice)."""
        if self.native.is_loaded(MODULE):
            return
        try:
            self.native.bundled(MODULE)
        except ConvError as e:
            seen = '%s %s' % (e, json.dumps(e.evidence, default=str) if e.evidence else '')
            try:
                lines = (self.native.log() or {}).get('lines') or []
                seen += ' '.join(str(line.get('text')) for line in lines[-20:])
            except Exception:  # noqa: BLE001 - the log is only a second source for the same fact
                pass
            if CALL_SITE in seen.upper():
                raise self._taken() from None
            raise
        if not self._c('sfx_info') & 1:
            raise self._taken()

    @staticmethod
    def _taken():
        return ConvError('CALL_SITE_TAKEN', 'The screen-effect call site 0x%s is not the expected call: another mod '
                         'has patched it, so the shader module did not start. Nothing was changed. Disable that mod '
                         '(an ENB or ReShade style injector, or another renderer hook) and try again.' % CALL_SITE)

    def _push(self, entry):
        """Create or replace the module slot from the saved bytecode, then resend the stored params (a replace zeroes
        them) and the enabled flag."""
        data = (self.folder / entry['bytecode_file']).read_bytes()
        addr = self._c('sfx_buffer', len(data))
        if not addr:
            raise ConvError('NO_BUFFER', 'The shader module could not make an upload buffer of %d bytes.' % len(data))
        self.live.link.write(addr & 0xFFFFFFFF, data)
        hr = self._c('sfx_effect_set', entry['slot'], addr, len(data), entry['order'])
        if hr < 0:
            raise ConvError('EFFECT_REJECTED', 'The game refused the shader (HRESULT 0x%08X). Check that it is '
                            'ps_3_0 and uses only registers a pixel shader may.' % (hr & 0xFFFFFFFF))
        self._send_params(entry['slot'], entry['constants'], entry['params'])
        if not entry['enabled']:
            self._c('sfx_effect_enable', entry['slot'], 0)

    def _free_slot(self, m):
        used = {e['slot'] for e in m['effects'].values()}
        return next((s for s in range(MAX_EFFECTS) if s not in used), None)

    def _journal(self, what, name, undo, **extra):
        return self.live.journal.add({'kind': 'shader', 'what': what, 'effect': name, 'undo': undo, **extra})

    # ------------------------------------------------------------------ pictures
    def _look(self, photo, baseline=None):
        """(result, note): a picture, or None and why not. A menu or any failure to see never stops the change."""
        if not photo:
            return None, None
        try:
            if baseline:
                return self.live.eyes.photo('compare', baseline=baseline), None
            return self.live.eyes.photo('view'), None
        except LiveError as e:
            if e.code == 'MENU_OPEN':
                return None, MENU_NOTE
            return None, 'no picture: %s' % e

    def _pictures(self, before, after):
        out = {}
        if before:
            out['before'] = _rel(before['path'], self.base)
        if after:
            out['after'] = _rel(after.get('now') or '', self.base) if after.get('now') else None
            out['changedPercent'] = after.get('changedPercent')
        return out

    # ------------------------------------------------------------------ actions
    def effect_add(self, name, source, order=None, params=None, photo=True):
        if not isinstance(name, str) or not NAME_RE.match(name) or name == 'all':
            raise ConvError('BAD_NAME', 'name is 1-40 letters, digits, _ . - (and not "all"); got %r.' % (name,))
        if not isinstance(source, str) or not source.strip():
            raise ConvError('NO_SOURCE', 'source is the HLSL text of a ps_3_0 pixel shader (action template gives '
                            'a starting point).')
        self.live.ready()
        m, _ = self.state()
        if name in m['effects']:
            raise ConvError('EFFECT_EXISTS', 'Effect %r exists: use effect_set to change it, or effect_remove it '
                            'first.' % name)
        if len(m['effects']) >= MAX_EFFECTS:
            raise ConvError('TOO_MANY_EFFECTS', 'Already %d effects (the most there can be): effect_remove one '
                            'first. Now: %s.' % (MAX_EFFECTS, ', '.join(m['effects'])))
        compiled = compile_hlsl(source, 'ps_3_0')
        if not compiled['ok']:
            first = compiled['errors'][0]
            raise ConvError('COMPILE_FAILED', 'The shader did not compile: line %s: %s %s (%d errors).' % (
                first.get('line'), first.get('code'), first.get('text'), len(compiled['errors'])),
                {'errors': compiled['errors'], 'warnings': compiled['warnings']})
        if version_of(compiled['bytecode']) != PS_3_0:
            raise ConvError('NOT_PS3', 'Screen effects are ps_3_0 pixel shaders only.')
        constants = self._constants(compiled)
        values = self._check_params(constants, params)
        self._module()
        m, _ = self.state()                       # the module may have just started: look again
        slot = self._free_slot(m)
        if slot is None:
            raise ConvError('TOO_MANY_EFFECTS', 'All %d effect slots are in use.' % MAX_EFFECTS)
        if order is None:
            order = max([e['order'] for e in m['effects'].values()], default=-1) + 1
        m['counter'] += 1
        stem = '%s-%d' % (name, m['counter'])
        entry = {'slot': slot, 'order': int(order), 'params': values, 'source_file': stem + '.hlsl',
                 'bytecode_file': stem + '.ps', 'enabled': True, 'constants': constants}
        before, note = self._look(photo)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / entry['source_file']).write_text(source, encoding='utf-8')
        (self.folder / entry['bytecode_file']).write_bytes(compiled['bytecode'])
        try:
            self._push(entry)
        except Exception:
            if self.native.is_loaded(MODULE):
                self._c('sfx_effect_free', slot)
            raise
        m['effects'][name] = entry
        self._write(m)
        self._journal('add', name, {'op': 'shader_effect_remove', 'name': name}, slot=slot)
        after, note2 = self._look(photo and before is not None, before['path'] if before else None)
        unset = [n for n, _, _, d in constants if n not in values and not d]
        out = verdict('Effect %s added in slot %d' % (name, slot),
                      'Compiled, uploaded and saved; effect_set changes its params live.',
                      effect=name, slot=slot, order=entry['order'], costUs=self._c('sfx_cost_us'),
                      warnings=compiled['warnings'] or None, paramsNotSet=unset or None,
                      **self._pictures(before, after))
        out.setdefault('before', None)
        out.setdefault('after', None)
        out.setdefault('changedPercent', None)
        out['note'] = note or note2
        if unset and not out['note']:
            out['note'] = 'params with no default start at zero: %s' % ', '.join(unset)
        return out

    def effect_set(self, name, params=None, order=None, enabled=None, photo=True):
        self.live.ready()
        m, live_game = self.state()
        entry = m['effects'].get(name)
        if entry is None:
            raise ConvError('NO_EFFECT', 'No live effect %r. Live effects: %s.' % (name, ', '.join(m['effects'])
                                                                                    or 'none'))
        new = self._check_params(entry['constants'], params)
        old = {'params': {k: list(v) for k, v in entry['params'].items()}, 'order': entry['order'],
               'enabled': entry['enabled']}
        before, note = self._look(photo)
        entry['params'].update(new)
        reorder = order is not None and int(order) != entry['order']
        if order is not None:
            entry['order'] = int(order)
        if enabled is not None:
            entry['enabled'] = bool(enabled)
        if reorder:
            self._push(entry)                 # the order is set with the shader: a replace, then every param again
        else:
            self._send_params(entry['slot'], entry['constants'], entry['params'], only=new)
            if enabled is not None:
                self._c('sfx_effect_enable', entry['slot'], 1 if entry['enabled'] else 0)
        self._write(m)
        self._journal('set', name, {'op': 'shader_effect_params', 'name': name, **old}, slot=entry['slot'])
        after, note2 = self._look(photo and before is not None, before['path'] if before else None)
        out = verdict('Effect %s changed' % name, None, effect=name, params=entry['params'], order=entry['order'],
                      enabled=entry['enabled'], costUs=self._c('sfx_cost_us'), **self._pictures(before, after))
        out.setdefault('before', None)
        out.setdefault('after', None)
        out.setdefault('changedPercent', None)
        out['note'] = note or note2
        return out

    def effect_list(self):
        m, known = self.state()
        loaded = known and self.native.is_loaded(MODULE)
        effects, gone = [], []
        for name, e in sorted(m['effects'].items(), key=lambda kv: kv[1]['order']):
            bits = self._c('sfx_effect_state', e['slot']) if loaded else 0
            if not bits & 1:                  # the module was unloaded or lost it since: only the source is left
                gone.append(name)
                continue
            effects.append({'name': name, 'slot': e['slot'], 'order': e['order'], 'enabled': bool(bits & 2),
                            'failed': bool(bits & 4), 'usesHdrScene': bool(bits & 8), 'params': e['params'],
                            'paramNames': [c[0] for c in e['constants']], 'source': e['source_file']})
        saved = sorted({n for n in m['saved'] if n not in m['effects']} | set(gone))
        out = verdict('%d live effects' % len(effects), None, effects=effects, savedSources=saved,
                      moduleLoaded=bool(loaded), costUs=self._c('sfx_cost_us') if loaded and effects else 0)
        if saved and not effects:
            out['note'] = 'The sources are saved but not in the running game (it restarted, or the module was ' \
                          'unloaded): effect_add them again.'
        return out

    def effect_remove(self, name, photo=True):
        self.live.ready()
        m, _ = self.state()
        names = list(m['effects']) if name == 'all' else [name]
        if name != 'all' and name not in m['effects']:
            raise ConvError('NO_EFFECT', 'No live effect %r. Live effects: %s.' % (name, ', '.join(m['effects'])
                                                                                    or 'none'))
        blind = name == 'all'
        before, note = (None, None) if blind else self._look(photo)
        loaded = self.native.is_loaded(MODULE)
        for n in names:
            e = m['effects'].pop(n)
            if loaded:
                self._c('sfx_effect_free', e['slot'])
            m['counter'] += 1
            rid = '%s#%d' % (n, m['counter'])
            m['removed'][rid] = {**e, 'name': n, 'game': m['game']}
            self._write(m)
            self._journal('remove', n, {'op': 'shader_effect_add', 'id': rid}, slot=e['slot'])
        after, note2 = (None, None) if blind else self._look(photo and before is not None,
                                                             before['path'] if before else None)
        out = verdict('%d effect(s) removed' % len(names), 'Undo adds one back from the saved source.',
                      removed=names, **self._pictures(before, after))
        out.setdefault('before', None)
        out.setdefault('after', None)
        out.setdefault('changedPercent', None)
        out['note'] = note or note2
        return out

    # ------------------------------------------------------------------ undo (called by the ops in fnv_live)
    def undo_add(self, name):
        """Undo of an add: free the slot. {'undone': False, 'reason'} when the game restarted or the effect is gone."""
        m, known = self.state()
        e = m['effects'].get(name)
        if e is None:
            return {'undone': False, 'reason': 'effect %r is not in the running game (it was removed, or the game '
                    'restarted)' % name}
        if self.native.is_loaded(MODULE):
            self._c('sfx_effect_free', e['slot'])
        del m['effects'][name]
        self._write(m)
        return None

    def undo_remove(self, rid):
        """Undo of a remove: put the effect back from the manifest entry `rid`."""
        m, known = self.state()
        e = m['removed'].get(rid)
        if e is None:
            return {'undone': False, 'reason': 'the saved effect %s is gone from the manifest' % rid}
        name = e['name']
        if not known:
            return {'undone': False, 'reason': 'no running game'}
        if name in m['effects']:
            return {'undone': False, 'reason': 'an effect named %r exists again' % name}
        if len(m['effects']) >= MAX_EFFECTS:
            return {'undone': False, 'reason': 'all %d effect slots are in use' % MAX_EFFECTS}
        if not (self.folder / e['bytecode_file']).exists():
            return {'undone': False, 'reason': 'the saved bytecode of %s is gone' % name}
        game = GameIdentity.parse(e.get('game'))
        if game is None or not game.same_game(self._identity()):
            return {'undone': False, 'reason': 'the game restarted since: effect_add the source again'}
        self._module()
        m, _ = self.state()
        entry = {k: v for k, v in e.items() if k not in ('name', 'game')}
        entry['slot'] = self._free_slot(m)
        self._push(entry)
        m['effects'][name] = entry
        del m['removed'][rid]
        self._write(m)
        return None

    def undo_params(self, name, params, order, enabled):
        m, known = self.state()
        e = m['effects'].get(name)
        if e is None:
            return {'undone': False, 'reason': 'effect %r is not in the running game (it was removed, or the game '
                    'restarted)' % name}
        reorder = order != e['order']
        e['params'] = {k: [float(x) for x in v] for k, v in params.items()}
        e['order'], e['enabled'] = order, bool(enabled)
        if reorder:
            self._push(e)
        else:
            self._send_params(e['slot'], e['constants'], e['params'])
            self._c('sfx_effect_enable', e['slot'], 1 if e['enabled'] else 0)
        self._write(m)
        return None

    # ================================================================== engine shaders (SHADER-PLAN Task 5)
    # The engine's own D3D shaders, named by the package (shaderpackageNNN.sdp) the game loaded. The module's scan
    # lists the live shader objects by the hash of their code; a replacement is swapped into every object holding the
    # hash of the named original. The original bytecode is read from the package only for checks and answers: it is
    # never written anywhere. The AI's own source and compiled bytecode are saved in the live folder.
    def _running(self):
        try:
            return self._identity()
        except Exception:  # noqa: BLE001 - no link, no game: the package is then a guess
            return None

    def _layout(self):
        return self.live.game.layout()

    def _where(self, path, layout):
        """A file as text for the answer: relative to the MO2 folder or the game folder, else just its name."""
        for root in (layout.mo2_root, layout.game_root):
            if root:
                try:
                    return Path(path).relative_to(root).as_posix()
                except ValueError:
                    pass
        return Path(path).name

    def _index(self, layout, ident):
        """(package index, guessed). The game's own flags when it runs, else a guess from FalloutPrefs."""
        if ident is not None:
            try:
                flags = self.live.u32s(FLAG_ADDRESSES)
            except Exception:  # noqa: BLE001
                flags = None
            if flags and all(f is not None for f in flags):
                gpu, f94a4, f941e, f941f = flags
                index = package_index(gpu & 0xFF, f94a4 & 0xFF, f941e & 0xFF, f941f & 0xFF)
                if index:
                    return index, False
        try:
            hdr = ini_value(layout.prefs_ini, 'Display', 'bDoHighDynamicRange') == '1'
        except Exception:  # noqa: BLE001 - an MO2 install with no instance named has no prefs file to read
            hdr = False
        return package_index(5, 0, 1 if hdr else 0, 0), True      # gpu class unknown: the most common one

    @staticmethod
    def _holder(layout, index):
        """The Shaders folder of the highest-priority place that holds the package, or None."""
        for root in layout.loose_roots():
            if (root / 'Shaders' / ('shaderpackage%03d.sdp' % index)).exists():
                return root / 'Shaders'
        return None

    def data_shaders_dir(self):
        layout = self._layout()
        index, _ = self._index(layout, self._running())
        return self._holder(layout, index) or layout.data_dir / 'Shaders'

    def package(self):
        """The Package in use (cached per game session), with `.index`, `.guessed` and `.file` set on it."""
        ident = self._running()
        layout = self._layout()
        index, guessed = self._index(layout, ident)
        folder = self._holder(layout, index)
        path = (folder or layout.data_dir / 'Shaders') / ('shaderpackage%03d.sdp' % index)
        if guessed and not path.exists():
            shaders = layout.data_dir / 'Shaders'
            pool = sorted(shaders.glob('shaderpackage*.sdp'), key=lambda p: p.stat().st_size) if shaders.is_dir() else []
            if pool:
                path = pool[-1]
                index = int(re.sub(r'\D', '', path.stem) or 0)
        if not path.exists():
            raise ConvError('NO_PACKAGE', 'The shader package %s was not found (looked in the Shaders folder of every '
                            'enabled mod and in Data).' % path.name, {'index': index, 'guessed': guessed})
        key = (ident.key() if ident else None, str(path), path.stat().st_mtime_ns, guessed)
        if self._pkg is None or self._pkg[0] != key:
            pkg = Package(path)
            pkg.index, pkg.guessed, pkg.file = index, guessed, self._where(path, layout)
            self._pkg = (key, pkg)
        return self._pkg[1]

    def _scan(self, force=False):
        """The engine's shader objects as rows (obj, kind, d3d, size, hash), scanned once per game session; the
        package is confirmed by the share of hashes it holds."""
        ident = self._identity()
        pkg = self.package()
        if self._scanned and self._scanned[0] == ident.key() and not force:
            return self._scanned[1]
        self._module()
        count = self._c('sfx_scan')
        rows = []
        if count > 0:
            table, total, parts = self._c('sfx_table') & 0xFFFFFFFF, count * ROW.size, []
            for off in range(0, total, READ_CHUNK):       # the companion refuses a read over 65536 bytes
                parts.append(self.live.link.read(table + off, min(READ_CHUNK, total - off)) or b'')
            raw = b''.join(parts)
            rows = [ROW.unpack_from(raw, i * ROW.size) for i in range(len(raw) // ROW.size)]
        known = {pkg.hash_of(n) for n in pkg.names()}
        found = sum(1 for r in rows if r[4] in known)
        if rows and found < MIN_FOUND * len(rows):
            raise ConvError('PACKAGE_MISMATCH', 'Only %d of the %d shader objects in the game are in %s (at least %d%% '
                            'is needed): the game loaded another shader package. Nothing was changed.'
                            % (found, len(rows), pkg.file, MIN_FOUND * 100),
                            {'objects': len(rows), 'found': found, 'package': pkg.file, 'index': pkg.index})
        self._scanned = (ident.key(), rows)
        return rows

    def _resolve(self, pkg, name):
        names = set(pkg.names())
        if isinstance(name, str) and name in names:
            return name
        if isinstance(name, str):
            lower = {n.lower(): n for n in names}
            if name.lower() in lower:
                return lower[name.lower()]
        text = str(name or '')
        near = [n for n in sorted(names) if text and text.lower() in n.lower()][:5]
        near += [n for n in difflib.get_close_matches(text, sorted(names), n=5, cutoff=0.5) if n not in near]
        raise ConvError('NO_SUCH_SHADER', 'No shader %r in %s. Closest names: %s.' % (
            name, pkg.file, ', '.join(near[:5]) or 'none (action engine_list searches by name)'),
            {'suggestions': near[:5]})

    @staticmethod
    def _reg_text(c):
        letter = REG_LETTER.get(c.regset, '?')
        last = '-%s%d' % (letter, c.index + c.count - 1) if c.count > 1 else ''
        return '%s:%s%d%s' % (c.name, letter, c.index, last)

    def _describe(self, pkg, name):
        code = pkg.code(name)
        try:
            model = profile_for(code)
        except ConvError:
            model = 'unknown'
        ctab = parse_ctab(code)
        return {'name': name, 'kind': 'vs' if version_of(code) >> 16 == 0xFFFE else 'ps', 'model': model,
                'constants': [self._reg_text(c) for c in ctab if c.regset != 'sampler'],
                'samplers': [self._reg_text(c) for c in ctab if c.regset == 'sampler'],
                'sharedWith': pkg.shared_with(name)}

    def _live_scan(self):
        """(rows, note): the live objects when the game runs; a failure of the scan other than a wrong package is
        only a note (the listing itself needs no game)."""
        if self._running() is None:
            return None, None
        try:
            return self._scan(), None
        except (ConvError, LiveError) as e:
            if getattr(e, 'code', None) == 'PACKAGE_MISMATCH':
                raise
            return None, 'live objects not scanned: %s' % e

    def engine_list(self, filter=None):
        pkg = self.package()
        rows, note = self._live_scan()
        counts = {}
        for r in rows or []:
            counts[r[4]] = counts.get(r[4], 0) + 1
        names = sorted(set(pkg.names()))
        if filter:
            pat = str(filter).lower()
            if any(c in pat for c in '*?['):
                names = [n for n in names if fnmatch.fnmatchcase(n.lower(), pat)]
            else:
                names = [n for n in names if pat in n.lower()]
        shown = names[:LIST_LIMIT]
        out_rows = []
        for n in shown:
            row = self._describe(pkg, n)
            if rows is not None:
                row['liveObjects'] = counts.get(pkg.hash_of(n), 0)
            out_rows.append(row)
        known = {pkg.hash_of(n) for n in pkg.names()}
        out = verdict('%d engine shaders' % len(names), None, rows=out_rows, total=len(names), package=pkg.file,
                      guessed=bool(pkg.guessed))
        out['unknownObjects'] = sum(1 for r in rows if r[4] not in known) if rows is not None else None
        if len(names) > len(shown):
            out['truncated'] = 'only the first %d of %d are shown: narrow it with filter' % (len(shown), len(names))
        out['note'] = note or ('the package is a guess from FalloutPrefs (the game is not running)' if pkg.guessed
                               else None)
        return out

    def engine_show(self, name, disasm=False):
        pkg = self.package()
        name = self._resolve(pkg, name)
        rows, note = self._live_scan()
        out = verdict('Engine shader %s' % name, None, **self._describe(pkg, name))
        out['package'] = pkg.file
        out['guessed'] = bool(pkg.guessed)
        out['liveObjects'] = sum(1 for r in rows if r[4] == pkg.hash_of(name)) if rows is not None else None
        out['replaced'] = any(name in e['names'] for e in self._read()['replacements'].values())
        out['note'] = note
        if disasm:
            out['disassembly'] = disassemble(pkg.code(name))
            out['gameContent'] = True       # the engine's own code: shown here only, never saved or committed
        return out

    def template(self, name=None):
        if not name:
            return {'name': None, 'profile': 'ps_3_0', 'source': effect_template()}
        pkg = self.package()
        name = self._resolve(pkg, name)
        code = pkg.code(name)
        return {'name': name, 'profile': profile_for(code), 'source': replacement_template(name, code)}

    # ------------------------------------------------------------------ replace and restore
    def _frame_ms(self):
        try:
            return self.live.world.frame_profile(1.0).get('msPerFrame')
        except Exception:  # noqa: BLE001 - a menu, or a profiler that cannot hook: the swap is still worth doing
            return None

    def _swap(self, h, data):
        """Upload the bytecode and swap it into every object with the code hash `h`; the object count."""
        addr = self._c('sfx_buffer', len(data))
        if not addr:
            raise ConvError('NO_BUFFER', 'The shader module could not make an upload buffer of %d bytes.' % len(data))
        self.live.link.write(addr & 0xFFFFFFFF, data)
        n = self._c('sfx_swap', h, addr, len(data))
        if n >= 0x80000000:
            n -= 1 << 32
        if n < 0:
            raise ConvError('SWAP_FAILED', 'The game refused the replacement (module result %d). Check that it is a '
                            'drop-in for the original (action template).' % n)
        return n

    @staticmethod
    def _find(m, name):
        return next(((k, e) for k, e in m['replacements'].items() if name in e['names']), (None, None))

    def replace(self, name, source, photo=True):
        if not isinstance(source, str) or not source.strip():
            raise ConvError('NO_SOURCE', 'source is the HLSL text of the replacement (action template gives a '
                            'starting point that matches the original).')
        self.live.ready()
        pkg = self.package()
        name = self._resolve(pkg, name)
        original = pkg.code(name)
        profile = profile_for(original)
        compiled = compile_hlsl(source, profile)
        if not compiled['ok']:
            first = compiled['errors'][0]
            raise ConvError('COMPILE_FAILED', 'The shader did not compile as %s: line %s: %s %s (%d errors).' % (
                profile, first.get('line'), first.get('code'), first.get('text'), len(compiled['errors'])),
                {'errors': compiled['errors'], 'warnings': compiled['warnings']})
        problems = check_replacement(original, compiled['bytecode'])
        hard = [p for p in problems if not p.get('warning')]
        if hard:
            raise ConvError('INCOMPATIBLE', 'The replacement is not a drop-in for %s: %s' % (
                name, ' '.join(p['text'] for p in hard)), {'problems': problems})
        h = pkg.hash_of(name)
        names = [name] + pkg.shared_with(name)
        warnings = [p['text'] for p in problems if p.get('warning')]
        warnings += [w['text'] for w in compiled['warnings'] if isinstance(w, dict) and w.get('text')]
        if len(names) > 1:
            warnings.append('%s share the same code: %s also change too.' % (
                ', '.join(names), ', '.join(n for n in names if n != name)))
        m, _ = self.state()
        key = '%08X' % h
        prior = m['replacements'].get(key)
        m['counter'] += 1
        stem = 'engine-%s-%d' % (key, m['counter'])
        entry = {'hash': h, 'names': names, 'source_file': stem + '.hlsl', 'bytecode_file': stem + '.ps',
                 'profile': profile, 'game': m['game']}
        before, note = self._look(photo)
        frame_before = self._frame_ms()
        # Always a fresh scan right before the swap: sfx_swap only reaches the objects in the module's table, and a
        # new cell makes new ones (the cached scan is for engine_list / engine_show).
        rows = self._scan(force=True)
        if not any(r[4] == h for r in rows):
            raise ConvError('NO_LIVE_OBJECTS', 'No shader object in the running game has the code of %s: the game '
                            'has not made one yet (it makes them as cells and effects need them). Nothing was '
                            'changed.' % name)
        n = self._swap(h, compiled['bytecode'])
        if n == 0:
            raise ConvError('NO_LIVE_OBJECTS', 'The shader objects of %s are gone from the game: nothing was changed.'
                            % name)
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / entry['source_file']).write_text(source, encoding='utf-8')
        (self.folder / entry['bytecode_file']).write_bytes(compiled['bytecode'])
        entry['objects'] = n
        prior_id = None
        if prior is not None:
            prior_id = '%s#%d' % (key, m['counter'])
            m['restored'][prior_id] = prior
        m['replacements'][key] = entry
        self._write(m)
        self._journal('replace', name, {'op': 'shader_restore', 'name': name, 'prior': prior_id}, objects=n)
        frame_after = self._frame_ms()
        after, note2 = self._look(photo and before is not None, before['path'] if before else None)
        out = verdict('%s replaced in %d shader objects' % (name, n), 'Undo restores the engine code.', names=names,
                      objects=n, warnings=warnings or None, frameMs={'before': frame_before, 'after': frame_after},
                      source=entry['source_file'], **self._pictures(before, after))
        out.setdefault('before', None)
        out.setdefault('after', None)
        out.setdefault('changedPercent', None)
        out['note'] = note or note2
        return out

    def restore(self, name):
        self.live.ready()
        m, _ = self.state()
        if name == 'all':
            keys = list(m['replacements'])
        else:
            key, _e = self._find(m, name)
            if key is None:
                replaced = sorted({n for e in m['replacements'].values() for n in e['names']})
                raise ConvError('NOT_REPLACED', 'No replacement of %r is in the running game. Replaced: %s.' % (
                    name, ', '.join(replaced) or 'none'))
            keys = [key]
        loaded = self.native.is_loaded(MODULE)
        if name == 'all' and loaded:
            self._c('sfx_restore_all')
        names = []
        for key in keys:
            e = m['replacements'].pop(key)
            if name != 'all' and loaded:
                self._c('sfx_restore', e['hash'])
            m['counter'] += 1
            rid = '%s#%d' % (key, m['counter'])
            m['restored'][rid] = {**e, 'game': m['game']}
            self._write(m)
            self._journal('restore', e['names'][0], {'op': 'shader_replace', 'id': rid})
            names += e['names']
        self._write(m)
        return verdict('%d engine shader replacement(s) restored' % len(keys), 'Undo puts the replacement back.',
                       restored=names)

    # ------------------------------------------------------------------ undo (called by the ops in fnv_live)
    def undo_replace(self, name, prior):
        """Undo of a replace: back to the engine code, or to the replacement it had replaced (`prior`)."""
        m, known = self.state()
        key, e = self._find(m, name)
        if e is None:
            return {'undone': False, 'reason': 'the replacement of %r is not in the running game (it was restored, or '
                    'the game restarted)' % name}
        old = m['restored'].get(prior) if prior else None
        if prior and old is None:
            return {'undone': False, 'reason': 'the earlier replacement %s is gone from the manifest' % prior}
        if old is not None:
            if not (self.folder / old['bytecode_file']).exists():
                return {'undone': False, 'reason': 'the saved bytecode of the earlier replacement is gone'}
            self._swap(old['hash'], (self.folder / old['bytecode_file']).read_bytes())
            m['replacements'][key] = old
            del m['restored'][prior]
        else:
            if self.native.is_loaded(MODULE):
                self._c('sfx_restore', e['hash'])
            del m['replacements'][key]
        self._write(m)
        return None

    def undo_restore(self, rid):
        """Undo of a restore: swap the saved replacement back in."""
        m, known = self.state()
        e = m['restored'].get(rid)
        if e is None:
            return {'undone': False, 'reason': 'the saved replacement %s is gone from the manifest' % rid}
        if not known:
            return {'undone': False, 'reason': 'no running game'}
        game = GameIdentity.parse(e.get('game'))
        if game is None or not game.same_game(self._identity()):
            return {'undone': False, 'reason': 'the game restarted since: replace the shader again'}
        key = '%08X' % e['hash']
        if key in m['replacements']:
            return {'undone': False, 'reason': 'the shader is replaced again by another call'}
        if not (self.folder / e['bytecode_file']).exists():
            return {'undone': False, 'reason': 'the saved bytecode is gone'}
        self._module()
        n = self._swap(e['hash'], (self.folder / e['bytecode_file']).read_bytes())
        if n == 0:
            return {'undone': False, 'reason': 'no shader object in the game has that code any more'}
        m['replacements'][key] = {**e, 'objects': n}
        del m['restored'][rid]
        self._write(m)
        return None

    # ================================================================== export and sets (SHADER-PLAN Task 6)
    # Screen effects become an xNVSE plugin: the shaderfx module source with `#define SFX_BAKED 1` and the generated
    # sfx_baked.h in front of it, built through PluginTools. Engine replacements become patched copies of the user's
    # own installed packages, written only under `<state>/fnv/shaders/export/<set or live>/Shaders` (game content: never
    # tracked, never written into the game folder). A set is a copy of the live folder (manifest and sources).
    def _plugin_tools(self):
        if self.plugins is None:
            from .fnv_plugin_build import PluginTools
            self.plugins = PluginTools(self.live)
        return self.plugins

    @staticmethod
    def _floats(values):
        return ', '.join('%.9ef' % (float(x) if math.isfinite(float(x)) else 0.0) for x in values)

    def _baked_header(self, folder, effects):
        """The generated sfx_baked.h text: per enabled effect its order, its params (stored, else the CTAB default,
        else zeros: as live) and its bytecode."""
        out = ['/* Generated by fnv_shader export: the screen effects of the plugin. */',
               'typedef struct { int reg; float v[4]; } SfxBakedParam;',
               'typedef struct { int order, size; const unsigned char *code; int nparams; const SfxBakedParam *params; }'
               ' SfxBakedEffect;', '']
        for i, (name, e) in enumerate(effects):
            code = (folder / e['bytecode_file']).read_bytes()
            rows = []
            for cname, reg, count, default in e.get('constants') or []:
                flat = (e.get('params') or {}).get(cname) or default or [0.0] * (4 * count)
                flat = list(flat) + [0.0] * (4 * count - len(flat))
                for k in range(count):
                    rows.append('{%d, {%s}}' % (reg + k, self._floats(flat[4 * k:4 * k + 4])))
            out.append('/* %s */' % name)
            out.append('static const unsigned char sfx_baked_code_%d[] = {%s};' % (
                i, ', '.join('0x%02X' % b for b in code)))
            out.append('static const SfxBakedParam sfx_baked_params_%d[] = {%s};' % (
                i, ', '.join(rows) or '{0, {0.0f, 0.0f, 0.0f, 0.0f}}'))
        out.append('static const SfxBakedEffect sfx_baked[] = {')
        for i, (name, e) in enumerate(effects):
            nparams = sum(c[2] for c in e.get('constants') or [])
            out.append('    {%d, %d, sfx_baked_code_%d, %d, sfx_baked_params_%d},' % (
                int(e['order']), (folder / e['bytecode_file']).stat().st_size, i, nparams, i))
        out += ['};', '#define SFX_BAKED_COUNT %d' % len(effects), '']
        return '\n'.join(out)

    @staticmethod
    def _installed_packages(layout):
        """{file name: path}: the user's own packages, the highest-priority holder of each name first, never an
        earlier export of ours (a patched copy must not be patched again)."""
        found = {}
        for root in layout.loose_roots():
            if root.name.lower().startswith('fnvmcp shaders'):
                continue
            folder = root / 'Shaders'
            for path in sorted(folder.glob('shaderpackage*.sdp')) if folder.is_dir() else []:
                found.setdefault(path.name, path)
        return found

    def _patch_packages(self, layout, replacements, dest):
        """(packages written, skipped): each replacement recompiled as the profile of that package's own original and
        checked as in replace; a package where a check fails loses that name, and names it in `skipped`."""
        packages, skipped, cache = [], [], {}
        for file, path in sorted(self._installed_packages(layout).items()):
            try:
                entries = read_sdp(path)
            except ConvError as e:
                skipped.append({'package': file, 'name': None, 'problems': [{'code': 'BAD_PACKAGE', 'text': str(e)}]})
                continue
            codes = {}
            for n, c in entries:
                codes.setdefault(n, c)
            patched = {}
            for e in replacements:
                source = (e['folder'] / e['source_file']).read_text(encoding='utf-8')
                for name in e['names']:
                    if name not in codes:
                        continue
                    original = codes[name]
                    profile = profile_for(original)
                    key = (e['source_file'], profile)
                    if key not in cache:
                        cache[key] = compile_hlsl(source, profile)
                    compiled = cache[key]
                    if not compiled['ok']:
                        problems = [{'code': 'COMPILE', 'text': '%s: line %s: %s' % (
                            profile, er.get('line'), er.get('text'))} for er in compiled['errors'][:3]]
                    else:
                        problems = [p for p in check_replacement(original, compiled['bytecode'])
                                    if not p.get('warning')]
                    if problems:
                        skipped.append({'package': file, 'name': name, 'problems': problems})
                    else:
                        patched[name] = compiled['bytecode']
            if not patched:
                continue
            (dest / 'Shaders').mkdir(parents=True, exist_ok=True)
            write_sdp(dest / 'Shaders' / file, [(n, patched.get(n, c)) for n, c in entries])
            packages.append({'file': _rel(dest / 'Shaders' / file, self.base), 'replaced': sorted(patched)})
        return packages, skipped

    def _save_set(self, name):
        """Copy the live folder (manifest and sources) to sets/<name> when the live one holds anything. The set's
        folder, or None when neither the live one nor an earlier save has anything."""
        target = self.base / 'fnv' / 'shaders' / 'sets' / name
        with locked(self.manifest_path):
            m = self._read()
            if any(m[k] for k in ('effects', 'saved', 'replacements', 'savedReplacements')):
                if target.exists():
                    shutil.rmtree(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(self.folder, target)
        return target if (target / 'manifest.json').exists() else None

    def export(self, set=None, install=False):
        if set is not None and (not isinstance(set, str) or not NAME_RE.match(set) or set == 'live'):
            raise ConvError('BAD_NAME', 'set is 1-40 letters, digits, _ . - (and not "live"); got %r.' % (set,))
        label = set or 'live'
        if install and self.live.game.process(fresh=True):
            raise ConvError('GAME_RUNNING', 'Quit the game first (fnv_game quit): a loaded plugin DLL cannot be '
                            'replaced, and the game reads its shader packages at start.')
        source = self._save_set(set) if set else self.folder
        m = self._read(source) if source else self._read(self.base / 'fnv' / 'shaders' / 'sets' / 'none')
        effects = sorted(((n, e) for n, e in {**m['saved'], **m['effects']}.items()
                          if e.get('enabled', True) and source and (source / e['bytecode_file']).is_file()),
                         key=lambda kv: kv[1]['order'])
        reps = [{**e, 'folder': source} for e in {**m['savedReplacements'], **m['replacements']}.values()
                if source and (source / e['source_file']).is_file()]
        if not effects and not reps:
            raise ConvError('NOTHING_TO_EXPORT', 'Nothing to export%s: no enabled screen effect and no engine shader '
                            'replacement is saved (effect_add or replace first).' % (' in set %r' % set if set else ''))
        dest = self.base / 'fnv' / 'shaders' / 'export' / label
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        notes, plugin = [], None
        if effects:
            if len(effects) > MAX_EFFECTS:
                notes.append('only the first %d of %d effects (by order) fit in the plugin' % (MAX_EFFECTS, len(effects)))
                effects = effects[:MAX_EFFECTS]
            from .fnv_native import c_name
            header = self._baked_header(source, effects)
            (dest / 'sfx_baked.h').write_text(header, encoding='utf-8')
            text = '#define SFX_BAKED 1\n' + header + '\n' + SHADERFX_SOURCE.read_text(encoding='utf-8')
            plugin_name = c_name('fnvmcp_shaders_' + label)
            built = self._plugin_tools().build(plugin_name, source=text, hooks=list(EXPORT_HOOKS))
            dll = Path(built['dll'])
            if dll.is_file():
                shutil.copyfile(dll, dest / dll.name)
            plugin = {'name': plugin_name, 'dll': _rel(dll, self.base)}
        packages, skipped, layout = [], [], None
        if reps:
            layout = self._layout()
            packages, skipped = self._patch_packages(layout, reps, dest)
            if skipped:
                notes.append('%d package(s) or name(s) failed the compatibility check and were left out: GPU classes '
                             'that load those packages keep the original shader' % len(skipped))
        installed = False
        mo2_mod = None
        if install:
            if plugin:
                self._plugin_tools().install(plugin['name'])
                installed = True
            if packages and layout.kind == 'mo2':
                mo2_mod = layout.mods_dir / ('fnvmcp shaders %s' % label)
                if (mo2_mod / 'Shaders').exists():
                    shutil.rmtree(mo2_mod / 'Shaders')
                shutil.copytree(dest / 'Shaders', mo2_mod / 'Shaders')
                enable_mod(layout.mo2_root, layout.profile, mo2_mod.name)
                installed = True
                notes.append('the patched packages are in the MO2 mod "%s" (enabled): start the game again'
                             % mo2_mod.name)
        if packages and mo2_mod is None:
            notes.append('the patched packages are in %s (under the bridge state folder). Copy them into a mod '
                         'manager mod, or into Data\\Shaders of the game after backing up the originals: fnv-mcp never '
                         'copies them into the game folder%s' % (_rel(dest / 'Shaders', self.base),
                                                                  '' if layout.kind == 'mo2' else ' of a direct install'))
        made = (['plugin %s' % plugin['name']] if plugin else []) + (['%d package(s)' % len(packages)] if packages else [])
        out = verdict('Exported %s: %s' % (label, ', '.join(made) or 'nothing was patched'),
                      'Start the game to load it.' if installed else 'Install it with export install=true (game '
                      'closed), then start the game.', plugin=plugin, packages=packages, skipped=skipped,
                      folder=_rel(dest, self.base), installed=installed)
        out['note'] = '; '.join(notes) or None
        return out
