"""Plan 2 W1 companion pieces with no tool before: the frame sampler (samp) and the xNVSE event sinks (sink).

FrameSampler: values read inside the game every N frames, with no frame missed and no request per sample. Each item
is predicate-language text that leaves one value ("[[0x011DEA3C]+0x30]:f32", "frame"), or an alias (player.x,
player.y, player.z, player.yaw, frame). The items are compiled into one program; the companion runs it in the
main-loop message and keeps every value it leaves (at most 16) in a ring, which is read once at the end.

SinkTools: subscribe to xNVSE events by name (onactivate, onhit, ondeath, onadd, onload, ...); the companion keeps a
record per event with the frame, the reference's form id and the first six words of the event's argument block.
"""
import struct
import threading
import time

from .fnv_args import hex_int
from .fnv_conv import ConvError
from .fnv_link import hexs, word
from .fnv_pred import compile_predicate

PLAYER = 0x011DEA3C
ALIASES = {
    'frame': 'frame',
    'player.x': '[[0x%08X]+0x30]:f32' % PLAYER,
    'player.y': '[[0x%08X]+0x34]:f32' % PLAYER,
    'player.z': '[[0x%08X]+0x38]:f32' % PLAYER,
    'player.yaw': '[[0x%08X]+0x2C]:f32' % PLAYER,
}
SAMP_SLOTS, MAX_VALUES, RING_MAX, READ_PAGE = 16, 16, 0x10000, 2000
TIMELINE = 200
NVSE_EVENTS = ('onactivate', 'onadd', 'onactorequip', 'ondrop', 'onactorunequip', 'ondeath', 'onmurder',
               'oncombatend', 'onhit', 'onhitwith', 'onpackagechange', 'onpackagestart', 'onpackagedone', 'onload',
               'onmagiceffecthit', 'onsell', 'onstartcombat', 'saytodone', 'ongrab', 'onopen', 'onclose', 'onfire',
               'ontrigger', 'ontriggerenter', 'ontriggerleave', 'onreset', 'ondropitem', 'exitgame',
               'exittomainmenu', 'loadgame', 'savegame', 'qqq', 'postloadgame', 'runtimescripterror', 'deletegame')


_SLOT_LOCK = threading.Lock()    # picking a free slot and setting it is one step (the companion overwrites a used slot)


class FrameSampler:
    def __init__(self, live, sleep=time.sleep):
        self.live = live
        self.sleep = sleep

    def _program(self, items):
        if not items or len(items) > MAX_VALUES:
            raise ConvError('BAD_ARGUMENTS', 'frames sampling takes 1 to %d items. Next: split the list.' % MAX_VALUES)
        texts = [ALIASES.get(str(i).strip().lower(), str(i)) for i in items]
        code, ops = '', 0
        for text in texts:
            compiled = compile_predicate(text)
            code += compiled['hex']
            ops += compiled['ops']
        if ops > 64:
            raise ConvError('PREDICATE_TOO_LONG', 'The items need %d ops together; the limit is 64. Next: fewer items '
                            'per call.' % ops)
        return code, texts

    def _free_slot(self):
        listed = self.live.link.one('samp', 'list')
        used = {int(s['id']) for s in listed.get('samplers', listed.get('samps', [])) if s.get('used', True)}
        for slot in range(SAMP_SLOTS - 1, -1, -1):
            if slot not in used:
                return slot
        raise ConvError('SAMPLERS_FULL', 'All %d samplers are in use. Next: wait for the other runs to end.' % SAMP_SLOTS)

    def sample(self, items, frames, every=1, timeout=120):
        self.live.in_game()
        code, texts = self._program(items)
        frames, every = int(frames), max(1, int(every))
        ring = min(RING_MAX, frames // every + 8)
        with _SLOT_LOCK:
            slot = self._free_slot()
            out = self.live.link.one('samp', 'set', word(slot), word(every), code, 'ff', word(ring))
        if not out.get('ok'):
            raise ConvError('SAMPLER_REFUSED', 'The companion refused the sampler (%s).' % out.get('error'), out)
        try:
            start = int(self.live.link.ping().get('frame') or 0)
            deadline = time.monotonic() + timeout
            stalled = False
            while time.monotonic() < deadline:
                frame = int(self.live.link.ping().get('frame') or 0)
                if frame - start >= frames:
                    break
                self.sleep(min(0.25, max(0.02, (frames - (frame - start)) / 120.0)))
            else:
                stalled = True
            samples, since, dropped = [], 0, 0
            while True:
                page = self.live.link.one('samp', 'read', word(slot), word(since), word(READ_PAGE))
                got = page.get('samples') or []
                samples += got
                dropped = page.get('dropped', dropped)
                if not got or page.get('next', since) == since or len(samples) >= ring:
                    break
                since = page['next']
        finally:
            try:
                self.live.link.one('samp', 'clear', word(slot))
            except Exception:  # noqa: BLE001 - a dead pipe here must not replace the error of the run
                pass
        samples = samples[:max(1, frames // every)]          # the run starts at the set, not at the first ping
        return self._summary(items, texts, samples, dropped, stalled, frames, every)

    @staticmethod
    def _summary(items, texts, samples, dropped, stalled, frames, every):
        rows = []
        for k, (item, text) in enumerate(zip(items, texts)):
            values = [(s['frame'], (s.get('values') or [None] * MAX_VALUES)[k] if k < len(s.get('values') or [])
                       else None) for s in samples]
            seen = [v for _, v in values if v is not None]
            timeline = []
            for frame, v in values:
                if not timeline or v != timeline[-1]['value']:
                    timeline.append({'frame': frame, 'value': v})
            rows.append({'item': item, 'program': text if text != item else None,
                         'first': seen[0] if seen else None, 'last': seen[-1] if seen else None,
                         'min': min(seen) if seen else None, 'max': max(seen) if seen else None,
                         'changes': max(0, len(timeline) - 1), 'unreadable': len(values) - len(seen),
                         'timeline': timeline[:TIMELINE], 'timelineCut': len(timeline) > TIMELINE})
        first = samples[0]['frame'] if samples else None
        last = samples[-1]['frame'] if samples else None
        gaps = sum(1 for a, b in zip(samples, samples[1:]) if b['frame'] - a['frame'] != every)
        return {'items': rows, 'samples': len(samples), 'frames': [first, last], 'every': every,
                'missedFrames': gaps, 'dropped': dropped, 'stalled': stalled,
                'how': 'the companion sampler: one program run in the main-loop message every %d frame(s), values '
                       'kept in a ring in the game and read once' % every}


class SinkTools:
    def __init__(self, live):
        self.live = live
        self.slots = {}

    def subscribe(self, names):
        out = {}
        for name in names:
            key = str(name).strip().lower()
            if key.startswith('msg:'):
                try:      # msg:abc was a bare ValueError (bug hunt 2026-11-18)
                    message = int(key[4:], 0)
                    if not 0 <= message <= 0xFFFFFFFF:
                        raise ValueError(key)
                except ValueError:
                    raise ConvError('BAD_ARGUMENTS', 'A window message sink is msg:<number>, for example msg:0x0100, '
                                    'not %r. Next: give the message number.' % name) from None
                result = self.live.link.one('sink', 'add', 'msg', word(message))
            else:
                if key not in NVSE_EVENTS:
                    raise ConvError('UNKNOWN_EVENT', 'No xNVSE event %r. Next: use one of %s.'
                                    % (name, ', '.join(NVSE_EVENTS)))
                result = self.live.link.one('sink', 'add', 'nvse', hexs(key))
            if not result.get('ok'):
                raise ConvError('SINK_REFUSED', 'The companion refused the %s sink (%s).' % (key, result.get('error')),
                                result)
            self.slots[key] = int(result['slot'])
            out[key] = int(result['slot'])
        return out

    def unsubscribe(self, names=None):
        if not names:
            self.live.link.one('sink', 'remove', 'ff')
            removed = sorted(self.slots)
            self.slots = {}
            return removed
        removed = []
        for name in names:
            key = str(name).strip().lower()
            if key in self.slots:
                self.live.link.one('sink', 'remove', word(self.slots.pop(key)))
                removed.append(key)
        return removed

    def _forms(self, pointers):
        """{pointer: form id} for the pointers whose first word is a vtable in the game's image and whose form id
        resolves."""
        want = sorted(p for p in pointers if 0x01400000 <= p < 0x7FFF0000)
        if not want:
            return {}
        out = {}
        for p, raw in zip(want, self.live.reads([(p, 16) for p in want])):
            if not raw or len(raw) < 16:
                continue
            vtable, _, _, form_id = struct.unpack('<IIII', raw)
            if 0x01000000 <= vtable < 0x01300000 and form_id:
                out[p] = '%08X' % form_id
        return out

    def read(self, since=0, ref=None, limit=500):
        args = ['sink', 'read', word(int(since)), word(min(int(limit), 500))]
        if ref:
            args.append(word(hex_int('ref', ref)))
        out = self.live.link.one(*args)
        if 'records' not in out:
            raise ConvError('SINK_READ_FAILED', 'The companion did not return sink records (%s).' % out.get('error'),
                            out)
        rows = [{'seq': r['seq'], 'frame': r['frame'], 'event': r['source'],
                 'ref': None if int(r['ref'], 16) == 0 else '%08X' % int(r['ref'], 16), 'words': r['words']}
                for r in out['records']]
        forms = self._forms({int(w, 16) for r in rows for w in r['words']})
        for r in rows:
            # The event's arguments: a word that points to a game form becomes its form id (the activated
            # reference, the activator, the attacker...).
            r['args'] = [forms.get(int(w, 16)) or w for w in r['words']]
        return {'records': rows, 'next': out.get('next'), 'dropped': out.get('dropped'),
                'subscribed': sorted(self.slots)}
