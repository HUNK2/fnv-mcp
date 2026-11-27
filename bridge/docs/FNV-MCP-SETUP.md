# Setting up fnv-mcp

This is the one setup guide. It takes a Windows machine from nothing to `fnv_status` answering from the running
game. Run every command from the `bridge` folder of your clone (`cd bridge` first), in PowerShell.

Tool references: [FNV-ENGINE-TOOLS.md](FNV-ENGINE-TOOLS.md) (offline engine knowledge, crash doctor) and
[TOOLS.md](TOOLS.md) (every tool, generated from the code), and how the live tools reach the game in
[FNV-LIVE-TOOLS.md](FNV-LIVE-TOOLS.md). A worked example of a mod built and
tested end to end: [examples/hello-quest](../examples/hello-quest/README.md).

## What you need (the downloads)

You download everything below yourself; fnv-mcp downloads nothing and ships no game files, no decompilation and
nothing generated from them. "Tested" is the version the tools are tested with. The links were checked on
2026-11-27.

| What | Tested | Where it comes from | Needed? | Which tools need it |
| --- | --- | --- | --- | --- |
| Windows 10 or 11 (64-bit) | Windows 11 | | required | all (Windows only) |
| Fallout: New Vegas, **Steam edition**, FalloutNV.exe 1.4.0.525 | 1.4.0.525 | Steam (app 22380) | required | everything that reads or runs the game. GOG copies are untested. Epic, Bethesda.net and Game Pass copies do not run xNVSE (the FNV 4GB Patcher's page says so), so the live tools cannot work with them |
| Python 3.12 with the `py` launcher | 3.12.10 | https://www.python.org | required | the server itself |
| The Python packages, pinned in `requirements.lock.txt` (direct ones: `mcp` 2.2.0, `pydantic` 2.13.5, `anyio` 4.15.1, `pillow` 12.3.0, `numpy` 2.5.3; also `pywin32` 312) | as pinned | PyPI, through `pip install -r requirements.lock.txt` (step 1) | required | the server itself |
| `capstone` 5.0.7 (the `naming` extra) | 5.0.7 | PyPI (https://www.capstone-engine.org) | optional | `fnv_probe`, `fnv_atlas`, `fnv_engine_get` purity check, the `fnv_name_*` tools; without it they answer `CAPSTONE_MISSING` |
| An MCP client (Claude Code, Claude Desktop, Codex...) | Claude Code | its maker | required | |
| xNVSE | 6.4.9 | https://github.com/xNVSE/NVSE (its releases; the file is `nvse_6_4_9.7z`) | required | the engine companion is an xNVSE plugin: every live tool |
| JIP LN NVSE | 57.30 | Nexus Mods: https://www.nexusmods.com/newvegas/mods/58277; source https://github.com/jazzisparis/JIP-LN-NVSE | required (setup check) | live tools that call its script commands, such as `CloseActiveMenu` in test runs and menu handling |
| Yvile's Crash Logger | 5.17a | Nexus Mods: https://www.nexusmods.com/newvegas/mods/82540; source and releases https://github.com/yvileapsis/yUI-NVSE | required (setup check) | `fnv_crash` reads its logs |
| 4 GB patch (makes FalloutNV.exe large address aware) | the FNV 4GB Patcher (`FalloutNVpatch.exe`); its version on the test machine was not recorded | Nexus Mods: https://www.nexusmods.com/newvegas/mods/62552 | recommended | none directly; a modded game runs out of memory without it |
| Mod Organizer 2, portable | 2.5.2 (`Mod.Organizer-2.5.2.7z`) | https://github.com/ModOrganizer2/modorganizer (its releases) | optional (without it the tools use the game as Steam installed it, see "Without Mod Organizer 2") | the MO2 dev profile: `fnv_game launch`, `fnv_install`, `fnv_profile_set`, `fnv_bisect` on a profile of its own |
| 7-Zip | 24.09 | https://www.7-zip.org | needed for step 3 only | `tools\fnv_setup_dev_profile.py` unpacks the archives with it (found on PATH or from its install; `--7zip` otherwise) |
| Zig | 0.14.1 (`zig-x86_64-windows-0.14.1.zip`) | https://ziglang.org/download/0.14.1/zig-x86_64-windows-0.14.1.zip (the CI downloads this file and checks its SHA-256 `554f5378228923ffd558eac35e21af020c73789d87afeabf4bfd16f2e6feed2c`) | required for the live tools | builds the engine companion (`fnv_companion_install`), `fnv_native`, `fnv_plugin_build`; without it they answer `ZIG_MISSING` |
| A checkout of the fnv_decomp decompilation | the current `main` | https://github.com/momusdigital/fnv_decomp | required for engine knowledge | the engine map (`fnv_func`, `fnv_class`, `fnv_symbolize`, `fnv_global_find`, `fnv_setting_info`, `fnv_command_info`, `fnv_script_explain`, `fnv_probe`, the debugger's names); `fnv_code` and `fnv_func` source need the checkout itself |
| Your own unpacked FalloutNV.exe (see below) | from 1.4.0.525 | you make it from your own copy | optional | `fnv_patch_map`, `fnv_code`, `fnv_probe`, the symbol database and planning hooks while the game is closed; without it they answer `CLEAN_EXE_MISSING` or ask for the running game |
| The compiler kit fnv_decomp builds with: Visual C++ 2008 SP1 (15.00.30729.01), some units 2008 RTM (15.00.21022.08) or 2005 SP1 | as named | Microsoft (no download link was checked for this guide); fnv_decomp's `tools/msvc.py` says how it is laid out | optional | `fnv_code build` only; set `FNV_MSVC9_ROOT` (and `FNV_MSVC9_RTM_ROOT`, `FNV_MSVC8_ROOT`) to the folder with `VC\` and `SDK\`; without it, `COMPILER_MISSING` |
| The xNVSE and JIP LN NVSE source trees | as above | https://github.com/xNVSE/NVSE; https://github.com/jazzisparis/JIP-LN-NVSE | optional | `engine_map.generate --nvse-src/--plugin-src`: script command names in the engine map |
| An fnvsym proof folder | | not public | optional | the `fnv_name_*` tools (`FNVSYM_ROOT`); without it they answer `NO_PROOF_FOLDER` |
| Git | any | https://git-scm.com | optional | to clone this repository |

`fnv_setup_check` reports the same things on your machine: the game and its version, the 4 GB patch, xNVSE, JIP LN
NVSE, the crash logger, MO2 (or the direct install), Zig 0.14.1, the engine companion, the engine map, the symbol
database, the proof folder, the compiler kit, capstone, the unpacked exe and the control level, each with its fix.

**Your own unpacked FalloutNV.exe.** The Steam FalloutNV.exe keeps its code encrypted on disk (SteamStub), so tools
that read the engine's code with the game closed need an unpacked copy. Make it from your own game: copy the
original exe (if you used the 4 GB patcher, it kept the original as `FalloutNV_backup.exe`) to a folder of your
own, run Steamless (https://github.com/atom0s/Steamless, tested with 3.1.0.5) on the copy, and set `fnv.clean_exe`
(or `FNV_CLEAN_EXE`) to the unpacked file. fnv-mcp reads it on your machine only and never copies it. Never put it in
a repository or share it: it is the game's code.

## Steps

1. **Python environment.**

   ```powershell
   cd bridge
   py -3.12 -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.lock.txt
   .venv\Scripts\python.exe -m pip install capstone==5.0.7
   ```

   The second line is the `naming` extra (`fnv_probe`, `fnv_atlas`); everything else works without it. fnv-mcp
   is not installed as a package: it runs from this folder (`run_mcp.py`, step 6).

2. **Project file.** Copy the example and edit the copy:

   ```powershell
   Copy-Item projects\example-fnv.json projects\my-fnv.json
   ```

   `game_root` is `"auto"`: the server finds the Steam game (its registry key and every Steam library). If it is
   somewhere else, or the server stops with "game_root is auto but no Fallout: New Vegas install was found", set it
   to your game folder, the one with `FalloutNV.exe`. The other values work as they are; the settings table below
   lists every key. Keep `engine_control` at `"read"` until you want the agent to change the game (see "Safety levels").

3. **MO2 dev instance.** First unpack the Mod Organizer 2 archive (for example `Mod.Organizer-2.5.2.7z`) into a
   new folder, `C:\Modding\FNV-Dev` below, and put the three downloads in one folder, `C:\Modding\downloads`
   below: the xNVSE archive (`nvse_*.7z`) and the JIP LN NVSE and Yvile's Crash Logger archives as Nexus names them
   (the script finds them by `jip ln` and `crash logger` in the name). Then `tools\fnv_setup_dev_profile.py` sets up
   the instance with a `Dev` profile. It is a dry run by default (it prints what it would do, or what is missing);
   `--apply` does it and records what it added to the game folder. It finds the game and 7-Zip itself; pass
   `--game <game folder>` or `--7zip <path to 7z.exe>` when it cannot:

   ```powershell
   .venv\Scripts\python.exe tools\fnv_setup_dev_profile.py --mo2 C:\Modding\FNV-Dev --downloads C:\Modding\downloads
   .venv\Scripts\python.exe tools\fnv_setup_dev_profile.py --mo2 C:\Modding\FNV-Dev --downloads C:\Modding\downloads --apply
   ```

   It writes `bAlwaysActive=1` (the game keeps running unfocused), skips the intro, uses a 1280x720 window,
   installs xNVSE into the game folder (it cannot go through MO2) and JIP LN NVSE and Yvile's Crash Logger as MO2
   mods. Then set `fnv.mo2_root` to `C:/Modding/FNV-Dev` and `fnv.mo2_profile` to `Dev` in `my-fnv.json`.

4. **Engine map.** Generate it from your fnv_decomp checkout into the path `fnv.engine_map` names (about 45
   seconds; it stays on your machine):

   ```powershell
   .venv\Scripts\python.exe -m engine_map.generate --decomp C:\src\fnv_decomp --out state\engine_map.json
   ```

   [FNV-ENGINE-TOOLS.md](FNV-ENGINE-TOOLS.md) has the optional inputs (`--exe` for the full call graph, the
   script-extender sources) and the symbol database.

5. **Zig.** Unpack Zig 0.14.1 and set `fnv.zig` to its `zig.exe` (or set the `FNV_MCP_ZIG` environment variable,
   or put `zig.exe` on PATH).

6. **Register the MCP server** with your client. With Claude Code (use your own absolute paths):

   ```powershell
   claude mcp add fnv -- C:\path\to\bridge\.venv\Scripts\python.exe C:\path\to\bridge\run_mcp.py --project C:\path\to\bridge\projects\my-fnv.json
   ```

   For a client with a JSON file, the entry is:

   ```json
   "fnv": {
     "command": "C:/path/to/bridge/.venv/Scripts/python.exe",
     "args": ["C:/path/to/bridge/run_mcp.py", "--project", "C:/path/to/bridge/projects/my-fnv.json"]
   }
   ```

   Restart the client. If the project file is not valid, the server stops at once with one line saying which key
   is wrong (the client shows it in its MCP log).

7. **Check the machine.** Ask the agent to call **`fnv_setup_check`**: it lists each prerequisite with what to do
   when one is missing.
8. With the game closed, call **`fnv_companion_install`**: it builds the engine companion with Zig and installs it
   as its own MO2 mod. (Without MO2: see "Without Mod Organizer 2" below.)
9. **`fnv_game launch start=coc:GSProspectorSaloonInterior`** (or `continue` for your newest dev save), then
   **`fnv_status`**: it answers from the running game. From here, `fnv_guide` has step-by-step guides.

A first prompt to give the agent: *"Call fnv_setup_check and fix what it reports with me, then launch the game
with fnv_game launch and show me fnv_status."*

Before the agent starts on a mod, give it [AGENT-GUIDE.md](AGENT-GUIDE.md): the five rules, the working loop and
what it must ask you first. Copy it into your mod project so the agent reads it in every session.

## Without Mod Organizer 2 (the direct install)

The tools also work with the game as Steam installed it: you start it with Steam's Play button (or
`nvse_loader.exe` in the game folder) and the agent attaches to it. Skip step 3 and leave `fnv.mo2_root` and
`fnv.mo2_profile` unset (`null`), then:

1. Install xNVSE into the game folder as its readme says, and JIP LN NVSE and Yvile's Crash Logger into
   `Data\NVSE\Plugins` (by hand or with your mod manager). `fnv_setup_check` looks for them there.
2. With the game closed, call **`fnv_companion_install target=game`**. It puts `fnvmcp_engine.dll` into the game
   folder's `Data\NVSE\Plugins`, keeps a receipt in `<state_dir>\engine\install-game.json` and a copy of any file it
   replaces; `action=uninstall target=game` removes it again (only the file it put there).
3. Start the game however you like. **`fnv_status`** then answers from it, and its `install` part says
   `"kind": "direct"` and which folders are in use: saves and `Fallout.ini` in `Documents\My Games\FalloutNV`,
   `plugins.txt` in `%LOCALAPPDATA%\FalloutNV`. `fnv_game launch` (or `via=direct` when the project also names an
   MO2 profile) starts it the same way.

How the tools tell the two apart: a running game with MO2's file system (`usvfs_x86.dll`) loaded is the MO2 dev
profile; one without it is the direct install, even when the project also names an MO2 profile. With no game
running, the project file decides.

**This is your real game.** The tools that would change its files refuse with `DIRECT_INSTALL_PROTECTED` unless you
set `fnv.allow_direct_install_changes` to `true`: installing plugins into `Data` (`fnv_install`, `fnv_plugin_build
install`), editing `plugins.txt` (`fnv_profile_set`, `fnv_bisect`), writing `Fallout.ini` (`fnv_window persist`) or
xNVSE's config (`fnv_warnings enable`), and loading or writing saves (`fnv_game launch` with a save start,
`fnv_test` save starts, `fnv_test_save`, `fnv_replay`). Start tests at the menu or with a `coc:` start instead. The
companion itself is the one exception: you ask for it by name with `target=game`. With the setting on, a plugin
`fnv_install` copies gets the current file time (without a mod manager the game orders plugins by file time, so it
loads last), and the replaced file and the old `plugins.txt` are kept under `<state_dir>\fnv`.

The live tools need nothing else: reading, looking, playing, debugging and `engine_control = "full"` changes of the
running game work the same on both. `fnv_game quit` still closes only a game the bridge started unless
`engine_control` is `"full"`.

## Safety levels

`fnv.engine_control` has two levels.

- **`"read"`** (the default): look at the game, start and stop the dev game (only the game `fnv_game launch` started),
  and install the engine companion.
- **`"full"`**: change the game and run code in it: memory writes, engine calls, console changes, hot-swap, hooks,
  and anything that puts agent-chosen code or files into the game or its mod profile (`fnv_plugin_build` build
  and install, `fnv_install`, `fnv_profile_set` without `dry_run`). **`"full"` means code execution as your
  Windows user**: `fnv_native` compiles C and loads it into the game process. Changes to the running game are
  journaled and undoable; files put into the mod profile are not.

The tools act on the MO2 profile named in `fnv.mo2_profile`, so point it at a profile of its own, not the one you
play with. On the direct install (no MO2) they act on your real game, and changes to its files need a second switch,
`fnv.allow_direct_install_changes` (see "Without Mod Organizer 2").

## What each tool family needs

| Tool family | Needs |
| --- | --- |
| `fnv_guide`, `fnv_setup_check`, `fnv_crash latest`, `fnv_crash` (without engine names), `fnv_save_inspect`, `fnv_save_diff` | nothing beyond the project file |
| Engine knowledge: `fnv_func`, `fnv_class`, `fnv_symbolize`, `fnv_global_find`, `fnv_setting_info`, `fnv_func action=strings`, `fnv_command_info`, `fnv_script_explain` | the engine map |
| `fnv_symbols`, source lines and named locals in other tools | the symbol database (optional) |
| `fnv_name_*` (the naming tools, an optional extra) | the fnvsym proof folder (`FNVSYM_ROOT`) |
| Live tools: `fnv_status`, `fnv_player`, `fnv_ref`, `fnv_photo`, tests, profiling, debugging... | a running game with the engine companion |
| Act tools and actions (they change the game or the mod profile) | a running game and `fnv.engine_control = "full"` |
| `fnv_patch_map`; hook plans while the game is closed | your unpacked FalloutNV.exe (`fnv.clean_exe`) |
| `fnv_code` (recompile a decomp function) | the fnv_decomp checkout (`fnv.decomp_root`), the compiler kit (`FNV_MSVC9_ROOT`...) and the running game |
| `fnv_probe`, `fnv_atlas`, the `fnv_name_*` tools | `capstone` (the `naming` extra) |
| `fnv_spec` (plugins built from a JSON spec) | the `plugin` and `spec` settings and the master index ([bridge/README.md](../README.md)); without them it answers `NO_SPEC` |

## Settings

Paths in the project file may be relative to the project file. `fnv_*` keys go in the `fnv` block.

| Key or variable | Default | What it is |
| --- | --- | --- |
| `project_id` | (required) | Lower-case name of the project; used in state file names |
| `game_root` | (required) | The game folder (with `FalloutNV.exe` and `Data`), or `"auto"`: found from the game's registry key and Steam's libraries |
| `state_dir` | (required) | Where the tools keep their state, logs, builds and journals |
| `toolsets` | both | Which tool families the server registers: `game` (fnv_*, fnv_spec) and `naming` (fnv_name_*, fnv_probe, fnv_atlas), and the opt-in `core` (29 everyday tools: building and testing a plugin, plus the engine loop fnv_native, fnv_func, fnv_input, fnv_mem_read, fnv_screenshot, fnv_wait_for). For example `["game"]` for a game session. `run_mcp.py --toolsets game,naming` overrides it; `fnv_guide` is always on and its find names hidden tools. Reconnect the server after a change |
| `plugin` | none | The `.esp` `fnv_spec` builds |
| `spec` | none | The JSON spec `fnv_spec` builds the plugin from |
| `fnv.engine_control` | `read` | `read` or `full` (see "Safety levels") |
| `fnv.startup_hang_seconds` | 45 | A starting game that answers but draws no frames this long is ended and started once more (0: never; a machine whose starts are slower gets three times its usual start time) |
| `fnv.call_log_arguments` | `names` | What the call log (`<state_dir>/fnv/calls.jsonl`, read by `fnv_journal action=calls`) keeps of each call's arguments: `names`, `values` (also the values, each cut to 200 characters, your profile folder written as `%USERPROFILE%`) or `off`. The log stays on this machine |
| `fnv.engine_map` | `<state_dir>/engine_map.json` | The engine map from `engine_map.generate` |
| `fnv.decomp_root` | the path in the map | Your fnv_decomp checkout |
| `fnv.symdb` | `<state_dir>/symdb.sqlite` | The symbol database (optional) |
| `fnv.research_dir` | none | A folder of your own Markdown notes that `fnv_guide engine` searches; without it, it searches the shipped guides |
| `fnv.crash_dirs` | `[]` | More folders for crash logs |
| `fnv.mo2_root` | none | The portable MO2 instance; without it (and `mo2_profile`) the tools use the direct install |
| `fnv.mo2_profile` | none | The MO2 profile the tools act on (one of its own) |
| `fnv.allow_direct_install_changes` | `false` | Lets the tools change the direct install's files: plugins into `Data`, `plugins.txt`, `Fallout.ini`, xNVSE's config, saves (see "Without Mod Organizer 2"). No effect on the MO2 dev profile |
| `fnv.game_user_dir` | `Documents\My Games\FalloutNV` | The direct install's folder with `Fallout.ini` and the saves, when it is somewhere else |
| `fnv.game_appdata_dir` | `%LOCALAPPDATA%\FalloutNV` | The direct install's folder with `plugins.txt`, when it is somewhere else |
| `fnv.mo2_executable` | `NVSE` | The MO2 executable entry that starts the game |
| `fnv.zig` | none | Zig 0.14.1 `zig.exe` |
| `fnv.engine_pipe` | `fnvmcp-engine` | Name of the engine companion's pipe. The tools talk only to a pipe the running FalloutNV.exe serves (`PIPE_NOT_GAME` otherwise) |
| `fnv.tests_dir` | none | Folder of scenario tests for `fnv_test run_all` |
| `fnv.clean_exe` | none | Your unpacked FalloutNV.exe 1.4.0.525 (read locally, never copied) |
| `FNV_ENGINE_MAP`, `FNV_DECOMP`, `FNV_SYMDB`, `FNV_CRASH_DIRS`, `FNV_RESEARCH_DIR` | none | The same as the `fnv` keys above; the project file wins |
| `FNV_CLEAN_EXE` | none | The same as `fnv.clean_exe`; without either, the tools look for `%USERPROFILE%\.fnvdecomp\FalloutNV.exe` (where the fnvdecomp kit keeps one) |
| `FNV_MSVC9_ROOT`, `FNV_MSVC9_RTM_ROOT`, `FNV_MSVC8_ROOT` | none | The compiler kits `fnv_code build` runs through fnv_decomp's `tools/msvc.py`: the folder with `VC\` and `SDK\` |
| `FNV_DATA` | the found game's `Data` | The game's `Data` folder for `esm_index.py` and the tests |
| `FNV_LIVE_PROJECT` | `projects/my-fnv.json`, or the only project file | The project file `tools/fnv_live_regress.py` and the live tests use (`--project` wins) |
| `FNV_MCP_PIPE` | none | The same as `fnv.engine_pipe` |
| `FNV_MCP_SAME_FRAME` | on | `0` makes every request to the game wait for its own frame again (read tools get slower); see "How the tools reach the game" in `FNV-LIVE-TOOLS.md` |
| `FNVSYM_ROOT` | none | The fnvsym checkout the `fnv_name_*` tools need; without it they answer `NO_PROOF_FOLDER` |
| `FNV_MCP_ZIG` | none | Zig, when `fnv.zig` is not set |
| `FNV_MCP_RELOAD` | off | `1` starts the reloading supervisor, for development (same as `run_mcp.py --reload`) |
| `FNV_MCP_SUPERVISOR_LOG` | `<state_dir>/supervisor.log` | The supervisor's log file; empty for none |

The `FNV_MCP_*` variables had other names before 0.5 (`GECK_MCP_ZIG`, `FNVMCP_PIPE`, `GECK_MCP_RELOAD`,
`GECK_SUPERVISOR_LOG`); the old names still work, and the new name wins when both are set. The keys of the removed
GECK editor and legacy tools (`launcher`, `helper`, `records`, `source_roots`, `helper_timeout_seconds`, `transport`,
`legacy_tools`, and `editor` or `legacy` in `toolsets`) are ignored, so an older project file still loads.

## Uninstall

Nothing here runs by itself; there is no uninstall tool. Close the game and the MCP client first.

1. **The MCP registration.** With Claude Code: `claude mcp remove fnv`. For a client with a JSON file, delete
   the `"fnv"` entry.
2. **xNVSE in the game folder.** xNVSE cannot go through MO2, so `tools\fnv_setup_dev_profile.py --apply`
   copied its loader and DLLs into the game folder (`nvse_loader.exe`, `nvse_*.dll`; its `Data` part is the MO2
   mod `xNVSE Data`). Every file it added is listed under `gameFilesAdded` in
   `<mo2 root>\fnv-mcp-install.json`; delete those files. Files that were there before the setup are not listed,
   so an xNVSE you installed yourself stays. Do this before step 3, which deletes the list.
3. **The MO2 dev instance** (`fnv.mo2_root`, `C:\Modding\FNV-Dev` in the steps). The setup made it only for
   fnv-mcp, so deleting the whole folder removes the `Dev` profile, its saves and INIs, and every mod the tools
   added there. To keep the instance and remove only the tools' parts instead:
   - the companion: the mod folder `mods\fnvmcp engine companion`, and its line `+fnvmcp engine companion` in
     `profiles\Dev\modlist.txt`;
   - plugins shipped with `fnv_plugin_build`: the `mods\fnvmcp plugin <name>` folders and their lines in
     `modlist.txt`;
   - plugins installed with `fnv_install`: their mod folders, their `modlist.txt` lines, and their names in
     `profiles\Dev\plugins.txt` and `loadorder.txt`.
4. **The direct install** (only if you used it): `fnv_companion_install action=uninstall target=game` removes the
   companion from `Data\NVSE\Plugins`. With `fnv.allow_direct_install_changes` on, plugins the tools copied into
   `Data` stay there until you delete them and take them out of `%LOCALAPPDATA%\FalloutNV\plugins.txt`; the files
   they replaced and the old `plugins.txt` are under `bridge\state\fnv` (`install-backups`,
   `plugin-lists-backups`).
5. **Files the companion and the server write outside the clone:**
   - `%LOCALAPPDATA%\fnvmcp`: the companion's exit notes, crash-context files and `companion.log`;
   - `%TEMP%\geck-mcp`: the busy files the server writes while a call runs.
6. **The clone.** Everything else stays inside it: `bridge\state` (journals, the engine map, builds, test
   reports, screenshots, backups), `bridge\.venv` and your `bridge\projects\*.json` files. Delete the clone.

With the MO2 dev profile, the Steam game itself, its saves in `Documents\My Games\FalloutNV` and its INIs there
are not changed by the setup: the dev profile keeps its own copies.

## Known issues

The open problems are listed in [KNOWN-ISSUES.md](KNOWN-ISSUES.md). The ones met during setup:

- Turn Discord's in-game overlay off globally (Discord > User Settings > Game Overlay): its hook crashed the
  game at startup with JIP LN loaded on the test machine, even with the per-game switch off.
- Every quit used to leave a Crash Logger log: xNVSE 6.4.9 faults in its exit cleanup (a static destructor reads
  objects in the game's heap after the game has freed it), with or without the companion. The companion (from
  2026-11-22) ends the process after the game's exit message, before that cleanup, so a quit leaves no log;
  without the companion such logs remain, and the crash doctor marks them `duringShutdown`.
- The game opens its pause menu when its window loses focus. Test runs keep the world running (the companion
  closes the pause menu and message boxes during a run; `allow_menus: true` opts out).
- MCP clients that put their server in a job object without breakaway stop MO2 from starting the game; the
  bridge then opens a launch shortcut through Explorer. Claude Code's launcher allows breakaway.
- The running game keeps no quest EditorIDs, so scripts with `Quest.variable` references cannot be compiled
  (`fnv_compile_ingame`, `fnv_scripts_compile`); `fnv_spec` leaves them uncompiled.
