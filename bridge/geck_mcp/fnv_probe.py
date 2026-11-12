"""Mass probes for naming the decomp's functions from what runs (fnv-mcp NAMING-PLAN N1-N2).

N1, the planner (offline): for every chosen function it decides how a probe can count its calls without breaking
it, from your own clean FalloutNV.exe and the engine map:
  - entry: a 5-byte jump over whole prologue instructions (the companion's watch-hook decoder), with three extra
    checks a mass install needs: the moved bytes fit inside the function, no branch anywhere in the function lands
    inside them, and no call sits before the last moved instruction (a thread inside that callee would return into
    the jump);
  - sites: functions too small for that (a 3-byte getter) are counted at their callers instead: every vtable slot
    that holds them (MSVC RTTI vtables) and the rel32 of every direct call or jmp to them (from the call graph).
    Pointer calls from elsewhere are not seen, so their counts are lower bounds;
  - none, with the reason.
N2, the probe set (live): the companion's `probe` ops build a 48-byte counting stub per function, then patch every
probe at once with the game's other threads suspended. The counters, caller tables and samples are read in bulk
with ReadProcessMemory. Probes are journalled; fnv_undo or `remove` puts every original byte back.

A function that crashed the game while probed goes on a quarantine list (state/probe_quarantine.json) and is never
probed again; it is found from the companion's crash-context file of the crashed process.
"""
import ctypes
import json
import os
import re
import struct
import time
from array import array
from collections import Counter
from ctypes import wintypes
from pathlib import Path

from .fnv_deep import HookError, decode_prologue
from .fnv_link import word
from .fnv_state import GameIdentity, locked, read_json, update_json, write_json

TARGET_STATUSES = ('unnamed', 'decomp', 'class_proven', 'probable', 'rejected')
ANALYSIS_VERSION = 1
WORDS = 20
CALLER_SLOTS = 16
STATES = {0: 'staged', 1: 'armed', 2: 'disarmed', 3: 'changed'}
MODE_ENTRY, MODE_SITES, MODE_INT3 = 0, 1, 2
SITE_POINTER, SITE_REL32 = 1, 2
BATCH = 2000


class ProbeError(HookError):
    pass


_DISASSEMBLER = None


def disassembler():
    """capstone (x86-32), imported on first use: it is the optional `naming` extra, so the server still starts
    without it and only the probe planner and caller decoding need it."""
    global _DISASSEMBLER
    if _DISASSEMBLER is None:
        try:
            import capstone
        except ImportError:
            raise ProbeError('CAPSTONE_MISSING', 'The probe planner needs capstone: pip install "capstone==5.0.7" '
                             '(the naming extra of the bridge)') from None
        _DISASSEMBLER = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    return _DISASSEMBLER


# ===================================================================================== reading the game's memory
class ProcessMemory:
    """ReadProcessMemory on the game (or the test host): bulk reads of the probe arrays without the pipe."""

    def __init__(self, pid):
        self.k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.k32.OpenProcess.restype = wintypes.HANDLE
        self.k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                               ctypes.POINTER(ctypes.c_size_t)]
        self.k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.k32.OpenProcess(0x0010 | 0x0400, False, pid)  # VM_READ | QUERY_INFORMATION
        if not self.handle:
            raise ProbeError('PROCESS_UNREADABLE', 'Cannot open process %s for reading (error %d)'
                             % (pid, ctypes.get_last_error()))

    def read(self, address, length):
        out = bytearray(length)
        done = 0
        while done < length:
            n = min(1 << 20, length - done)
            buf = (ctypes.c_char * n).from_buffer(out, done)
            got = ctypes.c_size_t()
            if not self.k32.ReadProcessMemory(self.handle, ctypes.c_void_p(address + done), buf, n, ctypes.byref(got)) \
                    or got.value != n:
                raise ProbeError('READ_FAILED', 'ReadProcessMemory failed at 0x%08X' % (address + done))
            done += n
        return bytes(out)

    def u32s(self, address, count):
        a = array('I')
        a.frombytes(self.read(address, 4 * count))
        return a

    def close(self):
        if self.handle:
            self.k32.CloseHandle(self.handle)
            self.handle = None


# ===================================================================================== N1: the planner
def _target(op_str):
    try:
        return int(op_str, 16)
    except ValueError:
        return None


class ProbePlanner:
    def __init__(self, engine, image, quarantine=(), int3=False):
        self.engine = engine
        self.image = image
        self.quarantine = set(quarantine)
        self.int3 = int3  # also plan int3 probes for functions with no entry and no call site (N7)
        self.md = disassembler()
        self._slots = None

    # ------------------------------------------------------------------ selection
    def select(self, statuses=None, addresses=None, start=None, end=None, cls=None, callees_of=None, limit=None,
               part=None):
        e = self.engine
        if addresses:
            chosen = [e.resolve(a) for a in addresses]
        else:
            wanted = set(statuses or TARGET_STATUSES)
            chosen = [a for a in e.addresses if e.functions[a]['status'] in wanted]
            if callees_of:
                roots = [e.resolve(r) for r in callees_of]
                reach = set()
                for r in roots:
                    reach.update(e.calls.get(r, []))
                    reach.update(e.tails.get(r, []))
                chosen = [a for a in chosen if a in reach]
        if start is not None:
            chosen = [a for a in chosen if a >= start]
        if end is not None:
            chosen = [a for a in chosen if a < end]
        if cls:
            prefix = cls.lower() + '::'
            chosen = [a for a in chosen if e.functions[a]['name'].lower().startswith(prefix)]
        if part:
            k, n = (int(x) for x in str(part).split('/'))
            chosen = chosen[k - 1::n]
        chosen = sorted(set(chosen))
        return chosen[:limit] if limit else chosen

    # ------------------------------------------------------------------ per function
    def slots(self):
        if self._slots is None:
            self._slots = {}
            for cls, info in (self.engine.rtti or {}).items():
                for vt in info.get('vtables', []):
                    base = int(vt['address'], 16)
                    for k, s in enumerate(vt.get('slots') or []):
                        a = s if isinstance(s, str) else (s or {}).get('address')
                        if a:
                            self._slots.setdefault(int(a, 16), []).append(base + 4 * k)
        return self._slots

    def analyse(self, address):
        f = self.engine.functions.get(address)
        if f is None:
            return {'mode': None, 'reason': 'not a decomp function'}
        if address in self.quarantine:
            return {'mode': None, 'reason': 'quarantined (crashed the game while probed)'}
        size = int(f.get('size') or 0)
        code = self.image.read(address, max(size, 32))
        if code is None:
            return {'mode': None, 'reason': 'outside the exe image'}
        entry = self._entry(address, code, size)
        if entry.get('mode') == 'entry':
            return entry
        sites = self._sites(address)
        if sites['sites']:
            sites['entryProblem'] = entry['reason']
            return sites
        if self.int3:
            trap = self._int3(code)
            if trap.get('mode'):
                trap['entryProblem'] = entry['reason']
                return trap
        return {'mode': None, 'reason': entry['reason'] + '; no vtable slot or direct call site either'}

    @staticmethod
    def _int3(code):
        """One 0xCC on the first instruction; the trampoline runs that instruction relocated (rel32 only)."""
        if code[:1] == bytes([0xC3]):
            return {'mode': 'int3', 'stolen': 1, 'relocs': []}  # an empty function: `ret` runs anywhere
        if code[:1] == bytes([0xC2]):
            return {'mode': 'int3', 'stolen': 3, 'relocs': []}  # `ret n`
        try:
            stolen, relocs = decode_prologue(code[:32], need=1)
        except HookError as e:
            return {'mode': None, 'reason': str(e.message if hasattr(e, 'message') else e)}
        if len(relocs) > 3 or stolen > 15:
            return {'mode': None, 'reason': 'first instruction cannot be relocated'}
        return {'mode': 'int3', 'stolen': stolen, 'relocs': relocs}

    def _entry(self, address, code, size):
        try:
            stolen, relocs = decode_prologue(code[:32])
        except HookError as e:
            return {'mode': None, 'reason': str(e.message if hasattr(e, 'message') else e)}
        if size and stolen > size:
            return {'mode': None, 'reason': 'the function is shorter than the moved bytes'}
        if len(relocs) > 3:
            return {'mode': None, 'reason': 'more than 3 relative fields in the moved bytes'}
        moved = list(self.md.disasm_lite(code[:stolen], address))
        for k, (_, _, mnemonic, _) in enumerate(moved):
            if mnemonic == 'call' and k != len(moved) - 1:
                return {'mode': None, 'reason': 'a call before the last moved instruction'}
        for at, _, mnemonic, op_str in self.md.disasm_lite(code[:size] if size else code, address):
            if mnemonic.startswith('j') or mnemonic.startswith('loop') or mnemonic == 'call':
                t = _target(op_str)
                if t is not None and address < t < address + stolen:
                    return {'mode': None, 'reason': 'a branch at 0x%08X lands inside the moved bytes' % at}
        return {'mode': 'entry', 'stolen': stolen, 'relocs': relocs}

    def _sites(self, address):
        sites, short = [], 0
        for slot in self.slots().get(address, []):
            sites.append((SITE_POINTER, slot))
        for caller, _kind in self.engine.callers.get(address, []):
            f = self.engine.functions.get(caller)
            size = int((f or {}).get('size') or 0)
            if not size:
                continue
            code = self.image.read(caller, size)
            for insn in self.md.disasm(code, caller):
                if insn.mnemonic not in ('call', 'jmp') and not insn.mnemonic.startswith('j'):
                    continue
                if _target(insn.op_str) != address:
                    continue
                b = insn.bytes
                if b[0] in (0xE8, 0xE9) and insn.size == 5:
                    sites.append((SITE_REL32, insn.address + 1))
                elif b[0] == 0x0F and 0x80 <= b[1] <= 0x8F and insn.size == 6:
                    sites.append((SITE_REL32, insn.address + 2))
                else:
                    short += 1
        sites = sorted(set(sites), key=lambda s: s[1])
        return {'mode': 'sites' if sites else None, 'sites': sites, 'shortJumpsMissed': short,
                'reason': None if sites else 'no sites'}

    # ------------------------------------------------------------------ the whole set
    def plan(self, addresses, cache=None):
        results = {}
        for a in addresses:
            key = '%08X' % a
            r = cache.get(key) if cache is not None else None
            if r is not None and r.get('mode') is None and self.int3 and not r.get('reason', '').startswith('quarantined'):
                r = None  # a function the cached plan could not probe may take an int3 probe
            if r is None:
                r = self.analyse(a)
                if cache is not None and r.get('mode') != 'int3' and not (r.get('reason') or '').startswith('quarantined'):
                    cache[key] = r
            results[a] = r
        return results


def summarise(results, engine, examples=6):
    modes = Counter(r['mode'] or 'none' for r in results.values())
    reasons = Counter()
    for r in results.values():
        if r['mode'] is None:
            reasons[_reason_kind(r['reason'])] += 1
    by_status = Counter((engine.functions[a]['status'], r['mode'] or 'none') for a, r in results.items())
    none = [a for a, r in results.items() if r['mode'] is None][:examples]
    site_reasons = Counter(_reason_kind(r['entryProblem']) for r in results.values() if r.get('entryProblem'))
    return {'functions': len(results), 'entry': modes.get('entry', 0), 'sites': modes.get('sites', 0),
            'int3': modes.get('int3', 0),
            'notProbeable': modes.get('none', 0), 'whyAtSites': dict(site_reasons.most_common()),
            'siteCount': sum(len(r.get('sites') or []) for r in results.values()),
            'reasons': dict(reasons.most_common()),
            'byStatus': {'%s/%s' % k: v for k, v in sorted(by_status.items())},
            'examplesNotProbeable': [dict(engine.summary(a, with_location=False), reason=results[a]['reason'])
                                     for a in none]}


def _reason_kind(text):
    for key in ('quarantined', 'shorter than', 'branch at', 'call before', 'relative fields', 'Prologue longer',
                'jump inside', 'jumps away', 'outside the exe'):
        if key in text:
            return {'branch at': 'a branch lands inside the moved bytes'}.get(key, text.split(';')[0])
    return text.split(';')[0].split(' in the ')[0]


# ===================================================================================== N2: the live probe set
class ProbeTools:
    def __init__(self, live):
        self.live = live
        self._cache = None

    @property
    def link(self):
        return self.live.link

    @property
    def state_dir(self):
        return Path(self.live.project.state_dir)

    def _path(self, name):
        return self.state_dir / name

    def _load(self, name, default):
        return read_json(self._path(name), default)

    def _save(self, name, value):
        with locked(self._path(name)):
            write_json(self._path(name), value)

    def quarantine(self):
        return {int(k, 16): v for k, v in self._load('probe_quarantine.json', {}).items()}

    def planner(self, int3=False):
        engine = self.live.engine()
        image = self.live.deep._image(required=True)
        return ProbePlanner(engine, image, self.quarantine(), int3=int3)

    def _analysis_cache(self, planner):
        g = planner.engine.generated
        st = os.stat(planner.image.path)
        key = '%s|%s|%d|%d|v%d' % (g.get('at'), (g.get('decomp') or {}).get('commit'), st.st_size, int(st.st_mtime),
                                   ANALYSIS_VERSION)
        if self._cache is None or self._cache.get('key') != key:
            loaded = self._load('probe_analysis.json', {})
            self._cache = loaded if loaded.get('key') == key else {'key': key, 'results': {}}
        return self._cache

    def plan(self, selection=None, int3=False):
        planner = self.planner(int3)
        addresses = planner.select(**(selection or {}))
        cache = self._analysis_cache(planner)
        before = len(cache['results'])
        started = time.monotonic()
        results = planner.plan(addresses, cache['results'])
        if len(cache['results']) != before:
            self._save('probe_analysis.json', cache)
        out = summarise(results, planner.engine)
        out['seconds'] = round(time.monotonic() - started, 1)
        out['quarantined'] = len(planner.quarantine)
        return out, results, planner

    # ------------------------------------------------------------------ install
    def supported(self):
        r = self.link.one('probe', 'info')
        if r.get('error') == 'unknown_op':
            raise ProbeError('COMPANION_TOO_OLD', 'The engine companion has no probe support: reinstall it with '
                             'fnv_companion_install (game closed)')
        return r

    def _memory(self):
        pid = (self.link.hello or self.link.ping()).get('pid')
        return ProcessMemory(int(pid))

    def install(self, selection=None, samples=8, exit_samples=4, every=64, dry_run=False, int3=False, int3_budget=None):
        self.live.ready()
        info = self.supported()
        if info.get('ok') and info.get('armed'):
            raise ProbeError('PROBES_ARMED', '%d probes are armed already: remove them first' % info['armed'])
        crash = self._quarantine_from_last_session()
        summary, results, planner = self.plan(selection, int3)
        chosen = [(a, r) for a, r in sorted(results.items()) if r['mode']]
        if not chosen:
            raise ProbeError('NOTHING_TO_PROBE', 'No probeable function in the selection', summary)
        if every and every & (every - 1):
            raise ValueError('every must be a power of two (or 0 for no sampling after the first calls)')
        memory = self._memory()
        try:
            live_changed, probes, sites, skipped = self._live_check(memory, planner, chosen)
        finally:
            memory.close()
        out = {'plan': summary, 'liveChanged': len(live_changed), 'liveChangedExamples': live_changed[:8],
               'probes': len(probes), 'sites': len(sites)}
        if skipped:
            # another module hooked these call sites: calls through them are not counted
            out['sitesSkipped'] = len(skipped)
            out['sitesSkippedExamples'] = skipped[:8]
        if crash:
            out['quarantinedFromLastCrash'] = crash
        if dry_run:
            return out
        hooked = {int(h['target'], 16) for h in (self.link.one('hooks').get('hooks') or []) if h.get('stolen')}
        if hooked:
            keep = [p for p in probes if p[0] not in hooked]
            out['skippedWatchHooks'] = len(probes) - len(keep)
            index = {p[0]: k for k, p in enumerate(keep)}
            sites = [(index[probes[i][0]], kind, at) for i, kind, at in sites if probes[i][0] in index]
            probes = keep
        r = self.link.one('probe', 'init', word(len(probes)), word(samples), word(exit_samples), word(every))
        if not r.get('ok'):
            raise ProbeError('PROBE_INIT_FAILED', 'probe init failed: %s' % r.get('error'), r)
        if int3_budget:
            self.link.one('probe', 'int3budget', word(int(int3_budget)))
        for k in range(0, len(probes), BATCH):
            records = b''.join(struct.pack('<IBBB3sH', a, mode, stolen, len(relocs), bytes((relocs + [0, 0, 0])[:3]), 0)
                               for a, mode, stolen, relocs in probes[k:k + BATCH])
            r = self.link.one('probe', 'add', records.hex())
            if not r.get('ok'):
                raise ProbeError('PROBE_ADD_FAILED', 'probe add failed at %d: %s' % (k + r.get('at', 0), r.get('error')),
                                 {'probe': '0x%08X' % probes[k + r.get('at', 0)][0]})
        for k in range(0, len(sites), BATCH):
            records = b''.join(struct.pack('<IB3xI', i, kind, at) for i, kind, at in sites[k:k + BATCH])
            r = self.link.one('probe', 'site', records.hex())
            if not r.get('ok'):
                raise ProbeError('PROBE_SITE_FAILED', 'probe site failed: %s' % r.get('error'), r)
        session = {'schema': 1, 'pid': (self.link.hello or {}).get('pid'), 'started': (self.link.hello or {}).get('started'),
                   'installedAt': time.time(), 'selection': selection or {}, 'config': [samples, exit_samples, every],
                   'probes': [[a, mode, stolen] for a, mode, stolen, _ in probes], 'sites': sites,
                   'code': r.get('code') or info.get('code'), 'armed': False}
        arms = []
        for _ in range(6):
            a = self.link.one('probe', 'arm')
            arms.append(a)
            if not a.get('busy'):
                break
            time.sleep(0.05)
        info = self.link.one('probe', 'info')
        session['code'], session['codeSize'] = info.get('code'), info.get('codeSize')
        session['armed'] = True
        self._save('probe_session.json', session)
        entry = self.live.journal.add({'kind': 'probe', 'probes': len(probes), 'sites': len(sites),
                                       'undo': {'op': 'probe_disarm'}})
        out.update({'armed': info.get('armed'), 'staged': info.get('staged'), 'changed': info.get('changed'),
                    'arm': arms, 'journalId': entry['id'],
                    'config': {'samples': samples, 'exitSamples': exit_samples, 'every': every}})
        return out

    def _live_check(self, memory, planner, chosen):
        """Entry bytes and site fields in the running game must equal the clean exe: another module may have patched
        them since (xNVSE and plugins hook game code at start-up)."""
        changed, probes, sites, skipped = [], [], [], []
        for a, r in chosen:
            if r['mode'] in ('entry', 'int3'):
                n = r['stolen']
                live, clean = memory.read(a, n), planner.image.read(a, n)
                if live != clean:
                    changed.append({'address': '0x%08X' % a, 'live': live.hex(' '), 'clean': clean.hex(' ')})
                    continue
                probes.append((a, MODE_ENTRY if r['mode'] == 'entry' else MODE_INT3, n, list(r['relocs'])))
            else:
                good = []
                for kind, at in r['sites']:
                    live, clean = memory.read(at, 4), planner.image.read(at, 4)
                    if live == clean:
                        good.append((kind, at))
                    else:
                        skipped.append({'function': '0x%08X' % a, 'site': '0x%08X' % at, 'live': live.hex(' '),
                                        'clean': clean.hex(' ')})
                if not good:
                    changed.append({'address': '0x%08X' % a, 'note': 'every call site changed'})
                    continue
                index = len(probes)
                probes.append((a, MODE_SITES, 0, []))
                sites.extend((index, kind, at) for kind, at in good)
        return changed, probes, sites, skipped

    def _quarantine_from_last_session(self):
        """If the last probe set was still armed when its game process ended, find the faulting function in the
        companion's crash-context file of that process and quarantine it."""
        session = self._load('probe_session.json', None)
        if not session or not session.get('armed'):
            return None
        here = GameIdentity.parse(self.link.hello or self.link.ping())
        then = GameIdentity.parse(session)
        if here and then and here.same_game(then):
            return None
        session['armed'] = False
        self._save('probe_session.json', session)
        folder = Path(os.environ.get('LOCALAPPDATA', '')) / 'fnvmcp'
        found = []
        for path in sorted(folder.glob('crash-context-%s-*.json' % session.get('pid'))):
            try:
                ctx = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            eip = int(ctx.get('address') or '0', 16)
            culprit = self._culprit(session, eip, ctx)
            if culprit:
                found.append(culprit)
        if found:
            def add(q):
                for c in found:
                    q['%08X' % c['address']] = {'reason': c['why'], 'pid': session.get('pid'),
                                                'started': session.get('started'), 'at': time.time()}
            update_json(self._path('probe_quarantine.json'), add, {})
        return [{'address': '0x%08X' % c['address'], 'why': c['why']} for c in found] or \
            {'note': 'the last probe set was armed when its game process ended; no crash context points at a probe'}

    @staticmethod
    def _culprit(session, eip, ctx):
        code = int(session.get('code') or '0', 16)
        size = int(session.get('codeSize') or 0)
        probes = session.get('probes') or []
        if code and code <= eip < code + size:
            k = (eip - code) // 80
            if k < len(probes):
                return {'address': probes[k][0], 'why': 'fault inside its probe stub at 0x%08X' % eip}
        for a, mode, stolen in probes:
            if mode == MODE_ENTRY and a <= eip < a + max(stolen, 5) + 16:
                return {'address': a, 'why': 'fault in its first bytes at 0x%08X' % eip}
        for frame in ctx.get('hookedCalls') or []:
            if frame.get('label') == 'probe' and frame.get('probeIndex') is not None and frame['probeIndex'] < len(probes):
                return {'address': probes[frame['probeIndex']][0],
                        'why': 'innermost probed call with its result pending at the fault (0x%08X)' % eip}
        return None

    # ------------------------------------------------------------------ status, data
    def session(self):
        s = self._load('probe_session.json', None)
        if not s:
            raise ProbeError('NO_PROBES', 'No probe set installed (probe install)')
        return s

    def info(self):
        r = self.supported()
        if not r.get('ok'):
            raise ProbeError('NO_PROBES', 'No probe set in this game process', r)
        return r

    def counts(self, memory=None):
        info = self.info()
        own = memory is None
        memory = memory or self._memory()
        try:
            return memory.u32s(int(info['counts'], 16), info['used'])
        finally:
            if own:
                memory.close()

    def status(self, top=25):
        info = self.info()
        session = self.session()
        counts = self.counts()
        engine = self.live.engine()
        ranked = sorted(range(len(counts)), key=lambda k: -counts[k])[:top]
        probes = session['probes']
        return {'probes': info['used'], 'armed': info['armed'], 'staged': info['staged'], 'changed': info['changed'],
                'disarmed': info['disarmed'], 'sites': info['sites'], 'called': sum(1 for c in counts if c),
                'totalCalls': sum(counts), 'action': info['action'],
                'config': {'samples': info['limit'], 'exitSamples': info['exitSamples'],
                           'every': 0 if info['mask'] == '0xffffffff' else int(info['mask'], 16) + 1},
                'top': [dict(engine.summary(probes[k][0], with_location=False), calls=counts[k]) for k in ranked if counts[k]]}

    def read(self, functions, max_samples=8):
        """Samples and callers of chosen probed functions, decoded."""
        info = self.info()
        session = self.session()
        engine = self.live.engine()
        index = {p[0]: k for k, p in enumerate(session['probes'])}
        main = (self.link.hello or self.link.ping()).get('mainThreadId')
        memory = self._memory()
        out = []
        try:
            counts = memory.u32s(int(info['counts'], 16), info['used'])
            meta = memory.u32s(int(info['meta'], 16), 2 * info['used'])
            per = info['samplesPer']
            for ref in functions:
                a = engine.resolve(ref)
                k = index.get(a)
                if k is None:
                    out.append({'address': '0x%08X' % a, 'probed': False})
                    continue
                raw = memory.u32s(int(info['samples'], 16) + 4 * WORDS * per * k, WORDS * per)
                table = memory.u32s(int(info['callers'], 16) + 8 * CALLER_SLOTS * k, 2 * CALLER_SLOTS)
                n = min(meta[2 * k], per, info['limit'])
                samples = [decode_sample(raw[WORDS * j:WORDS * (j + 1)], engine, main) for j in range(n)]
                callers = [describe_caller(table[2 * j], table[2 * j + 1], engine, self.live.deep._image(required=False))
                           for j in range(CALLER_SLOTS) if table[2 * j]]
                out.append(dict(engine.summary(a), probed=True, calls=counts[k], sampledCalls=meta[2 * k],
                                callerTableOverflow=meta[2 * k + 1], callers=sorted(callers, key=lambda c: -c['count']),
                                samples=samples[:max_samples]))
        finally:
            memory.close()
        return out

    def remove(self):
        r = self.link.one('probe', 'disarm')
        if not r.get('ok'):
            raise ProbeError('PROBE_DISARM_FAILED', 'probe disarm failed: %s' % r.get('error'), r)
        session = self._load('probe_session.json', None)
        if session:
            session['armed'] = False
            self._save('probe_session.json', session)
        check = self._verify_restored(session) if session else None
        return {'disarm': r, 'restoredCheck': check}

    def _verify_restored(self, session, sample=400):
        image = self.live.deep._image(required=False)
        if not image:
            return None
        probes = [p for p in session['probes'] if p[1] in (MODE_ENTRY, MODE_INT3)]
        step = max(1, len(probes) // sample)
        memory = self._memory()
        bad = []
        try:
            for a, _, n in probes[::step]:
                if memory.read(a, n) != image.read(a, n):
                    bad.append('0x%08X' % a)
            for _, _, at in (session.get('sites') or [])[::step]:
                if memory.read(at, 4) != image.read(at, 4):
                    bad.append('site 0x%08X' % at)
        finally:
            memory.close()
        return {'checked': len(probes[::step]), 'differFromCleanExe': bad[:20]}

    def tag(self, action):
        return self.link.one('probe', 'tag', word(int(action)))

    def clear(self):
        return self.link.one('probe', 'clear')

    def config(self, samples, exit_samples, every):
        return self.link.one('probe', 'config', word(samples), word(exit_samples), word(every))


# ===================================================================================== decoding
def vtable_class(engine, vt):
    hit = engine.vtables.get(vt)
    return hit[0] if hit else None


def decode_sample(w, engine, main_thread=None):
    out = {'action': w[0], 'thread': 'main' if main_thread and w[1] == main_thread else w[1],
           'this': '0x%08X' % w[2], 'edx': '0x%08X' % w[3], 'caller': '0x%08X' % w[4],
           'stack': ['0x%08X' % x for x in w[5:11]], 'frame': w[17]}
    for name, vt in (('thisClass', w[11]), ('arg0Class', w[12]), ('arg1Class', w[13])):
        cls = vtable_class(engine, vt)
        if cls:
            out[name] = cls
    state = w[16]
    if state == 2:
        out['returned'] = {'eax': w[14]}
    elif state == 3:
        out['returned'] = {'eax': w[14], 'float': round(struct.unpack('<f', struct.pack('<I', w[15]))[0], 6)}
    elif state == 1:
        out['returned'] = None
    if state in (2, 3):
        out['ticks'] = w[18]
    return out


def call_kind(image, ret):
    """The call instruction that returns to `ret`: direct (E8), virtual ([reg+disp]: slot disp/4), pointer
    ([absolute]) or register."""
    if image is None:
        return None
    code = image.read(ret - 7, 7)
    if code is None:
        return None
    if code[2] == 0xE8:
        return {'kind': 'direct', 'target': '0x%08X' % ((ret + struct.unpack('<i', code[3:7])[0]) & 0xFFFFFFFF),
                'site': '0x%08X' % (ret - 5)}
    for n in (6, 3, 2, 7):
        for insn in disassembler().disasm(code[7 - n:], ret - n):
            if insn.address + insn.size == ret and insn.mnemonic == 'call':
                op = insn.op_str
                if '[' not in op:
                    return {'kind': 'register', 'site': '0x%08X' % insn.address, 'operand': op}
                inner = op[op.index('[') + 1:op.index(']')].replace(' ', '')
                site = '0x%08X' % insn.address
                if inner.startswith('0x'):
                    return {'kind': 'pointer', 'site': site, 'slot': inner}
                m = re.fullmatch(r'(eax|ebx|ecx|edx|esi|edi|ebp|esp)(?:([+-])(0x[0-9a-f]+|\d+))?', inner)
                if not m:  # indexed ([reg*4+table]): a table of function pointers
                    return {'kind': 'pointer', 'site': site, 'operand': op, 'note': 'indexed function-pointer table'}
                reg, sign, num = m.group(1), m.group(2), m.group(3)
                disp = int(num, 0) if num else 0
                if reg in ('ebp', 'esp'):
                    return {'kind': 'pointer', 'site': site, 'operand': op, 'note': 'function pointer held in a local'}
                if sign == '-' or disp % 4:
                    return {'kind': 'pointer', 'site': site, 'operand': op, 'note': 'function pointer in a struct field'}
                return {'kind': 'virtual', 'site': site, 'operand': op, 'slotIndex': disp // 4}
            break
    return {'kind': 'unknown'}


def describe_caller(ret, count, engine, image):
    hit = engine.containing(ret - 1)
    out = {'returnAddress': '0x%08X' % ret, 'count': count}
    if hit:
        out['function'] = engine.summary(hit[0], with_location=False)
    k = call_kind(image, ret)
    if k:
        out['call'] = k
    return out
