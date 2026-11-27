"""Named fields of FNV records (tool gaps 1b): one table that fnv_spec writes with ("set") and fnv_record reads with.

Layouts follow the FNVEdit record definitions (https://tes5edit.github.io/fopdoc/FalloutNV/Records.html) and were
checked against every record of the type in FalloutNV.esm (tests/test_layouts.py: each subrecord has the layout's
size, or a shorter size the layout allows, and decodes and encodes back to the same bytes).

A record type has:
- whole-subrecord fields: name -> (tag, kind), kind 'text' (a zero-ended string), 'form' (one FormID), 'forms' (one
  subrecord per FormID, repeated), or a struct code ('i', 'B', 'f', ...);
- struct subrecords: tag -> list of (name, code, table). code is a struct code; a name of None is padding (kept as
  it was, zero in a new subrecord). table: FLAGS (a dict name -> bit; the value is a list of names), an enum dict
  (name -> number; the value is a name or the number), 'form' (the value is "@EditorID" or a FormID), or None.
  MIN_SIZES lets a vanilla subrecord be shorter than the layout (older records); setting a field past its end grows
  the subrecord with zeros up to that field.
- list fields (entries, ingredients, outputs, menu, effects, conditions), written by builders in this module.

Field names are lower case with underscores. A name that two subrecords of one type share is written "TAG.name".
"""
import struct

from .codec import Subrecord, cstring, zstring
from .records import SpecError

# -- tables ------------------------------------------------------------------------------------------------------

BIPED = {name: 1 << i for i, name in enumerate([
    'head', 'hair', 'upper_body', 'left_hand', 'right_hand', 'weapon', 'pipboy', 'backpack', 'necklace', 'headband',
    'hat', 'eyeglasses', 'nose_ring', 'earrings', 'mask', 'choker', 'mouth_object', 'body_addon_1', 'body_addon_2',
    'body_addon_3'])}
ARMOR_GENERAL = {'power_armor': 0x20, 'non_playable': 0x40, 'heavy': 0x80}
ARMOR_FLAGS = {'modulates_voice': 0x0001}
EQUIP_TYPE = {'none': -1, 'big_guns': 0, 'energy_weapons': 1, 'small_guns': 2, 'melee_weapons': 3,
              'unarmed_weapon': 4, 'thrown_weapons': 5, 'mine': 6, 'body_wear': 7, 'head_wear': 8, 'hand_wear': 9,
              'chems': 10, 'stimpack': 11, 'food': 12, 'alcohol': 13}
SKILLS = ['barter', 'big_guns', 'energy_weapons', 'explosives', 'lockpick', 'medicine', 'melee_weapons', 'repair',
          'science', 'guns', 'sneak', 'speech', 'survival', 'unarmed']
SKILL = dict({'none': -1}, **{name: i for i, name in enumerate(SKILLS)})            # BOOK: index in the skill list
SKILL_AV = dict({'none': -1}, **{name: 32 + i for i, name in enumerate(SKILLS)})   # RCPE: actor value code
AMMO_FLAGS = {'ignores_normal_weapon_resistance': 0x1, 'non_playable': 0x2}
NOTE_TYPE = {'sound': 0, 'text': 1, 'image': 2, 'voice': 3}
BOOK_FLAGS = {'scroll': 0x1, 'cant_be_taken': 0x2}
LEVELED_FLAGS = {'all_levels': 0x1, 'each': 0x2, 'use_all': 0x4}
DOOR_FLAGS = {'automatic': 0x02, 'hidden': 0x04, 'minimal_use': 0x08, 'sliding': 0x10}
TERM_DIFFICULTY = {'very_easy': 0, 'easy': 1, 'average': 2, 'hard': 3, 'very_hard': 4, 'requires_key': 5}
TERM_FLAGS = {'leveled': 0x1, 'unlocked': 0x2, 'alternate_colors': 0x4, 'hide_welcome_text': 0x8}
MENU_FLAGS = {'add_note': 0x1, 'force_redraw': 0x2}
RACE_FLAGS = {'playable': 0x1, 'child': 0x4}
BODY_PART_FLAGS = {'severable': 0x01, 'ik_data': 0x02, 'ik_biped_data': 0x04, 'explodable': 0x08, 'ik_is_head': 0x10,
                   'ik_headtracking': 0x20, 'to_hit_chance_absolute': 0x40}
BODY_PART_TYPE = {name: i for i, name in enumerate([
    'torso', 'head_1', 'head_2', 'left_arm_1', 'left_arm_2', 'right_arm_1', 'right_arm_2', 'left_leg_1', 'left_leg_2',
    'left_leg_3', 'right_leg_1', 'right_leg_2', 'right_leg_3', 'brain', 'weapon'])}
SOUND_TYPE = {'walk': 0, 'sneak': 1, 'run': 2, 'sneak_armor': 3, 'run_armor': 4, 'walk_armor': 5}

COMMON = {'name': ('FULL', 'text'), 'model': ('MODL', 'text'), 'icon': ('ICON', 'text'), 'small_icon': ('MICO', 'text'),
          'script': ('SCRI', 'form'), 'description': ('DESC', 'text'), 'pickup_sound': ('YNAM', 'form'),
          'drop_sound': ('ZNAM', 'form')}

BOUNDS = [('bounds_x1', 'h', None), ('bounds_y1', 'h', None), ('bounds_z1', 'h', None),
          ('bounds_x2', 'h', None), ('bounds_y2', 'h', None), ('bounds_z2', 'h', None)]

WHOLE = {
    'ARMO': dict(COMMON, enchantment=('EITM', 'form'), male_model=('MODL', 'text'), male_world_model=('MOD2', 'text'),
                 female_model=('MOD3', 'text'), female_world_model=('MOD4', 'text'), female_icon=('ICO2', 'text'),
                 female_small_icon=('MIC2', 'text'), ragdoll=('BMCT', 'text'), repair_list=('REPL', 'form'),
                 biped_model_list=('BIPL', 'form'), equip_type=('ETYP', 'i'),
                 overrides_animation_sounds=('BNAM', 'I'), animation_sounds_template=('TNAM', 'form')),
    'AMMO': dict(COMMON, short_name=('ONAM', 'text'), abbreviation=('QNAM', 'text'), ammo_effects=('RCIL', 'forms')),
    'KEYM': dict(COMMON, loop_sound=('RNAM', 'form')),
    'NOTE': dict(COMMON, quests=('ONAM', 'forms'), texture=('XNAM', 'text'), text=('TNAM', 'text'),
                 sound=('SNAM', 'form')),
    'BOOK': dict(COMMON),
    'IMOD': dict(COMMON),
    'LVLI': {'chance_none': ('LVLD', 'B'), 'global': ('LVLG', 'form')},
    'LVLN': {'chance_none': ('LVLD', 'B'), 'model': ('MODL', 'text')},
    'LVLC': {'chance_none': ('LVLD', 'B'), 'model': ('MODL', 'text')},
    'RCPE': {'name': ('FULL', 'text')},
    'DOOR': {'name': ('FULL', 'text'), 'model': ('MODL', 'text'), 'script': ('SCRI', 'form'),
             'open_sound': ('SNAM', 'form'), 'close_sound': ('ANAM', 'form'), 'loop_sound': ('BNAM', 'form')},
    'TERM': {'name': ('FULL', 'text'), 'model': ('MODL', 'text'), 'script': ('SCRI', 'form'),
             'welcome_text': ('DESC', 'text'), 'sound': ('SNAM', 'form'), 'password_note': ('PNAM', 'form')},
    'PERK': {'name': ('FULL', 'text'), 'description': ('DESC', 'text'), 'icon': ('ICON', 'text'),
             'small_icon': ('MICO', 'text')},
    'IDLE': {'animation': ('MODL', 'text')},
    'RACE': {'name': ('FULL', 'text'), 'description': ('DESC', 'text'), 'older': ('ONAM', 'form'),
             'younger': ('YNAM', 'form'), 'facegen_main_clamp': ('PNAM', 'f'), 'facegen_face_clamp': ('UNAM', 'f')},
    'BPTD': {'model': ('MODL', 'text'), 'ragdoll': ('RAGA', 'form')},
}
for _t in WHOLE.values():
    _t.setdefault('model', ('MODL', 'text'))
WHOLE['RCPE'].pop('model')
WHOLE['RACE'].pop('model')
WHOLE['PERK'].pop('model')

STRUCTS = {
    'ARMO': {'BMDT': [('biped_flags', 'I', BIPED), ('general_flags', 'B', ARMOR_GENERAL), (None, '3s', None)],
             'DATA': [('value', 'i', None), ('health', 'i', None), ('weight', 'f', None)],
             'DNAM': [('damage_resistance', 'h', None), ('flags', 'H', ARMOR_FLAGS), ('damage_threshold', 'f', None),
                      (None, '4s', None)],
             'SNAM': [('sound', 'I', 'form'), ('sound_chance', 'B', None), (None, '3s', None),
                      ('sound_type', 'I', SOUND_TYPE)]},
    'AMMO': {'DATA': [('speed', 'f', None), ('flags', 'B', AMMO_FLAGS), (None, '3s', None), ('value', 'i', None),
                      ('clip_rounds', 'B', None)],
             'DAT2': [('projectiles_per_shot', 'I', None), ('projectile', 'I', 'form'), ('weight', 'f', None),
                      ('consumed_ammo', 'I', 'form'), ('consumed_percentage', 'f', None)]},
    'KEYM': {'DATA': [('value', 'i', None), ('weight', 'f', None)]},
    'NOTE': {'DATA': [('note_type', 'B', NOTE_TYPE)]},
    'BOOK': {'DATA': [('flags', 'B', BOOK_FLAGS), ('skill', 'b', SKILL), ('value', 'i', None), ('weight', 'f', None)]},
    'IMOD': {'DATA': [('value', 'I', None), ('weight', 'f', None)]},
    'LVLI': {'LVLF': [('flags', 'B', LEVELED_FLAGS)]},
    'LVLN': {'LVLF': [('flags', 'B', LEVELED_FLAGS)]},
    'LVLC': {'LVLF': [('flags', 'B', LEVELED_FLAGS)]},
    'RCPE': {'DATA': [('skill', 'i', SKILL_AV), ('skill_level', 'I', None), ('category', 'I', 'form'),
                      ('subcategory', 'I', 'form')]},
    'DOOR': {'FNAM': [('flags', 'B', DOOR_FLAGS)]},
    'TERM': {'DNAM': [('difficulty', 'B', TERM_DIFFICULTY), ('flags', 'B', TERM_FLAGS), ('server_type', 'B', None),
                      (None, '1s', None)]},
    'PERK': {'DATA': [('trait', 'B', None), ('min_level', 'B', None), ('ranks', 'B', None), ('playable', 'B', None),
                      ('hidden', 'B', None)]},
    'RACE': {'DATA': [
                      ('skill_1', 'b', SKILL_AV), ('boost_1', 'b', None),
                      ('skill_2', 'b', SKILL_AV), ('boost_2', 'b', None),
                      ('skill_3', 'b', SKILL_AV), ('boost_3', 'b', None),
                      ('skill_4', 'b', SKILL_AV), ('boost_4', 'b', None),
                      ('skill_5', 'b', SKILL_AV), ('boost_5', 'b', None),
                      ('skill_6', 'b', SKILL_AV), ('boost_6', 'b', None),
                      ('skill_7', 'b', SKILL_AV), ('boost_7', 'b', None),
                      (None, '2s', None), ('male_height', 'f', None), ('female_height', 'f', None),
                      ('male_weight', 'f', None), ('female_weight', 'f', None), ('flags', 'I', RACE_FLAGS)],
             'VTCK': [('male_voice', 'I', 'form'), ('female_voice', 'I', 'form')],
             'DNAM': [('male_default_hair', 'I', 'form'), ('female_default_hair', 'I', 'form')],
             'CNAM': [('male_default_hair_color', 'B', None), ('female_default_hair_color', 'B', None)]},
    'BPTD': {'BPND': [
        ('damage_mult', 'f', None), ('flags', 'B', BODY_PART_FLAGS), ('part_type', 'B', BODY_PART_TYPE),
        ('health_percent', 'B', None), ('actor_value', 'b', None), ('to_hit_chance', 'B', None),
        ('explodable_explosion_chance', 'B', None), ('explodable_debris_count', 'H', None),
        ('explodable_debris', 'I', 'form'), ('explodable_explosion', 'I', 'form'), ('tracking_max_angle', 'f', None),
        ('explodable_debris_scale', 'f', None), ('severable_debris_count', 'i', None), ('severable_debris', 'I', 'form'),
        ('severable_explosion', 'I', 'form'), ('severable_debris_scale', 'f', None),
        ('gore_translate_x', 'f', None), ('gore_translate_y', 'f', None), ('gore_translate_z', 'f', None),
        ('gore_rotate_x', 'f', None), ('gore_rotate_y', 'f', None), ('gore_rotate_z', 'f', None),
        ('severable_impact_dataset', 'I', 'form'), ('explodable_impact_dataset', 'I', 'form'),
        ('severable_decal_count', 'B', None), ('explodable_decal_count', 'B', None), (None, '2s', None),
        ('limb_replacement_scale', 'f', None)]},
    'IDLE': {'ANAM': [('parent', 'I', 'form'), ('previous', 'I', 'form')],
             'DATA': [('group_section', 'B', None), ('loop_min', 'B', None), ('loop_max', 'B', None),
                      (None, '1s', None), ('replay_delay', 'h', None), ('flags', 'B', None), (None, '1s', None)]},
}
for _t in ('ARMO', 'AMMO', 'KEYM', 'NOTE', 'BOOK', 'IMOD', 'LVLI', 'LVLN', 'LVLC', 'DOOR', 'TERM'):
    STRUCTS[_t]['OBND'] = BOUNDS

# Shorter sizes seen in FalloutNV.esm (older records): the fields that fit are read; the rest are absent.
MIN_SIZES = {('AMMO', 'DAT2'): 12, ('ARMO', 'DNAM'): 4, ('TERM', 'DNAM'): 3, ('IDLE', 'DATA'): 6}

# Subrecords that are a list's parts, or a record's own conditions: not fields of their own.
LIST_TAGS = {'LVLI': {'LVLO', 'COED'}, 'LVLN': {'LVLO', 'COED'}, 'LVLC': {'LVLO', 'COED'},
             'RCPE': {'RCIL', 'RCQY', 'RCOD', 'CTDA'},
             'TERM': {'ITXT', 'RNAM', 'ANAM', 'INAM', 'TNAM', 'SCHR', 'SCDA', 'SCTX', 'SLSD', 'SCVR', 'SCRO', 'SCRV',
                      'CTDA'},
             'PERK': {'PRKE', 'PRKC', 'CTDA', 'EPFT', 'EPFD', 'EPF2', 'EPF3', 'PRKF', 'SCHR', 'SCDA', 'SCTX', 'SCRO',
                      'SLSD', 'SCVR', 'SCRV'},
             'IDLE': {'CTDA'},
             'BPTD': {'BPTN', 'BPNN', 'BPNT', 'BPNI', 'BPND', 'NAM1', 'NAM4', 'NAM5'}}
LISTS = {'LVLI': ('entries',), 'LVLN': ('entries',), 'LVLC': ('entries',),
         'RCPE': ('ingredients', 'outputs', 'conditions'), 'TERM': ('menu',), 'PERK': ('effects', 'conditions'),
         'IDLE': ('conditions',), 'BPTD': ('parts',)}

TYPES = sorted(set(WHOLE) | set(STRUCTS))
# Struct subrecords that repeat per list item: set through their list (BPTD parts), read per subrecord.
BY_LIST = {('BPTD', 'BPND')}

# Subrecord order of a record made without a template (FNVEdit's order); '*' is where a list's parts go. A tag not
# named here stays after the one before it (MODT after MODL).
ORDER = {
    'ARMO': 'EDID OBND FULL SCRI EITM BMDT MODL MOD2 ICON MICO MOD3 MOD4 ICO2 MIC2 BMCT REPL BIPL ETYP YNAM ZNAM DATA '
            'DNAM BNAM SNAM TNAM',
    'AMMO': 'EDID OBND FULL MODL ICON MICO SCRI YNAM ZNAM DATA DAT2 ONAM QNAM RCIL',
    'KEYM': 'EDID OBND FULL MODL ICON MICO SCRI YNAM ZNAM DATA RNAM',
    'NOTE': 'EDID OBND FULL MODL ICON MICO YNAM ZNAM DATA ONAM XNAM TNAM SNAM',
    'BOOK': 'EDID OBND FULL MODL ICON MICO SCRI DESC YNAM ZNAM DATA',
    'IMOD': 'EDID OBND FULL MODL ICON MICO SCRI DESC YNAM ZNAM DATA',
    'LVLI': 'EDID OBND LVLD LVLF LVLG *', 'LVLN': 'EDID OBND LVLD LVLF * MODL', 'LVLC': 'EDID OBND LVLD LVLF * MODL',
    'RCPE': 'EDID FULL CTDA DATA *',
    'DOOR': 'EDID OBND FULL MODL SCRI SNAM ANAM BNAM FNAM',
    'TERM': 'EDID OBND FULL MODL SCRI DESC SNAM PNAM DNAM *',
    'PERK': 'EDID FULL DESC ICON MICO CTDA DATA *',
    'IDLE': 'EDID MODL CTDA ANAM DATA',
}


def canonical_order(rtype, subs):
    """Subrecords of a new record in the type's order (list parts kept together and in their own order)."""
    order = ORDER.get(rtype, '').split()
    if not order:
        return subs
    parts = LIST_TAGS.get(rtype, set())
    keyed, last, in_list = [], -1, False
    for i, s in enumerate(subs):
        inside = rtype in OWN_TAGS_IN_LIST and s.tag == 'DATA'      # a PERK effect's DATA
        in_list = (s.tag in parts and s.tag not in order) or (in_list and (s.tag in parts or inside))
        if in_list and '*' in order:
            last = order.index('*')          # a list's parts (and the DATA or CTDA inside them) stay together
        elif s.tag in order:
            last = order.index(s.tag)
        elif s.tag in parts and '*' in order:
            last = order.index('*')
        keyed.append((last, i, s))
    return [s for _k, _i, s in sorted(keyed, key=lambda t: (t[0], t[1]))]


# -- field lookup -------------------------------------------------------------------------------------------------

def fields_of(rtype):
    """{name: (tag, index or None, code, table)} of one record type; shared names only as TAG.name."""
    out, seen = {}, {}
    for name, (tag, kind) in WHOLE.get(rtype, {}).items():
        if tag in STRUCTS.get(rtype, {}):
            continue
        out[name] = (tag, None, kind, None)
    for tag, layout in STRUCTS.get(rtype, {}).items():
        if (rtype, tag) in BY_LIST:
            continue
        for i, (name, code, table) in enumerate(layout):
            if name is None:
                continue
            out['%s.%s' % (tag, name)] = (tag, i, code, table)
            seen.setdefault(name, []).append((tag, i, code, table))
    for name, hits in seen.items():
        if len(hits) == 1 and name not in out:
            out[name] = hits[0]
    return out


def describe(rtype):
    """The named fields of a type, for fnv_spec action=fields."""
    if rtype not in TYPES:
        return None
    rows = []
    for name, (tag, index, code, table) in sorted(fields_of(rtype).items()):
        if '.' in name and name.split('.', 1)[1] in fields_of(rtype):
            continue                                   # the plain name is listed
        kind = {'text': 'text', 'form': '"@EditorID"', 'forms': 'list of "@EditorID"'}.get(code)
        if kind is None:
            kind = 'number' if code in ('f',) else 'integer'
        if isinstance(table, dict):
            kind = ('list of flags: ' if _is_flags(table) else 'one of: ') + ', '.join(table)
        elif table == 'form':
            kind = '"@EditorID"'
        rows.append({'field': name, 'subrecord': tag, 'value': kind})
    return {'type': rtype, 'fields': rows, 'lists': list(LISTS.get(rtype, ())),
            'how': 'records.<EditorID>.set = {field: value}; lists: see the fnv_spec guide'}


def _is_flags(table):
    return any(table is t for t in (BIPED, ARMOR_GENERAL, ARMOR_FLAGS, AMMO_FLAGS, BOOK_FLAGS, LEVELED_FLAGS,
                                    DOOR_FLAGS, TERM_FLAGS, MENU_FLAGS, RACE_FLAGS, BODY_PART_FLAGS))


# -- encode / decode ----------------------------------------------------------------------------------------------

def _struct(layout, upto=None):
    return struct.Struct('<' + ''.join(code for _n, code, _t in layout[:upto]))


def f32(value):
    """The shortest decimal that is the same float32 (1.5707963 stays itself; 0.1f reads 0.1, not 0.100000001)."""
    if value != value or value in (float('inf'), float('-inf')):
        return value
    packed = struct.pack('<f', value)
    for digits in range(6, 10):
        short = float('%.*g' % (digits, value))
        if struct.pack('<f', short) == packed:
            return short
    return value


def decode(rtype, tag, data, form_name=None):
    """{name: value} of a struct subrecord with a layout, or None. Values stay raw numbers (flags as ints) unless
    named: flags become lists of names, enums names; form_name(raw) may name a FormID."""
    layout = STRUCTS.get(rtype, {}).get(tag)
    if layout is None:
        return None
    full = _struct(layout).size
    if len(data) != full:
        upto = next((n for n in range(len(layout), 0, -1) if _struct(layout, n).size == len(data)), None)
        if upto is None:
            return None
        layout = layout[:upto]
    out = {}
    for (name, code, table), value in zip(layout, _struct(layout).unpack(data)):
        if name is None:
            continue
        if isinstance(table, dict):
            if _is_flags(table):
                value = [k for k, bit in table.items() if value & bit] + (
                    ['0x%X' % (value & ~sum(table.values()))] if value & ~sum(table.values()) else [])
            else:
                value = next((k for k, v in table.items() if v == value), value)
        elif table == 'form':
            value = (form_name(value) if form_name else None) or '%08X' % value
        elif code == 'f':
            value = f32(value)
        out[name] = value
    return out


def _value(code, table, value, resolve, where):
    if isinstance(table, dict):
        if _is_flags(table):
            if not isinstance(value, (list, tuple)):
                raise SpecError(where, 'a list of flag names (known: %s)' % ', '.join(table))
            bits = 0
            for f in value:
                if f not in table:
                    raise SpecError(where, 'unknown flag %r (known: %s)' % (f, ', '.join(table)))
                bits |= table[f]
            return bits
        if isinstance(value, str):
            if value not in table:
                raise SpecError(where, 'one of %s (got %r)' % (', '.join(table), value))
            return table[value]
    if table == 'form' or code == 'form':
        return resolve(value, where)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError(where, 'expected a number')
    if code in ('f', 'd'):
        return float(value)
    if isinstance(value, float) and not value.is_integer():
        raise SpecError(where, 'expected an integer')
    value = int(value)
    try:
        struct.pack('<' + code, value)
    except struct.error:
        raise SpecError(where, 'out of range for this field (%s)' % code)
    return value


def set_fields(rtype, subs, values, resolve, where):
    """Apply {name: value} to a record's subrecords (a list, changed in place). Lists go to their builders."""
    if not isinstance(values, dict):
        raise SpecError(where, 'expected an object of field names and values')
    known = fields_of(rtype)
    for name, value in values.items():
        w = '%s.%s' % (where, name)
        if name in LISTS.get(rtype, ()):
            LIST_BUILDERS[name](rtype, subs, value, resolve, w)
            continue
        if name not in known:
            raise SpecError(w, 'unknown field for %s (known: %s)' % (rtype, ', '.join(sorted(
                [k for k in known if '.' not in k] + list(LISTS.get(rtype, ()))))))
        tag, index, code, table = known[name]
        if index is None:
            _set_whole(subs, tag, code, value, resolve, w, rtype)
            continue
        layout = STRUCTS[rtype][tag]
        packed = _value(code, table, value, resolve, w)
        hits = _own(rtype, subs, tag)
        data = bytearray(subs[hits[0]].data) if hits else bytearray(_struct(layout).size)
        end = _struct(layout, index + 1).size
        if len(data) < end:              # an older, shorter subrecord: grow it with zeros up to this field
            data += bytes(end - len(data))
        struct.pack_into('<' + code, data, _struct(layout, index).size, packed)
        if hits:
            subs[hits[0]] = Subrecord(tag, bytes(data))
        else:
            subs.insert(_place(rtype, subs, tag), Subrecord(tag, bytes(data)))
    return subs


# Lists whose items hold subrecords with the record's own tags: a PERK effect (from PRKE on) has a DATA. In other
# types a DATA after a list (an IDLE's or RCPE's after its conditions) is the record's own (live run 2026-11-23).
OWN_TAGS_IN_LIST = {'PERK': 'PRKE'}


def _own(rtype, subs, tag):
    """Indices of the record's own `tag` subrecords: not those inside a list item that has the same tag."""
    start = OWN_TAGS_IN_LIST.get(rtype)
    first = next((i for i, s in enumerate(subs) if s.tag == start), len(subs)) if start else len(subs)
    return [i for i, s in enumerate(subs[:first]) if s.tag == tag]


def _set_whole(subs, tag, code, value, resolve, where, rtype=None):
    if code == 'forms':
        if not isinstance(value, (list, tuple)):
            raise SpecError(where, 'a list of "@EditorID" references')
        items = [Subrecord(tag, struct.pack('<I', resolve(v, where))) for v in value]
        at = next((i for i, s in enumerate(subs) if s.tag == tag), None)
        subs[:] = [s for s in subs if s.tag != tag]
        at = len(subs) if at is None else min(at, len(subs))
        subs[at:at] = items
        return
    if code == 'text':
        if value is None:
            subs[:] = [s for s in subs if s.tag != tag]
            return
        if not isinstance(value, str) or '\0' in value:
            raise SpecError(where, 'expected text without NUL characters')
        try:
            data = zstring(value)
        except UnicodeEncodeError:
            raise SpecError(where, 'text must be representable in Windows-1252')
    elif code == 'form':
        data = struct.pack('<I', 0 if value is None else resolve(value, where))
    else:
        table = EQUIP_TYPE if tag == 'ETYP' else None
        data = struct.pack('<' + code, _value(code, table, value, resolve, where))
    hits = _own(rtype, subs, tag)
    if hits:
        subs[hits[0]] = Subrecord(tag, data)
    else:
        subs.insert(_place(rtype, subs, tag), Subrecord(tag, data))


def _place(rtype, subs, tag):
    """Where a new struct subrecord goes: after OBND/FULL/MODL-like heads, before list parts."""
    stop = LIST_TAGS.get(rtype, set())
    for i, s in enumerate(subs):
        if s.tag in stop:
            return i
    return len(subs)


# -- lists --------------------------------------------------------------------------------------------------------

def _drop(subs, tags):
    first = next((i for i, s in enumerate(subs) if s.tag in tags), None)
    subs[:] = [s for s in subs if s.tag not in tags]
    return len(subs) if first is None else first


def _int(value, where, lo, hi):
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise SpecError(where, 'an integer from %d to %d' % (lo, hi))
    return value


def leveled_entries(rtype, subs, entries, resolve, where):
    """[{"form": "@X", "level": 1, "count": 1}] -> LVLO (level i16, form, count i16), sorted by level as GECK keeps
    them. Replaces the template's entries (and their COED extra data)."""
    if not isinstance(entries, list):
        raise SpecError(where, 'a list of {"form", "level", "count"}')
    rows = []
    for n, e in enumerate(entries):
        w = '%s[%d]' % (where, n)
        if not isinstance(e, dict) or 'form' not in e or set(e) - {'form', 'level', 'count'}:
            raise SpecError(w, 'give "form" and optionally "level" (1) and "count" (1)')
        rows.append((_int(e.get('level', 1), w + '.level', 1, 32767), resolve(e['form'], w + '.form'),
                     _int(e.get('count', 1), w + '.count', 1, 32767)))
    at = _drop(subs, {'LVLO', 'COED'})
    subs[at:at] = [Subrecord('LVLO', struct.pack('<h2sIh2s', lvl, b'\0\0', form, count, b'\0\0'))
                   for lvl, form, count in sorted(rows, key=lambda r: r[0])]


def _pairs(key, tag, rtype, subs, items, resolve, where):
    if not isinstance(items, list):
        raise SpecError(where, 'a list of {"form", "count"}')
    made = []
    for n, e in enumerate(items):
        w = '%s[%d]' % (where, n)
        if not isinstance(e, dict) or 'form' not in e or set(e) - {'form', 'count'}:
            raise SpecError(w, 'give "form" and optionally "count" (1)')
        made += [Subrecord(tag, struct.pack('<I', resolve(e['form'], w + '.form'))),
                 Subrecord('RCQY', struct.pack('<I', _int(e.get('count', 1), w + '.count', 1, 2 ** 31 - 1)))]
    # rebuild: ingredients first, then outputs, each with its quantity
    groups, current = {'RCIL': [], 'RCOD': []}, None
    rest = []
    for s in subs:
        if s.tag in ('RCIL', 'RCOD'):
            current = s.tag
            groups[current].append(s)
        elif s.tag == 'RCQY' and current:
            groups[current].append(s)
        else:
            rest.append(s)
    groups[tag] = made
    at = next((i for i, s in enumerate(rest) if s.tag not in ('EDID', 'FULL', 'CTDA', 'DATA')), len(rest))
    subs[:] = rest[:at] + groups['RCIL'] + groups['RCOD'] + rest[at:]


def recipe_ingredients(rtype, subs, items, resolve, where):
    _pairs('ingredients', 'RCIL', rtype, subs, items, resolve, where)


def recipe_outputs(rtype, subs, items, resolve, where):
    _pairs('outputs', 'RCOD', rtype, subs, items, resolve, where)


def _conditions(items, resolve, where):
    from .dialogue import condition
    if not isinstance(items, list):
        raise SpecError(where, 'a list of conditions')
    return [Subrecord('CTDA', condition(c, lambda v, w: resolve(v, w), '%s[%d]' % (where, i)))
            for i, c in enumerate(items)]


def record_conditions(rtype, subs, items, resolve, where):
    """The record's own conditions (RCPE: after FULL, before DATA; IDLE: after MODL)."""
    made = _conditions(items, resolve, where)
    if rtype == 'PERK':
        head = [s for s in subs if s.tag in ('EDID', 'FULL', 'DESC', 'ICON', 'MICO')]
        old = [s for s in subs[len(head):] if s.tag == 'CTDA' and not _in_effects(subs, s)]
        rest = [s for s in subs[len(head):] if not any(s is o for o in old)]
        subs[:] = head + made + rest
        return
    at = _drop(subs, {'CTDA'})
    if rtype == 'RCPE':
        at = next((i for i, s in enumerate(subs) if s.tag == 'DATA'), at)
    subs[at:at] = made


def _in_effects(subs, sub):
    i = next(i for i, s in enumerate(subs) if s is sub)
    return any(s.tag == 'PRKE' for s in subs[:i])


EMPTY_SCHR = b'\0' * 20


def terminal_menu(rtype, subs, items, resolve, where):
    """[{"text", "result", "note": "@NOTE", "submenu": "@TERM", "flags": ["add_note"], "conditions": [...]}] -> menu
    items (ITXT RNAM ANAM [INAM] [TNAM] SCHR [CTDA...]). Result scripts are not written: an item's script is empty.
    Replaces the template's items."""
    if not isinstance(items, list) or not items:
        raise SpecError(where, 'a list of menu items, each {"text": ...}')
    made = []
    for n, item in enumerate(items):
        w = '%s[%d]' % (where, n)
        if not isinstance(item, dict) or not isinstance(item.get('text'), str) or \
                set(item) - {'text', 'result', 'note', 'submenu', 'flags', 'conditions'}:
            raise SpecError(w, 'give "text" and optionally "result", "note", "submenu", "flags", "conditions"')
        made += [Subrecord('ITXT', zstring(item['text'])), Subrecord('RNAM', zstring(item.get('result', '')))]
        made.append(Subrecord('ANAM', struct.pack('<B', _value('B', MENU_FLAGS, item.get('flags', []), resolve,
                                                             w + '.flags'))))
        if item.get('note'):
            made.append(Subrecord('INAM', struct.pack('<I', resolve(item['note'], w + '.note'))))
        if item.get('submenu'):
            made.append(Subrecord('TNAM', struct.pack('<I', resolve(item['submenu'], w + '.submenu'))))
        made.append(Subrecord('SCHR', EMPTY_SCHR))
        made += _conditions(item.get('conditions', []), resolve, w + '.conditions')
    at = _drop(subs, LIST_TAGS['TERM'])
    subs[at:at] = made


ENTRY_FUNCTIONS = {'set': 1, 'add': 2, 'multiply': 3, 'add_range': 4, 'absolute': 6, 'negative_absolute': 7,
                   'add_leveled_list': 8}


# The engine's entry point table (ms_kEntryPointTable, 74 entries): (name, number of condition tabs). Also used by
# fnv_engine_ai (fnv_perk_entries).
ENTRY_POINTS = [
    ('Calculate Weapon Damage', 3), ('Calculate My Critical Hit Chance', 3), ('Calculate My Critical Hit Damage', 3),
    ('Calculate Weapon Attack AP Cost', 2), ('Calculate Mine Explode Chance', 2), ('Adjust Range Penalty', 2),
    ('Adjust Limb Damage', 3), ('Calculate Weapon Range', 2), ('Calculate To Hit Chance', 3),
    ('Adjust Experience Points', 1), ('Adjust Gained Skill Points', 1), ('Adjust Book Skill Points', 1),
    ('Modify Recovered Health', 1), ('Calculate Inventory AP Cost', 1), ('Get Disposition', 2),
    ('Get Should Attack', 2), ('Get Should Assist', 3), ('Calculate Buy Price', 2), ('Get Bad Karma', 1),
    ('Get Good Karma', 1), ('Ignore Locked Terminal', 1), ('Add Leveled List On Death', 2), ('Get Max Carry Weight', 1),
    ('Modify Addiction Chance', 1), ('Modify Addiction Duration', 1), ('Modify Positive Chem Duration', 1),
    ('Adjust Drinking Radiation', 1), ('Activate', 2), ('Mysterious Stranger', 1), ('Has Paralyzing Palm', 1),
    ('Hacking Science Bonus', 1), ('Ignore Running During Detection', 1), ('Ignore Broken Lock', 1),
    ('Has Concentrated Fire', 1), ('Calculate Gun Spread', 2), ('Player Kill AP Reward', 3),
    ('Modify Enemy Critical Hit Chance', 3), ('Reload Speed', 2), ('Equip Speed', 2), ('Action Point Regen', 2),
    ('Action Point Cost', 2), ('Miss Fortune', 1), ('Modify Run Speed', 1), ('Modify Attack Speed', 2),
    ('Modify Radiation Consumed', 1), ('Has Pip Hacker', 1), ('Has Meltdown', 1), ('See Enemy Health', 1),
    ('Has Jury Rigging', 1), ('Modify Threat Range', 2), ('Modify Threat', 2), ('Has Fast Travel Always', 1),
    ('Knockdown Chance', 2), ('Modify Weapon Strength Req', 2), ('Modify Aiming Move Speed', 2),
    ('Modify Light Items', 1), ('Modify Damage Threshold (defender)', 3), ('Modify Chance for Ammo Item', 2),
    ('Modify Damage Threshold (attacker)', 3), ('Modify Throwing Velocity', 2), ('Chance for Item On Fire', 2),
    ('Has Unarmed Forward Power Attack', 1), ('Has Unarmed Back Power Attack', 1),
    ('Has Unarmed Crouched Power Attack', 1), ('Has Unarmed Counter Attack', 1), ('Has Unarmed Left Power Attack', 1),
    ('Has Unarmed Right Power Attack', 1), ('VATS HelperChance', 1), ('Modify Item Damage', 1),
    ('Has Improved Detection', 1), ('Has Improved Spotting', 1), ('Has Improved Item Detection', 1),
    ('Adjust Explosion Radius', 2), ('Adjust Heavy Weapon Weight', 1)]


def _entry_points():
    return ENTRY_POINTS


def perk_effects(rtype, subs, effects, resolve, where):
    """Perk effects, replacing the template's:
      {"quest": "@Q", "stage": 10}                       quest stage
      {"ability": "@SPEL"}                               ability
      {"entry_point": "Calculate Weapon Damage" | 0, "function": "multiply", "value": 1.25,
       "conditions": [[...first tab...], [...]]}         entry point (value: [min, max] for add_range,
                                                         "@LVLI" for add_leveled_list)
    each optionally with "rank" (0) and "priority" (0)."""
    if not isinstance(effects, list):
        raise SpecError(where, 'a list of effects')
    points = _entry_points()
    made = []
    for n, e in enumerate(effects):
        w = '%s[%d]' % (where, n)
        if not isinstance(e, dict):
            raise SpecError(w, 'expected an object')
        rank, priority = _int(e.get('rank', 0), w + '.rank', 0, 255), _int(e.get('priority', 0), w + '.priority', 0, 255)
        if 'quest' in e:
            if set(e) - {'quest', 'stage', 'rank', 'priority'}:
                raise SpecError(w, 'a quest stage effect takes quest, stage, rank, priority')
            made += [Subrecord('PRKE', struct.pack('<BBB', 0, rank, priority)),
                     Subrecord('DATA', struct.pack('<IB3s', resolve(e['quest'], w + '.quest'),
                                                   _int(e.get('stage', 0), w + '.stage', 0, 255), b'\0' * 3))]
        elif 'ability' in e:
            if set(e) - {'ability', 'rank', 'priority'}:
                raise SpecError(w, 'an ability effect takes ability, rank, priority')
            made += [Subrecord('PRKE', struct.pack('<BBB', 1, rank, priority)),
                     Subrecord('DATA', struct.pack('<I', resolve(e['ability'], w + '.ability')))]
        elif 'entry_point' in e:
            if set(e) - {'entry_point', 'function', 'value', 'conditions', 'rank', 'priority'}:
                raise SpecError(w, 'an entry point effect takes entry_point, function, value, conditions, rank, '
                                'priority')
            ep = e['entry_point']
            if isinstance(ep, str):
                names = {name.lower(): i for i, (name, _tabs) in enumerate(points)}
                if ep.lower() not in names:
                    raise SpecError(w + '.entry_point', 'unknown entry point %r (fnv_perk_entries lists them)' % ep)
                ep = names[ep.lower()]
            ep = _int(ep, w + '.entry_point', 0, len(points) - 1)
            tabs = points[ep][1]
            fn = e.get('function', 'set')
            if fn not in ENTRY_FUNCTIONS:
                raise SpecError(w + '.function', 'one of %s' % ', '.join(ENTRY_FUNCTIONS))
            made += [Subrecord('PRKE', struct.pack('<BBB', 2, rank, priority)),
                     Subrecord('DATA', struct.pack('<BBB', ep, ENTRY_FUNCTIONS[fn], tabs))]
            conds = e.get('conditions', [])
            if not isinstance(conds, list) or len(conds) > tabs or not all(isinstance(t, list) for t in conds):
                raise SpecError(w + '.conditions', 'a list of up to %d condition lists (one per tab)' % tabs)
            for tab, items in enumerate(conds):
                if items:
                    made.append(Subrecord('PRKC', struct.pack('<B', tab)))
                    made += _conditions(items, resolve, '%s.conditions[%d]' % (w, tab))
            value = e.get('value')
            if fn == 'add_range':
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    raise SpecError(w + '.value', '[min, max] for add_range')
                made += [Subrecord('EPFT', b'\x02'), Subrecord('EPFD', struct.pack(
                    '<ff', *[_value('f', None, v, resolve, w + '.value') for v in value]))]
            elif fn == 'add_leveled_list':
                made += [Subrecord('EPFT', b'\x03'), Subrecord('EPFD', struct.pack('<I', resolve(value, w + '.value')))]
            else:
                made += [Subrecord('EPFT', b'\x01'), Subrecord('EPFD', struct.pack(
                    '<f', _value('f', None, 1.0 if value is None else value, resolve, w + '.value')))]
        else:
            raise SpecError(w, 'give "quest", "ability" or "entry_point"')
        made.append(Subrecord('PRKF', b''))
    first = next((i for i, s in enumerate(subs) if s.tag == 'PRKE'), None)
    if first is None:
        first = next((i + 1 for i in range(len(subs) - 1, -1, -1) if subs[i].tag == 'DATA'), len(subs))
    subs[:] = subs[:first] + made


def body_parts(rtype, subs, items, resolve, where):
    """[{"part": "Head" | 0, <BPND field>: value, ...}]: changes the data (BPND) of existing body parts, found by
    their name (BPTN) or their position. A part's other subrecords (node names, models) are kept."""
    if not isinstance(items, list):
        raise SpecError(where, 'a list of {"part": name or index, field: value}')
    starts = [i for i, s in enumerate(subs) if s.tag == 'BPTN']
    names = [cstring(subs[i].data).lower() for i in starts]
    layout = {name: (i, code, table) for i, (name, code, table) in enumerate(STRUCTS['BPTD']['BPND']) if name}
    for n, item in enumerate(items):
        w = '%s[%d]' % (where, n)
        if not isinstance(item, dict) or 'part' not in item:
            raise SpecError(w, 'give "part" (its name or position) and the fields to change')
        part = item['part']
        if isinstance(part, str):
            if part.lower() not in names:
                raise SpecError(w + '.part', 'no body part %r (parts: %s)' % (part, ', '.join(names)))
            k = names.index(part.lower())
        else:
            k = _int(part, w + '.part', 0, len(starts) - 1)
        end = starts[k + 1] if k + 1 < len(starts) else len(subs)
        at = next((i for i in range(starts[k], end) if subs[i].tag == 'BPND'), None)
        if at is None:
            raise SpecError(w, 'body part %s has no data (BPND)' % names[k])
        data = bytearray(subs[at].data)
        for field, value in item.items():
            if field == 'part':
                continue
            if field not in layout:
                raise SpecError('%s.%s' % (w, field), 'unknown body part field (known: %s)' % ', '.join(layout))
            index, code, table = layout[field]
            struct.pack_into('<' + code, data, _struct(STRUCTS['BPTD']['BPND'], index).size,
                             _value(code, table, value, resolve, '%s.%s' % (w, field)))
        subs[at] = Subrecord('BPND', bytes(data))


LIST_BUILDERS = {'parts': body_parts, 'entries': leveled_entries, 'ingredients': recipe_ingredients, 'outputs': recipe_outputs,
                 'menu': terminal_menu, 'effects': perk_effects, 'conditions': record_conditions}


# -- reading lists back (fnv_record) ------------------------------------------------------------------------------

def read_lists(rtype, subs, form_name=None):
    """{list name: items} of a record, for fnv_record (best effort: what this module writes reads back)."""
    def form(raw):
        return (form_name(raw) if form_name else None) or '%08X' % raw
    out = {}
    if rtype in ('LVLI', 'LVLN', 'LVLC'):
        out['entries'] = [dict(zip(('level', 'form', 'count'), (lv, form(f), c)))
                          for lv, _p, f, c, _q in (struct.unpack('<h2sIh2s', s.data) for s in subs
                                                   if s.tag == 'LVLO' and len(s.data) == 12)]
    elif rtype == 'RCPE':
        current, rows = None, {'RCIL': [], 'RCOD': []}
        for s in subs:
            if s.tag in rows and len(s.data) == 4:
                current = s.tag
                rows[current].append({'form': form(struct.unpack('<I', s.data)[0]), 'count': 1})
            elif s.tag == 'RCQY' and current and rows[current] and len(s.data) == 4:
                rows[current][-1]['count'] = struct.unpack('<I', s.data)[0]
        out['ingredients'], out['outputs'] = rows['RCIL'], rows['RCOD']
    elif rtype == 'TERM':
        items = []
        for s in subs:
            if s.tag == 'ITXT':
                items.append({'text': cstring(s.data)})
            elif items and s.tag == 'RNAM':
                items[-1]['result'] = cstring(s.data)
            elif items and s.tag == 'INAM' and len(s.data) == 4:
                items[-1]['note'] = form(struct.unpack('<I', s.data)[0])
            elif items and s.tag == 'TNAM' and len(s.data) == 4:
                items[-1]['submenu'] = form(struct.unpack('<I', s.data)[0])
        out['menu'] = items
    elif rtype == 'BPTD':
        parts = []
        for s in subs:
            if s.tag == 'BPTN':
                parts.append({'part': cstring(s.data)})
            elif parts and s.tag == 'BPNN':
                parts[-1]['node'] = cstring(s.data)
            elif parts and s.tag == 'BPND':
                parts[-1].update(decode('BPTD', 'BPND', s.data, form_name) or {})
        out['parts'] = parts
    elif rtype == 'PERK':
        effects, current = [], None
        for s in subs:
            if s.tag == 'PRKE' and len(s.data) == 3:
                current = {'kind': {0: 'quest', 1: 'ability', 2: 'entry_point'}.get(s.data[0], s.data[0]),
                           'rank': s.data[1], 'priority': s.data[2]}
                effects.append(current)
            elif current is not None and s.tag == 'DATA' and 'data' not in current:
                current['data'] = s.data.hex()
                if current['kind'] == 'entry_point' and len(s.data) == 3:
                    points = _entry_points()
                    current['entry_point'] = points[s.data[0]][0] if s.data[0] < len(points) else s.data[0]
                    current['function'] = next((k for k, v in ENTRY_FUNCTIONS.items() if v == s.data[1]), s.data[1])
                elif current['kind'] == 'ability' and len(s.data) == 4:
                    current['ability'] = form(struct.unpack('<I', s.data)[0])
            elif current is not None and s.tag == 'EPFD' and len(s.data) == 4:
                current['value'] = f32(struct.unpack('<f', s.data)[0])
            elif s.tag == 'PRKF':
                current = None
        out['effects'] = effects
    return out
