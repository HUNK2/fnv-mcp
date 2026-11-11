"""Plan 2 W6 inspectors: fnv_anim, fnv_audio, fnv_physics, fnv_systems (reads and engine calls; record hooks only).

fnv_anim     state: per body section the current and queued animation group (id decoded: type name from the group
             table 0x011977D8, weapon, move, power armour) and the sequence playing; play: a group or an idle.
fnv_audio    log seconds: every sound the engine creates (InitSoundForm 0x00AE5870, InitSoundPath 0x00AE5A50), with
             the sound form or file and the calling function; play: a sound form, 2D or on a reference.
fnv_physics  controller: an actor's character controller (fall time, fall distance by GetFallDistance); body: the
             chain from a reference's 3D to its Havok body, with the class of each object.
fnv_systems  formula: call a game formula (a catalog of known ones, or any function) with arguments; sweep one argument
             over a range; progression: level, XP, the XP the next level needs; needs: hardcore values and radiation.
"""
import struct
import time

from .fnv_conv import ConvError, verdict
from .fnv_link import hexs, word

ANIM_GROUPS, ANIM_GROUP_SIZE, ANIM_GROUP_COUNT = 0x011977D8, 0x24, 245
GET_ANIMATION = 0x008B70D0
SOUND_CREATORS = ((0x00AE5870, 'sound_form'), (0x00AE5A50, 'sound_path'))
GET_FALL_DISTANCE = 0x00C70550
FORMULAS = {
    'skill_value': {'address': 0x00643C20, 'args': ['base', 'attribute', 'luck'], 'types': ['float'] * 3,
                    'returns': 'float', 'what': 'a skill from its base, the governing attribute and luck'},
    'xp_for_level': {'address': 0x00648B50, 'args': ['level'], 'types': ['int'], 'returns': 'int',
                     'what': 'the experience a level needs'},
    'walk_speed': {'address': 0x00647D10, 'args': ['base'], 'types': ['float'], 'returns': 'float',
                   'what': 'walk speed'},
    'run_speed': {'address': 0x00647F00, 'args': ['base'], 'types': ['float'], 'returns': 'float',
                  'what': 'run speed'},
    'item_value_by_condition': {'address': 0x00647C00, 'args': ['value', 'condition'], 'types': ['float', 'float'],
                                'returns': 'float', 'what': 'an item value scaled by its condition'},
    'force_lock_chance': {'address': 0x00648D30, 'args': ['skill', 'lock_level'], 'types': ['float', 'float'],
                          'returns': 'float', 'what': 'the chance to force a lock'},
}


def _as_int(value):
    if value is None:
        return 0
    if isinstance(value, str):
        return int(value, 16) if value.lower().startswith('0x') else int(value)
    return int(value)


def _decode_group(gid):
    return {'id': '0x%04X' % gid, 'type': gid & 0xFF, 'weapon': (gid >> 8) & 0xF, 'move': (gid >> 12) & 0x7,
            'powerArmour': bool(gid >> 15)}


class AnimTools:
    def __init__(self, live):
        self.live = live
        self._names = None

    def _group_names(self):
        if self._names is None:
            raw = self.live.reads([(ANIM_GROUPS, ANIM_GROUP_SIZE * ANIM_GROUP_COUNT)])[0] or b''
            ptrs = [struct.unpack_from('<I', raw, i * ANIM_GROUP_SIZE)[0] for i in range(len(raw) // ANIM_GROUP_SIZE)]
            texts = self.live.reads([(p, 48) for p in ptrs]) if ptrs else []
            self._names = [(t.split(b'\0', 1)[0].decode('latin-1') if t else None) for t in texts]
        return self._names

    def state(self, actor):
        self.live.ready()
        r = self.live.resolve(actor)
        out = self.live.engine_call('0x%08X' % GET_ANIMATION, '0x%08X' % r['ptr'], [], 'ptr', 'thiscall', True)
        anim = _as_int(out.get('result'))
        if not anim:
            return verdict('%s has no animation object (not loaded, or no 3D)' % r['formId'], None, actor=r['formId'])
        raw = self.live.reads([(anim + 0x4C, 16), (anim + 0x9C, 16), (anim + 0xE0, 32)])
        names = self._group_names()
        current = struct.unpack('<8H', raw[0]) if raw[0] else ()
        queued = struct.unpack('<8H', raw[1]) if raw[1] else ()
        seqs = struct.unpack('<8I', raw[2]) if raw[2] else ()
        seq_names = {}
        live_seqs = [s for s in seqs if s]
        if live_seqs:
            name_ptrs = self.live.u32s([s + 0x08 for s in live_seqs])
            texts = self.live.reads([(p, 80) for p in name_ptrs if p])
            it = iter(texts)
            for s, p in zip(live_seqs, name_ptrs):
                t = next(it) if p else None
                seq_names[s] = t.split(b'\0', 1)[0].decode('latin-1') if t else None
        sections = []
        for i in range(8):
            gid = current[i] if i < len(current) else 0xFFFF
            if gid in (0, 0xFFFF) and not (i < len(seqs) and seqs[i]):
                continue
            g = _decode_group(gid)
            g['name'] = names[g['type']] if g['type'] < len(names) else None
            q = queued[i] if i < len(queued) else 0xFFFF
            row = {'section': i, 'current': g, 'sequence': seq_names.get(seqs[i]) if i < len(seqs) else None}
            if q not in (0, 0xFFFF) and (q & 0xFF) != 0xFF:
                qg = _decode_group(q)
                qg['name'] = names[qg['type']] if qg['type'] < len(names) else None
                row['queued'] = qg
            sections.append(row)
        return verdict('%s: %d animating sections' % (r['formId'], len(sections)),
                       'Animation object from Actor::GetAnimation 0x008B70D0 (+0x4C current groups, +0x9C queued, '
                       '+0xE0 sequences); group names from the table 0x011977D8.', actor=r['formId'],
                       animation='0x%08X' % anim, sections=sections)

    def play(self, actor, group=None, idle=None):
        self.live.in_game()
        r = self.live.resolve(actor)
        if not group and not idle:
            raise ConvError('BAD_ARGUMENTS', 'play needs group (a name such as Idle, JumpStart) or idle (an IDLE form).')
        line = 'PlayGroup %s 1' % group if group else 'PlayIdle %s' % idle
        self.live.changing('anim play')
        out = self.live.console(line, r['ptr'])
        return verdict('%s on %s: %s' % (line, r['formId'], 'accepted' if out['accepted'] else 'refused'),
                       'Check with fnv_anim state.', accepted=out['accepted'], output=out.get('output'))

    def run(self, action, **a):
        if not a.get('ref'):
            raise ConvError('BAD_ARGUMENTS', 'ref (an actor) is needed.')
        if action == 'state':
            return self.state(a['ref'])
        if action == 'play':
            return self.play(a['ref'], a.get('group'), a.get('idle'))
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: state, play.' % action)


class AudioTools:
    def __init__(self, live, deep=None):
        self.live = live
        self.deep = deep

    def log(self, seconds=3.0, limit=200):
        self.live.ready()
        from .fnv_deep import decode_prologue
        hooks = []
        try:
            for address, label in SOUND_CREATORS:
                raw = self.live.reads([(address, 24)])[0]
                stolen, relocs = decode_prologue(raw)
                h = self.live.link.one('hook', word(address), word(stolen), ','.join('%x' % r for r in relocs) or '-',
                                       word(2), '0', '0', '0', word(5), '0', '0', '0', '0', hexs(label))
                if not h.get('ok'):
                    raise ConvError('HOOK_REFUSED', 'The sound hook was refused (%s).' % h.get('error'), h)
                hooks.append((label, h['hook']))
            time.sleep(float(seconds))
            records = []
            for label, h in hooks:
                _, got, _ = self.deep.records(h, since=0)
                records += [(label, r) for r in got]
        finally:
            for _, h in hooks:
                self.live.link.one('unhook', word(h['id']))
        words = {w for _, r in records for w in r[12:17] if 0x00400000 <= w < 0x7FFF0000}
        forms, strings = self._forms_and_strings(words)
        engine = self.live.engine()
        rows, callers = [], {}
        for label, r in records[:limit]:
            caller = r[5]
            if caller not in callers:
                callers[caller] = engine.symbolize(caller, None, return_address=True).get('name')
            args = r[12:17]
            sound = next((forms[w] for w in args if w in forms), None)
            path = next((strings[w] for w in args if w in strings), None)
            rows.append({'kind': label, 'sound': sound, 'file': path, 'caller': callers[caller],
                         'callerAddress': '0x%08X' % caller, 'thread': r[2]})
        return verdict('%d sounds created in %.1f s' % (len(records), float(seconds)),
                       'Record hooks on InitSoundForm 0x00AE5870 and InitSoundPath 0x00AE5A50 (removed after); sound '
                       'is the form among the arguments, file a path string among them; caller says who asked.',
                       sounds=rows)

    def _forms_and_strings(self, words):
        words = sorted(words)
        forms, strings = {}, {}
        raws = self.live.reads([(w, 64) for w in words]) if words else []
        for w, raw in zip(words, raws):
            if not raw or len(raw) < 16:
                continue
            vtable, _, _, fid = struct.unpack_from('<IIII', raw, 0)
            if 0x01000000 <= vtable < 0x01300000 and fid:
                forms[w] = '%08X' % fid
                continue
            text = raw.split(b'\0', 1)[0]
            if len(text) >= 5 and all(32 <= b < 127 for b in text) and (b'\\' in text or b'.' in text):
                strings[w] = text.decode('latin-1')
        return forms, strings

    def play(self, sound, ref=None):
        self.live.in_game()
        if not sound:
            raise ConvError('BAD_ARGUMENTS', 'play needs sound (a SOUN form).')
        self.live.changing('audio play')
        if ref:
            r = self.live.resolve(ref)
            out = self.live.console('PlaySound3D %s' % sound, r['ptr'])
        else:
            out = self.live.console('PlaySound %s' % sound)
        return verdict('Played %s%s: %s' % (sound, ' on %s' % ref if ref else '', 'accepted' if out['accepted']
                                             else 'refused'), None, accepted=out['accepted'], output=out.get('output'))

    def run(self, action, **a):
        if action == 'log':
            return self.log(a.get('seconds') or 3.0)
        if action == 'play':
            return self.play(a.get('sound'), a.get('ref'))
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: log, play.' % action)


class PhysicsTools:
    def __init__(self, live):
        self.live = live

    def controller(self, actor):
        self.live.ready()
        r = self.live.resolve(actor)
        process = self.live.u32s([r['ptr'] + 0x68])[0]
        ctrl = self.live.u32s([process + 0x138])[0] if process else 0
        if not ctrl:
            return verdict('%s has no character controller (low process or no 3D)' % r['formId'], None,
                           actor=r['formId'])
        raw = self.live.reads([(ctrl + 0x544, 8)])[0]
        fall_start, fall_time = struct.unpack('<ff', raw) if raw else (None, None)
        dist = self.live.engine_call('0x%08X' % GET_FALL_DISTANCE, '0x%08X' % ctrl, [], 'float', 'thiscall', True)
        cls = (self.live.rtti([ctrl]).get(ctrl) or {}).get('class')
        return verdict('%s: falling for %.2f s, fall distance %s' % (r['formId'], fall_time or 0.0,
                                                                     dist.get('result')),
                       'The character controller at process +0x138 (fall start +0x544, fall time +0x548); distance '
                       'from GetFallDistance 0x00C70550.', actor=r['formId'], controller='0x%08X' % ctrl,
                       controllerClass=cls, fallStartZ=fall_start, fallTime=fall_time,
                       fallDistance=dist.get('result'))

    def body(self, ref):
        self.live.ready()
        r = self.live.resolve(ref)
        loaded = self.live.u32s([r['ptr'] + 0x64])[0]
        root = self.live.u32s([loaded + 0x14])[0] if loaded else 0
        chain = []
        node = root
        for name, off in (('collisionObject', 0x1C), ('worldObject', 0x10), ('havokBody', 0x08)):
            node = self.live.u32s([node + off])[0] if node else 0
            chain.append((name, node))
        ptrs = [root] + [p for _, p in chain if p]
        classes = self.live.rtti(ptrs) if ptrs else {}
        out = [{'what': '3D root', 'ptr': '0x%08X' % root if root else None,
                'class': (classes.get(root) or {}).get('class') if root else None}]
        for name, p in chain:
            out.append({'what': name, 'ptr': '0x%08X' % p if p else None,
                        'class': (classes.get(p) or {}).get('class') if p else None})
        has_body = bool(chain[-1][1])
        return verdict('%s %s a Havok body' % (r['formId'], 'has' if has_body else 'has no'),
                       '3D root +0x1C collision object, +0x10 world object, +0x08 hkpRigidBody (classes from RTTI).',
                       ref=r['formId'], chain=out)

    def run(self, action, **a):
        if not a.get('ref'):
            raise ConvError('BAD_ARGUMENTS', 'ref is needed.')
        if action == 'controller':
            return self.controller(a['ref'])
        if action == 'body':
            return self.body(a['ref'])
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: controller, body.' % action)


class SystemsTools:
    def __init__(self, live):
        self.live = live

    def formulas(self):
        return verdict('%d formulas in the catalog' % len(FORMULAS), 'Any engine function can also be called by '
                       'name or address with formula.', formulas=[
                           {'name': k, 'address': '0x%08X' % v['address'], 'args': v['args'], 'returns': v['returns'],
                            'what': v['what']} for k, v in FORMULAS.items()])

    def _call(self, name, args):
        spec = FORMULAS.get(name)
        if spec:
            if len(args) != len(spec['args']):
                raise ConvError('BAD_ARGUMENTS', '%s takes %s.' % (name, ', '.join(spec['args'])))
            typed = [{'type': t, 'value': v} for t, v in zip(spec['types'], args)]
            out = self.live.engine_call('0x%08X' % spec['address'], None, typed, spec['returns'], 'cdecl', True)
        else:
            typed = [a if isinstance(a, dict) else {'type': 'float' if isinstance(a, float) else 'int', 'value': a}
                     for a in args]
            out = self.live.engine_call(name, None, typed, 'float', None, True)
        return out.get('result')

    def formula(self, name, args=None):
        self.live.ready()
        if not name:
            raise ConvError('BAD_ARGUMENTS', 'formula needs name (see formulas) and args.')
        value = self._call(name, list(args or []))
        return verdict('%s(%s) = %s' % (name, ', '.join(str(a) for a in args or []), value),
                       'A guarded engine call on the main thread with these inputs; the game settings it reads are '
                       'the live ones.', result=value)

    def sweep(self, name, args, index, start, stop, steps=10):
        self.live.ready()
        args = list(args or [])
        rows = []
        for k in range(int(steps) + 1):
            v = start + (stop - start) * k / float(steps)
            spec = FORMULAS.get(name)
            if spec and spec['types'][index] == 'int':
                v = int(round(v))
            call = list(args)
            call[index] = v
            rows.append({'input': v, 'result': self._call(name, call)})
        return verdict('%s over argument %d from %s to %s' % (name, index, start, stop), None, rows=rows)

    def progression(self):
        self.live.ready()
        level, xp = self.live.values(['player.GetLevel', 'player.GetAV XP'])
        nxt = self._call('xp_for_level', [int(level or 1) + 1]) if level is not None else None
        return verdict('Level %s, %s XP; level %s at %s XP' % (level, xp, (level or 0) + 1, nxt), None, level=level,
                       xp=xp, nextLevelXp=nxt)

    def needs(self):
        self.live.ready()
        # The game's own actor value name is SleepDeprevation (its spelling); hardcore is a flag at player +0x7BC.
        names = ['player.GetAV Dehydration', 'player.GetAV Hunger', 'player.GetAV SleepDeprevation',
                 'player.GetAV RadiationRads']
        vals = self.live.values(names)
        player = self.live.resolve('player')
        flag = self.live.reads([(player['ptr'] + 0x7BC, 1)])[0]
        hardcore = bool(flag and flag[0])
        return verdict('Hardcore %s; thirst %s, hunger %s, sleep %s, rads %s' % ((hardcore,) + tuple(vals)), None,
                       hardcore=hardcore, dehydration=vals[0], hunger=vals[1], sleepDeprivation=vals[2],
                       rads=vals[3])

    def run(self, action, **a):
        if action == 'formulas':
            return self.formulas()
        if action == 'formula':
            return self.formula(a.get('name'), a.get('args'))
        if action == 'sweep':
            if a.get('index') is None or a.get('start') is None or a.get('stop') is None:
                raise ConvError('BAD_ARGUMENTS', 'sweep needs name, args, index, start, stop.')
            return self.sweep(a.get('name'), a.get('args'), int(a['index']), float(a['start']), float(a['stop']),
                              a.get('steps') or 10)
        if action == 'progression':
            return self.progression()
        if action == 'needs':
            return self.needs()
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: formulas, formula, sweep, progression, needs.'
                        % action)
