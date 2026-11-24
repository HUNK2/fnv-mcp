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
import math
import struct
import time

from .fnv_conv import ConvError, verdict
from .fnv_link import hexs, word

ANIM_GROUPS, ANIM_GROUP_SIZE, ANIM_GROUP_COUNT = 0x011977D8, 0x24, 245
GET_ANIMATION = 0x008B70D0
SOUND_CREATORS = ((0x00AE5870, 'sound_form'), (0x00AE5A50, 'sound_path'))
GET_FALL_DISTANCE = 0x00C70550
IDLE_MANAGER, FIND_IDLE, IDLE_ARRAY, IDLE_VALID = 0x011CB6A0, 0x00600950, 0x00600690, 0x005FF430
IDLE_SEARCH = 0x005FF2E0
GENERAL_SUBTITLES = 0x011D8928          # Setting bGeneralSubtitles:GamePlay (value at +4)
REF_MODEL_PATH, BUILD_IDLE_PATH, IDLE_DIR_ARRAY = 0x005715D0, 0x006007F0, 0x00600560
INIT_WORN, SET_ANIM_ACTION = 0x00606050, 0x008A73E0
BIPED_SLOTS = ('head', 'hair', 'upper body', 'left hand', 'right hand', 'weapon', 'pip-boy', 'backpack', 'necklace',
               'headband', 'hat', 'eyeglasses', 'nose ring', 'earrings', 'mask', 'choker', 'mouth object',
               'body addon 1', 'body addon 2', 'body addon 3')
ANIM_ACTIONS = {-1: 'none', 0: 'equip', 1: 'unequip', 2: 'attack', 3: 'attack eject', 4: 'follow through',
                5: 'throw', 6: 'throw attach', 7: 'block', 8: 'recoil', 9: 'reload', 10: 'stagger', 11: 'dodge',
                12: 'lower body', 13: 'special idle', 14: 'script anim', 15: 'reload loop start',
                16: 'reload loop end', 17: 'reload loop'}
AUDIO_MANAGER, SOUND_STOP, CAN_PLAY_AT = 0x011F6EF0, 0x00ADB610, 0x0082ECA0
MUSIC_BLOCK, PLAY_MEDIA, FADE_OUT_MEDIA = 0x011DD0F0, 0x008300C0, 0x00830680
RADIO_CURRENT, RADIO_ENABLED, RADIO_STATIONS, SET_RADIO = 0x011DD42C, 0x011DD434, 0x011DD554, 0x008324E0
SHOW_SUBTITLE = 0x00705210
SOUND_FLAGS = ((0x1, '2D'), (0x2, '3D'), (0x4, 'voice'), (0x8, 'footsteps'), (0x10, 'loop'),
               (0x20, 'not dialogue'), (0x800, 'music'), (0x10000, 'no cache'), (0x100000, 'radio'),
               (0x800000, 'music category'), (0x8000000, '2D radius'))
SOUND_STATES = ((0x10, 'stopped'), (0x20, 'playing'), (0x40, 'paused'), (0x400, 'muted'), (0x20000, 'looping'),
                (0x80000, 'decaying'))
MUSIC_FLAGS = ((0x02, 'stopped'), (0x08, 'fading'), (0x10, 'play'), (0x20, 'loop'))
MEDIA_TYPES = {6: 'special', 7: 'radio', 8: 'main menu', 9: 'start menu'}   # others: location music, by number


PERK_DATA, PRICE_MULTIPLIERS, ZONE_LEVEL = 0x38, 0x00649050, 0x00526190
EXTRA_BY_TYPE, ENTRY_VALUE, BASE_VALUE = 0x00410220, 0x004BD400, 0x0048E8A0


def _menu_round(value):
    """The barter menu's rounding (InventoryStackRecord::RoundToNearestStep 0x004BD510): half up, to a whole number
    above 1, to a tenth otherwise."""
    step = 1.0 if abs(value) > 1.0 else 0.1
    ratio = value / step
    whole = int(ratio)
    out = (whole + (0 if ratio - whole < 0.5 else 1)) * step
    return int(out) if step == 1.0 else round(out, 1)


def _flag_names(value, table):
    return [name for bit, name in table if value & bit]


HAVOK_TO_GAME, SET_LINEAR_VELOCITY = 6.9991255, 0x005615D0
# tool gaps 5 (research notes tool-gaps-research-gaps-4-5.md): bhkRigidBody::SetAngularVelocity (NiPoint3*, rad/s,
# no unit change; the save loader calls it after SetLinearVelocity), hkpEntity::activate (wakes a sleeping body)
SET_ANGULAR_VELOCITY, HKP_ACTIVATE = 0x00561630, 0x00C9C1D0
# a push on an actor's character controller: the engine's own knockback (Actor hit code, actor.cpp) calls
# bhkCharacterController::UpdateMaxVelocity 0x00C6D4D0 (NiPoint3* game units, float seconds): velocity = vector /
# seconds in Havok units at +0x500 and the seconds at +0x524, kept only if faster than the push already running
PUSH_IMPULSE, PUSH_VELOCITY, PUSH_TIME = 0x00C6D4D0, 0x500, 0x524
RAISE_SCRIPT_EVENT, TRIGGER_EVENTS, PROCESS_LISTS = 0x005AC750, 0x70000000, 0x011E0E80
CONTROLLER_STATES = ('on ground', 'jumping', 'in air', 'climbing', 'flying', 'swimming', 'projectile')
COLLISION_LAYERS = ('unidentified', 'static', 'anim static', 'transparent', 'clutter', 'weapon', 'projectile',
                    'spell', 'biped', 'trees', 'props', 'water', 'trigger', 'terrain', 'trap', 'noncollidable',
                    'cloud trap', 'ground', 'portal', 'debris small', 'debris large', 'acoustic space', 'actor zone',
                    'projectile zone', 'gas trap', 'shell casing', 'transparent small', 'invisible wall',
                    'transparent small anim', 'dead biped', 'char controller', 'avoid box', 'collision box',
                    'camera sphere', 'door detection', 'camera', 'item picker', 'line of sight', 'pathing pick',
                    'custom pick 1', 'custom pick 2', 'spell explosion', 'dropping pick')
PHONEMES = ('Aah', 'BigAah', 'BMP', 'ChJSh', 'DST', 'Eee', 'Eh', 'FV', 'I', 'K', 'N', 'Oh', 'OohQ', 'R', 'Th', 'W')
FACE_BLOCKS = (('expression', 0x10, ()), ('animExpression', 0x24, ()), ('animPhoneme', 0x4C, PHONEMES),
               ('animCustomMorph', 0x60, ()), ('modifier', 0x94, ()), ('phoneme', 0xC8, PHONEMES),
               ('customMorph', 0xFC, ()))
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


def record_hooks(live, deep, targets, seconds):
    """Record hooks on (address, label) pairs for some seconds, then removed; returns [(label, record words)].

    Record words: [0] seq, [2] thread, [3] ecx, [4] edx, [5] caller, [12..19] stack arguments, [22] frame."""
    from .fnv_deep import decode_prologue
    hooks = []
    try:
        for address, label in targets:
            raw = live.reads([(address, 24)])[0]
            stolen, relocs = decode_prologue(raw)
            h = live.link.one('hook', word(address), word(stolen), ','.join('%x' % r for r in relocs) or '-',
                              word(2), '0', '0', '0', word(5), '0', '0', '0', '0', hexs(label))
            if not h.get('ok'):
                raise ConvError('HOOK_REFUSED', 'The %s hook was refused (%s).' % (label, h.get('error')), h)
            hooks.append((label, h['hook']))
        time.sleep(float(seconds))
        records = []
        for label, h in hooks:
            _, got, _ = deep.records(h, since=0)
            records += [(label, r) for r in got]
    finally:
        for _, h in hooks:
            live.link.one('unhook', word(h['id']))
    records.sort(key=lambda lr: (lr[1][22], lr[1][0]))
    return records


def _text(raw):
    return raw.split(b'\0', 1)[0].decode('latin-1') if raw else None


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

    def _animation(self, r):
        out = self.live.engine_call('0x%08X' % GET_ANIMATION, '0x%08X' % r['ptr'], [], 'ptr', 'thiscall', True)
        return _as_int(out.get('result'))

    def groups(self, actor, limit=300):
        """Every group the actor has a sequence for (the map at animation +0xDC: group id -> BSAnimGroupSequence)."""
        self.live.ready()
        r = self.live.resolve(actor)
        anim = self._animation(r)
        if not anim:
            return verdict('%s has no animation object (not loaded, or no 3D)' % r['formId'], None, actor=r['formId'])
        head = self.live.reads([(anim + 0xDC, 4)])[0]
        table = struct.unpack('<I', head)[0] if head else 0
        if not table:
            return verdict('%s: no sequence map' % r['formId'], None, actor=r['formId'])
        buckets, bucket_array, count = struct.unpack('<III', self.live.reads([(table + 4, 12)])[0])
        if not bucket_array or not 0 < buckets <= 4096:
            return verdict('%s: the sequence map is empty' % r['formId'], None, actor=r['formId'])
        bucket_ptrs = struct.unpack('<%dI' % buckets, self.live.reads([(bucket_array, buckets * 4)])[0])
        items, frontier, seen = [], [p for p in bucket_ptrs if p], set()
        while frontier and len(items) < 4 * limit:
            raws = self.live.reads([(p, 12) for p in frontier])
            nxt = []
            for p, raw in zip(frontier, raws):
                if not raw or p in seen:
                    continue
                seen.add(p)
                link, key, value = struct.unpack('<III', raw)
                items.append((key & 0xFFFF, value))
                if link:
                    nxt.append(link)
            frontier = nxt
        names = self._group_names()
        # Values are AnimSequenceBase: a single one holds its BSAnimGroupSequence at +4 (a multiple one a list there).
        seqs = [v for _, v in items if v]
        inner = self.live.u32s([s + 4 for s in seqs]) if seqs else []
        name_ptrs = self.live.u32s([p + 0x08 for p in inner]) if inner else []
        texts = self.live.reads([(p, 96) for p in name_ptrs]) if name_ptrs else []
        file_of = {}
        for s, t in zip(seqs, texts):
            name = _text(t)
            file_of[s] = name if name and name.lower().endswith('.kf') else '(several sequences)'
        rows = []
        for gid, seq in sorted(items)[:limit]:
            g = _decode_group(gid)
            g['name'] = names[g['type']] if g['type'] < len(names) else None
            g['file'] = file_of.get(seq)
            rows.append(g)
        return verdict('%s has sequences for %d groups' % (r['formId'], len(items)),
                       'The group -> sequence map at the animation object +0xDC (NiTPointerMap, %d buckets); each '
                       'sequence names its KF file.' % buckets, actor=r['formId'], count=count, groups=rows)

    def idle_tree(self, actor, target=None, explain=6, limit=80):
        """The idle the engine picks now and the walk that leads there. The answer is the engine's own
        IdleAnimationManager::FindIdleFormForObject 0x00600950 (what HighProcess::StartIdleAnimation 0x008DAB40 asks):
        the idle folder of the reference's model, each idle asked in the array's order with
        TESIdleForm::FindIdleFormForObject 0x005FF2E0 and the actor's replay delays (animation +0x134), first answer
        wins. The walk repeats that per idle so each node shows whether it passes (IsValidForReferences 0x005FF430),
        is held back by a replay delay, and which conditions fail (fnv_condition_eval)."""
        self.live.in_game()
        r = self.live.resolve(actor)
        t = self.live.resolve(target) if target else None
        manager = self.live.u32s([IDLE_MANAGER])[0]
        if not manager:
            raise ConvError('NOT_READY', 'The idle manager is not created yet.')
        args = [{'type': 'int', 'value': r['ptr']}, {'type': 'int', 'value': t['ptr'] if t else 0}]
        anim = self._animation(r)
        delays_list = anim + 0x134 if anim else 0
        delayed = set()
        node, guard = delays_list, 0
        while node and guard < 256:                     # BSSimpleList {item, next}; item = {idle, time...}
            item, nxt = self.live.u32s([node, node + 4])
            if item:
                delayed.add(self.live.u32s([item])[0])
            node, guard = nxt, guard + 1
        engine = _as_int(self.live.engine_call('0x%08X' % FIND_IDLE, '0x%08X' % manager, args, 'ptr', 'thiscall',
                                               True).get('result'))
        # The same idle folder as the engine: the reference's model path (TESObjectREFR::GetModelFilePath
        # 0x005715D0) through BuildIdlePath 0x006007F0, then the folder's array (GetFormArrayForIdleDir 0x00600560).
        model = _as_int(self.live.engine_call('0x%08X' % REF_MODEL_PATH, '0x%08X' % r['ptr'], [], 'ptr', 'thiscall',
                                              True).get('result'))
        folder, roots = None, 0
        if model:
            buf = self.live.scratch(b'\0' * 264)
            self.live.engine_call('0x%08X' % BUILD_IDLE_PATH, '0x%08X' % manager,
                                  [{'type': 'int', 'value': model}, {'type': 'int', 'value': buf}], 'void', 'thiscall',
                                  True)
            folder = _text(self.live.reads([(buf, 264)])[0]) or None
            if folder:
                roots = _as_int(self.live.engine_call('0x%08X' % IDLE_DIR_ARRAY, '0x%08X' % manager,
                                                      [{'type': 'int', 'value': buf}], 'ptr', 'thiscall',
                                                      True).get('result'))
        rows, failing = [], []

        def members(arr):
            if not arr:
                return []
            head = self.live.reads([(arr + 4, 12)])[0]
            if not head:
                return []
            data, _, size = struct.unpack('<III', head)
            if not data or not 0 < size < 4096:
                return []
            return [p for p in struct.unpack('<%dI' % size, self.live.reads([(data, size * 4)])[0]) if p]

        def describe(idle, depth):
            ok = _as_int(self.live.engine_call('0x%08X' % IDLE_VALID, '0x%08X' % idle,
                                               args + [{'type': 'int', 'value': 0}], 'int', 'thiscall',
                                               True).get('result')) & 0xFF
            fid, path_ptr, children = self.live.u32s([idle + 0x0C, idle + 0x1C, idle + 0x40])
            path = _text(self.live.reads([(path_ptr, 120)])[0]) if path_ptr else None
            row = {'depth': depth, 'idle': '%08X' % fid, 'file': path, 'passes': bool(ok)}
            if idle in delayed:
                row['replayDelayed'] = True
            rows.append(row)
            if not ok:
                failing.append(row)
            return ok, children

        walked, asked = 0, 0
        for root in members(roots):       # every entry, in the array's order, as the manager loops
            if len(rows) >= limit:
                break
            asked += 1
            found = _as_int(self.live.engine_call('0x%08X' % IDLE_SEARCH, '0x%08X' % root,
                                                  args + [{'type': 'int', 'value': delays_list}], 'ptr', 'thiscall',
                                                  True).get('result'))
            ok, children = describe(root, 0)
            if found:
                walked = found
                level, depth = children, 1        # show the path into the answering root
                while level and depth < 8 and len(rows) < limit:
                    nxt = None
                    for child in members(level):
                        c_ok, c_children = describe(child, depth)
                        if c_ok and child not in delayed:
                            nxt = c_children
                            break
                    level, depth = nxt, depth + 1
                break
        for row in failing[:int(explain)]:
            try:
                ev = self.live.agent.condition_eval(row['idle'], r['formId'], t['formId'] if t else None,
                                                    only_failed=True)
                row['failing'] = [{'index': c['index'], 'function': c.get('function'), 'value': c.get('value'),
                                   'compare': c.get('compare')} for lst in ev['lists']
                                  for c in lst['conditions'] if not c.get('passed')][:4]
            except ConvError as exc:
                row['failing'] = exc.code
        ids = self.live.form_ids([x for x in (engine, walked) if x])
        names = dict(zip([x for x in (engine, walked) if x], [i['formId'] for i in ids]))
        win_id, walk_id = names.get(engine), names.get(walked)
        for row in rows:
            row['chosen'] = row['idle'] == win_id
        out = dict(actor=r['formId'], chosen=win_id, folder=folder, replayDelays=len(delayed), rootsAsked=asked,
                   walk=rows)
        if walk_id != win_id:
            # The engine drops an answer that is a group with no file of its own (its last check).
            out['walkAnswer'] = walk_id
            if walk_id and not engine:
                out['walkAnswerNote'] = 'a group with no animation file of its own: the engine drops it (no idle)'
        return verdict('%s would play %s' % (r['formId'], win_id or 'no idle'),
                       'The engine\'s own IdleAnimationManager::FindIdleFormForObject 0x00600950 (with the actor\'s '
                       'replay delays). The game asks it only when no special idle plays and the package allows it '
                       '(HighProcess::StartIdleAnimation 0x008DAB40): a sitting or scripted actor keeps its own idle. '
                       'walk: the idle folder in order, the path into the answer; failing: the conditions that fail '
                       '(fnv_condition_eval).', **out)

    def biped(self, actor, rebuild=False):
        """The 20 equipment slots of the actor's BipedAnim (Character +0x1B4, slots of 16 bytes at +0x2C)."""
        self.live.ready()
        r = self.live.resolve(actor)
        if rebuild:
            self.live.changing('anim biped rebuild')
        biped = self.live.u32s([r['ptr'] + 0x1B4])[0]
        if not biped:
            return verdict('%s has no biped (not a loaded character)' % r['formId'], None, actor=r['formId'])
        if rebuild:
            base = self.live.u32s([r['ptr'] + 0x20])[0]
            self.live.engine_call('0x%08X' % INIT_WORN, '0x%08X' % base, [{'type': 'int', 'value': r['ptr']},
                                                                          {'type': 'int', 'value': biped}],
                                  'int', 'thiscall', True)
        raw = self.live.reads([(biped + 0x2C, 20 * 16)])[0]
        slots = [struct.unpack_from('<IIIB', raw, i * 16) for i in range(20)]
        forms = [s[0] for s in slots if s[0]]
        heads = {p: h for p, h in zip(forms, self.live.form_ids(forms))} if forms else {}
        model_ptrs = self.live.u32s([s[1] + 4 for s in slots if s[1]]) if any(s[1] for s in slots) else []
        model_texts = iter(self.live.reads([(p, 160) for p in model_ptrs]) if model_ptrs else [])
        node_names = self.live.u32s([s[2] + 8 for s in slots if s[2]]) if any(s[2] for s in slots) else []
        node_texts = iter(self.live.reads([(p, 64) for p in node_names]) if node_names else [])
        rows = []
        for i, (form, model, node, skinned) in enumerate(slots):
            m = _text(next(model_texts)) if model else None
            n = _text(next(node_texts)) if node else None
            if not form and not node:
                continue
            rows.append({'slot': i, 'name': BIPED_SLOTS[i], 'item': (heads.get(form) or {}).get('formId'),
                         'model': m, 'node': n, 'skinned': bool(skinned)})
        return verdict('%s wears %d slots%s' % (r['formId'], len(rows), ' (rebuilt)' if rebuild else ''),
                       'BipedAnim at Character +0x1B4: slot = item, TESModel, node, skinned%s.' % (
                           '; rebuilt by TESNPC::InitWornObjects 0x00606050' if rebuild else ''),
                       actor=r['formId'], biped='0x%08X' % biped, slots=rows)

    def face(self, actor):
        """Face animation values (BSFaceGenAnimationData at process +0x178): each keyframe block's non-zero values."""
        self.live.ready()
        r = self.live.resolve(actor)
        process = self.live.u32s([r['ptr'] + 0x68])[0]
        face = self.live.u32s([process + 0x178])[0] if process else 0
        if not face:
            return verdict('%s has no face animation data (no high process or no head)' % r['formId'], None,
                           actor=r['formId'])
        blocks = []
        raw = self.live.reads([(face + off + 0x0C, 8) for _, off, _ in FACE_BLOCKS])
        for (label, off, names), head in zip(FACE_BLOCKS, raw):
            values_ptr, count = struct.unpack('<II', head) if head else (0, 0)
            if not values_ptr or not 0 < count <= 64:
                continue
            vals = struct.unpack('<%df' % count, self.live.reads([(values_ptr, count * 4)])[0])
            nz = {(names[i] if i < len(names) else str(i)): round(v, 3) for i, v in enumerate(vals) if abs(v) > 1e-4}
            blocks.append({'block': label, 'offset': '+0x%X' % off, 'count': count, 'values': nz})
        lip = self.live.u32s([process + 0x1A0])[0]
        return verdict('%s: %d face blocks with values' % (r['formId'], sum(1 for b in blocks if b['values'])),
                       'BSFaceGenAnimationData at process +0x178; keyframe blocks (float* +0xC, count +0x10). Which '
                       'block is the final applied one is not established: read them together.', actor=r['formId'],
                       blocks=blocks, lipSync='0x%08X' % lip if lip else None)

    def events(self, actor, seconds=3.0, limit=200):
        """Animation actions the actor starts (Actor::SetAnimAction 0x008A73E0), with frame and sequence file."""
        self.live.ready()
        r = self.live.resolve(actor)
        recs = record_hooks(self.live, self.live.deep, [(SET_ANIM_ACTION, 'anim_action')], seconds)
        mine = [rec for _, rec in recs if rec[3] == r['ptr']]
        seqs = sorted({rec[13] for rec in mine if rec[13]})
        name_ptrs = self.live.u32s([s + 0x08 for s in seqs]) if seqs else []
        texts = self.live.reads([(p, 96) for p in name_ptrs]) if name_ptrs else []
        file_of = {s: _text(t) for s, t in zip(seqs, texts)}
        rows = []
        for rec in mine[:limit]:
            action = struct.unpack('<i', struct.pack('<I', rec[12]))[0]
            rows.append({'frame': rec[22], 'action': ANIM_ACTIONS.get(action, action),
                         'sequence': file_of.get(rec[13])})
        return verdict('%s: %d animation actions in %.1f s (%d by all actors)' % (r['formId'], len(mine),
                                                                               float(seconds), len(recs)),
                       'Record hook on Actor::SetAnimAction 0x008A73E0 (removed after), filtered to this actor.',
                       actor=r['formId'], events=rows)

    def run(self, action, **a):
        if not a.get('ref'):
            raise ConvError('BAD_ARGUMENTS', 'ref (an actor) is needed.')
        if action == 'state':
            return self.state(a['ref'])
        if action == 'play':
            return self.play(a['ref'], a.get('group'), a.get('idle'))
        if action == 'groups':
            return self.groups(a['ref'])
        if action == 'idle_tree':
            return self.idle_tree(a['ref'], a.get('target'))
        if action == 'biped':
            return self.biped(a['ref'], bool(a.get('rebuild')))
        if action == 'face':
            return self.face(a['ref'])
        if action == 'events':
            return self.events(a['ref'], a.get('seconds') or 3.0)
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: state, play, groups, idle_tree, biped, face, '
                        'events.' % action)


class AudioTools:
    def __init__(self, live, deep=None):
        self.live = live
        self.deep = deep

    def log(self, seconds=3.0, limit=200):
        self.live.ready()
        records = record_hooks(self.live, self.deep, SOUND_CREATORS, seconds)
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

    def _sounds(self):
        """(id, BSGameSound*) of the audio manager's sound map (BSAudioManager 0x011F6EF0 +0x54)."""
        buckets, array, count = struct.unpack('<III', self.live.reads([(AUDIO_MANAGER + 0x58, 12)])[0])
        if not array or not 0 < buckets <= 0x10000:
            return []
        heads = struct.unpack('<%dI' % buckets, self.live.reads([(array, buckets * 4)])[0])
        items, frontier, seen = [], [p for p in heads if p], set()
        while frontier and len(items) < 2048:
            nxt = []
            for p, raw in zip(frontier, self.live.reads([(p, 12) for p in frontier])):
                if not raw or p in seen:
                    continue
                seen.add(p)
                link, key, value = struct.unpack('<III', raw)
                items.append((key, value))
                if link:
                    nxt.append(link)
            frontier = nxt
        return items

    def playing(self, limit=60):
        self.live.ready()
        items = [(k, v) for k, v in self._sounds() if v]
        raws = self.live.reads([(v, 0x198) for _, v in items]) if items else []
        rows, sources = [], []
        for (key, sound), raw in zip(items, raws):
            if not raw or len(raw) < 0x198:
                continue
            flags, state = struct.unpack_from('<II', raw, 0x08)[0], struct.unpack_from('<I', raw, 0x10)[0]
            volume = struct.unpack_from('<f', raw, 0x24)[0]
            path = _text(raw[0x36:0x136])
            source = struct.unpack_from('<I', raw, 0x134)[0]
            sources.append(source)
            rows.append({'id': key, 'file': path, 'flags': _flag_names(flags, SOUND_FLAGS),
                         'state': _flag_names(state, SOUND_STATES), 'volume': round(volume, 3), '_source': source})
        forms = {s: h for s, h in zip(sources, self.live.form_ids(sources)) if h} if sources else {}
        for row in rows:
            row['sound'] = (forms.get(row.pop('_source')) or {}).get('formId')
        audible = [r for r in rows if 'playing' in r['state']]
        return verdict('%d sounds, %d playing' % (len(rows), len(audible)),
                       'The audio manager\'s sound map (0x011F6EF0 +0x54): BSGameSound flags +8, state +0x10, '
                       'volume +0x24, file +0x36, sound form +0x134 (checked live). Read without the audio '
                       'thread\'s lock: a sound can end between reads.', sounds=rows[:int(limit)])

    def stop(self, sound=None, sound_id=None):
        """Stop by sound id (from playing) or every sound of a SOUN form: the stop message the handle sends."""
        self.live.in_game()
        ids = []
        if sound_id is not None:
            ids = [int(sound_id)]
        elif sound:
            form = self.live.resolve(sound)['ptr']
            items = [(k, v) for k, v in self._sounds() if v]
            srcs = self.live.u32s([v + 0x134 for _, v in items]) if items else []
            ids = [k for (k, _), s in zip(items, srcs) if s == form]
        else:
            raise ConvError('BAD_ARGUMENTS', 'stop needs sound (a SOUN form) or sound_id (from playing).')
        if not ids:
            return verdict('Nothing to stop: no playing sound matches', None, stopped=[])
        self.live.changing('audio stop')
        for i in ids:
            self.live.engine_call('0x%08X' % SOUND_STOP, '0x%08X' % AUDIO_MANAGER, [{'type': 'int', 'value': i}],
                                  'int', 'thiscall', True)
        return verdict('Stopped %d sounds' % len(ids), 'BSAudioManager message 4 (stop) through 0x00ADB610, as '
                       'BSSoundHandle::Stop sends it; the audio thread applies it within a few ms.', stopped=ids)

    def music(self, force=None, stop=False, fade_ms=1000):
        self.live.in_game()
        if force or stop:
            self.live.changing('music %s' % ('stop' if stop else 'force'))
            if stop:
                self.live.engine_call('0x%08X' % FADE_OUT_MEDIA, None, [{'type': 'int', 'value': int(fade_ms)},
                                                                        {'type': 'int', 'value': 0}], 'int', 'cdecl',
                                      True)
            else:
                args = [{'type': 'int', 'value': 6}, {'type': 'string', 'value': force},
                        {'type': 'int', 'value': int(fade_ms)}, {'type': 'int', 'value': 1},
                        {'type': 'int', 'value': 1}, {'type': 'float', 'value': 0.0}, {'type': 'int', 'value': 0}]
                self.live.engine_call('0x%08X' % PLAY_MEDIA, None, args, 'int', 'cdecl', True)
            time.sleep(0.3)
        raw = self.live.reads([(MUSIC_BLOCK, 0x298)])[0]
        channels = []
        for name, path_off, flag_off, type_off, id_off in (('A', 0, 0x220, 0x224, 0x244),
                                                           ('B', 0x108, 0x221, 0x258, 0x278)):
            path = _text(raw[path_off:path_off + 260])
            flags = raw[flag_off]
            mtype = struct.unpack_from('<I', raw, type_off)[0]
            channels.append({'channel': name, 'file': path or None, 'flags': _flag_names(flags, MUSIC_FLAGS),
                             'type': MEDIA_TYPES.get(mtype, mtype),
                             'occupied': bool(struct.unpack_from('<I', raw, id_off)[0])})
        controller, media_set = struct.unpack_from('<II', raw, 0x284)
        heads = self.live.form_ids([controller, media_set])
        now = [c for c in channels if c['occupied'] and 'play' in c['flags']]
        return verdict('Music: %s' % (', '.join('%s (%s)' % (c['file'], c['type']) for c in now) or 'nothing playing'),
                       'The music state block 0x011DD0F0 (two channels: path, flags, media type); the location '
                       'controller 0x011DD374 and media set 0x011DD378 decide the next track each frame '
                       '(FalloutAudioMedia::Update 0x0082FB70). force plays a file as special media (type 6), which '
                       'that update leaves alone; stop fades all media out (0x00830680).', channels=channels,
                       locationController=(heads[0] or {}).get('formId'), mediaSet=(heads[1] or {}).get('formId'))

    def radio(self, enable=None):
        self.live.in_game()
        if enable is not None:
            self.live.changing('radio %s' % ('on' if enable else 'off'))
            self.live.engine_call('0x%08X' % SET_RADIO, None, [{'type': 'int', 'value': 1 if enable else 0}],
                                  'bool', 'cdecl', True)
        current, = self.live.u32s([RADIO_CURRENT])
        enabled = bool((self.live.reads([(RADIO_ENABLED, 1)])[0] or b'\0')[0])
        stations, node, guard = [], RADIO_STATIONS, 0
        while node and guard < 64:
            item, nxt = struct.unpack('<II', self.live.reads([(node, 8)])[0])
            if item:
                stations.append(item)
            node, guard = nxt, guard + 1
        forms = self.live.u32s(stations) if stations else []
        signals = [(raw or b'\0')[0] for raw in self.live.reads([(s + 0x1B, 1) for s in stations])] if stations else []
        heads = self.live.form_ids(forms) if forms else []
        cur_form = self.live.u32s([current])[0] if current else 0
        rows = [{'station': (h or {}).get('formId'), 'signal': sig, 'current': f == cur_form}
                for f, h, sig in zip(forms, heads, signals)]
        cur = next((r['station'] for r in rows if r['current']), None)
        return verdict('Radio %s; %d stations, tuned to %s' % ('on' if enabled else 'off', len(rows), cur or 'none'),
                       'Stations from the list 0x011DD554 (RadioStationState: talking activator +0, signal 0..100 '
                       'at +0x1B); current 0x011DD42C; enable through FalloutRadio::SetRadioEnabled 0x008324E0.',
                       enabled=enabled, current=cur, stations=rows)

    def subtitles(self, seconds=5.0, limit=100, enable=None):
        """Subtitle lines shown meanwhile. Outside dialogue a spoken line gets one only when General Subtitles is on
        (bGeneralSubtitles, off by default; VoiceHost::PlayVoiceTopic 0x00578A80 checks it): enable=true turns it on
        for the recording and puts it back."""
        self.live.ready()
        general = bool(self.live.reads([(GENERAL_SUBTITLES + 4, 1)])[0][0])
        turned_on = bool(enable) and not general
        if turned_on:
            self.live.changing('audio subtitles enable')
            self.live.link.write(GENERAL_SUBTITLES + 4, b'\x01')
        rows = []
        try:
            # The text is a temporary string: read it while the line is shown, so poll the records meanwhile.
            from .fnv_deep import decode_prologue
            raw = self.live.reads([(SHOW_SUBTITLE, 24)])[0]
            stolen, relocs = decode_prologue(raw)
            h = self.live.link.one('hook', word(SHOW_SUBTITLE), word(stolen), ','.join('%x' % r for r in relocs) or '-',
                                   word(2), '0', '0', '0', word(5), '0', '0', '0', '0', hexs('subtitle'))
            if not h.get('ok'):
                raise ConvError('HOOK_REFUSED', 'The subtitle hook was refused (%s).' % h.get('error'), h)
            hook, seen, lost = h['hook'], 0, 0
            try:
                end = time.monotonic() + float(seconds)
                while True:
                    _, got, missed = self.deep.records(hook, since=seen)
                    lost += missed or 0
                    for rec in got:
                        seen = max(seen, rec[0])          # records() takes since as exclusive (R6)
                        text = _text(self.live.reads([(rec[12], 260)])[0]) if rec[12] else None
                        rows.append({'frame': rec[22], 'text': text, 'soundId': rec[13]})
                    if time.monotonic() >= end:
                        break
                    time.sleep(0.15)
            finally:
                self.live.link.one('unhook', word(hook['id']))
        finally:
            if turned_on:
                self.live.link.write(GENERAL_SUBTITLES + 4, b'\x00')
        out = dict(generalSubtitles=general or turned_on, subtitles=rows[:int(limit)], lost=lost)
        if not (general or turned_on):
            out['note'] = ('General Subtitles is off (bGeneralSubtitles): lines spoken outside the dialogue menu show '
                           'no subtitle and are not caught here. Pass enable=true to turn it on while recording; '
                           'fnv_audio playing lists the voice files that play.')
        return verdict('%d subtitles in %.1f s' % (len(rows), float(seconds)),
                       'Record hook on Interface::ShowSubtitle 0x00705210, read while it records (the text is a '
                       'temporary string); sound id from the voice handle. The call carries no speaker form: '
                       'fnv_audio playing names the voice file (its path holds the voice type and topic).%s' % (' General Subtitles was on only for the recording.' if turned_on else ''),
                       **out)

    def hear(self, sound, x, y, z, scale=1.0):
        self.live.ready()
        form = self.live.resolve(sound)['ptr']
        args = [{'type': 'int', 'value': form}, {'type': 'float', 'value': x}, {'type': 'float', 'value': y},
                {'type': 'float', 'value': z}, {'type': 'float', 'value': scale}]
        out = self.live.engine_call('0x%08X' % CAN_PLAY_AT, None, args, 'bool', 'cdecl', True)
        heard = bool(out.get('result'))
        return verdict('%s %s be heard at (%.0f, %.0f, %.0f)' % (sound, 'would' if heard else 'would not', x, y, z),
                       'SoundPlaybackManager::CanPlaySoundAt 0x0082ECA0: the engine\'s distance test against the '
                       'listener before it plays a positioned sound.', heard=heard)

    def voice_check(self, npc):
        if not getattr(self, 'crawl', None):
            raise ConvError('NOT_AVAILABLE', 'voice_check needs the crawl tools.')
        return self.crawl.dialogue(npc)

    def run(self, action, **a):
        if action == 'log':
            return self.log(a.get('seconds') or 3.0)
        if action == 'play':
            return self.play(a.get('sound'), a.get('ref'))
        if action == 'playing':
            return self.playing()
        if action == 'stop':
            return self.stop(a.get('sound'), a.get('sound_id'))
        if action == 'music':
            return self.music(a.get('file'), bool(a.get('stop')), a.get('fade_ms') or 1000)
        if action == 'radio':
            return self.radio(a.get('enable'))
        if action == 'subtitles':
            return self.subtitles(a.get('seconds') or 5.0, enable=a.get('enable'))
        if action == 'hear':
            pos = a.get('position') or []
            if not a.get('sound') or len(pos) != 3:
                raise ConvError('BAD_ARGUMENTS', 'hear needs sound and position [x, y, z].')
            return self.hear(a['sound'], *[float(v) for v in pos])
        if action == 'voice_check':
            if not a.get('ref'):
                raise ConvError('BAD_ARGUMENTS', 'voice_check needs ref (an NPC reference).')
            return self.voice_check(a['ref'])
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: log, play, playing, stop, music, radio, '
                        'subtitles, hear, voice_check.' % action)


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
        state_raw = self.live.reads([(ctrl + 0x3F0, 4)])[0]
        state = struct.unpack('<I', state_raw)[0] if state_raw else None
        dist = self.live.engine_call('0x%08X' % GET_FALL_DISTANCE, '0x%08X' % ctrl, [], 'float', 'thiscall', True)
        cls = (self.live.rtti([ctrl]).get(ctrl) or {}).get('class')
        state_name = CONTROLLER_STATES[state] if state is not None and state < len(CONTROLLER_STATES) else state
        return verdict('%s: %s, falling for %.2f s, fall distance %s' % (r['formId'], state_name, fall_time or 0.0,
                                                                         dist.get('result')),
                       'The character controller at process +0x138 (state +0x3F0, fall start +0x544, fall time '
                       '+0x548); distance from GetFallDistance 0x00C70550.', actor=r['formId'],
                       controller='0x%08X' % ctrl, controllerClass=cls, state=state_name, fallStartZ=fall_start,
                       fallTime=fall_time, fallDistance=dist.get('result'))

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
        extra = {}
        if has_body:
            raw = self.live.reads([(chain[-1][1] + 0x1B0, 32)])[0]
            if raw:
                lin, ang = struct.unpack_from('<3f', raw, 0), struct.unpack_from('<3f', raw, 16)
                extra = {'velocity': [round(v * HAVOK_TO_GAME, 2) for v in lin],
                         'angularVelocity': [round(v, 3) for v in ang]}
        return verdict('%s %s a Havok body' % (r['formId'], 'has' if has_body else 'has no'),
                       '3D root +0x1C collision object, +0x10 world object, +0x08 hkpRigidBody (classes from RTTI); '
                       'velocity from the body +0x1B0 in game units per second (Havok x 6.9991), angular +0x1C0.',
                       ref=r['formId'], chain=out, **extra)

    def _rigid_body(self, r):
        loaded = self.live.u32s([r['ptr'] + 0x64])[0]
        root = self.live.u32s([loaded + 0x14])[0] if loaded else 0
        col = self.live.u32s([root + 0x1C])[0] if root else 0
        return self.live.u32s([col + 0x10])[0] if col else 0

    def _vector(self, value, what):
        if value is None:
            return None
        if len(value) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value):
            raise ConvError('BAD_ARGUMENTS', '%s is [x, y, z] (numbers).' % what)
        return [float(v) for v in value]

    def set_velocity(self, ref, velocity=None, angular_velocity=None, impulse=None):
        """A body's linear velocity (game units per second), angular velocity (radians per second) and an impulse
        (mass x game units per second, at the centre of mass); the body is woken. An actor's velocity is a push on
        its character controller."""
        self.live.in_game()
        r = self.live.resolve(ref)
        velocity = self._vector(velocity, 'velocity')
        angular = self._vector(angular_velocity, 'angular_velocity')
        impulse = self._vector(impulse, 'impulse')
        if velocity is None and angular is None and impulse is None:
            raise ConvError('BAD_ARGUMENTS', 'set needs velocity [x, y, z] (game units per second), angular_velocity '
                                             '(radians per second) or impulse.')
        kind = self.live.reads([(r['ptr'] + 4, 1)])[0]
        if kind and kind[0] in self.live.ACTOR_TYPES:
            # an actor moves by its character controller (process +0x138), not by the bodies of its ragdoll
            if angular is not None or impulse is not None or velocity is None:
                raise ConvError('BAD_ARGUMENTS', '%s is an actor: it takes velocity only (a push on its character '
                                                 'controller).' % r['formId'])
            process = self.live.u32s([r['ptr'] + 0x68])[0]
            ctrl = self.live.u32s([process + 0x138])[0] if process else 0
            if not ctrl:
                raise ConvError('NO_BODY', '%s has no character controller (low process, or no 3D).' % r['formId'])
            return self._push(r, ctrl, velocity)
        body = self._rigid_body(r)
        if not body:
            raise ConvError('NO_BODY', '%s has no Havok body (no 3D, or no collision).' % r['formId'])
        hkp = self.live.u32s([body + 0x08])[0]
        if not hkp:
            raise ConvError('NO_BODY', '%s has a bhkRigidBody with no Havok body.' % r['formId'])
        raw = self.live.reads([(hkp + 0x1AC, 20)])[0]
        inv_mass = struct.unpack_from('<f', raw, 0)[0] if raw else 0.0
        motion = self.live.reads([(hkp + 0xE8, 1)])[0]
        if motion and motion[0] == 5:
            raise ConvError('FIXED_BODY', '%s has a fixed body (motion type 5): it does not move.' % r['formId'])
        if impulse is not None:
            if not inv_mass:
                raise ConvError('FIXED_BODY', '%s has no inverse mass: an impulse cannot move it.' % r['formId'])
            now = velocity or [v * HAVOK_TO_GAME for v in struct.unpack_from('<3f', raw, 4)]
            velocity = [v + i * inv_mass for v, i in zip(now, impulse)]
        self.live.changing('physics set')
        # wake first: a sleeping body's velocities are dropped (live run 2026-11-24: spin and impulse read back 0)
        self.live.engine_call('0x%08X' % HKP_ACTIVATE, '0x%08X' % hkp, [], 'int', 'thiscall', True)
        calls = ['hkpEntity::activate 0x%08X' % HKP_ACTIVATE]
        if velocity is not None:
            # 0x005615D0 takes an NiPoint3 in game units and converts it (FUN_004A3E00) for hkpRigidBody 0x00561690.
            vec = self.live.scratch(struct.pack('<4f', *velocity, 0.0))
            self.live.engine_call('0x%08X' % SET_LINEAR_VELOCITY, '0x%08X' % body, [{'type': 'int', 'value': vec}],
                                  'int', 'thiscall', True)
            calls.append('bhkRigidBody::SetLinearVelocity 0x%08X' % SET_LINEAR_VELOCITY)
        if angular is not None:
            vec = self.live.scratch(struct.pack('<4f', *angular, 0.0))
            self.live.engine_call('0x%08X' % SET_ANGULAR_VELOCITY, '0x%08X' % body, [{'type': 'int', 'value': vec}],
                                  'int', 'thiscall', True)
            calls.append('bhkRigidBody::SetAngularVelocity 0x%08X' % SET_ANGULAR_VELOCITY)
        out = self.body(ref)
        out.update(calls=calls, inverseMass=round(inv_mass, 6))
        if impulse is not None:
            out['impulse'] = impulse
        return out

    def _push(self, r, ctrl, velocity):
        """Push an actor the way the engine's knockback does: UpdateMaxVelocity on its character controller with
        velocity x seconds over seconds; the seconds grow with the speed (JIP's push mapping: |v| = 0.714 F^2 / 96
        for F / 96 seconds)."""
        speed = math.sqrt(sum(v * v for v in velocity))
        seconds = max(0.1, math.sqrt(speed / (0.714 * 96.0))) if speed else 0.1
        self.live.changing('physics push')
        vec = self.live.scratch(struct.pack('<4f', *[v * seconds for v in velocity], 0.0))
        self.live.engine_call('0x%08X' % PUSH_IMPULSE, '0x%08X' % ctrl,
                              [{'type': 'int', 'value': vec}, {'type': 'float', 'value': seconds}], 'void',
                              'thiscall', True)
        back = self.live.reads([(ctrl + PUSH_VELOCITY, 16), (ctrl + PUSH_TIME, 4)])
        now = [v * HAVOK_TO_GAME for v in struct.unpack_from('<3f', back[0], 0)] if back[0] else None
        return verdict('%s pushed at %.0f units per second for %.2f s' % (r['formId'], speed, seconds),
                       'bhkCharacterController::UpdateMaxVelocity 0x%08X, as the engine knocks an actor back when '
                       'hit: velocity at controller +0x500 (Havok units, shown in game units), time +0x524; a push '
                       'already running that is faster is kept.' % PUSH_IMPULSE,
                       actor=r['formId'], controller='0x%08X' % ctrl, seconds=round(seconds, 3),
                       pushVelocity=[round(v, 2) for v in now] if now else None,
                       pushTime=struct.unpack('<f', back[1])[0] if back[1] else None)

    def triggers(self, seconds=5.0, ref=None, limit=200):
        """Trigger events (OnTrigger, OnTriggerEnter, OnTriggerLeave) as the engine raises them to scripts."""
        self.live.ready()
        recs = record_hooks(self.live, self.live.deep, [(RAISE_SCRIPT_EVENT, 'script_event')], seconds)
        rows, last = [], {}
        want = self.live.resolve(ref)['ptr'] if ref else None
        pointers = set()
        for _, rec in recs:
            mask = rec[14] & TRIGGER_EVENTS
            if not mask:
                continue
            who, trigger = rec[12], (rec[13] - 0x44) & 0xFFFFFFFF
            if want and want not in (who, trigger):
                continue
            kind = {0x10000000: 'inside', 0x20000000: 'enter', 0x40000000: 'leave'}.get(mask, hex(mask))
            key = (who, trigger)
            if kind == 'inside' and last.get(key) and last[key]['event'] == 'inside' \
                    and rec[22] - last[key]['lastFrame'] <= 2:
                last[key]['lastFrame'] = rec[22]
                last[key]['frames'] += 1
                continue
            row = {'frame': rec[22], 'event': kind, '_who': who, '_trigger': trigger, 'lastFrame': rec[22],
                   'frames': 1}
            last[key] = row
            rows.append(row)
            pointers |= {who, trigger}
        heads = dict(zip(sorted(pointers), self.live.form_ids(sorted(pointers)))) if pointers else {}
        for row in rows:
            row['who'] = (heads.get(row.pop('_who')) or {}).get('formId')
            row['trigger'] = (heads.get(row.pop('_trigger')) or {}).get('formId')
            if row['event'] != 'inside':
                row.pop('lastFrame')
                row.pop('frames')
        return verdict('%d trigger events in %.1f s' % (len(rows), float(seconds)),
                       'Record hook on the script event raiser 0x005AC750 (action ref, the trigger\'s extra data list '
                       '= trigger + 0x44, event flag), filtered to OnTrigger 0x10000000 (inside, merged per frame run), '
                       'OnTriggerEnter 0x20000000, OnTriggerLeave 0x40000000. Only triggers with a script raise them.',
                       events=rows[:int(limit)])

    def projectiles(self, limit=100):
        """Projectiles in flight: the mobile object list of ProcessLists (0x011E0E80 +0x70) filtered by class."""
        self.live.ready()
        node, objs, guard = PROCESS_LISTS + 0x70, [], 0
        while node and guard < 4096:
            item, nxt = struct.unpack('<II', self.live.reads([(node, 8)])[0])
            if item:
                objs.append(item)
            node, guard = nxt, guard + 1
        classes = self.live.rtti(objs) if objs else {}
        projs = [o for o in objs if 'Projectile' in str((classes.get(o) or {}).get('class') or '')]
        raws = self.live.reads([(p, 0x150) for p in projs]) if projs else []
        rows, ptrs = [], set()
        for p, raw in zip(projs, raws):
            if not raw or len(raw) < 0x150:
                continue
            base, = struct.unpack_from('<I', raw, 0x20)
            pos = struct.unpack_from('<3f', raw, 0x30)
            weapon, source = struct.unpack_from('<II', raw, 0xF8)
            rows.append({'_p': p, '_base': base, '_weapon': weapon, '_source': source,
                         'class': classes[p]['class'], 'position': [round(v, 1) for v in pos],
                         'flags': '0x%X' % struct.unpack_from('<I', raw, 0xC8)[0],
                         'distance': round(struct.unpack_from('<f', raw, 0x110)[0], 1),
                         'impacted': bool(raw[0x90])})
            ptrs |= {p, base, weapon, source}
        ptrs.discard(0)
        heads = dict(zip(sorted(ptrs), self.live.form_ids(sorted(ptrs)))) if ptrs else {}
        for row in rows:
            for key, name in (('_p', 'ref'), ('_base', 'base'), ('_weapon', 'weapon'), ('_source', 'shooter')):
                row[name] = (heads.get(row.pop(key)) or {}).get('formId')
        return verdict('%d projectiles in flight' % len(rows),
                       'ProcessLists 0x011E0E80 +0x70 (mobile objects), kept when the RTTI class is a projectile; '
                       'fields: flags +0xC8, weapon +0xF8, shooter +0xFC, distance travelled +0x110, impacted '
                       '+0x90.', projectiles=rows[:int(limit)], mobileObjects=len(objs))

    def query(self, position, radius=64.0):
        """Is this spot free: rays from the point in six directions (the engine's own pick) and refs nearby."""
        self.live.ready()
        if not position or len(position) != 3:
            raise ConvError('BAD_ARGUMENTS', 'query needs position [x, y, z] and radius.')
        x, y, z = [float(v) for v in position]
        rad = float(radius)
        hits = []
        engine_ai = getattr(self, 'engine_ai', None)
        if engine_ai is None:
            raise ConvError('NOT_AVAILABLE', 'query needs the raycast tools.')
        for dx, dy, dz, name in ((1, 0, 0, '+x'), (-1, 0, 0, '-x'), (0, 1, 0, '+y'), (0, -1, 0, '-y'),
                                 (0, 0, 1, 'up'), (0, 0, -1, 'down')):
            out = engine_ai.raycast({'x': x, 'y': y, 'z': z},
                                    {'x': x + dx * rad, 'y': y + dy * rad, 'z': z + dz * rad}, None, None,
                                    'line_of_sight')
            if out.get('hit'):
                obj = out.get('hitObject') or {}
                ref = obj.get('reference') or {}
                hits.append({'direction': name, 'distance': out.get('distanceToHit'),
                             'ref': ref.get('formId') if isinstance(ref, dict) else ref, 'node': obj.get('node')})
        free = not [h for h in hits if h['direction'] != 'down']
        return verdict('The spot is %s within %.0f units%s' % ('free' if free else 'blocked', rad,
                                                                '' if free else ' (%s)' % ', '.join(
                                                                    h['direction'] for h in hits)),
                       'Six rays from the point through the Havok world with TES::Pick (fnv_raycast); a hit below '
                       'is ground, not a block.', free=free, hits=hits)

    def layers(self):
        return verdict('%d collision layers' % len(COLLISION_LAYERS),
                       'Names from the engine\'s layer enum (filter info = group << 16 | part << 8 | layer). The '
                       'layer-against-layer matrix is not read: the 43-layer test is not confirmed.',
                       layers=[{'layer': i, 'name': n} for i, n in enumerate(COLLISION_LAYERS)])

    def run(self, action, **a):
        if action == 'triggers':
            return self.triggers(a.get('seconds') or 5.0, a.get('ref'))
        if action == 'projectiles':
            return self.projectiles()
        if action == 'query':
            return self.query(a.get('position'), a.get('radius') or 64.0)
        if action == 'layers':
            return self.layers()
        if not a.get('ref'):
            raise ConvError('BAD_ARGUMENTS', 'ref is needed.')
        if action == 'controller':
            return self.controller(a['ref'])
        if action == 'body':
            return self.body(a['ref'])
        if action == 'set':
            return self.set_velocity(a['ref'], a.get('velocity'), a.get('angular_velocity'), a.get('impulse'))
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: controller, body, set, triggers, projectiles, '
                        'query, layers.' % action)


def _failing_conditions(lists):
    """The conditions that make a condition list fail: in the lists that do not pass, the members of every OR group
    where no member passed. A failed member of a group that another member rescued is not why the list fails
    (bug hunt 2026-11-18: those were listed, and `compare` read a key the evaluation never sets)."""
    out = []
    for lst in lists:
        if lst.get('passes', True):
            continue
        group = []
        for c in list(lst.get('conditions') or []) + [None]:
            if c is not None:
                group.append(c)
                if c.get('or'):
                    continue
            if group and not any(g.get('passed') for g in group):
                out += [{'list': lst['list'], 'index': g['index'], 'function': g.get('function'),
                         'value': g.get('value'), 'compare': g.get('compareTo')} for g in group]
            group = []
    return out


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
        if int(steps) < 1:
            raise ConvError('BAD_ARGUMENTS', 'sweep steps must be at least 1.')
        if not 0 <= int(index) < len(args):
            raise ConvError('BAD_ARGUMENTS', 'sweep index must be from 0 to %d (one of the args).' % (len(args) - 1))
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

    def perk(self, perk):
        """Can the player take this perk now: the perk's own requirements (level, rank, playable) and its condition
        list, run with the game's condition functions (fnv_condition_eval)."""
        self.live.in_game()
        p = self.live.resolve(perk)
        raw = self.live.reads([(p['ptr'] + PERK_DATA, 5)])[0]
        trait, min_level, ranks, playable, hidden = struct.unpack('<5B', raw) if raw else (0,) * 5
        level, = self.live.values(['player.GetLevel'])
        have, = self.live.values(['player.HasPerk %s' % p['formId']])
        ev = self.live.agent.condition_eval(p['formId'], 'player', None, None, False)
        failing = _failing_conditions(ev['lists'])
        reasons = []
        if not playable:
            reasons.append('not playable')
        if level is not None and min_level and level < min_level:
            reasons.append('needs level %d (player %s)' % (min_level, level))
        if have and ranks and have >= ranks:
            reasons.append('all %d ranks taken' % ranks)
        conds_pass = all(lst['passes'] for lst in ev['lists'] if lst['list'].lower().startswith('perk'))
        if not conds_pass:
            reasons.append('conditions fail')
        return verdict('%s %s' % (p['formId'], 'can be taken now' if not reasons else 'cannot be taken: ' +
                                  '; '.join(reasons)),
                       'Perk DATA (trait, minimum level, ranks, playable, hidden at +0x%X) and the perk conditions '
                       'run through the game\'s condition functions.' % PERK_DATA, perk=p['formId'],
                       minLevel=min_level, ranks=ranks, playable=bool(playable), trait=bool(trait),
                       hidden=bool(hidden), playerLevel=level, rankHeld=have, failing=failing)

    def _inventory_entries(self, ref_ptr):
        """{form pointer: entry pointer} from the reference's ExtraContainerChanges (extra type 0x15 via
        ExtraDataList::GetByType 0x00410220; data +0xC, its entry list at +0; entry: extras, count, form at +8)."""
        xcc = _as_int(self.live.engine_call('0x%08X' % EXTRA_BY_TYPE, '0x%08X' % (ref_ptr + 0x44),
                                            [{'type': 'int', 'value': 0x15}], 'ptr', 'thiscall', True).get('result'))
        data = self.live.u32s([xcc + 0x0C])[0] if xcc else 0
        node = self.live.u32s([data])[0] if data else 0
        out, guard = {}, 0
        while node and guard < 2000:
            item, nxt = self.live.u32s([node, node + 4])
            if item:
                form = self.live.u32s([item + 8])[0]
                if form:
                    out.setdefault(form, item)
            node, guard = nxt, guard + 1
        return out

    def barter(self, items, vendor=None):
        """Buy and sell prices the way the barter menu computes them (BarterMenu item price 0x0072ED00): the item's
        value from its inventory entry (0x004BD400: condition and weapon mods) or, when the player does not carry it,
        the form's own value (0x0048E8A0), times the skill multiplier pair (GetSkillPriceMultipliers 0x00649050),
        the buy price through perk entry point 17, rounded half up, then held at the value: a buy price never under
        it, a sell price never over it."""
        self.live.in_game()
        if not items:
            raise ConvError('BAD_ARGUMENTS', 'barter needs args = [item forms].')
        skill, = self.live.values(['player.GetAV Barter'])
        buf = self.live.scratch(b'\0' * 8)
        self.live.engine_call('0x%08X' % PRICE_MULTIPLIERS, None, [{'type': 'int', 'value': int(skill or 0)},
                                                                    {'type': 'int', 'value': buf},
                                                                    {'type': 'int', 'value': buf + 4}],
                              'int', 'cdecl', True)
        buy_mult, sell_mult = struct.unpack('<ff', self.live.reads([(buf, 8)])[0])
        player = self.live.resolve('player')
        entries = self._inventory_entries(player['ptr'])
        rows = []
        for item in items:
            f = self.live.resolve(item)
            entry = entries.get(f['ptr'])
            if entry:
                value = self.live.engine_call('0x%08X' % ENTRY_VALUE, '0x%08X' % entry, [], 'float', 'thiscall',
                                              True).get('result')
                source = 'carried'
            else:
                value = self.live.engine_call('0x%08X' % BASE_VALUE, None, [{'type': 'int', 'value': f['ptr']}],
                                              'int', 'cdecl', True).get('result')
                source = 'form'
            if value is None or float(value) < 0:
                rows.append({'item': f['formId'], 'error': 'no value (not an item with a price)'})
                continue
            value = round(float(value), 2)
            kind = self.live.reads([(f['ptr'] + 4, 1)])[0][0]
            flat = kind in (0x6C, 0x74)          # the menu leaves these two form types unmultiplied
            buy = value * (1.0 if flat else buy_mult)
            sell = value * (1.0 if flat else sell_mult)
            perk = None
            ai = getattr(self, 'engine_ai', None)
            if ai is not None:
                try:
                    perk = ai.perk_entries('evaluate', 17, 50, buy, 'player', [f['formId']])
                    buy = float(perk.get('valueAfter', buy))
                except Exception:  # noqa: BLE001 - keep the price without perks
                    perk = None
            rows.append({'item': f['formId'], 'value': value, 'valueFrom': source,
                         'buy': max(_menu_round(buy), value), 'sell': min(_menu_round(sell), value),
                         'perksOnBuy': (perk or {}).get('appliedEntries')})
        return verdict('Barter %s: buy x%.3f, sell x%.3f' % (skill, buy_mult, sell_mult),
                       'As the barter menu prices an item (0x0072ED00): value from the player\'s inventory entry '
                       '(0x004BD400, with condition and weapon mods) or the form (0x0048E8A0, for items not carried), '
                       'x GetSkillPriceMultipliers(Barter skill) 0x00649050, buy price through perk entry point 17, '
                       'rounded half up (0x004BD510), buy held at or over the value and sell at or under it. Not '
                       'applied: the modifier a script passes when it opens the menu (usually 0).',
                       barterSkill=skill, buyMultiplier=buy_mult, sellMultiplier=sell_mult, vendor=vendor, items=rows)

    def zone(self, zone):
        """An encounter zone: the level the game uses now (GetCalculatedLevel 0x00526190), its minimum and maximum,
        and whether the zone can reset now (0x005262A0)."""
        self.live.in_game()
        z = self.live.resolve(zone)
        level = self.live.engine_call('0x%08X' % ZONE_LEVEL, '0x%08X' % z['ptr'], [], 'int', 'thiscall', True)
        if level and level.get('result') is not None:
            level['result'] = int(level['result']) & 0xFFFF       # the function returns an unsigned short
        raw = self.live.reads([(z['ptr'] + 0x18, 0x18)])[0]
        owner, rank, min_level, flags, max_level = struct.unpack_from('<IbBBB', raw, 0) if raw else (0, 0, 0, 0, 0)
        return verdict('%s: level %s (min %d, max %s)' % (z['formId'], (level.get('result') if level else None),
                                                           min_level, max_level or 'none'),
                       'GetCalculatedLevel 0x00526190 on the zone (the player level clamped to the zone\'s minimum '
                       'and maximum); DATA at +0x18: owner, rank, minimum level, flags, maximum level.',
                       zone=z['formId'], level=(level or {}).get('result'), minLevel=min_level,
                       maxLevel=max_level, flags=flags, owner=(self.live.form_ids([owner])[0] or {}).get('formId')
                       if owner else None)

    def crime(self):
        self.live.in_game()
        karma, = self.live.values(['player.GetAV Karma'])
        return verdict('Karma %s' % karma, 'New Vegas keeps no bounty; crime shows as karma and faction reputation '
                       '(fnv_reaction for one faction). The bounty storage was not found in the decomp.',
                       karma=karma)

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
        if action == 'perk':
            if not a.get('name'):
                raise ConvError('BAD_ARGUMENTS', 'perk needs name (a PERK form).')
            return self.perk(a['name'])
        if action == 'barter':
            return self.barter(a.get('args'), a.get('name'))
        if action == 'zone':
            if not a.get('name'):
                raise ConvError('BAD_ARGUMENTS', 'zone needs name (an ECZN form).')
            return self.zone(a['name'])
        if action == 'crime':
            return self.crime()
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid: formulas, formula, sweep, progression, needs, '
                        'perk, barter, zone, crime.' % action)
