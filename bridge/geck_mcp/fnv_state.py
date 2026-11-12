"""One owner for the bridge's shared state files (PLAN-3 S2).

Several MCP server processes (a supervisor reload overlaps the old and the new child, and two clients can run
their own servers) share one state folder. Every file there is read, changed and written under `locked`, and
written with `atomic_write`, so a write never loses another process's change and a reader never sees half a file.

- `locked(path)`: an OS lock on `<path>.lock`, reentrant within a thread.
- `atomic_write(path, data)`: unique tmp file, fsync, os.replace (retried while a reader holds the file).
- `read_json(path, default)` / `update_json(path, fn, default)`: JSON state that falls back to the default.
- `GameIdentity`: the one way to say "this game process" (pid + start time, D4).
- `sqlite_connect` / `write_txn`: SQLite files shared by processes (WAL, busy timeout, BEGIN IMMEDIATE).
"""
import contextlib
import copy
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import NamedTuple, Optional

log = logging.getLogger(__name__)

if os.name == 'nt':
    import msvcrt
else:  # pragma: no cover - CI on Linux
    import fcntl


class StateError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ===================================================================== locks
_held = threading.local()          # {lock path: [fd, depth]} per thread
_waiters = {}                      # lock path -> threading.Lock (threads of this process queue here first)
_waiters_lock = threading.Lock()


def _thread_lock(key):
    with _waiters_lock:
        return _waiters.setdefault(key, threading.Lock())


def _try_lock(fd):
    try:
        if os.name == 'nt':
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:  # pragma: no cover
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd):
    try:
        if os.name == 'nt':
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:  # pragma: no cover
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def locked(path, timeout=10.0):
    """Hold the OS lock on `<path>.lock` (other processes and other threads wait; the same thread may nest).
    Raises StateError('STATE_LOCKED') when it cannot be had within `timeout` seconds."""
    key = os.path.normcase(os.path.abspath(str(path) + '.lock'))
    held = getattr(_held, 'locks', None)
    if held is None:
        held = _held.locks = {}
    if key in held:
        held[key][1] += 1
        try:
            yield
        finally:
            held[key][1] -= 1
        return
    deadline = time.monotonic() + timeout
    gate = _thread_lock(key)
    if not gate.acquire(timeout=max(0.0, timeout)):
        raise StateError('STATE_LOCKED', 'Another thread holds the state lock %s' % key)
    fd = None
    try:
        Path(key).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key, os.O_RDWR | os.O_CREAT, 0o666)
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise StateError('STATE_LOCKED', 'Another process holds the state lock %s for more than %.1f s'
                                 % (key, timeout))
            time.sleep(0.02)
        held[key] = [fd, 1]
        try:
            yield
        finally:
            del held[key]
            _unlock(fd)
    finally:
        if fd is not None:
            os.close(fd)
        gate.release()


# ===================================================================== files
def atomic_write(path, data):
    """Replace `path` with `data` (bytes or str) so no reader sees half of it."""
    path = Path(path)
    if isinstance(data, str):
        data = data.encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('%s.%s.tmp' % (path.name, uuid.uuid4().hex))
    with open(tmp, 'wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:   # a reader (antivirus, editor, another process) has the file open without delete share
            time.sleep(0.05)
    try:   # last resort: write in place (the lock still keeps writers apart)
        with open(path, 'wb') as f:
            f.write(data)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


_warned = set()


def read_json(path, default):
    """The JSON value in `path`, or a copy of `default` when the file is missing, empty or not JSON (a warning is
    logged once per file per process for a corrupt one)."""
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return copy.deepcopy(default)
    except OSError as e:
        _warn(path, 'cannot be read (%s)' % e)
        return copy.deepcopy(default)
    if not text.strip():
        return copy.deepcopy(default)
    try:
        value = json.loads(text)
    except ValueError as e:
        _warn(path, 'is not valid JSON (%s); using the default' % e)
        return copy.deepcopy(default)
    if default is not None and not isinstance(value, type(default)):
        _warn(path, 'holds a %s, not a %s; using the default' % (type(value).__name__, type(default).__name__))
        return copy.deepcopy(default)
    return value


def _warn(path, what):
    key = os.path.normcase(str(path))
    if key not in _warned:
        _warned.add(key)
        log.warning('state file %s %s', path, what)


def write_json(path, value, indent=None):
    atomic_write(path, json.dumps(value, indent=indent) + ('\n' if indent else ''))


def update_json(path, fn, default, indent=None):
    """Read, change and write a JSON state file under its lock. `fn(value)` returns the new value, or None after
    changing `value` in place. Returns the new value."""
    with locked(path):
        value = read_json(path, default)
        new = fn(value)
        if new is None:
            new = value
        write_json(path, new, indent)
        return new


# ===================================================================== game identity
class GameIdentity(NamedTuple):
    """A game process: its pid and its start time (FILETIME as an int; None when it could not be read)."""
    pid: int
    start: Optional[int] = None

    def same_game(self, other):
        """False only when the pids differ, or both start times are known and differ (R20: an unknown start
        keeps state, it never wipes it)."""
        if other is None:
            return False
        if self.pid != other.pid:
            return False
        return self.start is None or other.start is None or self.start == other.start

    def key(self):
        return '%d:%s' % (self.pid, '?' if self.start is None else self.start)

    def as_dict(self):
        return {'pid': self.pid, 'started': None if self.start is None else str(self.start)}

    @classmethod
    def parse(cls, value):
        """A GameIdentity from any form the bridge has stored: a GameIdentity, {'pid', 'started'|'start'},
        'pid:start', 'pid:?', [pid, start] or a bare pid. None for anything else."""
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, GameIdentity):
            return value
        try:
            if isinstance(value, dict):
                pid = value.get('pid')
                start = value.get('started', value.get('start'))
            elif isinstance(value, (list, tuple)):
                pid, start = (list(value) + [None])[:2]
            elif isinstance(value, str):
                pid, _, start = value.partition(':')
                start = None if start in ('', '?') else start
            else:
                pid, start = value, None
            if pid is None:
                return None
            pid = int(pid)
            if start in (None, '', '?'):
                start = None
            else:
                try:
                    start = int(start)
                except (TypeError, ValueError):
                    start = str(start)       # a start of another form (tests, older files) still compares by value
            return cls(pid, start) if pid > 0 else None
        except (TypeError, ValueError):
            return None


# ===================================================================== sqlite
def sqlite_connect(path, timeout=15.0, **kwargs):
    """A connection to a SQLite file shared by several processes: WAL, a busy timeout, autocommit outside
    explicit `write_txn` blocks."""
    conn = sqlite3.connect(str(path), timeout=timeout, isolation_level=None, **kwargs)
    conn.execute('PRAGMA busy_timeout=%d' % int(timeout * 1000))
    with contextlib.suppress(sqlite3.DatabaseError):
        conn.execute('PRAGMA journal_mode=WAL')
    return conn


@contextlib.contextmanager
def write_txn(conn):
    """BEGIN IMMEDIATE ... COMMIT, ROLLBACK on any exception (the write lock is taken up front, so two writers
    queue instead of failing half way)."""
    conn.execute('BEGIN IMMEDIATE')
    try:
        yield conn
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute('ROLLBACK')
        raise
    conn.execute('COMMIT')
