"""Tell the supervisor that this server is still running a call that changes something (PLAN-3 S1.4, bug R4).

A cancelled MCP call is never answered, so the supervisor stops counting it as in flight; but an act call is shielded
and keeps running in this process. While any such call is inside track(), the file named by GECK_MCP_BUSY_FILE holds
{"count": n, "calls": [names], "since": t}. The supervisor reads it (count) before a hot reload and waits until it is
0. Without the variable (a server run on its own) track() does nothing. Standard library only."""
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_lock = threading.Lock()
_calls = []        # names of the calls inside track(), oldest first
_since = None


def _write(path):
    tmp = path.with_name('%s.%d.tmp' % (path.name, os.getpid()))
    tmp.write_text(json.dumps({'count': len(_calls), 'calls': list(_calls), 'since': _since}), encoding='utf-8')
    os.replace(tmp, path)


@contextmanager
def track(name):
    global _since
    target = os.environ.get('GECK_MCP_BUSY_FILE')
    path = Path(target) if target else None
    with _lock:
        _calls.append(str(name))
        if len(_calls) == 1:
            _since = time.time()
        if path is not None:
            try:
                _write(path)
            except OSError:
                pass        # the file is advice for a reload; the call itself must not fail on it
    try:
        yield
    finally:
        with _lock:
            _calls.remove(str(name))
            if not _calls:
                _since = None
            if path is not None:
                try:
                    _write(path)
                except OSError:
                    pass


def count(path):
    """How many calls the server at `path` is running: 0 when the file is missing or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return max(0, int(data.get('count', 0)))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0
