"""Field watchpoints (NAMING-PLAN.md component G, proof rule 4.2 "Field watch"): which functions write (or read)
one field of one live object, with the values, from the CPU's debug registers.

  find   snapshot an object (and, one level down, the objects its pointer fields point to) before and after a
         console action, and list the dwords that changed, named from the class layouts: how to locate the
         field to watch (for example the player's health when DamageAV Health runs)
  watch  set a hardware watchpoint (DR0) on an address or object+offset, run the action (or wait), and name
         every function that touched it: the instruction after the access is where the CPU stops, so the
         writer is the function containing that address - 1, and the instruction itself is decoded
"""
import struct
import threading
import time
from collections import Counter, defaultdict

from .fnv_link import LinkError, word
from .fnv_naming import ancestors, hx, register_source

ACCESS = {'write': 1, 'readwrite': 3}
DEBUG_REGISTERS = 4
_DR_LOCK = threading.Lock()
_DR_CLAIMED = set()       # debug registers a watch of this process is using right now


def claim_debug_register(link):
    """A free debug register (0-3) for a watch, or None when all four are busy. Busy = in `dbg info`'s active mask (a
    watch or a hardware breakpoint of the debugger) or claimed by another watch of this process. Without `dbg info`
    (an older companion) only the claims count. Release with release_debug_register."""
    with _DR_LOCK:
        busy = set(_DR_CLAIMED)
        try:
            info = link.one('dbg', 'info')
        except LinkError:
            info = None
        if isinstance(info, dict):
            try:
                mask = int(info.get('active') or 0) | int(info.get('bpSlots') or 0)
            except (TypeError, ValueError):
                mask = 0
            busy |= {k for k in range(DEBUG_REGISTERS) if mask & (1 << k)}
        slot = next((k for k in range(DEBUG_REGISTERS) if k not in busy), None)
        if slot is not None:
            _DR_CLAIMED.add(slot)
        return slot


def release_debug_register(link, slot):
    """Clear only this watch's register (never `dbg clear f`, which would drop another watch's) and give it back."""
    try:
        link.one('dbg', 'clear', str(slot))
    finally:
        with _DR_LOCK:
            _DR_CLAIMED.discard(slot)


def hits_of_slot(rows, slot):
    """The hits of one debug register: `dbg read` returns the hits of every register, and word 1 is the slot."""
    out = []
    for h in rows or []:
        try:
            if len(h) < 2 or int(h[1]) == slot:
                out.append(h)
        except (TypeError, ValueError):
            out.append(h)
    return out


class FieldWatchTools:
    def __init__(self, live, naming):
        self.live, self.naming = live, naming

    @property
    def link(self):
        return self.live.link

    # ------------------------------------------------------------------ helpers
    def _ptr(self, ref):
        if isinstance(ref, int):
            return ref
        if isinstance(ref, str) and ref.lower().startswith('0x'):
            return int(ref, 16)
        return self.live.resolve(ref)['ptr']

    def _read(self, address, length):
        return self.link.read(address, length)

    def _lines(self, lines, ptr):
        out = []
        for line in lines or []:
            r = self.link.console(line, ref=ptr)
            out.append({'line': line, 'ok': bool(r.get('accepted', r.get('ok')))})
        return out

    def field_name(self, cls, offset):
        """The layout field at `offset` in `cls`, along its primary (offset 0) base chain."""
        engine = self.live.engine()
        for c in ancestors(engine, cls) if cls else []:
            try:
                _key, layout = engine.class_layout(c)
            except Exception:  # noqa: BLE001 - no layout for this class
                continue
            for f in layout.get('fields') or []:
                span = f.get('span') or 4
                if f.get('offset') is not None and f['offset'] <= offset < f['offset'] + span:
                    return '%s::%s (+0x%X%s)' % (c, f['name'], f['offset'],
                                                  '' if f['offset'] == offset else ' +%d' % (offset - f['offset']))
        return None

    # ------------------------------------------------------------------ find
    def find(self, ref='player', trigger=None, size=0x1000, follow=0x400, seconds=0.5):
        """Dwords of the object (and of the objects its pointer fields point at, `follow` bytes each) that a
        console action changed."""
        self.live.in_game()
        ptr = self._ptr(ref)
        cls = (self.live.rtti([ptr]).get(ptr) or {}).get('class')
        before = self._read(ptr, size)
        pointers = {}
        if follow:
            words = struct.unpack('<%dI' % (size // 4), before[:size // 4 * 4])
            cands = sorted({w for w in words if 0x01000000 <= w < 0x7FFF0000 and not w & 3})
            classes = self.live.rtti(cands) if cands else {}
            for k, w in enumerate(words):
                if w in classes and (classes[w] or {}).get('class') and w not in {p[0] for p in pointers.values()}:
                    try:
                        pointers[k * 4] = (w, classes[w]['class'], self._read(w, follow))
                    except LinkError:
                        pass
        ran = self._lines(trigger, ptr)
        time.sleep(seconds)
        after = self._read(ptr, size)
        changes = _diff(before, after, ptr, 0)
        for off, (w, wcls, old) in pointers.items():
            try:
                new = self._read(w, follow)
            except LinkError:
                continue
            for c in _diff(old, new, w, 0):
                c['via'] = '[this+0x%X] -> %s' % (off, wcls)
                c['class'] = wcls
                changes.append(c)
        for c in changes:
            c['field'] = self.field_name(c.get('class', cls), c['offset'])
        return {'object': hx(ptr), 'class': cls, 'trigger': ran, 'changed': changes[:80],
                'note': 'watch one of these with action=watch (address) to name the function that writes it'}

    # ------------------------------------------------------------------ watch
    def watch(self, address=None, ref=None, offset=0, length=4, access='write', scope='main', trigger=None,
              seconds=0.0, trigger_ref=None, max_hits=300):
        self.live.in_game()
        if address is None:
            if ref is None:
                raise ValueError('give an address, or a ref and an offset')
            address = self._ptr(ref) + int(offset)
        address = int(address, 16) if isinstance(address, str) else int(address)
        if access not in ACCESS:
            raise ValueError('access is write or readwrite')
        slot = claim_debug_register(self.link)
        if slot is None:
            raise ValueError('All four debug registers are in use (watches or hardware breakpoints). Next: clear a '
                             'hardware break (fnv_debug break clear) or wait for the other watch.')
        try:
            r = self.link.one('dbg', 'set', str(slot), word(address), word(int(length)), word(ACCESS[access]),
                              '1' if scope == 'all' else '0')
            if not r.get('ok'):
                raise ValueError('The companion could not set the watchpoint: %s' % (r.get('error') or r))
        except BaseException:
            try:
                release_debug_register(self.link, slot)
            except Exception:  # noqa: BLE001 - a dead pipe here must not replace the original error
                pass
            raise
        try:
            # inside the try: a reply without seq or a journal error must still free the register (bug hunt 2026-11-18)
            seq = r['seq']
            entry = self.live.journal.add({'kind': 'watchpoint', 'address': hx(address),
                                           'undo': {'op': 'dbg_clear', 'slot': slot, 'address': hx(address)}})
            ran = self._lines(trigger, self._ptr(trigger_ref) if trigger_ref else (self._ptr(ref) if ref else 0))
            if seconds:
                time.sleep(float(seconds))
            time.sleep(0.2)
            hits = hits_of_slot(self.link.one('dbg', 'read', word(seq)).get('hits') or [], slot)
        finally:
            release_debug_register(self.link, slot)
        out = dict(self._summarise(address, hits[:max_hits]), trigger=ran, journalId=entry['id'], access=access,
                   scope=scope, length=length)
        self._store(out, ref, offset)
        return out

    def _store(self, out, ref, offset):
        import json as _json
        db = self.naming.db()
        try:
            db.execute('CREATE TABLE IF NOT EXISTS watches (at REAL, address INTEGER, function INTEGER, access TEXT, '
                       'hits INTEGER, field TEXT, result TEXT)')
            field = None
            if ref is not None:
                try:
                    ptr = self._ptr(ref)
                    cls = (self.live.rtti([ptr]).get(ptr) or {}).get('class')
                    field = self.field_name(cls, int(offset))
                except Exception:  # noqa: BLE001 - a name for the field is optional
                    field = None
            for f in out['functions']:
                if f.get('address'):
                    db.execute('INSERT INTO watches VALUES (?,?,?,?,?,?,?)',
                               (time.time(), int(out['address'], 16), int(f['address'], 16), out['access'], f['hits'],
                                field, _json.dumps(f, default=str)))
            db.commit()
        finally:
            db.close()

    def _summarise(self, address, hits):
        engine = self.live.engine()
        image = self.live.deep._image(required=False)
        main = (self.link.hello or self.link.ping()).get('mainThreadId')
        by_fn = defaultdict(lambda: {'hits': 0, 'values': Counter(), 'callers': Counter(), 'threads': Counter()})
        rows = []
        for h in hits:
            eip, value, ret = int(h[2], 16), int(h[4], 16), int(h[5], 16)
            hit = engine.containing(eip - 1)
            fn = hit[0] if hit else None
            key = hx(fn) if fn else hx(eip)
            d = by_fn[key]
            d['hits'] += 1
            d['values'][value] += 1
            d['threads']['main' if h[3] == main else 'worker'] += 1
            up = engine.containing(int(h[6], 16) - 1) if int(h[6], 16) else None
            if up:
                d['callers'][engine.functions[up[0]]['name']] += 1
            if len(rows) < 12:
                rows.append({'seq': h[0], 'after': hx(eip), 'value': value,
                             'float': round(struct.unpack('<f', struct.pack('<I', value))[0], 4)})
        out = []
        for key, d in sorted(by_fn.items(), key=lambda kv: -kv[1]['hits']):
            fn = int(key, 16)
            entry = dict(engine.summary(fn, with_location=False)) if fn in engine.functions else {'address': key}
            entry.update(hits=d['hits'], threads=dict(d['threads']),
                         values=[{'int': v, 'float': round(struct.unpack('<f', struct.pack('<I', v))[0], 4), 'times': n}
                                 for v, n in d['values'].most_common(6)],
                         calledFrom=[{'function': n, 'times': c} for n, c in d['callers'].most_common(4)])
            out.append(entry)
        if image is not None:
            # the instruction that made the access, for each function
            seen = set()
            for h in hits:
                eip = int(h[2], 16)
                hit = engine.containing(eip - 1)
                if not hit or hit[0] in seen:
                    continue
                seen.add(hit[0])
                insn = _instruction_before(image, eip)
                for entry in out:
                    if entry.get('address') == hx(hit[0]):
                        entry['instruction'] = insn
        return {'address': hx(address), 'hits': len(hits), 'functions': out, 'firstHits': rows}


def _instruction_before(image, eip, window=16):
    """The instruction that ends at eip (a data breakpoint reports the one after the access)."""
    from .fnv_probe import disassembler
    code = image.read(eip - window, window)
    if not code:
        return None
    md = disassembler()
    best = None
    for start in range(window):
        insns = list(md.disasm_lite(code[start:], eip - window + start))
        if insns and insns[-1][0] + insns[-1][1] == eip and (best is None or len(insns) > len(best)):
            best = insns
    if not best:
        return None
    at, _size, mnemonic, op_str = best[-1]
    return '%s: %s %s' % (hx(at), mnemonic, op_str)


def _diff(old, new, base, start):
    out = []
    n = min(len(old), len(new)) // 4 * 4
    for k in range(0, n, 4):
        a, b = old[k:k + 4], new[k:k + 4]
        if a != b:
            ia, ib = struct.unpack('<I', a)[0], struct.unpack('<I', b)[0]
            fa, fb = struct.unpack('<f', a)[0], struct.unpack('<f', b)[0]
            out.append({'offset': start + k, 'address': hx(base + start + k), 'old': ia, 'new': ib,
                        'oldFloat': round(fa, 4), 'newFloat': round(fb, 4),
                        'floatDelta': round(fb - fa, 4) if abs(fa) < 1e9 and abs(fb) < 1e9 else None})
    return out
