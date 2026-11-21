"""Which checkout and commit this server runs (critique fix 2026-11-21): fnv_status carries it as "server", so an
answer from a server running old code, or from a second checkout, says so."""
import subprocess
import time
from pathlib import Path

BRIDGE = Path(__file__).resolve().parent.parent
STARTED_AT = round(time.time(), 1)
_cache = {}


def _git(*args):
    out = subprocess.run(['git', '-C', str(BRIDGE), *args], capture_output=True, text=True, timeout=5)
    return out.stdout.strip() if out.returncode == 0 else None


def stamp():
    """{path, commit, dirty, startedAt}: the bridge folder, its short commit and whether tracked files differ from it
    (None when git cannot tell), and when this server process started."""
    if not _cache:
        commit = dirty = None
        try:
            commit = _git('rev-parse', '--short', 'HEAD')
            if commit is not None:
                changed = _git('status', '--porcelain', '--untracked-files=no')
                dirty = None if changed is None else bool(changed)
        except (OSError, subprocess.SubprocessError):
            commit = dirty = None
        _cache.update(path=str(BRIDGE), commit=commit, dirty=dirty, startedAt=STARTED_AT)
    return dict(_cache)
