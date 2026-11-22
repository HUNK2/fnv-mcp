# Changelog

## fnv-mcp 0.5.0b1 (0.5.0 beta 1) - unreleased

The first version of the fork. The entries below this one are geck-mcp-nv's own history, by 2Shay.

- **No crash log on every quit under MO2 (2026-11-22):** each quit of the game under MO2 faulted after the game's
  own exit, in xNVSE 6.4.9's static destructor for per-cell objects it keeps in the game's heap
  (`nvse_1_4+0x74CF3`, `LdrShutdownProcess`), and Crash Logger wrote a log for it: 207 of 251 logs. After the
  exit message the engine companion now ends the process when Windows detaches it, before xNVSE's destructors
  run (NVTF's fast exit does the same). Its exit note says `fastExit`, and `fnv_status` `lastExit.fastExit`.
  The companion must be built and installed again (`fnv_companion_install`).

- **Fixes from real use (2026-11-21, 4,228 calls):** the Python package is `fnv_mcp` (was `geck_mcp`) and the
  server is registered as `fnv`. `fnv_native` follows the game's own module list (a game restart no longer leaves
  `ALREADY_LOADED` or dead addresses), returns only the log lines each action wrote (a `call` answer: median 4,966
  characters before, 514 after), pages its log with `since`, and rebuilds when an included header changes.
  `fnv_game launch` ends a start that hangs (frame 0, a hung window, or a game never resumed) after
  `fnv.startup_hang_seconds` (default: 45 s or three times the usual start) and starts once more, and keeps the
  display on while the game runs. `fnv_input` holds and taps go through the frame injector while the game window
  is not focused (key commands were ignored then). `fnv_raycast ground_at` casts a ray straight down, per point,
  passing the player's own capsule. `fnv_logs` reads a locked log, `fnv_photo` label rects are in image pixels,
  `fnv_plugin_build` plans hooks from the unpacked image. The `core` toolset holds the five most used tools (29
  tools). `fnv_status` names the checkout and commit that answered. `tools/fnv_live_regress.py --suite loop`
  checks this loop in the running game before a push.

- **The fnv_* tools** (138, with `fnv_spec`): engine knowledge from the fnv_decomp decompilation (functions, classes, settings,
  commands, a symbol database with source lines and named locals), a crash doctor, game control and scenario
  tests, live inspection, full control of the running game through the engine companion (time control, a
  debugger, hooks, C modules, decomp functions recompiled and swapped in, xNVSE plugin export), profiling,
  coverage and whole-plugin checks. `fnv_guide` has step-by-step guides.
- **Safety:** two levels, `fnv.engine_control` `read` (the default) and `full`; anything that changes the game or
  puts code or files into it or its mod profile needs `full`. Paths from tool arguments stay inside the project's
  folders; package names are plain names; `fnv_game quit` closes only the game `fnv_game launch` started; tools never take a
  compiler path.
- **Without Mod Organizer 2:** the tools attach to a game started by Steam's Play button or `nvse_loader.exe`
  (`fnv_companion_install target=game` puts the companion into `Data\NVSE\Plugins`; `fnv_game launch via=direct`
  starts it from the game folder). `fnv.mo2_root` and `fnv.mo2_profile` are optional. A running game is the MO2
  dev profile when MO2's file system is loaded in it, else the direct install; `fnv_status` says which and the
  folders in use, and saves, `Fallout.ini`, `plugins.txt`, the load order, setup check and plugin installs follow
  it. On the direct install, the tools that change its files or load its saves refuse unless
  `fnv.allow_direct_install_changes` is true.
- **`fnv_place` snaps by the object's bounds:** `snap`, and `at`/`array` with `snap=true`, rest the bottom of
  the object's bounds on the surface instead of its origin, and `check` reports floating and sinking by that
  bottom. A shack whose origin is at its centre sank half into the ground before.
- **`fnv_place` snaps by the whole footprint:** rays are cast over the object's bounds, not one under its origin,
  and `rest=low` (default) puts it on the lowest ground under it, so no corner floats on a slope and a boulder
  under its middle is no longer its seat (`rest=centre` keeps the old seat, `high` sinks nothing). The result
  reports how much sank or floats and any overhang, warns on steep ground with a suggested heading, and refuses
  ground rising more than the object's height (`TOO_STEEP`). `check` judges the footprint; `snap align=true` fits
  the slope over it and no longer sinks aligned objects a few units.
- **Finding the right tool:** `fnv_guide find` matches whole words and word stems, finds a single action of a
  tool and gives an example call for each match; every tool has task phrases, and a table of plain task phrases
  is tested ("put a building on a slope" finds `fnv_place`).
- **Errors say what to do:** a bug in a tool comes back as `INTERNAL_ERROR`, not as Python text; a misspelled
  field, EditorID, actor value, setting, global or function name gets "did you mean"; "the game is not running"
  is a plain sentence; `COMPANION_NOT_READY` names its likely cause (crashed, companion missing, or the game not
  drawing frames). `fnv_mem_read` and `fnv_mem_write` accept `address` for `target`; predicate parameters show
  valid examples.
- **Every tool parameter has a description**, and a test keeps it so. Rotation is in degrees everywhere, `value`
  is typed, `fnv_form` takes `since_seconds`; the old shapes still work.
- **A call log:** every tool call adds one line to `state/fnv/calls.jsonl` (tool, action, result code, time, answer
  size; no argument values and no answer content), kept 30 days. `fnv_journal action=calls` shows failure rates,
  error codes and the slowest and largest calls.
- **A smaller tool list:** the answer schema goes out on `fnv_status` only and default annotation values are left
  out; with every parameter described the list is about 6% smaller than before (217,593 characters, was
  231,716). An opt-in `core` toolset has 23 everyday tools (`"toolsets": ["core"]`). `fnv_threads` and
  `fnv_scene_graph` answer briefly by default; `brief=false` gives the full answer.
- **Documentation:** `bridge/docs/TOOLS.md` is the tool reference, generated from the live tool list and kept
  current by a test. The live-tools document is split by purpose and its dated material moved to
  `bridge/docs/history/`; `bridge/docs/README.md` is the index. Known issues now has the startup hang when the
  monitors are asleep.
- **Guidance for agents:** the server instructions give the first call, five rules, then the first tool for each
  common problem. Six new guides (`start`, `build-a-place`, `test-a-mod`, `quest-dialogue`, `npc-ai-combat`,
  `safety-undo`); every guide is also an MCP prompt. `bridge/docs/AGENT-GUIDE.md` is a page to copy into a mod
  project for its own agent; `AGENTS.md` at the root points agents to `CLAUDE.md`.
- **Fixes from the live check of the tool quality pass** (2026-11-18, 9 failed checks and the rough edges):
  an argument outside its bounds is a plain `INVALID_ARGUMENT` naming the parameter, not the schema library's
  text; each error code has its own recovery line. `fnv_test` runs the shipped examples
  (`fnv-tests/hello-wasteland.yaml`), takes `name` with `run`, says when `fnv.tests_dir` does not exist, and with
  `repeat` flags only steps that pass in one run and fail in another (value noise is listed apart) and keeps the
  debug bundle. `fnv_dialogue` numbers choices from 0 like `fnv_dialogue_choose` and reports `new`; the dialogue
  tree's `goodbye` comes from the goodbye flag. `fnv_place` and every other ref parameter accept `@EditorID`;
  export writes the pose the game shows (after `fnv_move`) rounded to 3 places; a suggested heading that is the
  current one says no turn helps. `fnv_scene_graph` counts children cut by `depth` and gives the rotation;
  `fnv_threads` names functions without a glued address, groups driver threads and shows no -1 lock owner.
  `fnv_setting_info`, `fnv_globals query` and every unknown actor value get "did you mean"; `set_position` with a
  bad value says what it needs. `fnv_guide find` finds `fnv_time` for "make it night time" and `fnv_threads hang`
  for "the game froze". `fnv_health` reports object growth since the last mark and not across a cell change;
  memory writes (and `fnv_globals set`) journal readable values.
- **Bug hunt** (2026-11-18, about 80 fixes, each with a test in `tests/test_bughunt_*.py`):
  - Safety: `fnv_anim events`, `fnv_audio log` and `subtitles`, and `fnv_physics triggers` install engine hooks and
    now need `fnv.engine_control` full. A test teardown after a failed start no longer reverts every change of the
    game (the user's own included). The stale-MO2 cleanup no longer takes another MO2 install in a sibling folder
    (`C:\MO2-other`) for this one. Plugin text with a quote is not put into a console line by `fnv_form reload`.
    Replay names stay inside the replays folder, and save names refuse a trailing newline. A digraph `%:include`
    is checked like `#include`.
  - Nothing left behind after an error: debugger condition slots, watch debug registers, the script stepping hook,
    a script trace on a script that no longer resolves, a native hook whose attach failed, the first perk-recording
    hook, and the frame gate after a `step` step in a test.
  - Wrong results: `fnv_undo` with count 0 undid everything; objective value "false" completed it; `add_item`
    with 0 added one; a removed placement's pose changes stayed undoable; a typed form lost its name on export;
    `fnv_inspect perk` listed OR-group members another member rescued; a dotted profile name reported a file that
    did not exist; a watched object's shared pointer was reported once per field; "100" did not equal 100 in an
    expect; a failed teardown still passed the test.
  - Clear errors instead of Python ones: a raw-address memory write without `as_type`, set_av without a value, a
    missing global in `fnv_script_set`, bad scenario steps and timeouts (refused when the file loads), bad
    positions, scales and menu names in `fnv_place`, `fnv_world_edit` and clicks, a truncated save, BSA or spec
    field, a non-JSON companion reply, and more. `fnv_hook_set action=list` needs no `function`; `fnv_input`
    refuses a `key` and `key_name` that disagree; `fnv_scene_graph` says when a node has more than 128 children.
- **No private data needed:** the watch hooks find their functions by address, so an engine map built without
  the fnvsym proof table is enough; the `fnv_name_*` tools are an optional extra that needs `FNVSYM_ROOT`.
- **A project file for the game alone** (`projects/example-fnv.json`): `plugin` and `spec` are optional.
  An invalid project file stops the server with one line.
- **One setup guide** (`bridge/docs/FNV-MCP-SETUP.md`) with a settings table; `fnv_setup_check` covers the fnv
  block, the symbol database, the proof folder, capstone and a large-address-aware exe.
- **Windows only.** The CrossOver and Wine launchers, the photo studio and `geck_actor_photo` are removed.
- **The GECK tools are removed** (2026-11-18): the 27 `geck_*` editor tools, their 32-bit helper (`Probe.Mcp`,
  `build_helper.py`) and the 15 legacy `geck_runtime_*` and `geck_console_*` tools with their C companion. The
  `fnv_*` tools do those jobs in the running game. The offline plugin builder stays as one tool, `fnv_spec`
  (validate, build, inspect, lookup), in place of `geck_spec_validate`, `geck_plugin_build`,
  `geck_plugin_inspect` and `geck_master_lookup`; `fnv_install` installs a build (`geck_plugin_install` is gone).
  The project keys `launcher`, `helper`, `records`, `source_roots`, `helper_timeout_seconds`, `transport` and
  `legacy_tools` are ignored, so older project files still load.
- **Toolsets:** the project key `toolsets` (or `--toolsets game,naming`) picks which tool families the server
  registers: `game` and `naming`; the default is both. `fnv_guide find` still names a hidden tool and the toolset
  that turns it on.
- **Tool merge** (old names stop working; the table is in `bridge/docs/FNV-LIVE-TOOLS.md`, "Renamed tools"):
  tools that did the same job became one tool with actions. `fnv_crash` (explain by default, latest, history,
  context, capture_on/off/status), `fnv_game` (launch, quit, restart), `fnv_test` (run by default, run_all,
  steps), `fnv_time` (calendar plus the clock and frame gate), `fnv_snapshot` (capture, diff), `fnv_menu` (state,
  tiles), `fnv_trace` (`stack` and several `when` conditions record call stacks), `fnv_undo all=true`,
  `fnv_ui_do close_all`, `fnv_place at export=false` (a test placement fnv_author export leaves out),
  `fnv_script_instances of=` for script variables. Groups that were easy to confuse became one tool too:
  `fnv_func` (find, code, callers, callees, strings), `fnv_class` (layout, tree, vtable), `fnv_record` (show,
  find, users), `fnv_quest` (state, targets), `fnv_dialogue` (now, tree), `fnv_threads` (list, hang),
  `fnv_memory` (address_space, pools, alloc_trace, refcount), `fnv_profile` (sample, diff, frame) and
  `geck_preview` (open, close). 193 tools became 164 (138 after the GECK tools were removed). A merged tool refuses a parameter its chosen action does
  not use (`INVALID_ARGUMENT`).
- **Removed:** the second server under `windows/`, the sprite tools and the PyFFI extra, tracked binaries, the
  upstream working notes, and three examples that needed game files the repository cannot ship.
- **Name and version:** the server announces itself as `fnv-mcp` 0.5.0b1 (the Python package stays `geck_mcp`).
  Environment variables have one prefix, `FNV_MCP_` (`FNV_MCP_ZIG`, `FNV_MCP_PIPE`, `FNV_MCP_RELOAD`,
  `FNV_MCP_SUPERVISOR_LOG`); the old names keep working.
- **Long calls and large answers:** a long tool call sends an MCP progress notification every 5 seconds; an
  answer over 40,000 characters is cut at a row boundary with `truncated: true` and names its paging argument; the
  hot-reload supervisor is opt-in (`--reload`).
- **The companion lets go:** when the bridge disconnects, the frame gate, held input and stopped threads are
  released at once instead of after two minutes.
- **Clearer tool listings:** every parameter of the tools with six or more parameters has a description;
  positions and rotations are `[x, y, z]` everywhere (`fnv_move` and `fnv_raycast` still take the old object);
  tools have display titles; every failure carries an error code; the server's first advice is `fnv_status`, and
  `GECK_NOT_RUNNING` says how to start the GECK.
- **Slimmer tool list:** schemas are emitted without the generated `title` keys, an optional parameter as its own
  type instead of `anyOf [X, null]` with `default: null` (a client that sends null still works), and descriptions
  without docstring indentation; the listing is about 18% smaller. The envelope schema every tool answers with
  (ok, operation, data, error, outcome) is emitted in full on `fnv_status` and `geck_status` only; the other tools'
  outputSchema is a short object schema that names them (the listing shrinks by about another 27%). Results are
  unchanged and still checked against the full schema.
- **Speed:** a read tool on the running game no longer waits one game frame for each of its requests. The bridge
  marks the requests of a read-only tool call, and the companion serves the next marked request in the same frame
  when it comes within 2 ms, for at most 8 ms a frame (the game stays at 60 frames a second under a continuous
  stream of reads). Measured in a live game at 60 frames a second, with the same answers: 32 read tools together
  7.1 s to 1.1 s; `fnv_scene_graph` 1,083 ms to 35 ms, `fnv_render lights` 929 ms to 23 ms,
  `fnv_script_instances` 1,281 ms to 139 ms, `fnv_look` 1,030 ms to 103 ms, `fnv_quest` 262 ms to 21 ms,
  `fnv_player` 196 ms to 20 ms. Tools that change the game, the console and ray casts keep one request a frame.
  A frame's reads stop at its 8 ms budget, counted from its first request and including any time Windows switched
  the game's main thread out. It needs this version's companion (`fnv_companion_install`);
  `FNV_MCP_SAME_FRAME=0` turns it off. Without the game: `fnv_conflicts` 43 s to 0.6 s and `fnv_record users`
  4.9 s to 18 ms (two index lookups that scanned), `fnv_func` name search 560 ms to 30 ms and `strings` 1.2 s to
  0.3 s; the engine map loads in the background at server start instead of in the first `fnv_func` call
  (1.2 s); with the game closed a live tool says `GAME_NOT_RUNNING` in 10 ms instead of 570 ms.
  A write to the game took about 0.6 ms: each `VirtualQuery` of the companion's write measured the game heap's whole
  region (80 MB and more). Writes into plain data now skip it (250 writes in one request 150 ms to 17 ms, as fast as
  reads), and condition evaluation clears its result slots with one write per 250 calls instead of one each:
  `fnv_dialogue tree` 7.9 s to 1.4 s. `fnv_threads list` 1,073 ms to 80 ms (its single stack sample no longer
  sleeps out a 1 s sampling period), `fnv_patch_map` 496 ms to 80 ms (no source line looked up per patched spot,
  which it never showed). Without the game: the load order is kept until a profile file's text, the plugin names in
  a mod folder or a plugin's header record changes (not by size and time, which a rewrite in the same tick of the
  file clock keeps), the archive file lists until an archive changes, and the game's plugin list once per game process
  (an archive or plugin written less than 2 s before it was read is checked again by its bytes, for the same reason):
  `fnv_save_inspect` 1,594 ms to 392 ms, `fnv_asset_check` 134 ms to 4 ms, `fnv_asset nif` and `dds` 135 ms to
  6 ms; the conflicts of one plugin (`fnv_conflicts plugin=`) join from that plugin's records instead of grouping
  the whole load order: `fnv_author check` 549 ms to 12 ms. `fnv_asset users` kept the file paths each plugin
  names in memory only, so the first call of every server read the whole load order (7 to 8 s); they are now kept
  in the record index per plugin file and dropped with it when the file changes: 35 ms on a new server.
- **Breakpoints and threads:** a thread stopped on an int3 breakpoint (`fnv_debug`) used to put the original byte
  back before it parked, so every other thread ran past the breakpoint for as long as the stop lasted. The byte now
  stays while the thread is parked: another thread that reaches the breakpoint stops too, as its own stop. A
  breakpoint's hit count no longer loses hits of threads that trap at the same moment.
- **Hardening:** `fnv_run_script` refuses a quit anywhere in the script; the bridge checks that the companion
  pipe belongs to the game (`PIPE_NOT_GAME`); decompression is capped at 256 MiB; `fnv_func` regex searches
  refuse nested repeats and stop after 20 seconds; Windows tools are run from the system folder; a cached engine
  build is reused only when its digests match.
- **Documents:** the scenario file format, an uninstall section in the setup guide, `SECURITY.md`,
  `CONTRIBUTING.md` and third-party notices; `fnv_plugin_build`, like `fnv_code`, says a DLL built from decompiled
  code is for local use; `fnv_guide engine` searches the shipped guides when there is no research folder.

## 0.4.0 beta 1 - 25 October 2026

25 October teleport feedback correction: coc now requires an originating CELL
binding matching its EditorID on disk. Native destination proof compares the
player's exact parent cell with a live-index BuildRef, avoiding bare runtime
EditorID expression compilation. Receipts expose failed expressions/reasons;
missing/wrong bindings fail before queue/context changes. Existing no-replay,
loading and fresh-target gates remain. Real interior teleport passed with owner
confirmation of usable landing/normal movement; exterior COC landing safety is
not guaranteed. Larger GECK and controlled Shadow gates remain open.

Console feedback upgrade, 24 October: wire-v2 decimal-string process identity;
explicit named-plugin/local-form/hash binding resolved at dispatch; opt-in single
coc with context rotation and later destination/loading proof; typed item count,
equipped and condition queries; explicit MCP setup and readiness states. Tool
count is 44. One companion upgrade/restart required, no per-target-plugin DLL.
Existing no-replay/stale-target guards retained. Real v2 acceptance passed: separate
plugin item count 0/1/0, equipped condition 66%, coc destination and fresh context.

Owner scope addition, 23 October: separate opt-in xNVSE console actuator, four
CLI/MCP readiness/execute/status/release tools (initially 42; now 44 with v2), process/build/
context-bound requests, durable no-replay claims, typed GetModIndex readback and
optional later quest effect verification. Telemetry stays read-only. Console load/
quit/batch commands and arbitrary console-text capture remain unsupported;
explicit coc is supported by the v2 workflow above;
real FNV console smoke acceptance passed (lookup and later-loop quest start/stop).
Broader GECK/Shadow gates remain pending. Earlier console-actuator exclusion is superseded.

- Save reconciliation no longer waits for a script title asterisk to disappear.
  Checkpoint close rechecks EDID/full-source hash and plugin mutation; hidden
  retained editor HWNDs count as closed. Normal Exit preserves prompts and
  saved source/bytecode proof remains mandatory. Offline verified; live retest pending.
- Beta feedback: narrow PlayerRef validation, compression-aware semantic roundtrip,
  exact canonical-field review that retains saved bytes, transient-safe resumable
  load client, persistent cross-profile load ownership and bounded context checks.
  NPC_ uses the existing generic template/field writer without a process-local adapter.
  Status identifies startup Python source and actual helper binary independently.
- Shared CLI/MCP compile start/status/resume, dispatch journal and per-editor
  reservations across clients/profiles; UI progress never means compiled.
- Full RichEdit source/hash checks, guarded recognized prompts and verified
  close/save boundaries. Atomic cache updates require saved source, bytecode/header,
  reference and semantic-variable proof/provenance. Legacy unverifiable caches recompile.
- Loaded/installed/built identities, stale-save refusal and conservative final
  record/override roundtrip checks. Unsupported override loss blocks installation.
- Root/pane/modal restoration, normal clean-session close, native API/thread/window
  evidence and bounded native list search.
- Adapter readiness, unsupported-probe/manual routing, explicit setup preflight
  and save-context baseline invalidation. No console actuator.
- Explicit local working-tree candidate packaging embeds releaseReady=false.
  Live gates remain required; this candidate is not a published release.


## 0.3.0: runtime diagnostics (2026-10-19)

- Add optional read-only xNVSE companion with generated single-quest profiles,
  guarded install/build binding, runtime-resolved load index, process/module/load
  identity and strict fresh-sample checks. Add shared CLI and two automatic MCP
  tools (31 total). No console typing, screen control or arbitrary command endpoint.
- Native fake-host tests exercise the generated 32-bit DLL and failure/lifecycle
  cases; Windows CI builds with hash-verified Zig. The precursor passed live
  diagnostics/fix regression; the generalized adapter passed main-server MCP/CLI
  in-game acceptance on Windows/xNVSE 6.4.9 on 19 October.

- Add shared runtime diagnostics core, CLI and four MCP tools (29 total): prepare,
  collect, report and close. Vanilla batches are user-run; no screen control or
  assumed extender capability. Versioned profiles support quest variables, targeted
  inventory counts and persistent-reference disabled/dead flags with per-session mapping.
- Record explicit save/checkpoint labels, plugin/file hashes, mapping evidence and raw
  line provenance. Detect missing/partial/locked/rewritten logs; echoes never become
  observations. Compare checkpoints and evaluate declarative assertions separately
  from capture quality and user-attested identity.
- Add separate runtime locks/journal namespace, idempotent prepare/close, local
  JSON/Markdown reports, explicit sanitized export and ownership-safe helper archival.
  Closing collection does not stop SCOF. Windows vanilla item-count/delta/error acceptance passed; quest-variable and reference probes have offline coverage only.

## 0.2.1: shared bridge and final live acceptance (2026-10-06)

- Final native Windows 11 / vanilla GECK acceptance completed on 6 October: direct MCP load/reload/cell/render,
  stdio MCP preview and guarded save, actual script compilation through the CLI, and successful load/render after
  a clean GECK restart. Salvatore and game plugin activation list were unchanged. See docs/TESTING.md for evidence
  and the covered-window capture retry.

- Bridge 0.2.1: port the workshop sprite tool's `--collision-static` option and collision motion readback so
  the workshop can depend on this implementation without losing its unmovable defeated sprite. Fixed collision
  uses static layer, motion system 7, fixed quality, zero mass/inertia and clears the dynamic BSX flag.
  Existing dynamic collision behavior and deterministic hash-seed handling are preserved.

- Document file-local versus runtime FormIDs for game console commands. The Salvatore main-quest lookup failure
  was resolved on the unchanged bridge-built plugin by using runtime index `0A` instead of file-local `01`;
  `sqv 0A00080F` and `startquest 0A000818` were verified in game by 2Shay. No plugin writer change was required.

## 0.2.0: Windows fixes from the Salvatore build (2026-10-05)
### bridge/
- **MCP tools work through the Claude desktop device proxy (Cowork).** The proxy drops tool arguments named
  `session_id` (confirmed: a direct stdio client delivers it, the proxy forwarded `{}`), so every tool that required
  it failed with `session_id Field required`. The argument is now `editor_session` and optional on every tool; when it
  is left out the running GECK session is used, and the expected title / plugin hash / model guards still apply.
  `session_id` stays accepted as an alias.
- **Hidden GECK windows.** On native Windows GECK can leave Object Window, Cell View and Render Window hidden (after a
  load, or when GECK starts without user input), which failed load verification with `Cell View selector matched 0
  windows`. The helper has a new `windows.show` operation (ShowWindow without activation, no clicks; refused while GECK
  is busy or modal). `geck_plugin_load_status` and `geck_cell_verify` use it, and the new **`geck_show_windows`** tool
  exposes it. `geck_status` reports `windowsVisible` and `renderWindowReachable`.
- **`loaded_unverified`.** When the plugin is clearly loaded (title, responsive editor) but the check itself cannot run,
  the load ends `loaded_unverified` instead of failing and leaving a barrier.
- **Stale recovery barriers are released.** An uncertain operation from an earlier GECK session (GECK closed, crashed or
  killed, then restarted) no longer blocks editor work: the next editor call marks it `stale` and reports it under
  `releasedStaleOperations`. New CLI `recover.py --list / --release-stale / --ack ID --note ... [--offline]`.
- **Loads no longer use the mouse.** 0.1.1 parked the real cursor over each Data list row for up to 2 s, so a load
  failed when the user moved the mouse or was away. Posted double-clicks toggle the rows without the cursor (tested
  with the cursor elsewhere and while the mouse was in use); each toggle is read back and retried up to 3 times.
- **Failed loads clean up.** If the Data dialog step fails before OK (e.g. a checkbox that does not toggle), the dialog
  is cancelled and the load is `failed_before_change` (no barrier). `data.state` and `data.cancel` no longer need a
  dummy argument in the helper.
- **Reload completion.** Reloading the plugin GECK already shows was reported done before GECK even started reloading
  (the title names the plugin before and after). A reload is now done only after the reload was seen, or after 45 s.
- **Templates and indexes follow the profile.** Record templates and master indexes are read from the profile's
  `state_dir` first, then from `bridge/state` (job 038 failed with `no template dump` although the profile had it).
  `build_plugin.py` gains `--project`; its default output is `<state>/../build`, the folder the MCP tools use.
  `cell_verify.py` reads the receipt from that folder too.
- **`compile_scripts.py`** loads with `live_load.py --if-needed`: no reload when this GECK session already loaded the same
  plugin bytes; a load whose plugin is active but whose cell check failed still counts.
- **`geck_render_capture` on Windows** (was macOS only): PrintWindow in GECK's own DPI context (the image is the size GECK
  renders and reports), retried with a repaint request when it returns only the grey background; then a DPI-aware
  screen copy if no other window covers the Render Window. A flat (blank/black) result fails with `CAPTURE_BLANK`.
- `ALCH` (aid items) added to the generic record types.
- **Sprite tools.** `sprite_flipbook.py --collision-material organic` (a name or a number) replaces the Havok material
  of the borrowed vanilla collision, which sets the sound when the sprite is knocked about (ported from the Salvatore
  workshop). `sprite_flipbook.py` and `tools/creature_skeleton.py` re-run themselves with `PYTHONHASHSEED=0`, so the
  same input gives the same NIF/KF bytes (PyFFI writes string tables in set order; two hash seeds gave two different
  meshes). `creature_skeleton.py` finds the game Data folder from `--data`, `--project`, `$FNV_DATA` or the usual Steam
  folder (was a hard-coded macOS path). PyFFI 2.2.3 and setuptools 84.0.0 (PyFFI imports distutils, removed in
  Python 3.12) are in `requirements.lock.txt`, so the sprite tests run in CI.
- Unit tests pass on Windows (fixtures no longer assume POSIX/CrossOver paths; Wine launcher tests skip on Windows);
  CI runs them on windows-latest too.

## 0.1.1: first Windows-tested release (2026-10-03)
### bridge/
- Fixed on native Windows: plugin load failed with `MODAL_BLOCKED` because GECK's Data dialog stayed hidden; the helper
  now shows it.
- Fixed on native Windows: Data list checkboxes did not toggle (and a sent click could stall GECK until the mouse moved);
  the helper now posts the double-click with the cursor parked over the row. Wine/CrossOver behaviour is unchanged.
- First Windows 11 end-to-end run (status, build, install, live load, cell verify): see `docs/TESTING.md`.
- The Nexus download is source only (no `.exe`): build `Probe.Mcp.exe` with `build_helper.py`. GitHub keeps the prebuilt helper.

## 0.1.0: WIP prototype (2026-10-02)
First public release, as a work-in-progress prototype.

### bridge/
- Offline spec-to-plugin builder: codec, stable FormIDs, `@EditorID` references to indexed masters,
  template-cloned records, validation, install with backups.
- GECK control through `Probe.Mcp.exe`: status, plugin load, cell verification, record model edits, save,
  script compilation with GECK's compiler, and actor photos (macOS).
- New for the release:
  - `launcher` profile setting (`crossover` / `wine` / `native`) for Linux and Windows
  - a cross-platform lock
  - example profiles and the vanilla `hello-wasteland` spec
  - author paths removed
- Tested end to end only on macOS + CrossOver with vanilla GECK.

### windows/
- Fallout: New Vegas port of GECK MCP for Fallout 3 (maksimka2432fr23, MIT): FNV paths, NVSE loader,
  FalloutNV plugins.txt, works with MCP SDK 1.x and 2.x. Unit tests pass. Not yet run against the FNV GECK.

### Known gaps
- No Windows or Linux end-to-end run yet. GECK Extender is untested.
- The bridge loads FalloutNV.esm only (no DLC masters).
- Photos are macOS-only.
