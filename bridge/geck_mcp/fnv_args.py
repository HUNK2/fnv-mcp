"""Argument checks for the fnv_* tools (PLAN-3 S5.2). Each raises ValueError naming the parameter and the expected
form, so the envelope reports INVALID_ARGUMENT with a message the caller can act on."""
import re

HEX = re.compile(r'^(?:0x)?([0-9A-Fa-f]{1,8})$')
EDITOR_ID = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')

RANGES = {'u8': (0, 0xFF), 'i8': (-0x80, 0x7F), 'u16': (0, 0xFFFF), 'i16': (-0x8000, 0x7FFF),
          'u32': (0, 0xFFFFFFFF), 'i32': (-0x80000000, 0x7FFFFFFF), 'ptr': (0, 0xFFFFFFFF)}
FORMATS = {'<B': 'u8', '<b': 'i8', '<H': 'u16', '<h': 'i16', '<I': 'u32', '<i': 'i32'}


def hex_int(name, text):
    """A 32-bit hex number given as text (0x0087F9F0, 87F9F0) or as an int."""
    if isinstance(text, int) and not isinstance(text, bool):
        if 0 <= text <= 0xFFFFFFFF:
            return text
        raise ValueError('%s: expected a 32-bit number (0 to 0xFFFFFFFF), got %d' % (name, text))
    m = HEX.match(str(text).strip()) if isinstance(text, str) else None
    if not m:
        raise ValueError('%s: expected a hex number such as 0x0087F9F0, got %r' % (name, text))
    return int(m.group(1), 16)


def is_form_id(text):
    """Does this reference text name a form id rather than an EditorID? 0x14, 00000014 and FF000001 do; 14 does
    (an EditorID starts with a letter or _); a word that is both (Beef, ABCD) is an EditorID unless it has the 0x
    prefix or all 8 digits."""
    text = str(text).strip()
    return bool(HEX.match(text)) and (text.lower().startswith('0x') or len(text) == 8 or not EDITOR_ID.match(text))


def form_id(text):
    """A form id: 0x14, 14, 00000014 or FF000001 (or an int)."""
    if isinstance(text, int) and not isinstance(text, bool):
        return hex_int('form id', text)
    if not isinstance(text, str) or not is_form_id(text):
        raise ValueError('form id: expected 1 to 8 hex digits such as 0x14, 000A3E7C or FF000001, got %r' % (text,))
    return int(HEX.match(text.strip()).group(1), 16)


def ranged(name, value, as_type):
    """An integer that fits as_type (u8, i8, u16, i16, u32, i32, ptr, or a struct format such as '<B'); floats for
    f32/f64. Text numbers may be decimal or 0x hex."""
    kind = FORMATS.get(as_type, as_type)
    if kind in ('f32', 'f64', 'float', 'double'):
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValueError('%s: expected a number for %s, got %r' % (name, as_type, value)) from None
    try:
        number = int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        raise ValueError('%s: expected a whole number for %s, got %r' % (name, as_type, value)) from None
    low, high = RANGES.get(kind, (None, None))
    if low is not None and not low <= number <= high:
        raise ValueError('%s: %s holds %d to %d, got %d' % (name, kind, low, high, number))
    return number


def readable_type(name):
    """An MSVC decorated class name as C++ (?$SettingT@VGameSettingCollection@@ -> SettingT<GameSettingCollection>,
    .?AVTESForm@@ -> TESForm); other names unchanged."""
    text = str(name)
    m = re.match(r'^\.?\?\$(\w+)@(.*)$', text)
    if m:
        return '%s<%s>' % (m.group(1), ', '.join(re.findall(r'[VU](\w+)@', m.group(2))))
    m = re.match(r'^\.?\?A[VU](\w+)@@$', text)
    return m.group(1) if m else text


def capped(items, limit=200, newest_first=False):
    """A list cut to `limit` items: {'items', 'total', 'truncated'}. newest_first keeps the last ones (in
    their order) for lists that grow at the end."""
    items = list(items)
    total = len(items)
    kept = items[-limit:] if newest_first and total > limit else items[:limit]
    return {'items': kept, 'total': total, 'truncated': total > limit}
