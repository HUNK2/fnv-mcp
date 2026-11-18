"""The call log (tool quality part 1): one JSON line per tool call in <state_dir>/fnv/calls.jsonl.

A line says when, which tool and action, whether it worked, the error code, how long it took, how many characters
the answer had and whether it was cut. It holds no argument values and no answer content: arguments can carry
paths and script text. Written by BridgeServer for every call (the SDK's own refusals included), kept 30 days by
the state maintenance at server start (fnv_state.prune), read by fnv_journal action=calls."""
import collections
import json
import logging
import re
import time
from pathlib import Path

from .fnv_state import atomic_write, locked

log = logging.getLogger(__name__)

KEEP_DAYS = 30
ACTION = re.compile(r'^[a-z_]{1,40}$')      # an action name; anything else is not recorded


def log_path(state_dir):
    return Path(state_dir) / 'fnv' / 'calls.jsonl'


class CallLog:
    def __init__(self, state_dir):
        self.path = log_path(state_dir)

    def record(self, tool, action, ok, code, elapsed_ms, size, cut):
        """Append one line. Never raises: the log must not fail the call it describes."""
        line = {'t': round(time.time(), 3), 'tool': str(tool)[:80],
                'action': action if isinstance(action, str) and ACTION.match(action) else None,
                'ok': bool(ok), 'code': None if ok else (str(code)[:60] if code else 'UNKNOWN'),
                'ms': int(elapsed_ms), 'size': int(size), 'cut': bool(cut)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with locked(self.path, timeout=2.0):
                with open(self.path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(line) + '\n')
        except Exception as e:  # noqa: BLE001 - a full disk or a locked file loses one line, not the call
            log.warning('call log not written: %s', e)

    def read(self, since_seconds=None, now=None):
        """The lines (oldest first), only those of the last `since_seconds` when given. Broken lines are skipped."""
        if not self.path.is_file():
            return []
        cutoff = None if since_seconds is None else (now or time.time()) - since_seconds
        out = []
        for text in self.path.read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                row = json.loads(text)
            except ValueError:
                continue
            if isinstance(row, dict) and (cutoff is None or (row.get('t') or 0) > cutoff):
                out.append(row)
        return out

    def summary(self, since_seconds=None, limit=10):
        rows = self.read(since_seconds)
        failed = [r for r in rows if not r.get('ok')]
        per_tool = {}
        for r in rows:
            t = per_tool.setdefault(r.get('tool'), {'tool': r.get('tool'), 'calls': 0, 'failed': 0, 'maxMs': 0})
            t['calls'] += 1
            t['failed'] += 0 if r.get('ok') else 1
            t['maxMs'] = max(t['maxMs'], r.get('ms') or 0)
        tools = sorted(per_tool.values(), key=lambda t: (-t['failed'], -t['calls'], t['tool'] or ''))
        for t in tools:
            t['rate'] = round(t['failed'] / t['calls'], 3)
        codes = collections.Counter(r.get('code') for r in failed)

        def top(key):
            return [{k: r.get(k) for k in ('t', 'tool', 'action', 'ok', 'code', 'ms', 'size')}
                    for r in sorted(rows, key=lambda r: -(r.get(key) or 0))[:limit]]
        span = 'the last %d s' % since_seconds if since_seconds is not None else 'the log (%d days kept)' % KEEP_DAYS
        return {'verdict': '%d calls in %s, %d failed' % (len(rows), span, len(failed)),
                'reason': None if rows else 'No calls recorded in that range.',
                'total': len(rows), 'failed': len(failed),
                'failureRate': round(len(failed) / len(rows), 3) if rows else None,
                'cut': sum(1 for r in rows if r.get('cut')),
                'perTool': tools[:max(limit, 1) * 5],
                'perCode': [{'code': c, 'count': n} for c, n in codes.most_common()],
                'slowest': top('ms'), 'largest': top('size'), 'path': str(self.path)}


def prune(state_dir, now, days=KEEP_DAYS):
    """Drop lines older than `days` (and broken lines). Returns how many were removed."""
    path = log_path(state_dir)
    if not path.is_file():
        return 0
    cutoff = now - days * 86400
    with locked(path):
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines(True)
        kept = []
        for text in lines:
            try:
                row = json.loads(text)
            except ValueError:
                continue
            if isinstance(row, dict) and (row.get('t') or 0) >= cutoff:
                kept.append(text if text.endswith('\n') else text + '\n')
        if len(kept) != len(lines):
            atomic_write(path, ''.join(kept))
    return len(lines) - len(kept)
