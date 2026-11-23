"""The call log (tool quality part 1): one JSON line per tool call in <state_dir>/fnv/calls.jsonl.

A line says when, which tool and action, whether it worked, the error code, how long it took, how many characters
the answer had and whether it was cut, and the arguments given (tool gaps 2): by default their names only
(fnv.call_log_arguments "names"); with "values" also their values, each cut to 200 characters, the line kept under
2 KB and the user's profile folder written as %USERPROFILE%; with "off" neither. Never the answer's content. Written
by BridgeServer for every call (the SDK's own refusals included), kept 30 days by the state maintenance at server
start (fnv_state.prune), read by fnv_journal action=calls."""
import collections
import json
import logging
import os
import re
import time
from pathlib import Path

from .fnv_state import atomic_write, locked

log = logging.getLogger(__name__)

KEEP_DAYS = 30
ACTION = re.compile(r'^[a-z_]{1,40}$')      # an action name; anything else is not recorded
MODES = ('off', 'names', 'values')
VALUE_MAX = 200         # characters of one argument value
LINE_MAX = 2048         # characters of one line
NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,63}$')
# The tools whose text arguments are what agents type into the game: their first word is counted.
TYPED = {'fnv_console': 'line', 'fnv_run_script': 'source'}
WORD = re.compile(r'^\s*(?:[A-Za-z0-9_]+\.)?([A-Za-z][A-Za-z0-9_]*)')


def log_path(state_dir):
    return Path(state_dir) / 'fnv' / 'calls.jsonl'


def _homes():
    """The user's profile folder, as it may be written in a value (both slashes)."""
    home = os.environ.get('USERPROFILE') or str(Path.home())
    return sorted({home, home.replace('\\', '/')}, key=len, reverse=True) if home else []


def scrub(text):
    for home in _homes():
        text = re.sub(re.escape(home), '%USERPROFILE%', text, flags=re.I)
    return text


def _value(value, limit=VALUE_MAX):
    """One argument value for the log: numbers, booleans and null as they are; text scrubbed and cut; anything else
    (lists, objects) as is when its JSON is short, else as cut JSON text."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        text = scrub(value)
    else:
        text = scrub(json.dumps(value, default=str))
        if len(text) <= limit:
            return json.loads(text)
    return text if len(text) <= limit else text[:limit - 3] + '...'


def arguments(args, mode):
    """(the line's 'args' value, cut) for the given mode, or (None, False) when nothing is written."""
    if mode == 'off' or not isinstance(args, dict) or not args:
        return None, False
    names = sorted(str(k)[:64] for k in args if NAME.match(str(k)))
    if mode != 'values':
        return names, False
    return {k: _value(args[k]) for k in names}, False


class CallLog:
    def __init__(self, state_dir, arguments='names'):
        self.path = log_path(state_dir)
        self.mode = arguments if arguments in MODES else 'names'

    def record(self, tool, action, ok, code, elapsed_ms, size, cut, args=None):
        """Append one line. Never raises: the log must not fail the call it describes."""
        line = {'t': round(time.time(), 3), 'tool': str(tool)[:80],
                'action': action if isinstance(action, str) and ACTION.match(action) else None,
                'ok': bool(ok), 'code': None if ok else (str(code)[:60] if code else 'UNKNOWN'),
                'ms': int(elapsed_ms), 'size': int(size), 'cut': bool(cut)}
        try:
            given, _ = arguments(args, self.mode)
            if given is not None:
                line['args'] = given
                if isinstance(given, dict) and len(json.dumps(line)) > LINE_MAX:
                    # Too long: every value shorter in turn, then values dropped (the names stay).
                    for limit in (60, 20):
                        line['args'] = {k: _value(args[k], limit) for k in given}
                        if len(json.dumps(line)) + 16 <= LINE_MAX:
                            break
                    else:
                        line['args'] = {k: None for k in given}
                    line['argsCut'] = True
        except Exception as e:  # noqa: BLE001 - an odd argument loses its args, not the line
            log.warning('call log arguments not written: %s', e)
            line.pop('args', None)
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

    def summary(self, since_seconds=None, limit=10, known=None, tool=None):
        """Calls and failures per tool and code, the slowest and largest answers; with known (the registered tool
        names), also the tools never called in that range; with tool, only that tool's calls. consoleWords: the
        first word of each fnv_console line and fnv_run_script source, when values are logged."""
        rows = self.read(since_seconds)
        if tool:
            rows = [r for r in rows if r.get('tool') == tool]
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
        out = {'verdict': '%d calls in %s, %d failed' % (len(rows), span, len(failed)),
                'reason': None if rows else 'No calls recorded in that range.',
                'total': len(rows), 'failed': len(failed),
                'failureRate': round(len(failed) / len(rows), 3) if rows else None,
                'cut': sum(1 for r in rows if r.get('cut')),
                'cutMeans': 'answers that were too long and came back shortened (truncated: true); call those again '
                            'with a smaller limit, brief=true or fields',
                'perTool': tools[:max(limit, 1) * 5],
                'perCode': [{'code': c, 'count': n} for c, n in codes.most_common()],
                'slowest': top('ms'), 'largest': top('size'), 'path': str(self.path)}
        words = collections.Counter()
        for r in rows:
            given = r.get('args')
            field = TYPED.get(r.get('tool'))
            if field and isinstance(given, dict) and isinstance(given.get(field), str):
                m = WORD.match(given[field])
                if m:
                    words[m.group(1).lower()] += 1
        out['consoleWords'] = [{'word': w, 'count': n} for w, n in words.most_common(max(limit, 1) * 3)]
        out['consoleWordsNote'] = ('the first word of each fnv_console line and fnv_run_script source: commands '
                                   'agents type, often for lack of a tool. Only lines logged with values count '
                                   '(fnv.call_log_arguments "values"; now "%s").' % self.mode)
        if tool:
            out['tool'] = tool
        if known is not None and not tool:
            never = sorted(set(known) - set(per_tool))
            out['neverCalled'] = {'count': len(never), 'tools': never[:40]}
        return out


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
