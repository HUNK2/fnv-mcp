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
from pathlib import Path

from .fnv_conv import ConvError, verdict
from .fnv_live import LiveError
from .fnv_shader_hlsl import compile_hlsl, version_of
from .fnv_state import GameIdentity, locked, read_json, write_json

MODULE = 'fnvmcp_shaderfx'
MAX_EFFECTS = 8
FIRST_PARAM_REG = 2
CALL_SITE = '00B55AD6'
NAME_RE = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]{0,39}$')
PS_3_0 = 0xFFFF0300
MENU_NOTE = 'pictures need the game out of menus (effects do not draw in menus)'


def _rel(path, base):
    """A path as text for the answer: relative to the state dir, else just the file name (never a user folder)."""
    p = Path(path)
    try:
        return p.relative_to(base).as_posix()
    except ValueError:
        return p.name


class ShaderTools:
    def __init__(self, live, native):
        self.live = live
        self.native = native
        self._verified = None       # the game key whose manifest effects were checked against the module

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

    def _read(self):
        m = read_json(self.manifest_path, {})
        for key, empty in (('effects', {}), ('removed', {}), ('saved', {}), ('replacements', {})):
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
                changed = bool(m['effects']) or m['game'] != here.as_dict()
                m['effects'] = {}
                m['game'] = here.as_dict()
            elif m['effects'] and self._verified != here.key():
                loaded = self.native.is_loaded(MODULE)
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
        return [[c.name, c.index, c.count] for c in compiled['constants']
                if c.regset == 'float4' and c.index >= FIRST_PARAM_REG]

    @staticmethod
    def _valid(constants):
        return ', '.join('%s (c%d%s)' % (n, r, '-c%d' % (r + k - 1) if k > 1 else '') for n, r, k in constants) \
            or 'none (the shader declares no float4 constants at c2 or above)'

    def _check_params(self, constants, params):
        """{name: flat list of 4 * count floats}, or BAD_PARAM naming the valid names and registers. Nothing has been
        sent to the game yet."""
        out = {}
        by_name = {n: (r, k) for n, r, k in constants}
        for name, value in (params or {}).items():
            if name not in by_name:
                raise ConvError('BAD_PARAM', 'Unknown param %r. Valid params: %s.' % (name, self._valid(constants)),
                                {'valid': [{'name': n, 'register': 'c%d' % r, 'count': k} for n, r, k in constants]})
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

    def _send_params(self, slot, constants, params):
        regs = {n: r for n, r, _ in constants}
        for name, flat in params.items():
            for i in range(len(flat) // 4):
                self._c('sfx_effect_param', slot, regs[name] + i, *flat[4 * i:4 * i + 4])

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
        unset = [n for n, _, _ in constants if n not in values]
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
            out['note'] = 'params not set start at zero: %s' % ', '.join(unset)
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
            self._send_params(entry['slot'], entry['constants'], new)
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
                            'paramNames': [n for n, _, _ in e['constants']], 'source': e['source_file']})
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
