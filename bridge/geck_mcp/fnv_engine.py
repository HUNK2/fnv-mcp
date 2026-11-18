"""Offline engine knowledge: queries over engine_map.json and the decomp checkout it was generated from.

Read-only. Names are labelled with their proof status: only 'proven' names (fnvsym verified.csv) are
evidence; 'decomp' names are the decompilation's own, often machine-made, and 'unnamed' are placeholders.
"""
import json
import os
import re
import threading
import time
from bisect import bisect_right
from collections import OrderedDict, deque
from pathlib import Path

from . import fnv_symdb

GAME_BUILD = '1.4.0.525'
EXE_RANGE = (0x00400000, 0x01300000)
STATUS_RANK = {'proven': 0, 'class_proven': 1, 'probable': 2, 'decomp': 3, 'rejected': 4, 'unnamed': 5}
PLACEHOLDER_REF = re.compile(r'\b(?:FUN|sub|thunk_FUN)_([0-9a-fA-F]{8})\b')
ADDRESS = re.compile(r'^(?:0x)?([0-9a-fA-F]{1,8})$')


def nested_repeat(pattern):
    """Does the regex repeat (more than once) a part that itself holds a repeat? (a+)+ and (\\w+\\s*)* do."""
    import re._constants as C
    import re._parser as parser
    repeats = (C.MAX_REPEAT, C.MIN_REPEAT, getattr(C, 'POSSESSIVE_REPEAT', None))

    def walk(items, inside):
        for op, arg in items:
            if op in repeats and op is not None:
                low, high, sub = arg
                many = high is C.MAXREPEAT or high > 1
                if many and inside:
                    return True
                if walk(sub, inside or many):
                    return True
            elif op is C.SUBPATTERN:
                if walk(arg[-1], inside):
                    return True
            elif op is C.BRANCH:
                if any(walk(branch, inside) for branch in arg[1]):
                    return True
            elif op in (C.ASSERT, C.ASSERT_NOT):
                if walk(arg[1], inside):
                    return True
        return False
    try:
        return walk(parser.parse(pattern), False)
    except Exception:  # noqa: BLE001 - re.compile has already accepted it; an unknown node is not a refusal
        return False


REQUIRED_MIN = 3     # one or two characters are in nearly every file: no file would be skipped


def required_text(pattern, regex):
    """Lowercase ASCII bytes that every match of a code search holds (case ignored), or None when nothing is certain.

    A plain ASCII file without them cannot match, so the search skips it with one bytes search instead of a
    case-insensitive regex over its text (about 20 times the work, over 118 MB of decomp source). Plain text is its
    own requirement. For a regex it is the longest run of literal characters at the top level of the pattern: inside
    groups, alternatives and repeats nothing is certain. Line breaks split a run, because matches are per line.
    Characters outside ASCII give None: re.I pairs some ASCII letters with letters outside ASCII (s with the long
    s), which a bytes comparison cannot follow."""
    if regex:
        import re._constants as C
        import re._parser as parser
        try:
            items = list(parser.parse(pattern))
        except Exception:  # noqa: BLE001 - an unknown pattern shape: search every file, as before
            return None
        runs, run = [], []
        for op, arg in items:
            if op is C.LITERAL:
                run.append(chr(arg))
            else:
                runs.append(''.join(run))
                run = []
        runs.append(''.join(run))
    else:
        runs = [pattern]
    best = max((piece for run in runs for piece in re.split(r'[\r\n]', run)), key=len, default='')
    if len(best) < REQUIRED_MIN or not best.isascii():
        return None
    return best.lower().encode('ascii')


def read_whole(path):
    """A file's bytes through the OS calls alone. A code search reads every decomp source file (2,824), and this is
    100 ms less for them than Path.read_bytes (no file object, no buffer, no path object)."""
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0))
    try:
        want = os.fstat(fd).st_size + 1         # one byte more than its size: a shorter read is the end of the file
        parts = []
        while True:
            part = os.read(fd, want)
            parts.append(part)
            if len(part) < want:
                return part if len(parts) == 1 else b''.join(parts)
    finally:
        os.close(fd)


class EngineMapError(Exception):
    def __init__(self, code, message, evidence=None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence


def parse_address(text):
    if isinstance(text, int):
        return text
    m = ADDRESS.match(str(text).strip().replace('`', ''))
    if not m:
        return None
    return int(m.group(1), 16)


def hx(value):
    return '0x%08X' % value


def _words(text):
    """CamelCase and separators -> lowercase words: 'ApplyLimbDamage' -> ['apply','limb','damage']."""
    text = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)
    text = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', text)
    return [w for w in re.split(r'[^A-Za-z0-9]+', text.lower()) if w]


class EngineMap:
    """engine_map.json loaded once, with indexes for lookups."""

    def __init__(self, path, decomp_root=None):
        self.path = Path(path)
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if data.get('schema') != 1:
            raise EngineMapError('ENGINE_MAP_SCHEMA', 'Unsupported engine_map schema %r' % data.get('schema'))
        if data.get('game', {}).get('build') != GAME_BUILD:
            raise EngineMapError('ENGINE_MAP_BUILD', 'engine_map is for build %r, expected %s'
                                 % (data.get('game', {}).get('build'), GAME_BUILD))
        self.data = data
        self.generated = data.get('generated', {})
        root = decomp_root or (self.generated.get('decomp') or {}).get('path')
        self.decomp_root = Path(root) if root else None
        self.functions = {}
        for f in data['functions']:
            self.functions[int(f['address'], 16)] = f
        self.addresses = sorted(self.functions)
        self.calls = {int(a, 16): [int(t, 16) for t in ts] for a, ts in data.get('calls', {}).items()}
        self.tails = {int(a, 16): [int(t, 16) for t in ts] for a, ts in data.get('tailCalls', {}).items()}
        self.callers = {}
        for graph, kind in ((self.calls, 'call'), (self.tails, 'jump')):
            for caller, targets in graph.items():
                for t in targets:
                    self.callers.setdefault(t, []).append((caller, kind))
        self.by_name = {}
        for address, f in self.functions.items():
            names = {f['name'], f.get('decompName')}
            proof = f.get('proof') or {}
            if proof.get('name'):
                if proof['name'].endswith('::'):
                    # A class proof names only the class ("Actor::"): Class::Method then picks this function.
                    short = (f['name'] or '').split('::')[-1]
                    if short:
                        names.add(proof['name'] + short)
                else:
                    names.add(proof['name'])
            for n in names:
                if n:
                    self.by_name.setdefault(n.lower(), set()).add(address)
                    self.by_name.setdefault(n.split('::')[-1].lower(), set()).add(address)
        self.classes = data.get('classes', {})
        self.classes_lower = {k.lower(): k for k in self.classes}
        self.commands = data.get('commands', [])
        self.statics = data.get('statics', [])
        self.statics_by_address = {int(s['address'], 16): s for s in self.statics if s.get('address')}
        self.rtti = data.get('rtti') or {}
        self.rtti_lower = {k.lower(): k for k in self.rtti}
        self.vtables = {}
        self.derived = {}
        for cls, info in self.rtti.items():
            for vt in info.get('vtables', []):
                self.vtables[int(vt['address'], 16)] = (cls, vt)
            for base in info.get('directBases', []):
                self.derived.setdefault(base, []).append(cls)
        self.vtable_starts = sorted(self.vtables)
        self._files = OrderedDict()
        self._lock = threading.Lock()
        self._name_keys = None
        self._name_keys_lock = threading.Lock()
        self.symdb_path = self.path.parent / 'symdb.sqlite'

    @property
    def symdb(self):
        """The attached symbol database (source lines, locals, types), or None when there is none."""
        try:
            return fnv_symdb.load(self.symdb_path, self.decomp_root)
        except Exception:   # an unreadable database must not break symbolizing
            return None

    # ----------------------------------------------------------------------------- basics
    def identity(self):
        g = self.generated
        return {'engineMap': str(self.path), 'gameBuild': GAME_BUILD,
                'generatedAt': g.get('at'), 'decompCommit': (g.get('decomp') or {}).get('commit'),
                'verifiedSha256': (g.get('verified') or {}).get('sha256'),
                'exeImageCheck': ((g.get('exe') or {}).get('imageCheck') or {}).get('ok'),
                'callGraph': g.get('callGraph')}

    def summary(self, address, with_location=True):
        f = self.functions[address]
        out = {'address': f['address'], 'name': f['name'], 'status': f['status']}
        if f['decompName'] != f['name']:
            out['decompName'] = f['decompName']
        if with_location and f.get('file'):
            out['location'] = '%s:%s' % (f['file'], (f.get('lines') or [f.get('marker') or '?'])[0])
        return out

    def resolve(self, ref):
        """A function by address or by name (proven, decomp or placeholder). Raises on ambiguity."""
        address = parse_address(ref)
        if address is not None and address in self.functions:
            return address
        if address is not None and re.fullmatch(r'(0x)?[0-9a-fA-F]{6,8}', str(ref).strip()):
            hit = self.containing(address)
            if hit:
                return hit[0]
            raise EngineMapError('NOT_FOUND', 'No function contains address %s' % hx(address))
        m = PLACEHOLDER_REF.fullmatch(str(ref).strip())
        if m and int(m.group(1), 16) in self.functions:
            return int(m.group(1), 16)
        key = str(ref).strip().lower()
        hits = self.by_name.get(key, set())
        if not hits and '::' in key:
            short = self.by_name.get(key.split('::')[-1], set())
            if short:
                raise EngineMapError('NOT_FOUND', 'No function named %r; %d function(s) are named %r: pick one by '
                                     'address or by the class name shown' % (ref, len(short), key.split('::')[-1]),
                                     {'candidates': [self.summary(a) for a in sorted(short)[:25]]})
        if not hits:
            from .fnv_conv import nearest
            close = nearest(str(ref).strip(), {f['name'] for f in self.functions.values() if f.get('name')})
            raise EngineMapError('NOT_FOUND', 'No function named %r.%s Next: find it with fnv_func.'
                                 % (ref, ' Did you mean %s?' % ', '.join(close) if close else ''),
                                 {'didYouMean': close})
        if len(hits) > 1:
            ranked = sorted(hits, key=lambda a: (STATUS_RANK.get(self.functions[a]['status'], 9),
                                                 self.functions[a]['name'].lower() != key, a))
            exact = [a for a in ranked if self.functions[a]['name'].lower() == key
                     and self.functions[a]['status'] == 'proven']
            if len(exact) == 1:
                return exact[0]
            raise EngineMapError('AMBIGUOUS', '%d functions match %r; pass an address' % (len(hits), ref),
                                 {'candidates': [self.summary(a) for a in ranked[:25]]})
        return next(iter(hits))

    def containing(self, address):
        """(function address, offset) for an address inside the exe's functions, or None."""
        i = bisect_right(self.addresses, address) - 1
        if i < 0:
            return None
        start = self.addresses[i]
        size = self.functions[start].get('size') or 0
        if start <= address < start + max(size, 1):
            return start, address - start
        return None

    # ----------------------------------------------------------------------------- source
    def _lines(self, rel):
        if self.decomp_root is None:
            raise EngineMapError('DECOMP_UNAVAILABLE', 'No decomp checkout configured (fnv.decomp_root)')
        with self._lock:
            if rel in self._files:
                self._files.move_to_end(rel)
                return self._files[rel]
        path = self.decomp_root / rel
        if not path.exists():
            raise EngineMapError('DECOMP_UNAVAILABLE', 'Decomp file missing: %s' % path)
        lines = path.read_text(encoding='utf-8', errors='replace').split('\n')
        with self._lock:
            self._files[rel] = lines
            while len(self._files) > 24:
                self._files.popitem(last=False)
        return lines

    def code(self, address, max_lines=400, annotate=True):
        f = self.functions[address]
        if not f.get('file'):
            raise EngineMapError('NO_SOURCE', 'The decomp has no source for %s' % f['address'])
        lines = self._lines(f['file'])
        if f.get('lines'):
            start, end = f['lines']
            first = start
            # keep the marker and comments right above the definition
            m = f.get('marker')
            if m and m < start and start - m <= 40:
                first = m
            text = lines[first - 1:end]
            exact = True
        elif f.get('marker'):
            first = f['marker']
            text = lines[first - 1:first + 30]
            exact = False
        else:
            raise EngineMapError('NO_SOURCE', 'No // @fnv marker for %s in %s' % (f['address'], f['file']))
        truncated = len(text) > max_lines
        if truncated:
            text = text[:max_lines]
        body = '\n'.join(text)
        names = {}
        if annotate:
            def repl(m):
                target = int(m.group(1), 16)
                t = self.functions.get(target)
                if t and t['status'] == 'proven' and not t['name'].endswith(m.group(0)):
                    names[m.group(0)] = t['name']
                    return '%s/*%s*/' % (m.group(0), t['name'])
                return m.group(0)
            body = PLACEHOLDER_REF.sub(repl, body)
        return {'file': f['file'], 'firstLine': first, 'lastLine': first + len(text) - 1,
                'exactExtent': exact, 'truncated': truncated, 'code': body, 'resolvedNames': names}

    # ----------------------------------------------------------------------------- search
    def name_keys(self):
        """{address: the words of the function's name, decomp name and proven name, lowercase and run together}.
        Built once per map: splitting all its names into words on every name search took 0.57 s of a 0.59 s call."""
        with self._name_keys_lock:      # a search that arrives while the start-up warm-up builds them waits for it
            if self._name_keys is None:
                # Words run together are the name's letters and digits in lowercase (_words lowercases, then splits
                # at everything else), so no splitting is needed: one lower() and one substitution over all names
                # at once, a line per function.
                names = [''.join((f['name'], f.get('decompName') or '', (f.get('proof') or {}).get('name') or ''))
                         for f in self.functions.values()]
                flat = re.sub(r'[^a-z0-9\n]+', '', '\n'.join(names).lower()).split('\n')
                if len(flat) != len(names):     # a name with a line break in it: one at a time
                    flat = [re.sub(r'[^a-z0-9]+', '', name.lower()) for name in names]
                self._name_keys = dict(zip(self.functions, flat))
            return self._name_keys

    def find_by_name(self, query, statuses=None, limit=25, offset=0):
        words = _words(query)
        if not words:
            raise ValueError('query has no letters or digits')
        joined = ''.join(words)
        keys = self.name_keys()
        hits = []
        for address, f in self.functions.items():
            if statuses and f['status'] not in statuses:
                continue
            flat = keys[address]
            if not all(w in flat for w in words):
                continue
            leaf = f['name'].split('::')[-1].lower()
            score = (STATUS_RANK.get(f['status'], 9),
                     0 if leaf == joined or f['name'].lower() == query.lower() else 1,
                     0 if joined in flat else 1, len(f['name']))
            hits.append((score, address))
        hits.sort()
        return len(hits), [self.summary(a) for _, a in hits[offset:offset + limit]]

    def find_in_code(self, pattern, regex=False, statuses=None, limit=25, offset=0, max_matches_per_function=3,
                     timeout=None):
        """Functions whose decomp code matches. With `timeout` (seconds) the search stops with SEARCH_TIMEOUT once it
        runs longer; a regex with a quantified part inside a quantified part is refused first (release audit L4)."""
        return self.find_first_in_code([pattern], regex, statuses, limit, offset, max_matches_per_function,
                                       timeout)[1:]

    def find_first_in_code(self, patterns, regex=False, statuses=None, limit=25, offset=0,
                           max_matches_per_function=3, timeout=None):
        """find_in_code for several patterns in one pass over the files: (index, total, results) of the first
        pattern with a match anywhere, or of the last pattern (no matches) when none has one."""
        if self.decomp_root is None:
            raise EngineMapError('DECOMP_UNAVAILABLE', 'No decomp checkout configured (fnv.decomp_root)')
        searches = []
        for pattern in patterns:
            try:
                rx = re.compile(pattern if regex else re.escape(pattern), re.I)
            except re.error as e:
                raise EngineMapError('BAD_REGEX', 'The pattern is not a valid regular expression: %s' % e) from None
            if regex and nested_repeat(pattern):
                raise EngineMapError('REGEX_TOO_COSTLY', 'The pattern repeats a part that itself repeats (like '
                                     '(a+)+), which can take minutes on a long file. Write it without the nested '
                                     'repeat.')
            needle = required_text(pattern, regex)
            # plain text that is its own requirement: finding the bytes is the whole answer for an ASCII file
            searches.append((rx, needle, needle is not None and not regex and len(needle) == len(pattern)))
        deadline = None if timeout is None else time.monotonic() + timeout
        by_file = {}
        for address, f in self.functions.items():
            if f.get('file') and f.get('lines') and (not statuses or f['status'] in statuses):
                by_file.setdefault(f['file'], []).append(address)
        found = [[] for _ in searches]
        files = sorted(by_file)
        root = str(self.decomp_root)
        for done, rel in enumerate(files):
            if deadline is not None and time.monotonic() >= deadline:
                raise EngineMapError('SEARCH_TIMEOUT', 'The search ran over %s s and was stopped after %d of %d '
                                     'files. Narrow the pattern, or pass statuses.' % (timeout, done, len(files)))
            try:
                data = read_whole(os.path.join(root, rel))
            except OSError:
                continue
            ascii_only = data.isascii()
            lowered = text = lines = None
            for hits, (rx, needle, needle_is_all) in zip(found, searches):
                held = False
                if needle is not None and ascii_only:
                    if lowered is None:
                        lowered = data.lower()
                    if needle not in lowered:
                        continue        # most files end here: no decoding, no regex
                    held = needle_is_all
                if text is None:        # what read_text gave before: UTF-8 with replacement, any line end as \n
                    text = data.decode('utf-8', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
                if not held and not rx.search(text):
                    continue
                if lines is None:
                    lines = text.split('\n')
                for address in by_file[rel]:
                    start, end = self.functions[address]['lines']
                    matches = []
                    for n in range(start, end + 1):
                        if rx.search(lines[n - 1]):
                            matches.append({'line': n, 'text': lines[n - 1].strip()[:200]})
                            if len(matches) >= max_matches_per_function:
                                break
                    if matches:
                        hits.append((STATUS_RANK.get(self.functions[address]['status'], 9), address, matches))
        index = next((i for i, hits in enumerate(found) if hits), len(found) - 1)
        hits = sorted(found[index], key=lambda h: (h[0], h[1]))
        out = []
        for _, address, matches in hits[offset:offset + limit]:
            s = self.summary(address)
            s['matches'] = matches
            out.append(s)
        return index, len(hits), out

    # ----------------------------------------------------------------------------- graph
    def walk(self, address, direction, depth=1, limit=200):
        graph_out = (lambda a: [(t, 'call') for t in self.calls.get(a, [])] +
                     [(t, 'jump') for t in self.tails.get(a, [])])
        graph_in = lambda a: self.callers.get(a, [])
        step = graph_out if direction == 'callees' else graph_in
        seen = {address}
        out, queue, capped = [], deque([(address, 0)]), False
        while queue:
            node, d = queue.popleft()
            if d >= depth:
                continue
            for other, kind in sorted(step(node), key=lambda x: (STATUS_RANK.get(
                    self.functions.get(x[0], {}).get('status'), 9), x[0])):
                if other in seen:
                    continue
                seen.add(other)
                if len(out) >= limit:
                    capped = True
                    break
                entry = self.summary(other) if other in self.functions else {'address': hx(other)}
                entry.update({'depth': d + 1, 'via': hx(node), 'edge': kind})
                out.append(entry)
                queue.append((other, d + 1))
        return out, capped

    # ----------------------------------------------------------------------------- symbolize
    def symbolize(self, address, modules=None, return_address=False, source=True):
        """Function + offset inside the game exe, a known static object, or a module + offset.

        return_address=True: the address is the instruction after a call, so the function and the source line are
        looked up for address - 1 (the call itself). The reported address and offset stay the real ones.
        source=False: no source line (no symbol database session, no decomp file read), for callers that only name."""
        vt = self.vtable_at(address)
        if vt:
            return vt
        static = self.statics_by_address.get(address)
        if static:
            return {'address': hx(address), 'module': 'FalloutNV.exe', 'kind': 'static',
                    'name': static.get('setting') or static['name'], 'staticKind': static['kind']}
        for m in modules or []:
            base, size = m['base'], m.get('size') or 0
            if base <= address < base + size and not m['name'].lower().startswith('falloutnv'):
                return {'address': hx(address), 'module': m['name'], 'kind': 'module',
                        'offset': '0x%X' % (address - base), 'path': m.get('path')}
        probe = address - 1 if return_address and address > 0 else address
        hit = self.containing(probe)
        if hit:
            start, off = hit
            off += address - probe
            out = {'address': hx(address), 'module': 'FalloutNV.exe', 'kind': 'function',
                   'offset': '0x%X' % off}
            out.update(self.summary(start))
            out['functionAddress'] = out.pop('address')
            out['address'] = hx(address)
            if source:
                self._add_source(out, probe)
            return out
        if EXE_RANGE[0] <= address < EXE_RANGE[1]:
            i = bisect_right(self.addresses, address) - 1
            near = self.addresses[i] if i >= 0 else None
            out = {'address': hx(address), 'module': 'FalloutNV.exe', 'kind': 'unknown_exe_address'}
            if near is not None and address - near < 0x10000:
                out['after'] = self.summary(near, with_location=False)
                out['afterOffset'] = '0x%X' % (address - near)
            return out
        return {'address': hx(address), 'kind': 'unknown', 'note': 'outside FalloutNV.exe and the given modules'}

    def _add_source(self, out, address):
        """source, sourceLine and lineOffset from the symbol database, when it has lines for this address."""
        db = self.symdb
        hit = db.line_at(address) if db else None
        if not hit:
            return
        out['source'] = '%s:%d' % (hit['file'], hit['line'])
        out['lineOffset'] = '0x%X' % hit['lineOffset']
        try:
            text = self._lines(hit['file'])[hit['line'] - 1].strip()
        except (EngineMapError, IndexError, OSError):
            return
        out['sourceLine'] = text[:200]

    def vtable_at(self, address):
        """A virtual table (or one of its slots) containing this address, from the exe's RTTI."""
        i = bisect_right(self.vtable_starts, address) - 1
        if i < 0:
            return None
        start = self.vtable_starts[i]
        cls, vt = self.vtables[start]
        if not start <= address < start + 4 * len(vt['slots']):
            return None
        slot, rem = divmod(address - start, 4)
        out = {'address': hx(address), 'module': 'FalloutNV.exe', 'kind': 'vtable', 'class': cls,
               'vtable': vt['address'], 'thisOffset': '0x%X' % vt['thisOffset']}
        if address != start or rem:
            out['slot'] = slot
            target = int(vt['slots'][slot], 16)
            if target in self.functions:
                out['slotFunction'] = self.summary(target, with_location=False)
        return out

    def rtti_class(self, name):
        key = self.rtti_lower.get(name.strip().lower())
        if not key:
            words = _words(name)
            close = sorted((k for k in self.rtti if all(w in k.lower() for w in words)), key=len)[:20]
            raise EngineMapError('NOT_FOUND', 'No RTTI class %r (the map needs --exe for RTTI)' % name,
                                 {'similar': close})
        return key, self.rtti[key]

    def descendants(self, cls, limit=300):
        out, queue, seen = [], deque(self.derived.get(cls, [])), set()
        while queue and len(out) < limit:
            c = queue.popleft()
            if c in seen:
                continue
            seen.add(c)
            out.append(c)
            queue.extend(self.derived.get(c, []))
        return out, bool(queue)

    # ----------------------------------------------------------------------------- classes, commands
    def class_layout(self, name):
        key = self.classes_lower.get(name.lower()) or self.classes_lower.get(name.replace(' ', '').lower())
        if not key:
            words = _words(name)
            close = sorted((k for k in self.classes if all(w in k.lower() for w in words)), key=len)[:20]
            raise EngineMapError('NOT_FOUND', 'No class layout for %r' % name, {'similar': close})
        return key, self.classes[key]

    def command(self, name):
        key = name.strip().lower()
        return [c for c in self.commands if c['name'].lower() == key or (c.get('short') or '').lower() == key]

    def find_commands(self, query, limit=25):
        words = _words(query)
        hits = [c for c in self.commands
                if all(w in (c['name'] + ' ' + (c.get('short') or '')).lower() for w in words)]
        hits.sort(key=lambda c: (len(c['name']), c['name']))
        return hits[:limit]

    def statics_find(self, query, kind=None, limit=25):
        words = _words(query)
        out = []
        for s in self.statics:
            if kind and s['kind'] != kind:
                continue
            text = ' '.join([s['name'], s.get('setting') or '']).lower()
            if all(w in text for w in words):
                out.append(s)
        out.sort(key=lambda s: (len(s['name']), s['name']))
        return out[:limit]


_cache = {}
_cache_lock = threading.Lock()
_load_lock = threading.Lock()      # one parse at a time: a call that arrives during the start-up warm-up waits for it


def load(path, decomp_root=None, symdb=None):
    """Cached EngineMap for a path, reloaded when the file changes. `symdb` is the symbol database file to
    attach (default: symdb.sqlite next to the engine map); a missing file just means no database."""
    path = Path(path)
    if not path.exists():
        raise EngineMapError('ENGINE_MAP_UNAVAILABLE', 'engine_map.json not found at %s' % path,
                             {'next': 'Generate it: python -m engine_map.generate --decomp <your fnv_decomp checkout> '
                                      '[--verified fnvsym/proofs/verified.csv] [--exe unpacked FalloutNV.exe] '
                                      '--out %s' % path})
    stamp = (str(path.resolve()), path.stat().st_mtime_ns, str(decomp_root or ''))

    def cached():
        with _cache_lock:
            held = _cache.get('map')
            if held and held[0] == stamp:
                engine = held[1]
                engine.symdb_path = Path(symdb) if symdb else engine.path.parent / 'symdb.sqlite'
                return engine
        return None

    engine = cached()
    if engine is not None:
        return engine
    with _load_lock:
        engine = cached()           # parsed by another thread while this one waited (0.6 s and 200 MB, once)
        if engine is not None:
            return engine
        engine = EngineMap(path, decomp_root)
        if symdb:
            engine.symdb_path = Path(symdb)
        with _cache_lock:
            _cache['map'] = (stamp, engine)
        return engine


def default_paths(project):
    """(engine_map path, decomp root or None, symbol database path) from the project's fnv settings, env vars and
    defaults. The three go straight into load(): fnv_engine.load(*default_paths(project))."""
    fnv = getattr(project, 'fnv', None)
    engine_map = (fnv.engine_map if fnv and fnv.engine_map else None) or os.environ.get('FNV_ENGINE_MAP')
    decomp = (fnv.decomp_root if fnv and fnv.decomp_root else None) or os.environ.get('FNV_DECOMP')
    if not engine_map:
        engine_map = Path(project.state_dir) / 'engine_map.json'
    return Path(engine_map), Path(decomp) if decomp else None, fnv_symdb.default_path(project)
