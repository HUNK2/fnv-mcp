"""Where things are on this machine, found without a hard-coded path (detach from one PC, 2026-11-26).

- The game folder for a project file whose game_root is "auto": the game's own registry key, Steam's uninstall entry
  for app 22380, then every Steam library in libraryfolders.vdf.
- 7-Zip (tools/fnv_setup_dev_profile.py): PATH, its registry key, then %ProgramFiles%.
- The unpacked game image: fnv.clean_exe, FNV_CLEAN_EXE, then fnvdecomp's ~/.fnvdecomp/FalloutNV.exe.
- The local project file for live runs: FNV_LIVE_PROJECT, projects/my-fnv.json, or the only one in projects/.
Each answers None when nothing is found; the caller says what to set."""
import os
import re
import shutil
from pathlib import Path

STEAM_APP_ID = '22380'
GAME_EXE = 'FalloutNV.exe'
HKLM, HKCU = 'HKLM', 'HKCU'


def registry_value(hive, key, name):
    """A string value from the 32- or 64-bit registry view, or None (also off Windows)."""
    try:
        import winreg
    except ImportError:
        return None
    root = winreg.HKEY_LOCAL_MACHINE if hive == HKLM else winreg.HKEY_CURRENT_USER
    for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
        try:
            with winreg.OpenKey(root, key, 0, winreg.KEY_READ | view) as handle:
                value, _ = winreg.QueryValueEx(handle, name)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        except OSError:
            continue
    return None


def steam_libraries(steam_root):
    """The Steam folder itself and every library path in its steamapps/libraryfolders.vdf."""
    steam_root = Path(steam_root)
    out = [steam_root]
    vdf = steam_root / 'steamapps' / 'libraryfolders.vdf'
    try:
        text = vdf.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return out
    for raw in re.findall(r'"path"\s+"((?:[^"\\]|\\.)*)"', text):
        path = Path(raw.replace('\\\\', '\\'))
        if path not in out:
            out.append(path)
    return out


def game_candidates(read=registry_value):
    out = []
    for hive, key, name in ((HKLM, r'SOFTWARE\Bethesda Softworks\FalloutNV', 'Installed Path'),
                            (HKLM, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Steam App ' + STEAM_APP_ID,
                             'InstallLocation')):
        value = read(hive, key, name)
        if value:
            out.append(Path(value.rstrip('\\/')))
    steam = read(HKCU, r'Software\Valve\Steam', 'SteamPath') or read(HKLM, r'SOFTWARE\Valve\Steam', 'InstallPath')
    if steam:
        out += [library / 'steamapps' / 'common' / 'Fallout New Vegas' for library in steam_libraries(steam)]
    return out


def find_game_root(read=registry_value):
    """The Fallout: New Vegas folder (the one with FalloutNV.exe), or None."""
    for candidate in game_candidates(read):
        if (candidate / GAME_EXE).is_file():
            return candidate
    return None


def game_data():
    """The game's Data folder: FNV_DATA, else the found game's Data, else None (offline tools and tests)."""
    if os.environ.get('FNV_DATA'):
        return Path(os.environ['FNV_DATA'])
    game = find_game_root()
    return game / 'Data' if game else None


def find_7zip(read=registry_value):
    on_path = shutil.which('7z')
    if on_path:
        return Path(on_path)
    folder = read(HKLM, r'SOFTWARE\7-Zip', 'Path')
    candidates = [Path(folder) / '7z.exe'] if folder else []
    for var in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)'):
        if os.environ.get(var):
            candidates.append(Path(os.environ[var]) / '7-Zip' / '7z.exe')
    return next((c for c in candidates if c.is_file()), None)


def clean_image(project):
    """The user's own unpacked FalloutNV.exe: fnv.clean_exe, then FNV_CLEAN_EXE, then ~/.fnvdecomp/FalloutNV.exe
    (where the fnvdecomp kit keeps it). The path, whether or not the file exists."""
    fnv = getattr(project, 'fnv', None)
    path = getattr(fnv, 'clean_exe', None) or os.environ.get('FNV_CLEAN_EXE')
    if path:
        return Path(path)
    home = os.environ.get('USERPROFILE')
    if not home:
        try:
            home = str(Path.home())
        except RuntimeError:          # no profile folder known at all
            home = '~'
    return Path(home) / '.fnvdecomp' / GAME_EXE


# The compiler kits fnv_decomp's tools/msvc.py runs (fnv_code build): one environment variable each, the folder that
# holds VC/ and SDK/. Off Windows msvc.py runs them under Wine and checks for itself.
COMPILER_ROOTS = {'sp1': 'FNV_MSVC9_ROOT', 'rtm': 'FNV_MSVC9_RTM_ROOT', 'vc8': 'FNV_MSVC8_ROOT'}
COMPILER_NAMES = {'sp1': 'Visual C++ 2008 SP1', 'rtm': 'Visual C++ 2008 RTM', 'vc8': 'Visual C++ 2005 SP1'}


def compiler_problem(name, os_name=os.name):
    """None when the kit for compiler name (sp1, rtm, vc8) is in place, else what to set."""
    if os_name != 'nt':
        return None
    variable = COMPILER_ROOTS.get(name)
    if variable is None:
        return 'unknown compiler %r in build/units.json' % name
    root = os.environ.get(variable)
    if not root:
        return ('%s is not set: set it to the folder holding VC/ and SDK/ of %s (the kit fnv_decomp builds with), '
                'then restart the MCP server' % (variable, COMPILER_NAMES[name]))
    if not (Path(root) / 'VC' / 'bin' / 'cl.exe').is_file():
        return '%s=%s has no VC/bin/cl.exe' % (variable, root)
    return None


def project_file(projects_dir):
    """The local project file for live runs: FNV_LIVE_PROJECT, projects/my-fnv.json (the setup guide's name), or
    the only non-example file in projects/. None when there are several and none of those names one."""
    env = os.environ.get('FNV_LIVE_PROJECT')
    if env:
        return Path(env)
    projects_dir = Path(projects_dir)
    mine = projects_dir / 'my-fnv.json'
    if mine.is_file():
        return mine
    own = [p for p in sorted(projects_dir.glob('*.json')) if not p.name.startswith('example-')]
    return own[0] if len(own) == 1 else None


def project_file_or_exit(projects_dir):
    """project_file, or a SystemExit that says how to name one."""
    found = project_file(projects_dir)
    if found is None:
        own = [p.name for p in sorted(Path(projects_dir).glob('*.json')) if not p.name.startswith('example-')]
        raise SystemExit('No project file chosen: pass --project, set FNV_LIVE_PROJECT, or name it projects/my-fnv.json'
                         + (' (found: %s)' % ', '.join(own) if own else ' (copy projects/example-fnv.json)'))
    return found
