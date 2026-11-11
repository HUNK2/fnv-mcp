"""Dialogue records for the spec builder: topics (DIAL) and their lines (INFO), with conditions, choices and
result scripts.

Spec form (inside "records"):

  "HelloCourierAccept": {                      # a new topic: the player's line is FULL
    "type": "DIAL",
    "fields": [{"tag": "FULL", "text": "Sure, I'll look for them."}],
    "infos": [ <line>, ... ]
  },
  "GREETING": {                                # an existing master topic gets new lines
    "type": "DIAL", "override": true,          # copied from the template dump of the same name
    "infos": [ <line>, ... ]
  }

A line:

  {"key": "offer",                              # stable name: the FormID is kept by key
   "quest": "@HelloCourier",                    # the quest that owns the line (QSTI); added to the topic too
   "responses": ["What the NPC says.", {"text": "...", "emotion": "happy", "value": 50}],
   "conditions": [{"function": "GetIsID", "param1": "@Npc", "op": "==", "value": 1},
                  {"function": "GetStage", "param1": "@HelloCourier", "op": "<", "value": 20, "or": false}],
   "choices": ["@HelloCourierAccept"],          # player responses offered after the line (TCLT)
   "flags": ["goodbye"],                        # goodbye, random, say_once, run_immediately, ...
   "result": "SetStage HelloCourier 20",        # result script, run when the line ends
   "fields": [ generic field operations ]}

Result scripts are compiled by the running game (fnv_scripts_compile) and cached like SCPT records, keyed
"INFO:<topic>:<key>"; until then the line carries the source uncompiled (it says its line, the script does
nothing). The layout follows FalloutNV.esm's own lines: SCHR (begin, empty), NEXT, SCHR (end), SCDA, SCTX, SCRO.
"""
import hashlib
import struct

from .codec import Subrecord, zstring
from .records import SpecError, record

# Condition function indices (FNV condition table = script opcode - 0x1000), the ones a quest line needs most.
FUNCTIONS = {
    'getdistance': 1, 'getactorvalue': 14, 'getisid': 72, 'getstage': 58, 'getstagedone': 59,
    'getitemcount': 47, 'getdead': 46, 'gettalkedtopc': 50, 'getquestrunning': 56, 'getincell': 67,
    'getglobalvalue': 74, 'getquestvariable': 79, 'getisreference': 136, 'getquestcompleted': 546,
    'getiscurrentpackage': 161, 'getisvoicetype': 427, 'getinfaction': 71, 'getisrace': 69, 'getissex': 70,
}
# Functions whose first parameter is a form (resolved from "@EditorID").
FORM_PARAM1 = {'getisid', 'getstage', 'getstagedone', 'getitemcount', 'getquestrunning', 'getincell',
               'getglobalvalue', 'getquestvariable', 'getisreference', 'getquestcompleted', 'getiscurrentpackage',
               'getisvoicetype', 'getinfaction', 'getisrace', 'getdistance'}
OPS = {'==': 0x00, '!=': 0x20, '>': 0x40, '>=': 0x60, '<': 0x80, '<=': 0xA0}
RUN_ON = {'subject': 0, 'target': 1, 'reference': 2, 'combat_target': 3, 'linked_reference': 4}
FLAGS = {'goodbye': 0x0001, 'random': 0x0002, 'say_once': 0x0004, 'run_immediately': 0x0008,
         'info_refusal': 0x0010, 'random_end': 0x0020, 'run_for_rumors': 0x0040, 'speech_challenge': 0x0080,
         'say_once_a_day': 0x0100, 'always_darken': 0x0200}
EMOTIONS = {'neutral': 0, 'anger': 1, 'disgust': 2, 'fear': 3, 'sad': 4, 'happy': 5, 'surprise': 6, 'pained': 7}
LINE_KEYS = {'key', 'quest', 'responses', 'conditions', 'choices', 'flags', 'result', 'fields', 'speaker_next'}
HAS_RESULT_SCRIPT = 0x2000
EMPTY_SCHR = struct.pack('<4sIIIHH', b'\0' * 4, 0, 0, 0, 0, 1)


def info_key(topic, line):
    return 'INFO:%s:%s' % (topic, line['key'])


def check_lines(topic, rec, where):
    lines = rec.get('infos', [])
    if not isinstance(lines, list):
        raise SpecError(where + '.infos', 'expected a list of lines')
    keys = set()
    for i, line in enumerate(lines):
        w = '%s.infos[%d]' % (where, i)
        if not isinstance(line, dict):
            raise SpecError(w, 'expected an object')
        unknown = set(line) - LINE_KEYS
        if unknown:
            raise SpecError(w, 'unknown field(s): %s' % ', '.join(sorted(unknown)))
        key = line.get('key')
        if not isinstance(key, str) or not key.replace('_', '').replace('-', '').isalnum() or len(key) > 40:
            raise SpecError(w + '.key', 'a short name (letters, digits, _ -) that keeps the line\'s FormID')
        if key in keys:
            raise SpecError(w + '.key', 'duplicate key %r in this topic' % key)
        keys.add(key)
        if not line.get('quest'):
            raise SpecError(w + '.quest', 'every line belongs to a quest ("@QuestEditorID")')
        for flag in line.get('flags', []):
            if flag not in FLAGS:
                raise SpecError(w + '.flags', 'unknown flag %r (%s)' % (flag, ', '.join(FLAGS)))
    return lines


def result_sha(text):
    return hashlib.sha256(normalized_result(text).encode('cp1252') + b'|result').hexdigest()


def normalized_result(text):
    return '\r\n'.join(line.rstrip() for line in str(text).replace('\r\n', '\n').strip('\n').split('\n'))


def condition(c, resolve, where):
    fname = c.get('function')
    if isinstance(fname, int):
        index, key = fname, None
    else:
        key = str(fname or '').lower()
        if key not in FUNCTIONS:
            raise SpecError(where, 'unknown condition function %r (known: %s; or give its index)'
                            % (fname, ', '.join(sorted(FUNCTIONS))))
        index = FUNCTIONS[key]
    op = c.get('op', '==')
    if op not in OPS:
        raise SpecError(where + '.op', 'one of %s' % ' '.join(OPS))
    ctype = OPS[op] | (0x01 if c.get('or') else 0)

    def param(value, form):
        if value is None:
            return 0
        if isinstance(value, str) and (value.startswith('@') or form):
            return resolve(value, where)
        return int(value)
    p1 = param(c.get('param1'), key in FORM_PARAM1)
    p2 = param(c.get('param2'), False)
    run_on = RUN_ON.get(c.get('run_on', 'subject'))
    if run_on is None:
        raise SpecError(where + '.run_on', 'one of %s' % ', '.join(RUN_ON))
    ref = resolve(c['reference'], where) if c.get('reference') else 0
    return struct.pack('<B3sfH2sIIII', ctype, b'\0' * 3, float(c.get('value', 1)), index, b'\0' * 2, p1, p2, run_on, ref)


def build_line(form_id, topic, line, resolve, where, cache=None, apply_fields=None):
    """One INFO record. resolve(value, where) -> FormID. cache: the compiled-script cache (result scripts)."""
    flags = 0
    for f in line.get('flags', []):
        flags |= FLAGS[f]
    subs = [Subrecord('DATA', struct.pack('<BBH', 0, int(line.get('speaker_next', 0)), flags)),
            Subrecord('QSTI', struct.pack('<I', resolve(line['quest'], where + '.quest')))]
    responses = line.get('responses') or []
    if not responses:
        raise SpecError(where + '.responses', 'a line needs at least one response')
    for n, resp in enumerate(responses, 1):
        if isinstance(resp, str):
            resp = {'text': resp}
        emotion = EMOTIONS.get(resp.get('emotion', 'neutral'))
        if emotion is None:
            raise SpecError(where + '.responses', 'emotion is one of %s' % ', '.join(EMOTIONS))
        subs += [Subrecord('TRDT', struct.pack('<Ii4sB3sIB3s', emotion, int(resp.get('value', 50)), b'\0' * 4, n,
                                               b'\0' * 3, 0, 1, b'\0' * 3)),
                 Subrecord('NAM1', zstring(resp['text'])), Subrecord('NAM2', b'\0'), Subrecord('NAM3', b'\0')]
    for i, c in enumerate(line.get('conditions', [])):
        subs.append(Subrecord('CTDA', condition(c, resolve, '%s.conditions[%d]' % (where, i))))
    for choice in line.get('choices', []):
        subs.append(Subrecord('TCLT', struct.pack('<I', resolve(choice, where + '.choices'))))
    subs += [Subrecord('SCHR', EMPTY_SCHR), Subrecord('NEXT', b'')]
    compiled = True
    if line.get('result'):
        text = normalized_result(line['result'])
        entry = (cache or {}).get(info_key(topic, line))
        if entry and entry.get('sourceSha256') == result_sha(text) and entry.get('formId') == '%08X' % form_id:
            for tag, raw in entry['compiled']:
                subs.append(Subrecord(tag, text.encode('cp1252') if tag == 'SCTX' else bytes.fromhex(raw)))
        else:
            compiled = False
            subs += [Subrecord('SCHR', EMPTY_SCHR), Subrecord('SCTX', text.encode('cp1252'))]
    else:
        subs.append(Subrecord('SCHR', EMPTY_SCHR))
    if line.get('fields') and apply_fields:
        apply_fields(subs, line['fields'], where)
    rec = record('INFO', form_id, subs)
    rec.compiled = compiled
    if line.get('result') and compiled:
        # Record flag 0x2000: "has a result script". TESTopicInfo::GetResultScript reads the scripts from the
        # file only when it is set (all 6,277 lines of FalloutNV.esm with compiled result scripts carry it, none
        # of the 16,970 others; seen live: without it a compiled result script never ran).
        rec.flags |= HAS_RESULT_SCRIPT
    return rec
