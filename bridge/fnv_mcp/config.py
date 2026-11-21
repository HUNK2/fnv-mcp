from pathlib import Path
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator


RETIRED_KEYS = ('launcher', 'records', 'source_roots', 'helper', 'helper_timeout_seconds', 'transport',
                'legacy_tools')


class FnvSettings(BaseModel):
    """Optional settings for the fnv_* tools (engine knowledge, crash doctor). Paths may be relative to the
    project file. Every field can stay unset: engine_map defaults to <state_dir>/engine_map.json and the
    decomp checkout to the path recorded in the map."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    engine_map: Path | None = None
    decomp_root: Path | None = None
    # Symbol database built by engine_map.symdb (default: <state_dir>/symdb.sqlite). See docs/FNV-SYMDB.md.
    symdb: Path | None = None
    # Folder of saved engine research notes (Markdown) that fnv_guide searches.
    research_dir: Path | None = None
    crash_dirs: list[Path] = Field(default_factory=list, max_length=20)
    mo2_root: Path | None = None
    mo2_profile: str | None = Field(default=None, pattern=r'^[^\\/:*?"<>|]{1,64}$')
    # Game control and the engine companion (fnv-mcp milestones 4-6).
    mo2_executable: str = Field(default='NVSE', pattern=r'^[^\\/:*?"<>|]{1,64}$')
    zig: Path | None = None
    engine_pipe: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_.-]{1,64}$')
    # Act tools (memory writes, engine calls, console changes) refuse unless this is "full".
    engine_control: Literal['read', 'full'] = 'read'
    # Seconds after its process appears that a starting game that draws no frames counts as hung and is started once
    # more (critique fix 2026-11-21). Unset: 45, or three times the median of the last five ready times when that
    # is longer. 0 turns the early end off.
    startup_hang_seconds: float | None = Field(default=None, ge=0, le=600)
    tests_dir: Path | None = None
    # Your own unpacked copy of FalloutNV.exe 1.4.0.525 (the Steam exe's code is encrypted on disk): the
    # reference fnv_patch_map compares the running game's code with. Read locally, never copied.
    clean_exe: Path | None = None
    # The direct install (no MO2: the game started by Steam or nvse_loader.exe, see fnv_layout.py). It is the user's
    # real game, so the tools that write into its Data folder, plugins.txt or ini, or load or overwrite its saves,
    # refuse unless this is true. It has no effect on the MO2 dev profile.
    allow_direct_install_changes: bool = False
    # Where the direct install keeps Fallout.ini and the saves (default: Documents\My Games\FalloutNV) and its
    # plugins.txt (default: %LOCALAPPDATA%\FalloutNV). Only needed when those folders are somewhere else.
    game_user_dir: Path | None = None
    game_appdata_dir: Path | None = None


class Project(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: int = Field(default=1, ge=1, le=1)
    project_id: str = Field(pattern=r'^[a-z0-9][a-z0-9_-]{0,63}$')
    game_root: Path
    # The plugin built from a JSON spec (fnv_spec): the .esp file name and the spec (relative to the project file).
    plugin: str | None = None
    state_dir: Path
    spec: Path | None = None
    # fnv_* tools (engine map, crash doctor); optional.
    fnv: FnvSettings | None = None
    # Toolsets this server registers (fnv_mcp/toolsets.py): game, naming, core (opt-in: the everyday tools only).
    # Unset: game and naming.
    toolsets: list[Literal['game', 'naming', 'core']] | None = Field(default=None, max_length=3)

    def active_toolsets(self):
        from .toolsets import DEFAULT_TOOLSETS
        return set(self.toolsets if self.toolsets is not None else DEFAULT_TOOLSETS)

    @field_validator('plugin')
    @classmethod
    def plugin_name(cls, value):
        if value is None:
            return value
        if not value.lower().endswith('.esp') or any(c in value for c in '/\\:') or '..' in value:
            raise ValueError('plugin must be a single .esp filename')
        return value

    # The folder of the project file (set by load; None for a Project built in code). safe_paths reads it.
    _project_dir: Path | None = PrivateAttr(default=None)

    @property
    def project_dir(self):
        return self._project_dir

    @classmethod
    def load(cls, path):
        path = Path(path).resolve(strict=True)
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        # Keys of the removed GECK editor and legacy tools (2026-11-18): an older project file keeps loading.
        for key in RETIRED_KEYS:
            data.pop(key, None)
        if isinstance(data.get('toolsets'), list):
            data['toolsets'] = [t for t in data['toolsets'] if t not in ('editor', 'legacy')]
        for key in ('game_root', 'state_dir'):
            if data.get(key) is None:
                continue
            value = Path(data[key]).expanduser()
            data[key] = (value if value.is_absolute() else path.parent / value).resolve()
        if data.get('spec'):
            value = Path(data['spec']).expanduser()
            data['spec'] = str((value if value.is_absolute() else path.parent / value).resolve())
        if isinstance(data.get('fnv'), dict):
            fnv = dict(data['fnv'])

            def resolve(value):
                value = Path(value).expanduser()
                return str((value if value.is_absolute() else path.parent / value).resolve())
            for key in ('engine_map', 'decomp_root', 'symdb', 'research_dir', 'mo2_root', 'zig', 'tests_dir', 'clean_exe',
                        'game_user_dir', 'game_appdata_dir'):
                if fnv.get(key):
                    fnv[key] = resolve(fnv[key])
            fnv['crash_dirs'] = [resolve(v) for v in fnv.get('crash_dirs', [])]
            data['fnv'] = fnv
        project = cls.model_validate(data)
        project._project_dir = path.parent
        return project


def contained(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('path escapes configured root')
    return path
