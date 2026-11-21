"""Reader of the symbol database (state/symdb.sqlite): source lines per code offset, named parameters and
locals, and compiler-exact type layouts for the game's functions. See docs/FNV-SYMDB.md.

Read-only and thread safe. The file is open only while a call runs (one read-only connection per thread,
closed when the outermost call returns), so a rebuild can replace it on Windows at any time. The file is
built by engine_map.symdb; without it every caller keeps working and says how to build it.
"""
import contextlib
import functools
import json
import os
import re
import sqlite3
import threading
from pathlib import Path

from engine_map import symdb_schema

REGISTERS = symdb_schema.REGISTERS
DEFINITION_KINDS = ('struct', 'class', 'union')
TYPE_DEPTH = 12
POINTER_SIZE = 4
COUNT_KEYS = ('units', 'unitsOk', 'unitsFailed', 'functions', 'functionsWithLines', 'functionsVerified',
              'types', 'seconds')
ADDRESS = re.compile(r'^(?:0x)?([0-9a-fA-F]{1,8})$')


class SymbolDbError(Exception):
    def __init__(self, code, message, evidence=None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence


def _hex(value):
    return '0x%X' % value


def _git_head(root):
    """Commit of a checkout read straight from .git (no subprocess); None when it cannot be told."""
    try:
        git = Path(root) / '.git'
        if git.is_file():   # worktree or submodule: 'gitdir: <path>'
            text = git.read_text(encoding='utf-8').strip()
            git = Path(text[8:].strip()) if text.startswith('gitdir:') else git
            if not git.is_absolute():
                git = (Path(root) / git).resolve()
        head = (git / 'HEAD').read_text(encoding='utf-8').strip()
        if not head.startswith('ref:'):
            return head or None
        ref = head[4:].strip()
        loose = git / ref
        if loose.exists():
            return loose.read_text(encoding='utf-8').strip() or None
        common = git / 'commondir'
        base = (git / common.read_text(encoding='utf-8').strip()).resolve() if common.exists() else git
        for folder in (git, base):
            packed = folder / 'packed-refs'
            if packed.exists():
                for line in packed.read_text(encoding='utf-8').splitlines():
                    if line.endswith(' ' + ref):
                        return line.split()[0]
        if base != git and (base / ref).exists():
            return (base / ref).read_text(encoding='utf-8').strip() or None
    except (OSError, ValueError):
        pass
    return None


def _in_session(method):
    """Run a public method with the database file open once (nested calls share the connection)."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._session():
            return method(self, *args, **kwargs)
    return wrapper


def _like(text):
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


class SymbolDb:
    """One symbol database file, opened read-only."""

    def __init__(self, path, decomp_root=None):
        self.path = Path(path)
        self.decomp_root = Path(decomp_root) if decomp_root else None
        self._local = threading.local()
        self._best = {}     # type name -> id of its best definition (the file does not change while it is the cached one)
        try:
            with self._session() as db:
                self.meta = {r['key']: r['value'] for r in db.execute('SELECT key, value FROM meta')}
        except sqlite3.Error as e:
            raise SymbolDbError('SYMDB_UNREADABLE', 'Cannot read symbol database %s: %s' % (self.path, e))
        if self.meta.get('schema') != str(symdb_schema.SCHEMA_VERSION):
            raise SymbolDbError('SYMDB_SCHEMA', 'Symbol database %s has schema %r, this reader needs %s: rebuild it'
                                % (self.path, self.meta.get('schema'), symdb_schema.SCHEMA_VERSION))

    # ----------------------------------------------------------------------------- plumbing
    @contextlib.contextmanager
    def _session(self):
        """Open the file read-only on the first entry of this thread; close it when the outermost entry exits."""
        local = self._local
        depth = getattr(local, 'depth', 0)
        if depth == 0:
            uri = self.path.resolve().as_uri() + '?mode=ro'
            local.db = sqlite3.connect(uri, uri=True)
            local.db.row_factory = sqlite3.Row
        local.depth = depth + 1
        try:
            yield local.db
        finally:
            local.depth -= 1
            if local.depth == 0:
                db, local.db = local.db, None
                db.close()

    def _all(self, sql, args=()):
        with self._session() as db:
            return db.execute(sql, args).fetchall()

    def _one(self, sql, args=()):
        with self._session() as db:
            return db.execute(sql, args).fetchone()

    def close(self):
        """Nothing stays open between calls; kept so callers can still call it."""

    # ----------------------------------------------------------------------------- identity
    @_in_session
    def identity(self, decomp_root=None):
        """Where the file is, how it was built, what it holds, and whether the decomp has moved on since."""
        meta = self.meta
        counts = {}
        for key in COUNT_KEYS:
            try:
                counts[key] = int(meta[key]) if key != 'seconds' else float(meta[key])
            except (KeyError, ValueError):
                pass
        out = {'path': str(self.path)}
        out.update({k: v for k, v in meta.items() if k not in COUNT_KEYS})
        out['counts'] = counts
        out['stale'] = self.stale(decomp_root)
        return out

    @_in_session
    def stale(self, decomp_root=None):
        """True when the decomp checkout is at another commit than the one this file was built from; None when
        either commit is unknown."""
        built = self.meta.get('decompCommit')
        if not built:
            return None
        for root in (decomp_root, self.decomp_root, self.meta.get('decompPath')):
            current = _git_head(root) if root else None
            if current:
                return current != built
        return None

    @_in_session
    def coverage(self):
        """Counts of functions and units, with the names of up to 20 units that failed or are partial."""
        def count(sql):
            return self._one(sql)[0]
        functions = count('SELECT COUNT(*) FROM functions')
        out = {'functions': functions,
               'functionsWithDebug': count('SELECT COUNT(*) FROM functions WHERE has_debug = 1'),
               'functionsWithLines': count('SELECT COUNT(DISTINCT func_addr) FROM lines'),
               'functionsVerified': count('SELECT COUNT(*) FROM functions WHERE verified = 1'),
               'units': count('SELECT COUNT(*) FROM units')}
        out['unitsByStatus'] = {r['status']: r['n'] for r in self._all(
            'SELECT status, COUNT(*) AS n FROM units GROUP BY status')}
        rows = self._all("SELECT source, status, error FROM units WHERE status != 'ok' ORDER BY status, source")
        out['unitsNotOk'] = len(rows)
        out['unitsNotOkList'] = [dict(source=r['source'], status=r['status'],
                                      **({'error': r['error'][:200]} if r['error'] else {})) for r in rows[:20]]
        return out

    # ----------------------------------------------------------------------------- functions and lines
    @_in_session
    def function(self, addr):
        """Row of the function that starts at addr, or None."""
        row = self._one('SELECT * FROM functions WHERE addr = ?', (addr,))
        return dict(row) if row else None

    @_in_session
    def function_containing(self, address):
        row = self._one('SELECT * FROM functions WHERE addr <= ? ORDER BY addr DESC LIMIT 1', (address,))
        if row and address < row['addr'] + max(row['size'], 1):
            return dict(row)
        return None

    @_in_session
    def resolve(self, ref):
        """Function row by address (any address inside it) or by name; raises SymbolDbError when none or many."""
        text = str(ref).strip().replace('`', '')
        m = ADDRESS.match(text)
        if m and (text.lower().startswith('0x') or len(text) >= 6):
            row = self.function_containing(int(m.group(1), 16))
            if row:
                return row
            raise SymbolDbError('NOT_FOUND', 'No function in the symbol database contains %s' % text)
        rows = self._all('SELECT * FROM functions WHERE name = ? OR lower(name) = lower(?) '
                         'ORDER BY (name = ?) DESC, addr', (text, text, text))
        if not rows:
            raise SymbolDbError('NOT_FOUND', 'No function named %r in the symbol database' % text)
        exact = [r for r in rows if r['name'] == text]
        pick = exact or rows
        if len(pick) > 1:
            raise SymbolDbError('AMBIGUOUS', '%d functions match %r; pass an address' % (len(pick), text),
                                {'candidates': [{'address': '0x%08X' % r['addr'], 'name': r['name']}
                                                for r in pick[:25]]})
        return dict(pick[0])

    @_in_session
    def line_at(self, address):
        """Source position of any address inside a function that has lines, or None."""
        f = self.function_containing(address)
        if not f:
            return None
        offset = address - f['addr']
        row = self._one('SELECT l.offset, l.line, p.path FROM lines l JOIN files p ON p.id = l.file_id '
                        'WHERE l.func_addr = ? AND l.offset <= ? ORDER BY l.offset DESC LIMIT 1',
                        (f['addr'], offset))
        if not row:
            return None
        return {'functionAddress': f['addr'], 'offset': offset, 'file': row['path'], 'line': row['line'],
                'lineOffset': row['offset'], 'verified': bool(f['verified'])}

    @_in_session
    def lines_of(self, func_addr):
        """[(offset, file, line), ...] in code order."""
        return [(r['offset'], r['path'], r['line']) for r in self._all(
            'SELECT l.offset, l.line, p.path FROM lines l JOIN files p ON p.id = l.file_id '
            'WHERE l.func_addr = ? ORDER BY l.offset', (func_addr,))]

    @_in_session
    def files_matching(self, file):
        """Paths of the files whose path ends with `file` (case-insensitive, whole path components)."""
        want = str(file).replace('\\', '/').strip().lstrip('/').lower()
        if not want:
            return []
        rows = self._all("SELECT path FROM files WHERE lower(path) = ? OR lower(path) LIKE ? ESCAPE '\\' "
                         'ORDER BY path', (want, '%/' + _like(want)))
        return [r['path'] for r in rows]

    @_in_session
    def where(self, file, line):
        """[{'file', 'address', 'function', 'offset'}, ...] for a source line; `file` is a path suffix."""
        out = []
        for path in self.files_matching(file):
            for r in self._all('SELECT l.func_addr, l.offset, f.name FROM lines l JOIN files p ON p.id = l.file_id '
                               'JOIN functions f ON f.addr = l.func_addr WHERE p.path = ? AND l.line = ? '
                               'ORDER BY l.func_addr, l.offset', (path, line)):
                out.append({'file': path, 'address': r['func_addr'] + r['offset'], 'function': r['func_addr'],
                            'functionName': r['name'], 'offset': r['offset']})
        return out

    @_in_session
    def addresses_of(self, file, line):
        """[(address, func_addr), ...] of a source line; `file` is matched by case-insensitive path suffix."""
        return [(r['address'], r['function']) for r in self.where(file, line)]

    # ----------------------------------------------------------------------------- locals
    @staticmethod
    def _location(row):
        kind, reg, off = row['kind'], row['reg'], row['offset']
        name = REGISTERS.get(reg, 'reg%s' % reg) if reg is not None else None
        if kind == 'bprel':
            return 'ebp' + _signed(off or 0)
        if kind == 'regrel':
            return (name or 'reg') + _signed(off or 0)
        if kind == 'register':
            return name or 'reg%s' % reg
        return 'static'

    @_in_session
    def locals_of(self, func_addr, offset=None):
        """Parameters and locals; with `offset` (from the function start) only those whose scope covers it."""
        out = []
        for r in self._all('SELECT * FROM locals WHERE func_addr = ? ORDER BY seq', (func_addr,)):
            lo, hi = r['scope_lo'], r['scope_hi']
            if offset is not None and lo is not None and hi and not lo <= offset < hi:
                continue
            out.append({'name': r['name'], 'kind': r['kind'], 'location': self._location(r), 'reg': r['reg'],
                        'offset': r['offset'], 'type': self.type_text(r['type_id']) if r['type_id'] else None,
                        'size': self.type_size(r['type_id']) if r['type_id'] else None,
                        'param': r['kind'] == 'bprel' and (r['offset'] or 0) > 0,
                        'scope': [lo, hi] if lo is not None and hi else None})
        return out

    # ----------------------------------------------------------------------------- types
    def _type(self, type_id):
        if type_id is None:
            return None
        return self._one('SELECT * FROM types WHERE id = ?', (type_id,))

    def _attrs(self, row):
        try:
            return json.loads(row['attrs']) if row['attrs'] else {}
        except ValueError:
            return {}

    @_in_session
    def best_definition(self, name):
        """Id of the definition of a class, struct or union by name: most members, then highest `seen`; falls back
        to an enum of that name. None when the file has no definition."""
        if name in self._best:
            return self._best[name]
        marks = ','.join('?' * len(DEFINITION_KINDS))
        sql = ('SELECT t.id FROM types t WHERE t.name = ? AND t.kind IN (%s) '
               'ORDER BY (SELECT COUNT(*) FROM members m WHERE m.type_id = t.id) DESC, t.seen DESC, t.id LIMIT 1')
        row = self._one(sql % marks, (name,) + DEFINITION_KINDS)
        if not row:
            row = self._one("SELECT id FROM types WHERE name = ? AND kind = 'enum' ORDER BY seen DESC, id LIMIT 1",
                            (name,))
        if not row:   # exact case failed: try any case
            row = self._one(sql.replace('t.name = ?', 'lower(t.name) = lower(?)') % marks, (name,) + DEFINITION_KINDS)
        found = row['id'] if row else None
        self._best[name] = found
        return found

    @_in_session
    def type_size(self, type_id, _depth=0):
        """Size in bytes of a type; pointers are 4; udtref follows to the best definition; None if unknown."""
        row = self._type(type_id)
        if row is None or _depth > TYPE_DEPTH:
            return None
        kind = row['kind']
        if kind in ('ptr', 'ref'):
            return POINTER_SIZE
        if kind == 'udtref':
            best = self.best_definition(row['name'])
            return self.type_size(best, _depth + 1) if best else None
        if row['size'] is not None:
            return row['size']
        if kind in ('modifier', 'bitfield'):
            return self.type_size(row['target_id'], _depth + 1)
        if kind == 'array':
            count = self._attrs(row).get('count')
            elem = self.type_size(row['target_id'], _depth + 1)
            return count * elem if count is not None and elem is not None else None
        if kind == 'memptr':
            return POINTER_SIZE if self._attrs(row).get('mode') == 'data' else 8
        if kind == 'enum':
            return 4
        return None

    @_in_session
    def type_text(self, type_id):
        """C spelling of a type, for example 'const char *', 'TESObjectREFR *', 'float [3]'."""
        if type_id is None:
            return 'void'
        return self._text(type_id, 0, ())

    def _args(self, type_id, depth, seen):
        return ', '.join(self._text(r['type_id2'], depth + 1, seen) for r in self._all(
            'SELECT type_id2 FROM args WHERE type_id = ? ORDER BY seq', (type_id,))) or 'void'

    def _text(self, type_id, depth, seen):
        row = self._type(type_id)
        if row is None:
            return '?'
        if depth > TYPE_DEPTH or type_id in seen:
            return row['name'] or '...'
        seen = seen + (type_id,)
        kind, name, attrs = row['kind'], row['name'], self._attrs(row)
        if kind == 'prim':
            return name or 'prim'
        if kind in ('udtref', 'struct', 'class', 'union', 'enum'):
            if not name or '<unnamed' in name or '__unnamed' in name:
                return '<unnamed %s>' % (kind if kind != 'udtref' else 'type')
            return name
        target = self._type(row['target_id']) if row['target_id'] else None
        if kind in ('ptr', 'ref'):
            star = ('*' if kind == 'ptr' else '&') + (' const' if attrs.get('const') else '') \
                + (' volatile' if attrs.get('volatile') else '')
            if target is not None and target['kind'] in ('proc', 'mfunc'):
                return self._function_text(target, depth, seen, star)
            return '%s %s' % (self._text(row['target_id'], depth + 1, seen) if target is not None else 'void', star)
        if kind == 'modifier':
            inner = self._text(row['target_id'], depth + 1, seen) if target is not None else 'void'
            words = [w for w in ('const', 'volatile', 'unaligned') if attrs.get(w)]
            if inner.endswith(('*', '&')):
                return ' '.join([inner] + words)
            return ' '.join(words + [inner])
        if kind == 'array':
            inner = self._text(row['target_id'], depth + 1, seen) if target is not None else '?'
            count = attrs.get('count')
            return '%s [%s]' % (inner, '' if count is None else count)
        if kind == 'bitfield':
            inner = self._text(row['target_id'], depth + 1, seen) if target is not None else 'int'
            return inner
        if kind == 'memptr':
            cls = self._text(attrs['class'] and self._id_of(attrs['class']), depth + 1, seen) \
                if attrs.get('class') else '?'
            if target is not None and target['kind'] in ('proc', 'mfunc'):
                return self._function_text(target, depth, seen, '%s::*' % cls)
            return '%s %s::*' % (self._text(row['target_id'], depth + 1, seen) if target is not None else 'void', cls)
        if kind in ('proc', 'mfunc'):
            return self._function_text(row, depth, seen, '')
        return name or '<other>'

    def _id_of(self, hash_):
        row = self._one('SELECT id FROM types WHERE hash = ?', (hash_,))
        return row['id'] if row else None

    def _function_text(self, row, depth, seen, star):
        ret = self._text(row['target_id'], depth + 1, seen) if row['target_id'] else 'void'
        call = self._attrs(row).get('call')
        mark = ' '.join(x for x in ((('__' + call) if call and not call.startswith('_') else call), star) if x)
        return '%s (%s)(%s)' % (ret, mark, self._args(row['id'], depth, seen)) if mark else \
            '%s (%s)' % (ret, self._args(row['id'], depth, seen))

    # ----------------------------------------------------------------------------- layout
    @_in_session
    def layout(self, name):
        """Best definition of a class, struct, union or enum by name, or None. Size, members, bases, methods,
        how many distinct definitions exist (`variants`) and the other sizes seen (`conflicts`)."""
        best = self.best_definition(name)
        row = self._type(best) if best else None
        if row is None:
            return None
        marks = ','.join('?' * len(DEFINITION_KINDS))
        defs = self._all('SELECT id, size, seen FROM types WHERE name = ? AND kind = ?', (row['name'], row['kind'])) \
            if row['kind'] == 'enum' else \
            self._all('SELECT id, size, seen FROM types WHERE name = ? AND kind IN (%s)' % marks,
                      (row['name'],) + DEFINITION_KINDS)
        out = {'name': row['name'], 'kind': row['kind'], 'size': row['size'], 'typeId': row['id'],
               'seen': row['seen'], 'attrs': self._attrs(row), 'variants': len(defs),
               'conflicts': sorted({d['size'] for d in defs if d['size'] is not None and d['size'] != row['size']})}
        if row['kind'] == 'enum':
            out['enumerators'] = [{'name': r['name'], 'value': r['value']} for r in self._all(
                'SELECT name, value FROM enums WHERE type_id = ? ORDER BY seq', (row['id'],))]
            return out
        out['members'] = [{'name': r['name'], 'offset': r['offset'], 'type': self.type_text(r['type_id2']),
                           'size': self.type_size(r['type_id2']), 'bitPos': r['bit_pos'], 'bitLen': r['bit_len']}
                          for r in self._all('SELECT * FROM members WHERE type_id = ? ORDER BY seq', (row['id'],))]
        out['bases'] = [{'name': self.type_text(r['base_id']), 'offset': r['offset'],
                         'virtual': bool(r['is_virtual'])}
                        for r in self._all('SELECT * FROM bases WHERE type_id = ? ORDER BY seq', (row['id'],))]
        out['methods'] = [{'name': r['name'], 'virtual': bool(r['is_virtual']), 'static': bool(r['is_static']),
                           'vtableSlot': r['vt_offset'] // 4 if r['vt_offset'] is not None else None}
                          for r in self._all('SELECT * FROM methods WHERE type_id = ? ORDER BY seq', (row['id'],))]
        return out


def is_pointer_text(text):
    """True for a pointer or reference type spelling, also with trailing qualifiers ('T * const')."""
    words = (text or '').split()
    while words and words[-1] in ('const', 'volatile', 'unaligned'):
        words.pop()
    return bool(words) and words[-1].endswith(('*', '&'))


def _signed(value):
    text = '%d' % abs(value) if abs(value) < 10 else '0x%X' % abs(value)
    return ('-' if value < 0 else '+') + text


_cache = {}
_cache_lock = threading.Lock()


def load(path, decomp_root=None):
    """Cached SymbolDb for a path, reopened when the file changes; None when the file does not exist."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return None
    stamp = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        cached = _cache.get(stamp[0])
        if cached and cached[0] == stamp:
            db = cached[1]
            if decomp_root:
                db.decomp_root = Path(decomp_root)
            return db
    db = SymbolDb(path, decomp_root)
    with _cache_lock:
        _cache[stamp[0]] = (stamp, db)
    return db


def clear_cache():
    """Close and forget every cached database (tests that delete the file need this on Windows)."""
    with _cache_lock:
        entries = list(_cache.values())
        _cache.clear()
    for _, db in entries:
        db.close()


def default_path(project):
    """The project's fnv.symdb, else env FNV_SYMDB, else <state_dir>/symdb.sqlite."""
    fnv = getattr(project, 'fnv', None)
    chosen = (getattr(fnv, 'symdb', None) if fnv else None) or os.environ.get('FNV_SYMDB')
    return Path(chosen) if chosen else Path(project.state_dir) / 'symdb.sqlite'
