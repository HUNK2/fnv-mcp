# How the live tools work

The `fnv_*` live tools work on the **running game**: they read it, change it, play it and test it. This page says
how they reach the game, what they may do, and how changes are kept and undone. Each tool, with its actions,
parameters and an example call, is in the generated [tool reference](TOOLS.md); where to start is in
[docs/README.md](README.md). The offline engine tools (decompiled functions, classes, crash logs) are described in
[FNV-ENGINE-TOOLS.md](FNV-ENGINE-TOOLS.md), and setting up a machine in [FNV-MCP-SETUP.md](FNV-MCP-SETUP.md).

What the live tools cover:

- **Game control and tests**: start Fallout: New Vegas through a portable MO2 dev profile, or attach to the game
  started by Steam; load a save or a cell with no clicks; quit and restart; install plugins; read logs; run
  repeatable YAML scenario tests ([SCENARIOS.md](SCENARIOS.md)).
- **Looking at the game**: references, actors' AI, quests, actor values, active effects, extra data, inventories,
  script variables, the crosshair, menus, pictures of the game, and memory by `Class::field` path.
- **Changing the game**: console lines with their output, script snippets, memory writes, engine function calls,
  form edits, game settings, actor actions, placed objects, player input. Every change is journaled and most can
  be undone.
- **Finding problems**: a crash doctor, a hang doctor, script profiling and tracing, engine warnings, a debugger
  for engine code and for scripts, and a map of the engine code other plugins patched.

They need the **fnvmcp engine companion**, an xNVSE plugin that `fnv_companion_install` builds and installs.

## How the tools reach the game

```
fnv_* tool (Python) ──named pipe \\.\pipe\fnvmcp-engine──▶ fnvmcp_engine.dll (in the game)
                                                            pipe thread: queues one request
                                                            xNVSE MainGameLoop: serves it on the main thread
```

The DLL (`native/engine/fnvmcp_engine.c`) hard-codes no engine addresses. It offers primitives and the
bridge decides what to do with them:

| Op | What it does |
| --- | --- |
| `rd` `rs` `rp` `ls` | Read memory, a C string, a pointer chain, or a linked list (`{data, next}` nodes) |
| `wr` | Write memory; returns the old bytes for the undo journal |
| `call` | Call a function with explicit ecx/edx and stack words; returns eax, edx and st0 |
| `con` | Run a console line through xNVSE's console interface and return what it printed |
| `eval` / `run` | Evaluate an xNVSE expression or a script snippet; typed result (number, form, string, array) |
| `sched` | Run a console line later on the main thread (used to release held keys, and to quit) |
| `every` | Run a compiled script snippet every N ms for a while (closes arrival message boxes, keeps tests unpaused) |
| `events` | xNVSE messages seen: loads, saves, new game, exit to menu, script errors with text |
| `hook` `unhook` `hookctl` `hookfilter` `hooks` | Watch hooks on engine functions (below) |
| `compile` | Compile script text with xNVSE's CompileScript into a new Script object (not run, not cached) |
| `linger` | Read or set the same-frame wait and the frame budget in microseconds (below); `0 0` turns it off |

All ops of one request run in the same frame, in order. The companion serves one request per frame, with one
exception that makes read tools fast. A tool that reads the game in 200 small requests would wait 200 frames
(3.3 s at 60 fps). So the bridge marks the requests of a read-only tool call whose ops only read (`rd` `rs` `rp`
`ls` `info`) or evaluate or call for a value (`eval` `call`): their id starts with `r`. After a marked request the
main thread waits up to 2 ms for the next request and serves it in the same frame, for at most 8 ms per frame.
A request that is not marked (a write, a console line, a hook, the frame gate, any request of an act tool) is the
last one of its frame, so a frame still passes after every request that may change the game. `info` reports the
setting as `linger`; a setting made with the `linger` op ends when the bridge disconnects.
`FNV_MCP_SAME_FRAME=0` makes the bridge mark nothing: every request then waits for its own frame.

Reads and writes go through
`ReadProcessMemory`/`WriteProcessMemory` on the game's own process, so a bad address fails cleanly. A write into
plain data (every page resident, writable and not executable, told by `QueryWorkingSetEx`) goes straight to
`NtWriteVirtualMemory`: `VirtualQuery`, which the other way calls three times, also measures the page's whole
region, and the game's heap is one region of 80 MB and more (about 0.2 ms a call there). Code and every other
page go the old way, with the page protection opened and put back.
Engine calls are not guarded: a wrong call can crash the game, and the crash doctor explains it.

The companion refuses to load except on FalloutNV 1.4.0.525 with xNVSE 6.x. Only the current Windows user
(and SYSTEM) can open its pipe, and remote clients are rejected.

Console output capture uses the game's own mechanisms (decomp references in `fnv_mcp/fnv_facts.py`):
the companion routes console output to `%LOCALAPPDATA%\fnvmcp\console.log` with the vanilla `scof`
command, and the bridge sets the vanilla "verbose messages" byte and the console-visible byte for the one
frame a line runs, so commands print their results while the console is closed.

### Watch hooks

The bridge picks a proven decomp function, reads its first bytes and decodes whole instructions covering
five bytes (`fnv_deep.decode_prologue`: MSVC prologues, and a jump another plugin already put there, which is
relocated so both hooks run). The companion replaces them with a jump to a small stub:

```
target:      jmp entry                          (the moved bytes run from the trampoline)
entry:       push hook; jmp enter_common        pushad/pushfd, record the call, ret into the trampoline
trampoline:  moved bytes; jmp target+n
exit stub:   (optional) the caller's return address is swapped for it; records eax/edx and the time,
             then returns to the real caller kept on a per-thread shadow stack
```

A hook records into a ring (4096 calls: registers, up to 8 arguments, a 96-byte copy of what an argument
points to, the frame, the result and the time) and/or a per-key table (calls, total and worst time), which
the bridge reads with `rd`. A filter records only calls where a register or argument has a given value.
A watch hook without an action only observes: every call goes on unchanged. The stub code never touches the x87
stack (a float result in st0 survives) and keeps the thread's LastError. One exception: if a hook's return finds its
shadow stack inconsistent (a frame it pushed is gone), there is no safe address to return to, and the companion ends
the game on purpose (exit code `0xF4C0DE01`) instead of jumping somewhere wrong. Unhooking restores the original bytes if the jump is
still ours; stub memory stays allocated so calls already inside finish normally.

The companion also installs a vectored exception handler that only observes: at a hardware fault it writes
`%LOCALAPPDATA%\fnvmcp\crash-context-<pid>-<n>.json` (the hooked calls active on the faulting thread with
their `this`/argument form ids, the op being served, the console line being run, recent events). A fault at an
address that faulted before was survived (the game recovered), so it gets no new file. The first 7 fault
addresses get numbered files; each later new one overwrites `crash-context-<pid>-latest.json`, so a real crash
late in a session always leaves its context. It writes nothing once xNVSE has announced the game is exiting.
What the companion cannot say over its pipe (the pipe could not be created) goes to
`%LOCALAPPDATA%\fnvmcp\companion.log`.

### Mass probes and the action atlas

Watch hooks are rich but installed one at a time; to answer "which of the 43,000 unnamed functions runs when
the player does X", the companion installs thousands of lightweight call counters at once. The planner
(`fnv_probe.py`, offline, capstone against the clean exe) sorts each target into an **entry** probe (a
5-byte jump over whole prologue instructions, when the stolen bytes fit the function, have ≤3 relocs and no
branch in the body lands inside them) or, when the function is too small or branches into its own first five
bytes, **site** probes: the rel32 of every direct call or jump to it and the vtable slots that hold it. A
stub increments a counter and, one call in 2ⁿ, records a sample (this/edx/caller/stack/vtables; the exit
reads eax and detects an st0 float with `fxam`/`fnstsw`). Arming suspends the other threads (Toolhelp
snapshot) and refuses while any thread's EIP is inside the bytes being moved. Counters are read in bulk with
`ReadProcessMemory`, no pipe traffic per function.

The atlas (`fnv_atlas.py`) turns counts into attribution by dose and response: it reads the counters, runs a
scripted action a set number of times (0, 1, 2, 4), reads again, and least-squares fits each function's
delta against the dose. A clean slope through the origin is `per_unit` (n calls per action); a constant rise
only when the action happens at all is `with_action`; noise is `background`. Functions whose counts track a
`console_noop` dose are marked `driver` (console machinery, not the action). Runs, segments, counts and fits
are kept in `state/atlas.sqlite`, and an existing Frida `fnvsym-trace/1` capture can be imported as a run.

## What the tools may do: control levels

Every tool and action has a kind. A **read** changes nothing. A **control** action changes files or starts and stops
the game. An **act** action changes the running game or leaves something installed in it (a hook, a probe, a held
frame gate). The setting `fnv.engine_control` in the project file decides which may run:

- **`"read"`** (the default): looking at the game, starting and stopping the dev game (`fnv_game quit` only ends a
  game the bridge started), installing the engine companion, and writing into the bridge state folder. An act
  action refuses and its error names the setting.
- **`"full"`**: also changing the running game and running code in it. **This is code execution as your Windows
  user**: `fnv_native` compiles C and loads it into the game process. Use it with a game and a profile you test
  with.

Two tables in `fnv_mcp/fnv_live_tools.py` decide, so a tool cannot pass a weaker kind by mistake.
`MUTATING_ACTIONS` lists every action that changes the running game, including the hooks that tracing, profiling
and logging tools install (`fnv_trace`, `fnv_script_profile`, `fnv_script_trace`, `fnv_damage_log`,
`fnv_profile action=frame`, `fnv_crash capture_on`) and the scenario runners (`fnv_test`, `fnv_session start` and
`end`, `fnv_crawl`, `fnv_bisect`, `fnv_replay`). Their read actions (list, read, status, report) still work under
`"read"`. `FULL_ONLY_ACTIONS` lists the control actions that put agent-chosen code or files into the game or its mod
profile (`fnv_install`, `fnv_plugin_build build` and `install`, `fnv_profile_set` without `dry_run`); they need
`"full"` too. Every other control tool has a line in `READ_LEVEL_CONTROL` saying why it may run under `"read"`.

`fnv_engine_get` stays a read: it runs only an engine getter that a check of the game's machine code proves writes
nothing but its own stack, always as a guarded call; anything else answers `MAY_WRITE` (use `fnv_engine_call` under
`"full"`). It needs `fnv.clean_exe`, your own unpacked FalloutNV.exe 1.4.0.525 (read locally, never copied), and
capstone (`pip install "capstone==5.0.7"`, the `naming` extra).

## The journal and undo

Act tools record every change in `state/fnv/journal.jsonl`, with the old value, and back up the dev profile's saves
before the first change of a game session (`state/fnv/save-backups`, the newest 10 kept). `fnv_journal` lists the
changes and whether each can be undone; `fnv_undo` reverses the last ones, one by journal id, or all of them
(`all=true`). Console lines, script snippets and engine calls cannot be undone automatically: the journal says which
entries can. Files put into the mod profile (`fnv_install`) are not part of the journal.

## The answer of every tool

Every answer is an envelope: `ok`, `outcome`, `data` (the answer itself), `error`. For a call that worked,
`outcome` is `acknowledged` when a control or act tool made its change (the game or the companion confirmed it),
and `unchanged` for a read or when the change was already in place. A failure has `ok: false`, an `error.code` and
an `error.recovery` sentence that says what to do next. Action-style tools follow the conventions in
[FNV-TOOL-CONVENTIONS.md](FNV-TOOL-CONVENTIONS.md): the answer first (`verdict`, `reason`), lists paged the same
way, one way to name a reference.

## The MO2 dev profile and the direct install

With a portable MO2 instance and a dev profile of its own (the usual setup), the tools start the game through MO2
and change only that profile. Without MO2 (the direct install: the game started by Steam's Play button or
`nvse_loader.exe`), `fnv_companion_install target=game` puts the companion into `Data\NVSE\Plugins` and the tools
attach to the game however it was started; `fnv_status` names the install in `install.kind` (`mo2` or `direct`).
Changes to the direct install's files (plugins into `Data`, `plugins.txt`, `Fallout.ini`, xNVSE's config, saves)
refuse with `DIRECT_INSTALL_PROTECTED` unless `fnv.allow_direct_install_changes` is true.
[FNV-MCP-SETUP.md](FNV-MCP-SETUP.md#without-mod-organizer-2-the-direct-install) has the details.

## Launch notes

- MO2 is started with ShellExecute. A detached `CreateProcess` gave the game no window focus and its
  renderer failed to start ("Failed to initialize renderer").
- The Python MCP client puts its server in a kill-on-close job object without breakaway; inside it MO2's
  hooked start of `nvse_loader.exe` fails with `ERROR_ACCESS_DENIED`. The bridge detects such a job and then
  opens a launch shortcut through Explorer instead. Claude Code's launcher (libuv) allows breakaway.
- From the shell tools of a Claude desktop session, MO2's start of `nvse_loader.exe` failed with
  `ERROR_ACCESS_DENIED` too, although the enclosing jobs allowed breakaway (a test child started with
  `CREATE_BREAKAWAY_FROM_JOB` came up fine); cause not found. Starting the MCP client through WMI
  (`Win32_Process.Create`) launched the game normally.
- MCP clients may pass the server a short allow-list of environment variables; MO2's `basic_games` plugin
  then fails on a missing `ProgramW6432`. The bridge adds the user's missing standard variables
  (`CreateEnvironmentBlock`) before launching, never overriding any.
- A leftover `plugin_loadcheck.tmp` (from an MO2 that was killed) makes MO2 stop at a "Plugin error"
  dialog; when MO2 is not running the bridge moves it into `state/fnv/mo2-backups`.
- An MO2 of the dev instance left running without the game (after a crash) takes the next launch request,
  and a failed launch leaves it behind a modal "Cannot launch program" dialog that nobody clicks. Before a
  launch the bridge closes such an MO2 (WM_CLOSE, then terminate); while waiting for the game it watches for
  MO2's blocking dialogs and fails at once with `MO2_LAUNCH_FAILED`, closing the dialog.

## Credits

- [xNVSE](https://github.com/xNVSE/NVSE): the plugin interface, console/script/array interfaces, script
  commands used by the reads, the extra data type list, and the `ActorHitData` field offsets, body part
  names and hit flags `fnv_damage_log` decodes (`GameProcess.h`, `GameForms.h`). xNVSE has no licence file;
  nothing of its code is copied, only interface declarations (`native/runtime/nvse_abi.h`, upstream
  geck-mcp-nv), offsets and names.
- [JIP LN NVSE](https://github.com/jazzisparis/JIP-LN-NVSE): `CloseActiveMenu`, `MoveToCell`, and the layouts of
  `MagicTarget`, `ActiveEffect` and `EffectItem` and the control codes used by `fnv_effects` and
  `fnv_input` (offsets only, checked against the decompilation's class sizes); the `TES` singleton address
  and its grid and interior fields used by `fnv_script_instances`.
- xNVSE's menu ids, `Tile` / `Tile::Value` layouts and the data handler address (`g_dataHandler`), used by
  `fnv_menu` and `fnv_script_instances` (offsets and names only).
- The Fallout: New Vegas decompilation and fnvsym's proof table: addresses, layouts and proven names.
