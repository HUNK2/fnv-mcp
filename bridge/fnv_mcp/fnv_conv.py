"""Shared conventions for the action-style fnv_* tools (fnv_<domain>(action=..., ...)).
See docs/FNV-TOOL-CONVENTIONS.md. Small on purpose: dispatch, paging, verdicts, next steps, references."""
import difflib
import logging
import re
import traceback
from pathlib import Path

PAGE_MAX = 500
log = logging.getLogger('fnv_mcp')


class ConvError(Exception):
    """An error with a code and, when the code is known, the next step. live.run reports `code`."""

    def __init__(self, code, message, evidence=None):
        step = next_step(code)
        super().__init__(message + (' Next: ' + step if step and step not in message else ''))
        self.code = code
        self.evidence = evidence


def dispatch(action, table):
    """Call table[action](). An unknown action raises UNKNOWN_ACTION and lists the valid ones."""
    handler = table.get(action)
    if handler is None:
        raise ConvError('UNKNOWN_ACTION', 'Unknown action %r. Valid actions: %s.' % (action, ', '.join(sorted(table))),
                        {'actions': sorted(table)})
    return handler()


def page(rows, limit=50, offset=0, fields=None, brief=False, brief_fields=None):
    """One shape for every list: total, offset, count, more, rows. `fields` keeps only those keys of each row;
    brief keeps `brief_fields` (or the first three keys when none are given)."""
    rows = list(rows)
    limit = max(1, min(int(limit), PAGE_MAX))
    offset = max(0, int(offset))
    chosen = rows[offset:offset + limit]
    keep = list(fields) if fields else (list(brief_fields) if brief and brief_fields else None)
    if keep is None and brief and chosen and isinstance(chosen[0], dict):
        keep = list(chosen[0])[:3]
    if keep is not None:
        chosen = [{k: r.get(k) for k in keep if k in r} if isinstance(r, dict) else r for r in chosen]
    return {'total': len(rows), 'offset': offset, 'count': len(chosen), 'more': offset + len(chosen) < len(rows),
            'rows': chosen}


def verdict(text, reason=None, **evidence):
    """A result whose first keys are verdict and reason; the evidence follows."""
    out = {'verdict': text, 'reason': reason}
    out.update(evidence)
    return out


# Next steps for error codes that already exist in the code base (fnv_live, fnv_game, fnv_link, fnv_engine).
# The symbol database has no code yet: add it here when fnv_symdb arrives.
NEXT_STEPS = {
    'GAME_NOT_RUNNING': 'fnv_game launch, then call again.',
    'NOT_RUNNING': 'The game is not running: call fnv_game launch.',
    'GAME_EXITED': 'The game exited: call fnv_game launch (fnv_crash shows why when it crashed).',
    'GAME_STARTING': 'The game is still starting: wait, then call fnv_status.',
    'NO_GAME_LOADED': 'No save or cell is loaded: call fnv_game restart with start=continue, a save or a cell '
                      '(fnv_game launch when the game is closed).',
    'COMPANION_TIMEOUT': 'The engine companion did not answer: call fnv_status; if the game hangs call fnv_threads action=hang.',
    'COMPANION_DISCONNECTED': 'The engine companion is gone: call fnv_status, then fnv_game launch if the game is not running.',
    'COMPANION_UNREACHABLE': 'The engine companion does not answer: call fnv_status; if it is not installed, quit the game, '
                             'call fnv_companion_install, then fnv_game launch.',
    'COMPANION_NOT_INSTALLED': 'The engine companion is not installed: quit the game, call fnv_companion_install '
                               "(target='game' without MO2), then fnv_game launch.",
    'DIRECT_INSTALL_PROTECTED': 'This would change your real game (no MO2 dev profile): set '
                                'fnv.allow_direct_install_changes to true in the project file if you want that, or '
                                'start at the menu or with coc: instead of a save.',
    'ZIG_MISSING': 'Zig is not installed: call fnv_setup_check and follow the fix for Zig.',
    'CONTROL_DISABLED': 'This call needs fnv.engine_control = "full": it changes the running game, or puts code or '
                        'files into the game or its mod profile. Set it in the project file.',
    'ENGINE_MAP_UNAVAILABLE': 'The engine map is missing: call fnv_setup_check and follow the fix for the engine map.',
    'NOT_CONFIGURED': 'Set fnv.mo2_root and fnv.mo2_profile in the project file (or start the game without MO2); '
                      'fnv_setup_check lists what else is missing.',
    'REF_NOT_FOUND': 'No such reference: check the spelling, or find it with fnv_record action=find or fnv_refs_near.',
    'UNKNOWN_ACTION': 'Call the tool again with one of the valid actions.',
    'INTERNAL_ERROR': 'The tool hit a bug and the call changed nothing: report it with this message (the traceback '
                      'is in the bridge log). Another tool or other arguments may do the job meanwhile.',
}


def nearest(name, candidates, n=3):
    """Up to n candidates closest to name, for "did you mean": the same name in other case first, then names that
    start with it or it starts with, then names that contain it, then the closest spellings (difflib)."""
    key = str(name or '').strip().lower()
    if not key:
        return []
    by_lower = {}
    for c in candidates:
        if isinstance(c, str) and c:
            by_lower.setdefault(c.lower(), c)
    out = []

    def add(lows):
        for low in lows:
            if by_lower[low] not in out:
                out.append(by_lower[low])
    add([key] if key in by_lower else [])
    add(sorted((low for low in by_lower if low != key and (low.startswith(key) or key.startswith(low))
                and min(len(low), len(key)) >= 3), key=lambda low: (abs(len(low) - len(key)), low)))
    if len(out) < n and len(key) >= 3:
        add(sorted((low for low in by_lower if key in low and low not in (o.lower() for o in out)), key=len))
    if len(out) < n:
        pool = list(by_lower)
        if len(pool) > 5000:
            pool = [low for low in pool if low[:1] == key[:1] and abs(len(low) - len(key)) <= 3]
        add(difflib.get_close_matches(key, pool, n=n, cutoff=0.75 if len(key) > 4 else 0.5))
    return out[:n]


def did_you_mean(name, candidates, n=3):
    """' Did you mean X, Y?' or '' when nothing is close."""
    hits = nearest(name, candidates, n)
    return ' Did you mean %s?' % ', '.join(hits) if hits else ''


def internal_error(e):
    """(message, evidence) for an exception no tool meant to raise: the type and the place in the bridge code, never
    the exception's own text (it can be a Python detail or hold a path). The traceback goes to the bridge log."""
    frames = traceback.extract_tb(e.__traceback__) if e.__traceback__ else []
    ours = [f for f in frames if 'fnv_mcp' in f.filename.replace('\\', '/')]
    frame = (ours or frames or [None])[-1]
    where = '%s:%d' % (Path(frame.filename).name, frame.lineno) if frame else 'an unknown place'
    log.error('tool bug: %s in %s', type(e).__name__, where, exc_info=(type(e), e, e.__traceback__))
    return ('The tool hit a bug: %s in %s.' % (type(e).__name__, where),
            {'bug': type(e).__name__, 'where': where})


def next_step(code):
    return NEXT_STEPS.get(code)


# The recovery line of a failure whose code has no NEXT_STEPS entry (live check 2026-11-18: every one of these said
# "Fix the input or the game state (fnv_status) and call again."). Recovery only: ConvError messages are unchanged.
RECOVERY = {
    'INVALID_ARGUMENT': 'Change the argument the message names and call again.',
    'NO_FIELD': 'Use one of the names the message suggests (fnv_class layout lists every field) and call again.',
    'NO_GLOBAL': 'Use the name the message suggests (fnv_globals query=<part of the name> lists them) and call '
                 'again.',
    'NOT_FOUND': 'Check the name: use a suggestion from the message, or search for it (fnv_guide find, fnv_record '
                 'action=find) and call again.',
    'BAD_PREDICATE': 'Rewrite the predicate in one of the example forms the message shows and call again.',
    'WAIT_TIMEOUT': 'Call fnv_time action=status (a paused clock or held gate never advances), then call again with '
                    'a larger timeout or a condition that can become true.',
    'GAME_RUNNING': 'Quit the game first (fnv_game action=quit), call this again, then fnv_game action=launch.',
    'TOO_STEEP': 'Do what the message says: turn it (only when it names a heading), move it to flatter ground, or '
                 'pass rest="centre" or "high".',
}
DEFAULT_RECOVERY = 'Read the message: it says what to change. Change that and call again (fnv_status if the game ' \
                   'state is in doubt).'


def recovery_for(code):
    """The recovery line of a failure that changed nothing: the code's next step, else its recovery, else the
    default."""
    return NEXT_STEPS.get(code) or RECOVERY.get(code) or DEFAULT_RECOVERY


# ---------------------------------------------------------------- references
class Aliases:
    """In-memory `$name` -> reference spec. Lives as long as the server process; a name is letters, digits, _."""
    NAME = re.compile(r'^\$[A-Za-z_][A-Za-z0-9_]{0,39}$')

    def __init__(self):
        self.items = {}

    def save(self, name, spec):
        name = name if name.startswith('$') else '$' + name
        if not self.NAME.match(name):
            raise ValueError('A saved name is $ followed by letters, digits or _ (at most 40)')
        self.items[name] = spec
        return name

    def get(self, name):
        if name not in self.items:
            raise ConvError('REF_NOT_FOUND', 'No saved reference %s. Saved: %s.' % (name, ', '.join(sorted(self.items)) or 'none'))
        return self.items[name]


ALIASES = Aliases()


def resolve_ref(live, ref, save_as=None, aliases=ALIASES):
    """One entry point for references: form id, Plugin.esp:LOCALID, EditorID, player or crosshair (all handled by
    LiveService.resolve) and `$name` saved earlier. save_as stores the resolved form id under that name.
    `crosshair` is the existing live resolver's: no new engine code here."""
    spec = aliases.get(ref.strip()) if isinstance(ref, str) and ref.strip().startswith('$') else ref
    resolved = live.resolve(spec)
    if save_as:
        resolved = dict(resolved, savedAs=aliases.save(save_as, resolved.get('formId') or spec))
    return resolved
