"""Which kind of game install the fnv_* tools work on, and where its files are.

Two kinds:
- "mo2": the Mod Organizer 2 dev profile (fnv.mo2_root, fnv.mo2_profile). Saves, Fallout.ini and the plugin lists
  live in the profile; mods are folders under mods/ that MO2's virtual file system (usvfs) lays over Data.
- "direct": the game folder as Steam installed it, started by Steam's Play button or nvse_loader.exe. Saves and
  Fallout.ini are in Documents\\My Games\\FalloutNV (fnv.game_user_dir), plugins.txt in %LOCALAPPDATA%\\FalloutNV
  (fnv.game_appdata_dir), and plugins and NVSE plugins sit in the real Data folder. This is the user's real game:
  the tools that change its files refuse unless fnv.allow_direct_install_changes is true (Game.guard_direct).

A running FalloutNV.exe is judged by whether MO2's usvfs_x86.dll is loaded in it, so a game started from Steam is
treated as the direct install even when the project also names an MO2 profile. With no game running, the project
file decides: MO2 settings present means the dev profile.
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

MO2 = 'mo2'
DIRECT = 'direct'
USVFS_DLLS = ('usvfs_x86.dll', 'usvfs_x64.dll')


class LayoutError(Exception):
    def __init__(self, code, message, evidence=None):
        super().__init__(message)
        self.code = code
        self.evidence = evidence


# ---------------------------------------------------------------------- user folders
def documents_dir():
    """The user's Documents folder (it may be redirected, to OneDrive for one), from the shell."""
    try:
        buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:  # CSIDL_PERSONAL
            return Path(buffer.value)
    except (OSError, AttributeError):
        pass
    return Path(os.environ.get('USERPROFILE') or Path.home()) / 'Documents'


def default_user_dir():
    return documents_dir() / 'My Games' / 'FalloutNV'


def default_appdata_dir():
    return Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData' / 'Local')) / 'FalloutNV'


def ini_value(path, section, key):
    """One value from an ini file ([section] key=value, names without case), or None."""
    try:
        text = Path(path).read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    current = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('[') and line.endswith(']'):
            current = line[1:-1].strip().lower()
        elif current == section.lower() and '=' in line:
            name, _, value = line.partition('=')
            if name.strip().lower() == key.lower():
                return value.strip()
    return None


# ---------------------------------------------------------------------- the layout
@dataclass(frozen=True)
class Layout:
    kind: str                       # MO2 or DIRECT
    game_root: Path
    mo2_root: Path | None
    profile: str | None
    user_dir: Path                  # direct: Documents\My Games\FalloutNV
    appdata_dir: Path               # direct: %LOCALAPPDATA%\FalloutNV
    detected_by: str
    changes_allowed: bool           # fnv.allow_direct_install_changes (always true for the MO2 dev profile)

    @property
    def configured(self):
        """False for a game MO2 runs when the project file names no MO2 instance: its files are unknown."""
        return self.kind == DIRECT or bool(self.mo2_root and self.profile)

    def _mo2(self):
        if not self.configured:
            raise LayoutError('NOT_CONFIGURED', 'The running game was started by Mod Organizer 2, but the project file '
                              'names no MO2 instance: set fnv.mo2_root and fnv.mo2_profile, or start the game without '
                              'MO2 (Steam or nvse_loader.exe in the game folder).')

    @property
    def data_dir(self):
        return self.game_root / 'Data'

    @property
    def profile_dir(self):
        """The folder that holds Fallout.ini and (for MO2) the saves: the MO2 profile, or My Games\\FalloutNV."""
        if self.kind == DIRECT:
            return self.user_dir
        self._mo2()
        return self.mo2_root / 'profiles' / self.profile

    @property
    def saves_dir(self):
        if self.kind == MO2:
            return self.profile_dir / 'saves'
        local = ini_value(self.ini, 'General', 'SLocalSavePath')
        if local:
            path = Path(local.replace('\\', os.sep).rstrip('\\/' + os.sep))
            return path if path.is_absolute() else self.user_dir / path
        return self.user_dir / 'Saves'

    @property
    def ini(self):
        return self.profile_dir / 'Fallout.ini'

    @property
    def prefs_ini(self):
        return self.profile_dir / 'FalloutPrefs.ini'

    @property
    def lists_dir(self):
        """The folder with plugins.txt (and loadorder.txt): the MO2 profile, or %LOCALAPPDATA%\\FalloutNV."""
        return self.profile_dir if self.kind == MO2 else self.appdata_dir

    @property
    def plugins_txt(self):
        return self.lists_dir / 'plugins.txt'

    @property
    def loadorder_txt(self):
        return self.lists_dir / 'loadorder.txt'

    @property
    def modlist(self):
        return self.profile_dir / 'modlist.txt' if self.kind == MO2 else None

    @property
    def mods_dir(self):
        if self.kind == DIRECT:
            return None
        self._mo2()
        return self.mo2_root / 'mods'

    @property
    def overwrite_dir(self):
        return self.mo2_root / 'overwrite' if self.kind == MO2 and self.mo2_root else None

    @property
    def nvse_plugins_dir(self):
        """Where an xNVSE plugin DLL is installed on a direct install (MO2 puts each in its own mod folder)."""
        return self.data_dir / 'NVSE' / 'Plugins'

    def lock_path(self, state_dir):
        """The file whose lock every edit of the plugin lists holds: modlist.txt for MO2 (as before), and a file in
        the bridge state for the direct install, so nothing is created next to the user's plugins.txt."""
        if self.kind == MO2:
            return self.modlist
        return Path(state_dir) / 'fnv' / 'direct-plugin-lists'

    def enabled_mods(self):
        """MO2: the enabled mod names, highest priority first. Direct: none."""
        if self.kind == DIRECT or not self.modlist.exists():
            return []
        lines = self.modlist.read_text(encoding='utf-8', errors='replace').splitlines()
        return [line[1:].strip() for line in lines if line.startswith('+')]

    def loose_roots(self):
        """Folders loose files are found in, highest priority first, ending with Data."""
        roots = []
        if self.kind == MO2:
            roots = [self.mods_dir / m for m in self.enabled_mods() if (self.mods_dir / m).is_dir()]
        return roots + [self.data_dir]

    def describe(self):
        out = {'kind': self.kind, 'detectedBy': self.detected_by, 'changesAllowed': self.changes_allowed}
        if not self.configured:
            out['note'] = 'started by Mod Organizer 2, but the project file names no MO2 instance'
            return out
        paths = {'game': self.game_root, 'data': self.data_dir, 'saves': self.saves_dir, 'ini': self.ini,
                 'plugins': self.plugins_txt}
        if self.kind == MO2:
            paths.update(mo2=self.mo2_root, profile=self.profile_dir, mods=self.mods_dir)
        else:
            paths['nvsePlugins'] = self.nvse_plugins_dir
        out['paths'] = {k: str(v) for k, v in paths.items()}
        if self.kind == DIRECT and not self.changes_allowed:
            out['protected'] = ('Your real game: tools that write into Data, plugins.txt or the ini, or load or '
                                'overwrite saves, refuse until fnv.allow_direct_install_changes is true.')
        return out


def _make(project, kind, detected_by):
    fnv = getattr(project, 'fnv', None)
    mo2_root = getattr(fnv, 'mo2_root', None) if fnv else None
    profile = getattr(fnv, 'mo2_profile', None) if fnv else None
    user = getattr(fnv, 'game_user_dir', None) if fnv else None
    appdata = getattr(fnv, 'game_appdata_dir', None) if fnv else None
    allowed = bool(getattr(fnv, 'allow_direct_install_changes', False)) if fnv else False
    game_root = getattr(project, 'game_root', None)
    return Layout(kind=kind, game_root=Path(game_root) if game_root else None,
                  mo2_root=Path(mo2_root) if mo2_root else None,
                  profile=profile or None, user_dir=Path(user) if user else default_user_dir(),
                  appdata_dir=Path(appdata) if appdata else default_appdata_dir(), detected_by=detected_by,
                  changes_allowed=allowed or kind == MO2)


def configured(project):
    """The layout the project file names: the MO2 dev profile when fnv.mo2_root and fnv.mo2_profile are set, else
    the direct install."""
    fnv = getattr(project, 'fnv', None)
    has_mo2 = bool(fnv and getattr(fnv, 'mo2_root', None) and getattr(fnv, 'mo2_profile', None))
    return _make(project, MO2 if has_mo2 else DIRECT, 'configuration (no game running)')


# ---------------------------------------------------------------------- the running game
class _ModuleEntry(ctypes.Structure):          # MODULEENTRY32W
    _fields_ = [('dwSize', wintypes.DWORD), ('th32ModuleID', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                ('GlblcntUsage', wintypes.DWORD), ('ProccntUsage', wintypes.DWORD),
                ('modBaseAddr', ctypes.c_void_p), ('modBaseSize', wintypes.DWORD), ('hModule', wintypes.HMODULE),
                ('szModule', wintypes.WCHAR * 256), ('szExePath', wintypes.WCHAR * 260)]


_api = None


def _kernel32():
    global _api
    if _api is None:
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        api.Module32FirstW.argtypes = api.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ModuleEntry)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        _api = api
    return _api


def process_modules(pid):
    """File names of the modules loaded in process pid (32-bit ones too), or None when it cannot be read."""
    api = _kernel32()
    invalid = wintypes.HANDLE(-1).value
    for _ in range(5):          # ERROR_BAD_LENGTH while the process is loading modules: try again
        snapshot = api.CreateToolhelp32Snapshot(0x8 | 0x10, pid)     # TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32
        if snapshot and snapshot != invalid:
            break
        if ctypes.get_last_error() != 24:
            return None
        time.sleep(0.05)
    else:
        return None
    try:
        entry = _ModuleEntry()
        entry.dwSize = ctypes.sizeof(entry)
        names = []
        more = api.Module32FirstW(snapshot, ctypes.byref(entry))
        while more:
            names.append(entry.szModule)
            more = api.Module32NextW(snapshot, ctypes.byref(entry))
        return names
    finally:
        api.CloseHandle(snapshot)


def process_image(pid):
    from .fnv_game import image_path
    return image_path(pid)


_seen = {}
_seen_lock = threading.Lock()


def forget():
    """Drop what was learnt about running games (tests)."""
    with _seen_lock:
        _seen.clear()
    _running[0] = None


def for_running(project, proc):
    """The layout of the running game proc = (pid, start time), or None when it is not this project's game (its
    program is not in game_root) or its modules cannot be read. Kept per process: a game's install kind never
    changes while it runs."""
    if not proc:
        return None
    pid = proc[0]
    image = process_image(pid)
    if not image or os.path.normcase(os.path.dirname(image)) != os.path.normcase(str(Path(project.game_root))):
        return None
    key = (pid, proc[1])
    with _seen_lock:
        under_mo2 = _seen.get(key)
    if under_mo2 is None:
        modules = process_modules(pid)
        if modules is None:
            return None
        under_mo2 = any(m.lower() in USVFS_DLLS for m in modules)
        with _seen_lock:
            _seen[key] = under_mo2
    if under_mo2:
        return _make(project, MO2, 'running game (MO2 file system usvfs loaded)')
    return _make(project, DIRECT, 'running game (no MO2 file system: started by Steam or nvse_loader)')


_running = [None]       # (monotonic time, proc) - one process snapshot a second at most


def running_game(project):
    """(pid, start) of the one running FalloutNV.exe, or None (none, or several)."""
    cached = _running[0]
    if cached and time.monotonic() - cached[0] < 1.0:
        return cached[1]
    from .fnv_game import game_processes
    try:
        procs = game_processes()
    except OSError:
        procs = []
    proc = procs[0] if len(procs) == 1 else None
    _running[0] = (time.monotonic(), proc)
    return proc


def current(project, proc=None):
    """The layout of the running game (proc, or the one running), else the configured one."""
    proc = proc if proc is not None else running_game(project)
    return for_running(project, proc) or configured(project)


def guard(layout, what):
    """Refuse a change to the user's real game files on a direct install, unless the project allows it."""
    if layout.kind == DIRECT and not layout.changes_allowed:
        raise LayoutError('DIRECT_INSTALL_PROTECTED', 'Refused to %s: this is the direct install (your real game, '
                          'not an MO2 dev profile). Set fnv.allow_direct_install_changes to true in the project file '
                          'to let the tools change its Data folder, plugins.txt, ini and saves.' % what,
                          {'install': layout.kind, 'setting': 'fnv.allow_direct_install_changes'})

