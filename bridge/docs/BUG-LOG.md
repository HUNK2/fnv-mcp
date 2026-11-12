# Bug log

Audit and stress test of the geck MCP bridge, 2026-11-12, the fix pass the same day, and a second pass that ran every remaining check in the game (L entries are bugs that second pass found and fixed). Every entry has a Resolution line; the table status says where it stands. A later hunt the same day found 54 more bugs, all still open: see "Round 2" at the end.

**How it was found.** Six read-only audits, one per area (about 47,000 lines of Python and C), each looking for bugs that break normal use: thread safety, state left behind in the game, wrong offsets or layouts, error paths, protocol handling. Then stress tests: 208 parallel offline tool calls with odd inputs (no cross-talk found), parallel live calls on the running game, parallel placements and undo, supervisor reloads during long calls, and two full test-suite runs (867 tests pass).

**Status.** fixed = changed and covered by a test (tests/test_bugfix_*.py or the existing suites); fixed, verified live = also checked in the running game; fixed (diagnosis) = the bridge now records the cause when it happens again; open (client side) = not fixable in this repository. "Found as" keeps how sure the audit was (verified, confirmed, likely, possible).

**Severity.** high = wrong result, crash, hang, data loss, or the game or user files left changed in normal use; medium = wrong in a less common case; low = small or cosmetic.

Totals: 120 bugs, 21 high, 51 medium, 48 low.

## Resolution

- fixed: 91
- fixed, verified live: 27
- fixed (diagnosis), old cause unknown: 1
- open (client side): 1

Tests: all 1002 pass with GECK_MCP_ZIG set (4 skipped: Wine-only, symlink rights, MSVC9 tools), including 135 regression tests in tests/test_bugfix_*.py. The rebuilt engine companion is installed in the dev profile; every fix with a game part was exercised in the running game (debugger holds, return, step out, until and temporary sites, long waits, crawl of interior and exterior cells, screenshots, save backup, guarded calls into code with its own SEH frame, saves made with moving objects, quit tracing). The exterior crawl uses a one-cell test plugin, HelloExterior.esp, installed in the dev profile. The first round is committed as 133ed0e.

| ID | Severity | Status | Bug | Where |
|---|---|---|---|---|
| A1 | high | fixed | Act journal is not thread-safe: parallel act tools lose or corrupt undo entries | `geck_mcp/fnv_live.py:62-74` |
| A2 | high | fixed | Lazy `LiveService.game` creates several Game/EngineLink objects; a dropped one can block the single-instance companion pipe | `geck_mcp/fnv_live.py:86-94` |
| A3 | high | fixed, verified live | fnv_revert_all / fnv_undo apply undos recorded in earlier game processes | `geck_mcp/fnv_live.py:1253-1255` |
| B1 | high | fixed, verified live | Any int3 at an address in the coverage table loops forever (game hang) | `native/engine/w6_cov.c:187-206` |
| C1 | high | fixed | Reverse-reference index goes stale when the user's plugin is rebuilt (file id is reused) | `geck_mcp/fnv_records.py:170-172` |
| C2 | high | fixed | A failed index rebuild leaves the thread's connection holding the write lock ("database is locked" for every other thread) | `geck_mcp/fnv_data.py:157-170` |
| C3 | high | fixed | Two parallel calls that index the same plugin for the first time: one fails with IntegrityError | `geck_mcp/fnv_data.py:154-163` |
| D1 | high | fixed | fnv_form reload reads AMMO DATA as value/weight: writes a value of about 1e9 and weight about 0 | `geck_mcp/fnv_create.py:743-746` |
| D2 | high | fixed | A camera journal entry undoes with a blind "tfc", so fnv_undo / fnv_revert_all can turn the fly camera ON | `geck_mcp/fnv_eyes.py:249-257` |
| D3 | high | fixed | Parallel photo/camera calls fight over the one fly camera, saved state and HUD toggle | `geck_mcp/fnv_eyes.py:187` |
| E1 | high | fixed, verified live | fnv_debug freeze cannot be thawed: the game stays suspended for good | `fnv_debugger.py:172` |
| E2 | high | fixed, verified live | `return` from a hook-hold stop skips with pop=0: stack corruption for callee-cleans functions | `fnv_debugger.py:491` |
| E3 | high | fixed | `return` mid-function uses `resume ret` when the function is not in the symbol database | `fnv_debugger.py:1046-1077` |
| E4 | high | fixed | fnv_flow trace leaves the thread parked at a stop on refusal, timeout or read error, and _release resumes other tools' stops | `fnv_flow.py:199-204, 202-204` |
| E5 | high | fixed | fnv_script_debug continue hangs forever while a variable `when` stays false | `fnv_scriptdbg.py:374-382` |
| E6 | high | fixed | Pause, script freeze and thread freeze are lost on a server restart: the game is left paused or off with no way back | `fnv_time.py:105,117,183-187` |
| F1 | high | fixed | fnv_bisect level=plugins never finds a candidate on an MO2 New Vegas profile | `geck_mcp/fnv_suite.py:44-46` |
| F2 | high | fixed | fnv_bisect level=records reuses a different plugin's cached bytes and later writes them back | `geck_mcp/fnv_suite.py:82-83, 113-117, 128-130` |
| F3 | high | fixed | Records bisect edits the user's plugin in place with no backup on disk, and restore failures are swallowed | `geck_mcp/fnv_suite.py:111, 116, 160-169` |
| F4 | high | fixed, verified live | fnv_crawl cells never visits an exterior cell | `geck_mcp/fnv_crawl.py:91-96` |
| S1 | high | fixed, verified live | fnv_record_find failed on any worker thread but the first (shared SQLite connection) | `geck_mcp/fnv_data.py RecordIndex` |
| A4 | medium | fixed | Parallel GECK tool calls fail at once with EDITOR_BUSY, reported as BRIDGE_ERROR and not retry-safe | `geck_mcp/service.py:372` |
| A5 | medium | fixed | Supervisor never clears a cancelled request from `in_flight`, so hot reload stops for the life of that child | `geck_mcp/supervisor.py:354-358` |
| A6 | medium | fixed | A concurrent geck_runtime_save_context overwrites the editor operation's state on the shared Service | `geck_mcp/service.py:199-202` |
| A7 | medium | fixed | undo / revert_all lose track of undos already applied when one entry fails | `geck_mcp/fnv_live.py:1295-1296, 1305-1306` |
| A8 | medium | fixed | `fnv_session end` fails when the game is gone, and the session can then not be closed or restarted | `geck_mcp/fnv_session.py:116-118` |
| B2 | medium | fixed | Watch hooks and native-module hooks are written into live code with other threads running | `native/engine/fnvmcp_engine.c:787` |
| B3 | medium | fixed | fnv_code shadow replays fastcall functions with every argument shifted | `geck_mcp/fnv_code.py:530-541` |
| B4 | medium | fixed | fnv_code swap/compare reuse a blob placed from an older build | `geck_mcp/fnv_code.py:388` |
| B5 | medium | fixed | unswap forgets the swap before restoring it; failures leave the game patched with no record | `geck_mcp/fnv_code.py:469-476` |
| B6 | medium | fixed | Coverage states are not moved with the addresses when `cov add` re-sorts the table | `native/engine/w6_cov.c:241-263` |
| B7 | medium | fixed | `call` reads argv[2] before checking argc | `native/engine/fnvmcp_engine.c:1817` |
| B8 | medium | fixed | Exported loader for a function shorter than 5 bytes overwrites the next function | `geck_mcp/fnv_code.py:574-651` |
| B9 | medium | fixed, verified live | A guarded call that faults leaves fs:[0] pointing into the abandoned stack | `native/engine/w1_guard.c:78-90` |
| B10 | medium | fixed | flow-call module cannot call fastcall functions (edx = this) | `native/modules/fnvmcp_flowcall.c:29` |
| C4 | medium | fixed | fnv_record_users crashes ("too many SQL variables") on popular records | `geck_mcp/fnv_records.py:234-236` |
| C5 | medium | fixed | record_users misses references held in subrecords listed in STRING_TAGS (e.g. NPC_ RNAM race) | `geck_mcp/fnv_records.py:96-98` |
| C6 | medium | fixed | DLC override records resolve to a form id that does not exist (builder Resolver) | `geck_mcp/esp/formids.py:194-209` |
| C7 | medium | fixed | fnv_script_lint keeps the first load order and file ids forever | `geck_mcp/fnv_data.py:565-571` |
| C8 | medium | fixed | Save namer maps quest form ids through the current load order, not the save's | `geck_mcp/fnv_save.py:361-371` |
| D4 | medium | fixed | hide_hud toggles `tm` without reading the state: an already hidden HUD is shown for the photo | `geck_mcp/fnv_eyes.py:696-705` |
| D5 | medium | fixed | world_edit enable/disable journals an undo that does not depend on the state before | `geck_mcp/fnv_world.py:364-369` |
| D6 | medium | fixed | world_edit weather: the undo loses the original override and has no pid | `geck_mcp/fnv_world.py:417-424` |
| D7 | medium | fixed | fnv_asset reload leaves references disabled when the purge fails | `geck_mcp/fnv_create.py:177-193` |
| D8 | medium | fixed, verified live | Save backup runs on every change, or never, depending on a pid written only by fnv_launch | `geck_mcp/fnv_game.py:693-710` |
| D9 | medium | fixed | geck_actor_photo is macOS-only but runs on Windows: it loads the studio plugin, then always fails | `geck_mcp/photo.py:199, 218, 240 -> imaging.py:24-45, 62-70` |
| D10 | medium | fixed | Screenshot and contact-sheet file names collide (one-second stamps, fixed default names) | `geck_mcp/fnv_eyes.py:756, 787, 807, 819, 845` |
| E7 | medium | fixed | Breaks from a previous server process are orphaned and cannot be cleared, and their pred slots get reused | `fnv_debugger.py:396-397` |
| E8 | medium | fixed, verified live | Step out / frames / return are wrong inside a function prologue (writes an int3 into the stack) | `fnv_debugger.py:992-996` |
| E9 | medium | fixed, verified live | fnv_wait_for fails for long waits: `until` max is 0x2710, and its timeout is not enforced | `fnv_time.py:342,354` |
| E10 | medium | fixed | Watch hooks silently remove other tools' hooks on the same function | `fnv_deep.py:259-261` |
| E11 | medium | fixed | fnv_script_profile stop keeps the baseline: the next read is empty and crash_context disable never unhooks | `fnv_deep.py:400-421, 631` |
| E12 | medium | fixed | Parallel calls fight over bp ids and predicate slots | `fnv_debugger.py:396-405 and 465-472` |
| E13 | medium | fixed | fnv_blame field and fnv_field_watch always use debug-register slot 0 | `fnv_blame.py:115-119, fnv_watch.py:110-113` |
| E14 | medium | fixed | Debugger waits accept any newer stop as the result of a step | `fnv_debugger.py:252-262` |
| E15 | medium | fixed, verified live | A STOP_TIMEOUT leaves the temporary `to` site armed; recursion stops at the wrong frame | `fnv_debugger.py:260-261, 981-990` |
| E16 | medium | fixed | Script-debugger `when` with a hex number is treated as a script-variable condition | `fnv_scriptdbg.py:856-859` |
| E17 | medium | fixed | fnv_blame keeps `seen` sequence numbers across hook re-creation and game restarts | `fnv_blame.py:363-366` |
| F5 | medium | fixed | Bisect counts "game did not start" as "scenario fails", and never checks the final culprit | `geck_mcp/fnv_suite.py:64-66, 147-159` |
| F6 | medium | fixed | Spec JSON is read with the Windows locale encoding (cp1252) | `geck_mcp/authoring.py:69` |
| F7 | medium | fixed | Compile checkpoint gets stuck "blocked" forever if the process dies between install and the 'installed' write | `geck_mcp/compilation.py:310-316 together with :163` |
| F8 | medium | fixed | Scenario runner: exceptions other than Game/Link/Scenario errors skip all cleanup | `geck_mcp/fnv_scenario.py:376-447` |
| F9 | medium | fixed | Experiment battery can leave an engine function permanently skipped | `geck_mcp/fnv_experiment.py:220-227, 366-379` |
| F10 | medium | fixed | fnv_spawn: refs placed before a later PlaceAtMe failure are never journaled | `geck_mcp/fnv_agent.py:796-808` |
| F11 | medium | fixed | fnv_crawl items computes wrong form ids for override records | `geck_mcp/fnv_crawl.py:401-408` |
| L1 | medium | fixed, verified live | Replies say game null after a server reload until something pings | `geck_mcp/fnv_live.py` |
| L2 | medium | fixed, verified live | fnv_native forgets loaded modules after a server reload | `geck_mcp/fnv_native.py` |
| L4 | medium | fixed, verified live | Crawl and file log report present files as missing | `geck_mcp/fnv_create.py` |
| S2 | medium | fixed, verified live | fnv_spawn ignores distance: every spawned ref lands on the player | `geck_mcp/fnv_agent.py` |
| S3 | medium | fixed, verified live | fnv_actor_values on a non-actor calls actor-only engine code | `geck_mcp/fnv_inspect.py / fnv_agent.py` |
| S4 | medium | fixed (diagnosis), old cause unknown | The game quit during the audit and the cause is unknown | `unknown` |
| S5 | medium | open (client side) | The Claude app does not refetch tools after the supervisor's change notice (respawned connections) | `geck_mcp/supervisor.py / the Claude app` |
| A9 | low | fixed | Lazy EditorID index in LiveService: a concurrent call sees a half-loaded index | `geck_mcp/fnv_live.py:300-310` |
| A10 | low | fixed | Supervisor: a crashed child replaced before `child_exited` runs leaves its calls unanswered | `geck_mcp/supervisor.py:131-137` |
| A11 | low | fixed | `fnv_session start` leaves changes behind when it fails partway; two parallel starts both run | `geck_mcp/fnv_session.py:61-98` |
| A12 | low | fixed | `fnv_actor_control set_position` skips the save backup and journals only after all three moves | `geck_mcp/fnv_live.py:1133-1141` |
| A13 | low | fixed | Project file read with the locale code page (cp1252 here) | `geck_mcp/config.py:117` |
| A14 | low | fixed | One-shot helper output decoded with the locale, the persistent worker's as UTF-8 | `geck_mcp/transport.py:35` |
| A15 | low | fixed | Write timeout on the companion pipe closes the handle with the overlapped write still pending | `geck_mcp/fnv_link.py:100-102, then `_exchange` close at 137-138` |
| B11 | low | fixed | Export lookup reads AddressOfFunctions as the name count; flow call loads a second module copy | `geck_mcp/fnv_native.py:316` |
| B12 | low | fixed | `hookctl <id> 1 clear` on a hook without a call table dereferences NULL | `native/engine/fnvmcp_engine.c:818-823` |
| B13 | low | fixed | Stop skip stub slot can be rewritten before the parked thread runs it | `native/engine/w2_stop.c:217-218` |
| B14 | low | fixed, verified live | Guarded calls abort on first-chance faults the callee would handle itself | `native/engine/w1_guard.c:64-68` |
| B15 | low | fixed | dbg_apply reports STILL_ACTIVE (259) as the thread count after its 5 s timeout | `native/engine/fnvmcp_engine.c:1240-1243` |
| B16 | low | fixed | `cov read` with since above the hit count emits 8000 bogus addresses | `native/engine/w6_cov.c:290-291` |
| B17 | low | fixed | write_bytes restores the first page's protection on every page it touched | `native/engine/fnvmcp_engine.c:189-192` |
| C9 | low | fixed, verified live | Reference change forms with changed form flags never decode (missing spacer) | `geck_mcp/fnv_save.py:274-277` |
| C10 | low | fixed | Save parser crashes with raw IndexError/struct.error instead of SaveFormatError | `geck_mcp/fnv_save.py:136` |
| C11 | low | fixed | Record search treats "_" in the query as a wildcard | `geck_mcp/fnv_data.py:291-292` |
| C12 | low | fixed | Shared per-instance caches are published before they are filled (thread race) | `geck_mcp/fnv_naming.py:232-245` |
| C13 | low | fixed | Strict cp1252 decoding crashes on plugin strings with undefined bytes | `geck_mcp/esp/codec.py:145-149` |
| D11 | low | fixed | fnv_photo sequence opens a frame gate the user had closed | `geck_mcp/fnv_eyes.py:795-813` |
| D12 | low | fixed | vats_odds uses the agent's shared scratch buffer across three separate requests | `geck_mcp/fnv_world.py:728, 762-764` |
| D13 | low | fixed, verified live | wincapture.capture reports the wrong method when it falls back to a blank PrintWindow image | `geck_mcp/wincapture.py:225-243` |
| D14 | low | fixed | SystemsTools.sweep divides by zero for steps=0 | `geck_mcp/fnv_inspect.py:987-988` |
| E18 | low | fixed | `set` on a double variable writes integer bytes | `fnv_debugger.py:932-935` |
| E19 | low | fixed | Blame drops the exact script statement when the statement hook has id 0 | `fnv_blame.py:168` |
| E20 | low | fixed | Watch-hook status check uses 'class proven' but the engine map says 'class_proven' | `fnv_deep.py:240, fnv_blame.py:241` |
| E21 | low | fixed | Predicate compiler edge cases | `fnv_pred.py:207-214` |
| E22 | low | fixed | `step` with fps leaves the fixed frame step on after the step | `fnv_time.py:228-229` |
| E23 | low | fixed | Blame field leaves the EvaluateLine statement hook installed | `fnv_blame.py:110-114` |
| E24 | low | fixed, verified live | Function-entry breaks with thread=main still park worker threads | `fnv_debugger.py:419-424, 486-487` |
| F12 | low | fixed | Scenario YAML subset: apostrophes break comment stripping; some scalars mis-typed | `geck_mcp/fnv_scenario.py:55-90` |
| F13 | low | fixed, verified live | fnv_crawl cells leaves the warning capture hook and the file log running | `geck_mcp/fnv_crawl.py:156-190` |
| F14 | low | fixed, verified live | Engine screenshot treats a half-written BMP as done | `geck_mcp/fnv_agent.py:569-571, 533-537` |
| F15 | low | fixed | Records bisect quits the user's game before validating its arguments | `geck_mcp/fnv_suite.py:122-130` |
| F16 | low | fixed | fnv_crawl items changes the player's equipment and inventory beyond the item under test | `geck_mcp/fnv_crawl.py:418-431` |
| F17 | low | fixed | fnv_coverage writes outside its folder for a run name with path parts | `geck_mcp/fnv_coverage.py:73, 120-121` |
| F18 | low | fixed | engine_wait leaves the player's wait counter changed if a middle batch fails | `geck_mcp/fnv_agent.py:1467-1476` |
| F19 | low | fixed | Report and screenshot files are keyed by whole seconds | `geck_mcp/fnv_scenario.py:1037, 1047` |
| L3 | low | fixed, verified live | A native module fault is CALL_FAILED with a raw dict, and a silent build failure has no message | `geck_mcp/fnv_native.py` |
| L5 | low | fixed | until says "Reached" when another stop came first | `geck_mcp/fnv_debugger.py` |
| L6 | low | fixed, verified live | Companion ping counts removed hooks | `native/engine/fnvmcp_engine.c` |
| L7 | low | fixed | WAIT_TIMEOUT names an argument the tool does not have | `geck_mcp/fnv_time.py` |
| S6 | low | fixed | Test runs write into the real state/supervisor.log | `geck_mcp/supervisor.py main()` |
| S7 | low | fixed, verified live | fnv_record_show 0xFF000001 says "No record None:000001" | `geck_mcp/fnv_data.py` |
| S8 | low | fixed, verified live | fnv_func_code does not accept Class::Method names | `geck_mcp/fnv_engine.py` |
| S9 | low | fixed | fnv_look reports the prompt as "Button Text" when nothing is targeted | `geck_mcp/fnv_eyes.py` |
| S10 | low | fixed | fnv_wait_for on unreadable memory just times out | `geck_mcp/fnv_pred.py / fnv_time.py` |
| S11 | low | fixed, verified live | The envelope's "game" field is stale after the game exits | `geck_mcp/fnv_live_tools.py` |

IDs: A server core, B native companion, C offline data, D world and photos, E debugger, F agents and editor, S stress tests and live use, L found during the live checks.

## High

### A1. Act journal is not thread-safe: parallel act tools lose or corrupt undo entries

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/fnv_live.py:62-74 (Journal.add, Journal.mark_undone)
- Severity: high
- Found as: confirmed
- What happens: `add` opens the file in append mode separately in each thread. On Windows the CRT turns append into "seek to end, then write", and that pair is not atomic across handles. `mark_undone` reads the whole file and rewrites it with nothing guarding the read and write. - 4 threads x 500 small `add`: 1963, 1974 and 1984 of 2000 entries survived. - With some entries over 8 KB, the file was corrupted and `entries()` raised JSONDecodeError. That breaks fnv_journal, fnv_undo, fnv_revert_all and the revert in `fnv_session end`. - One thread running `add` while another ran `mark_undone`: 290 of 301 entries survived. - Each lost entry is a change in the game (memory write, spawn, setting) that can no longer be undone.
- Fix idea: guard every Journal method with one module-level `threading.Lock`, and write `mark_undone` to a temp file followed by `os.replace`.
- Resolution (fixed): Journal: one lock per file around every read and write; mark_undone writes a temp file and os.replace; torn lines are skipped. test_bugfix_a_c_s JournalTests (4 threads x 150 adds with 9 KB entries plus marks: all 600 kept).

### A2. Lazy `LiveService.game` creates several Game/EngineLink objects; a dropped one can block the single-instance companion pipe

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/fnv_live.py:86-94 (`game`, `link` properties); native/engine/fnvmcp_engine.c:2218-2220 (CreateNamedPipeA with max instances 1)
- Severity: high
- Found as: confirmed (confirmed (4 parallel first calls built 4 Game objects and handed out 4 different links); the pipe lockout is likely)
- What happens: two or more parallel first fnv_* calls (the client often opens with fnv_status and fnv_player together) each see `_game is None`. Each builds its own `Game`, and each Game has its own `EngineLink`, with its own RLock and pipe handle. - A thread that got a link which is later replaced can still connect with it. That handle is a raw `_winapi` int, so it is never closed. - The companion serves one client at a time on one pipe instance. It keeps serving that orphan connection, so every later call fails with COMPANION_UNREACHABLE (pipe busy) until the server restarts. - Even without the lockout, two links mean two locks, so requests are no longer serialized.
- Fix idea: create the Game under a lock (or eagerly in `register`), and keep exactly one EngineLink per process.
- Resolution (fixed): LiveService.game / engine() / EditorID index built under one lock (double-checked): one Game and one EngineLink per process. test_one_game_object_under_parallel_first_use.

### A3. fnv_revert_all / fnv_undo apply undos recorded in earlier game processes

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/fnv_live.py:1253-1255 (`_apply_undo` pid check), 1303-1304 (`revert_all` filter); fnv_agent.py:805 (spawn), 764 (move)
- Severity: high
- Found as: likely
- What happens: only 8 of the 23 undoable `journal.add` sites store `pid`. The filter `not e.get('pid') or e['pid'] == pid` lets every entry without a pid through, however old. - In a fresh game process, fnv_revert_all (documented as "made in the current game process", and called with no `since`) replays earlier days' undos that were never applied: - spawn undo: `Disable` + `MarkForDelete` on a dynamic `FFxxxxxx` id the new process may have reused for an unrelated object. - move undo: SetPos/MoveTo back to an old position. - set_av / force_av, settings, globals and add_item undos (add_item's undo is `RemoveItem`). - All of these land in whatever save is loaded now. `fnv_undo` without journal_id has the same problem, and so does `fnv_session end` when the game was relaunched inside the session.
- Fix idea: store `pid` in every undoable entry (in `Journal.add`, from `link.hello`), and treat an entry without a pid as from another process.
- Resolution (fixed, verified live): Journal.add stamps pid and start time of the running game on every entry; undo/revert_all only apply entries of the same process, and entries without a pid count as another process. Live: undo of two spawns took only this game's entries.

### B1. Any int3 at an address in the coverage table loops forever (game hang)

- Area: Native companion (C) and code injection
- Where: bridge/native/engine/w6_cov.c:187-206 (cov_on_breakpoint), called first in on_trap at fnvmcp_engine.c:1152
- Severity: high
- Found as: verified
- Checked: read w6_cov.c cov_on_breakpoint (lines 31-50): any int3 at a listed address is claimed and EIP reset to it, whatever its state
- What happens: cov_on_breakpoint takes every EXCEPTION_BREAKPOINT whose address is in cov_addr. It always sets Eip=address and returns CONTINUE_EXECUTION, even when its CAS fails (state 0 or 2). The table survives `cov disarm`; only `cov reset` empties it. Any other 0xCC at a listed line address then re-executes forever on that thread: a bp int3 site, a `resume to` site, a trace return site (tr_step puts these on return addresses, which are often line starts), or an int3 probe (mode 2). On the main thread the game hangs. cov add skips addresses that are already sites, but nothing stops a site being added later. A related case: isite_add on a line that is still armed saves 0xCC as the "original" byte, so removing that site later leaves a stray int3.
- Fix idea: claim the trap only when the CAS 1->2 succeeds, or when the state is 2 and the byte is no longer 0xCC (a trap in flight). Otherwise return false. Also refuse bp/probe sites on armed cov addresses.
- Resolution (fixed, verified live): cov_on_breakpoint claims a trap only when it arms->hit (CAS) or the byte is no longer 0xCC (in flight); cov_release_range puts armed coverage bytes back before a hook, probe, int3 site or code patch reads or writes there; arm skips lines a bp/probe owns. Live: coverage armed with two logging breaks on the same function, game fine.

### C1. Reverse-reference index goes stale when the user's plugin is rebuilt (file id is reused)

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/fnv_records.py:170-172 (ref_files check); geck_mcp/fnv_data.py:157-163 (RecordIndex.ensure delete + reinsert)
- Severity: high
- Found as: confirmed (confirmed (t5.py: index Mine.esp -> id 17, build refs (267); replace the file with other content, ensure -> new row gets id 17 again; `_ensure_refs` builds nothing; 267 old refs stay, 264 of them at offsets past the end of the new file))
- What happens: `files.id` is `integer primary key` without AUTOINCREMENT, so deleting the row with the highest id and inserting again gives the same id. The user's own plugin is normally the last one indexed (live cache: HelloCrash.esp = id 16, the max). After geck_plugin_build/fnv_install changes it, `ensure` rebuilds `records`, but `ref_files(file=16, version=2)` still matches, so `fnv_record_users` keeps reporting the old references (removed records still listed, new references missing). `ensure` also never deletes `refs`/`ref_files` rows for a file id it drops, so other rebuilds leave orphan rows behind.
- Fix idea: in `ensure`, delete `refs` and `ref_files` rows for the old id in the same transaction (or key ref_files by size+mtime, or use AUTOINCREMENT).
- Resolution (fixed): A rebuilt plugin gets a new file id (never a reused one: last id kept in a meta table) and its refs/ref_files rows are deleted in the same transaction. test_rebuilt_plugin_gets_a_new_id_and_drops_its_refs.

### C2. A failed index rebuild leaves the thread's connection holding the write lock ("database is locked" for every other thread)

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/fnv_data.py:157-170 (RecordIndex.ensure, no rollback on error)
- Severity: high
- Found as: verified
- Checked: read fnv_data.RecordIndex.ensure: no rollback, no lock
- What happens: Python sqlite3 opens a transaction at the first DELETE. If `scan_plugin` then raises (truncated/locked/half-written plugin, OSError, FormatError, struct error), or the INSERT fails (see the next entry), nothing rolls back. The thread-local connection keeps the RESERVED lock while the anyio worker thread lives. Every write from any other tool call (record index, refs index) then waits 30 s and fails, and later calls on that same thread run inside the stale transaction. The deletes also happen before the scan, so a healthy rebuild of FalloutNV.esm holds the write lock for the whole scan.
- Fix idea: scan first, then do delete+insert inside `with self.db:` (commit on success, rollback on any exception).
- Resolution (fixed): ensure scans first, then deletes and inserts in one transaction that rolls back on any error; a transaction left open is rolled back first. test_failed_rebuild_rolls_back_and_unlocks.

### C3. Two parallel calls that index the same plugin for the first time: one fails with IntegrityError

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/fnv_data.py:154-163 (RecordIndex.ensure check-then-insert)
- Severity: high
- Found as: verified
- Checked: read fnv_data.RecordIndex.ensure: check-then-insert with no lock
- What happens: both threads read "no row", both scan, the first commits, the second's insert hits `path unique`. The tool call fails (fnv_record_find / record_show / record_users / conflicts / script_lint, or the save namer, called in parallel on a new or changed plugin, e.g. right after a build or on first use). Because of the entry above, the failing thread also keeps the write lock. If both threads saw an old row, both delete and reinsert, and the second hits the same error.
- Fix idea: serialize ensure() per path with a process lock (threading.Lock keyed by path), then check again inside the lock; or use `BEGIN IMMEDIATE` and check again after taking the lock.
- Resolution (fixed): ensure is serialized per plugin path and checks again inside the lock. test_parallel_first_use_indexes_once.

### D1. fnv_form reload reads AMMO DATA as value/weight: writes a value of about 1e9 and weight about 0

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_create.py:743-746 (FormTools._field_ops). The same layout is in fnv_data.py:704 (decode_subrecord, outside this area)
- Severity: high
- Found as: confirmed
- What happens: for AMMO, DATA is speed f32, flags u8, 3 unused bytes, value i32, clip rounds u8 (13 bytes). Weight is in DAT2 at +8. The code unpacks `<if` at 0 as (value, weight). I decoded FalloutNV.esm with the bridge codec: Ammo9mmRobot -> "value" 1065353216 (the bits of speed 1.0), "weight" 2.8e-45, but the real value is 1 and DAT2 weight 0.029. AmmoSmallEnergyCellTurret -> "value" 1092616192 (speed 10.0). So `fnv_form reload` of a rebuilt plugin with any AMMO record runs `SetBaseItemValue <form> 1065353216` and `SetWeight 0` on the live ammo. That changes the economy and weight of the ammo in the running game and in the save, and nothing records how to undo it.
- Fix idea: for AMMO, read value as `<i` at +8 of DATA (len >= 12) and weight as `<f` at +8 of DAT2. Fix fnv_data.decode_subrecord the same way.
- Resolution (fixed): fnv_form reload reads AMMO value at DATA+8 and weight at DAT2+8; decode_subrecord shows the AMMO DATA/DAT2 layouts. test_bugfix_d D1Ammo.

### D2. A camera journal entry undoes with a blind "tfc", so fnv_undo / fnv_revert_all can turn the fly camera ON

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_eyes.py:249-257 (_remember), 368-393 (restore). The undo is run by fnv_live.py:1258 and 1299-1306
- Severity: high
- Found as: likely
- What happens: every first camera change (fnv_camera set/orbit, and every fnv_photo ref/cell, which call set/orbit) journals `{'kind': 'camera', 'undo': {'op': 'console', 'line': 'tfc'}}`. `restore()` (which every photo runs in its `finally`) turns fly mode off but never marks that entry undone. Then: (a) fnv_undo with count 1 after a photo picks this entry as the last undoable change and runs `tfc`, which switches the fly camera ON. (b) fnv_revert_all runs `tfc` once for each photo taken: an odd number of photos leaves the player in the fly camera. The entry has no top-level `pid` (it is only inside `saved`), so revert_all also runs it in a later game process.
- Fix idea: make the undo idempotent (an op that sets fly to the saved state, as `_fly(saved_fly)` does) with a top-level pid, and mark the entry undone in restore().
- Resolution (fixed): Camera restore marks its journal entry undone, so undo no longer runs a blind tfc.

### D3. Parallel photo/camera calls fight over the one fly camera, saved state and HUD toggle

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_eyes.py:187 (self.saved), 696-705 (_hud_off), 742-765 (_photo_ref), 767-793 (_photo_cell), 368-393 (restore)
- Severity: high
- Found as: likely
- What happens: EyesTools is one shared instance and nothing serializes camera use (fnv_live.run has no lock; tools run on worker threads). Take two fnv_photo ref calls at once, A and B: - A sends `tm` (HUD hidden), then B sends `tm` (HUD shown again). The captures from both calls then show the HUD. - A and B orbit the same fly camera alternately, so each one's screenshots can show the other's subject or angle. - The first call to finish runs restore(): it sets `self.saved = None` and turns fly off while the other is still shooting, so the other's remaining shots come from the player's eyes. The other's own restore then finds nothing saved, or sees fly on and toggles it off. A fnv_camera set made by another client at the same time is also undone by the photo's restore.
- Fix idea: one module lock (threading.RLock) held across set/orbit, capture and restore in photo/camera/pov, and the HUD hidden by setting state rather than toggling (see the next entry).
- Resolution (fixed): One module RLock held across camera set/orbit/pov/capture/restore.

### E1. fnv_debug freeze cannot be thawed: the game stays suspended for good

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_debugger.py:172 (run calls self.live.ready() first), fnv_debugger.py:1108-1120 (freeze/thaw)
- Severity: high
- Found as: verified
- Checked: read Debugger.run (live.ready() pings first) and freeze (suspend_all from outside, pipe thread included)
- What happens: freeze calls GameProcess.suspend_all() with no keep list, so it also suspends the companion's pipe thread and main thread. The next fnv_debug call (thaw, or status) goes through Debugger.run, which calls live.ready() -> link.ping(timeout=3). Nobody can answer, so the call fails with COMPANION_TIMEOUT before the thaw handler runs. thaw is never reached, the dead-man switch cannot fire (its thread is suspended too), and the only way out is to kill the game. Unit tests pass only because the fake companion answers the ping.
- Fix idea: dispatch freeze/thaw/threads/dump before live.ready() (they work from outside the process); in freeze, keep the pipe thread or refuse while nothing can thaw.
- Resolution (fixed, verified live): freeze/thaw/threads/dump run before ready() from outside the process; the companion ping reports pipeThread and freeze keeps it; frozen ids are in a state file. Live: freeze of 94 threads kept the pipe thread, thaw brought the game back.

### E2. `return` from a hook-hold stop skips with pop=0: stack corruption for callee-cleans functions

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_debugger.py:491 (`hookact <id> hold <pred>` sent without the pop argument), fnv_debugger.py:1041-1044 (`resume skip`)
- Severity: high
- Found as: likely (likely (companion read: w2_stop.c:250-268 takes an optional `[pop]`, stores it in act_aux; hook_stop passes act_aux to stop_park; the skip stub pops s->pop))
- What happens: a function-entry break without `hits` becomes a hook hold. `fnv_debug return 0` from that stop sends `resume <seq> skip 0`, and the skip stub returns with `ret 0`. For __thiscall/__stdcall functions with stack arguments (most engine methods) the caller expects the callee to pop them. ESP is off by 4*nargs. The caller's `pop edi/esi/ebx` before `mov esp,ebp` then load the leftover arguments into callee-saved registers, so the caller's caller crashes or misbehaves.
- Fix idea: pass `word(self._pop_bytes-equivalent for the function)` as the 5th hookact argument (0 for cdecl), or refuse `return` on a hook stop when the pop is unknown.
- Resolution (fixed, verified live): A hook hold sends the function's pop bytes; return on a hook stop refuses POP_UNKNOWN when unknown. Live: a break on BaseExtraList::GetExtraData (thiscall, pops 4) stopped the main thread; return 0 skipped the call with the right pop and the game ran on.

### E3. `return` mid-function uses `resume ret` when the function is not in the symbol database

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_debugger.py:1046-1077
- Severity: high
- Found as: likely
- What happens: `fn = db.function_containing(eip) if db else None`. The epilogue path runs only when fn is set and eip is not its start. With no symbol database, or a function the database does not have (no debug info), every stop falls through to `resume ret eax pop`. The companion then sets esp += 4 + pop and eip = [esp], which is only valid at the first instruction. In the middle of a function, [esp] is a local or a saved register, so execution jumps to garbage and the game crashes. `_pop_bytes` also returns 0 for an unknown function, so even at the first instruction the stack is wrong for stdcall/thiscall.
- Fix idea: take the function start from engine.containing(eip). Use `ret` only when eip is that start, and otherwise refuse with NO_EPILOGUE.
- Resolution (fixed): return uses ret only at the function start; mid-function without the symbol db gives NO_EPILOGUE; inside the prologue FRAME_NOT_SET_UP.

### E4. fnv_flow trace leaves the thread parked at a stop on refusal, timeout or read error, and _release resumes other tools' stops

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_flow.py:199-204, 202-204 (_read_trace timeout or TRACE_FAILED), 335-339 (_release)
- Severity: high
- Found as: likely
- What happens: (1) After the one-shot bp hits, the thread (normally the main thread) is parked as a `bp` stop. If `resume <seq> trace ...` is refused (COMPANION_REFUSED: old companion, bad log program, a race) the tool raises and never resumes that stop. The game stays frozen at the bp stop. Every later request counts as dead-man contact, so the 120 s switch does not fire while other tools are in use. (2) On reason 'timeout', or when `trace read` returns no entries (TRACE_FAILED), the thread later parks as a `step` stop and nobody resumes it. (3) `_release` resumes every stop of kind `step`, not only the one this trace ended in. A user mid-way through fnv_debug stepping (a `step` stop on another thread or seq) has that stop resumed out from under them.
- Fix idea: wrap everything after _await_bp in try/finally that resumes `stop['seq']` (and the end stop) with run, and in _release resume only `end['seq']`.
- Resolution (fixed): flow trace releases its stops on refusal, timeout or read errors, and resumes only its own end stop.

### E5. fnv_script_debug continue hangs forever while a variable `when` stays false

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_scriptdbg.py:374-382 (_settle loop), fnv_scriptdbg.py:629 (continue_ calls _settle(new, wait=True))
- Severity: high
- Found as: likely
- What happens: a break with script variables in `when` (for example script=X, when="HelloBugLevel > 50") holds on every statement of X, and the bridge checks the variables. In continue_, `_settle(..., wait=True)` loops with no count or total deadline: resume run, then wait up to 10 s for the next held statement, then check again. A script that runs every frame keeps producing stops whose condition is false, so the tool call never returns. A division by zero in the condition raises a raw ZeroDivisionError from eval_var_when (checked offline: `a / b > 1` with b=0) and leaves the stop parked.
- Fix idea: bound _settle by STEP_LIMIT and an overall deadline, then return "running, N stops refused". Catch arithmetic errors in eval_var_when as false.
- Resolution (fixed): script continue gives up after a step limit or 30 s of refused stops and reports running.

### E6. Pause, script freeze and thread freeze are lost on a server restart: the game is left paused or off with no way back

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_time.py:105,117,183-187 (paused_by_us), fnv_scriptdbg.py:173,646-648 (saved_scripts), fnv_debugger.py:161,1116-1120 (frozen)
- Severity: high
- Found as: likely
- What happens: the supervisor restarts the server on code changes, and each of these flags lives only in Python memory. After a restart: fnv_time_control resume answers "not paused by this tool" and leaves the BSTimer disable counter incremented, so the game clock stays stopped. fnv_script_debug thaw answers "Scripts were not frozen by the debugger" and leaves the process-scripts byte at 0, so no script runs again. fnv_debug status reports "running" for a game whose threads are suspended, and thaw resumes nothing (self.frozen is []). The same happens after a pid change for ScriptDebugger.saved_scripts, which is never reset.
- Fix idea: persist these flags with pid and start time in a small state file, or derive them from the game (clock counter, byte value, thread suspend counts), and offer a force resume/thaw.
- Resolution (fixed): Pause, script freeze, thread freeze and breaks persist in state/debug_state.json keyed by pid+start; fnv_time_control force_resume.

### F1. fnv_bisect level=plugins never finds a candidate on an MO2 New Vegas profile

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_suite.py:44-46
- Severity: high
- Found as: verified
- Checked: read fnv_suite.py:44-46 and the Dev profile plugins.txt (no * lines)
- What happens: `_candidates` keeps only plugins.txt lines that start with `*`. MO2 writes FNV plugins.txt with no asterisk (C:/Modding/FNV-Dev/profiles/Dev/plugins.txt is `# header`, `FalloutNV.esm`, `HelloWasteland.esp`, ...). fnv_data.py:75 and fnv_records.profile_set already strip `*` as optional. So every `fnv_bisect level=plugins` call raises NO_CANDIDATES ("No plugins to bisect").
- Fix idea: treat every non-comment, non-blank line as active and use `l.lstrip('*')`, the same way fnv_data.load_order does.
- Resolution (fixed): plugins.txt read in both styles (MO2 New Vegas lines without *, and the * style).

### F2. fnv_bisect level=records reuses a different plugin's cached bytes and later writes them back

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_suite.py:82-83, 113-117, 128-130
- Severity: high
- Found as: confirmed (confirmed (stub run: a records bisect on A.esp that ends in NO_CANDIDATES leaves `self._original = (A.esp, bytes)` on the shared instance))
- What happens: `_record_candidates` caches `(path, bytes)` in `self._original` and fills it only `if not hasattr(self, '_original')`. Two exits leave the cache in place. NO_CANDIDATES is raised before the `try/finally` (line 128-130). A failed restore (line 116 raises, the exception is swallowed at 168) never reaches `del self._original`. BisectTools is one instance per server (fnv_live_tools.py:292), so the next `fnv_bisect level=records plugin=B.esp` builds its candidates from A.esp, writes variants into A.esp's path, names an A record as B's culprit, and in `finally` writes the old A bytes back over A.esp. If A.esp was rebuilt in the meantime, the newer file is lost. Two parallel bisect calls also share `self.plugin` and `self._original`.
- Fix idea: keep `original` in a local variable for the call (or a dict keyed by path), and always clear it in a `finally` that wraps the whole method.
- Resolution (fixed): Original bytes kept per call and path, cleared in finally; parallel bisects refused.

### F3. Records bisect edits the user's plugin in place with no backup on disk, and restore failures are swallowed

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_suite.py:111, 116, 160-169
- Severity: high
- Found as: likely
- What happens: each round runs `path.write_bytes(codec.write(plugin))`, which overwrites the real plugin in its MO2 mod folder and is not atomic. The only copy of the original is in memory (`self._original`). A bisect runs for many minutes and relaunches the game each round. If the MCP server dies (client restart or disconnect) or the write is interrupted, the plugin stays as a half-records variant with nothing to restore from. The `finally` restore runs under `except Exception: pass`. So if `quit_game()` raises (QUIT_FAILED) or the write fails, the verdict still returns normally while the plugin, or for mods/plugins the MO2 profile with half the mods disabled, stays changed. Nothing in the verdict says so.
- Fix idea: copy the original to state/ (with its sha) and write variants via temp+rename before round 0. On a restore failure, add a `restoreFailed` field to the verdict (or raise) instead of passing silently.
- Resolution (fixed): Original backed up to state/fnv/bisect with its sha256 before round 0; variants and restore via temp file + os.replace; restore failures reported (restoreFailed).

### F4. fnv_crawl cells never visits an exterior cell

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_crawl.py:91-96
- Severity: high
- Found as: verified
- Checked: grep: form_edid and wait_loaded are defined nowhere
- What happens: for every exterior cell, `hasattr(self.live, 'form_edid')` is false, so `world` is None. The code then raises NO_WORLD ("Cannot name the worldspace..."). Each exterior row gets `error` and 50 problem points and is never visited. A worldspace mod is reported as broken in every exterior cell. Even with a lookup, `cell['world']` is the raw file form id (master index of the plugin file, not the load order), so a plugin-added worldspace would resolve to the wrong form.
- Fix idea: resolve the WRLD EditorID from the plugin's own records (codec) or the data index after mapping the file form id to load order, and use the existing waiter in place of `wait_loaded`.
- Resolution (fixed, verified live): Exterior cells: worldspace from the group label, mapped through the plugin masters; wait via the time control. Live: with a one-cell test plugin (HelloExterior.esp, an identical override of a WastelandNV cell) the crawl went from an interior cell to the exterior cell and checked it.

### S1. fnv_record_find failed on any worker thread but the first (shared SQLite connection)

- Area: Stress tests and live use
- Where: geck_mcp/fnv_data.py RecordIndex
- Severity: high
- Found as: fixed (uncommitted)
- What happens: Parallel or later calls landing on another worker thread failed with "SQLite objects created in a thread can only be used in that same thread". Found while building the robot.
- Fix idea: Done: each thread opens its own connection (RecordIndex.db property), test tests/test_record_index_threads.py. Not complete: see C2 and C3 (no lock or rollback in ensure).
- Resolution (fixed, verified live): Thread-local SQLite connections (RecordIndex.db), completed by C2/C3.

## Medium

### A4. Parallel GECK tool calls fail at once with EDITOR_BUSY, reported as BRIDGE_ERROR and not retry-safe

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/service.py:372 (`with bottle_lock(...)` in `run`), 459-469 (`failure`); bridge_lock.py:38-40 (`msvcrt.locking(..., LK_NBLCK, 1)`)
- Severity: medium
- Found as: confirmed (confirmed (a second thread got `RuntimeError('EDITOR_BUSY: ...')` immediately))
- What happens: `bottle_lock` does not wait, and it is a lock on a file region that also blocks other threads of the same process. Every `Service.run` takes it with the same lock_root, including read-only calls (geck_plugin_validate, geck_spec_validate, geck_plugin_inspect, geck_master_lookup, disk geck_record_read). - So two GECK tools sent in parallel (say geck_status and geck_plugin_inspect): one fails right away. - `failure()` turns the RuntimeError into code `BRIDGE_ERROR` with `retrySafe: False` and "Read status and operation evidence before retrying". No operation row was written, so geck_operation_get on that operationId says "not found".
- Fix idea: wrap bottle_lock in a per-process `threading.Lock` per lock_root, waited on with a timeout. Map EDITOR_BUSY to its own code with retrySafe true. Let read-only calls skip the lock.
- Resolution (fixed): service.process_lock: threads of one process queue for the editor lock (up to 120 s) before the bottle lock; EDITOR_BUSY is its own code with retrySafe true. ServiceLockTests.

### A5. Supervisor never clears a cancelled request from `in_flight`, so hot reload stops for the life of that child

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/supervisor.py:354-358 (only listen ids are handled on `notifications/cancelled`), 419-422 (`busy` check in `watch_loop`)
- Severity: medium
- Found as: verified
- Checked: read supervisor.from_client: notifications/cancelled for a call is forwarded but in_flight keeps the id, and the SDK never answers a cancelled call
- What happens: the client cancels a tool call (the user presses stop, or the client times out). The cancel is passed to the child, the child never answers, and `in_flight[id]` is never popped. From then on `busy` is always true, so code edits are never reloaded until the child exits. - When the child does exit, `child_exited` sends an error response for an id the client already cancelled.
- Fix idea: on `notifications/cancelled`, pop `in_flight[json.dumps(requestId)]` before forwarding it.
- Resolution (fixed): supervisor: notifications/cancelled pops the call from in_flight. SupervisorTests.test_cancel_clears_the_call.

### A6. A concurrent geck_runtime_save_context overwrites the editor operation's state on the shared Service

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/service.py:199-202 (`self.run(...)` on the shared instance with another lock_root), 430-447 (`_mutation_dispatched`, `_active_operation`, `_step_sequence` stored on `self`)
- Severity: medium
- Found as: confirmed (confirmed (simulation: an editor op that set `_mutation_dispatched=True` and then failed was reported `failed_before_change`; its `_active_operation` became None in the middle of the call))
- What happens: server.py uses one `Service` for every geck_* tool. `runtime_save_context` runs under `game_root/.geck-runtime-lock`, so it is not serialized with editor work (`runtime_call` uses a separate instance for this reason; save_context does not). If it runs during geck_plugin_save, geck_plugin_load or geck_record_set_model: - The mutation flag is reset, so a failure after the save or load was sent is journaled as `failed_before_change`. No recovery barrier is set. - Later helper steps are not journaled. - `load_plugin` can write a reservation with the wrong or None operationId.
- Fix idea: run `runtime_save_context` on `Service(self.project, transport=self.transport)` as `runtime_call` does, or keep the run context in a thread-local.
- Resolution (fixed): runtime_save_context runs on its own Service instance, as runtime_call does.

### A7. undo / revert_all lose track of undos already applied when one entry fails

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/fnv_live.py:1295-1296, 1305-1306
- Severity: medium
- Found as: likely
- What happens: `results = [self._apply_undo(e) for e in ...]` runs before `mark_undone`. If entry k raises (COMPANION_TIMEOUT, a resolve error other than REF_NOT_FOUND, a script error), entries 0..k-1 have already changed the game but are never marked undone. A retry applies them again; for add_item, the undo `RemoveItem X n` then removes the items twice.
- Fix idea: mark each entry undone right after its undo succeeds (or catch per entry, record the error, and go on).
- Resolution (fixed): undo/revert_all mark each entry undone as soon as its undo lands; a failing entry is reported with its error and the rest still run. test_a_failing_undo_does_not_lose_the_ones_already_applied.

### A8. `fnv_session end` fails when the game is gone, and the session can then not be closed or restarted

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/fnv_session.py:116-118 (`HealthTools(self.live).check()` first, no try), 183 (`link.events`), 213, 220
- Severity: medium
- Found as: likely
- What happens: the game crashed or was quit during the session, and you call `fnv_session end`. `check()` calls `link.events`, `snapshot` and `_refs`, which raise LinkError COMPANION_UNREACHABLE. - `end` then aborts before the revert, release and state reset, which are guarded for exactly this case, so `session_state` stays set. - `fnv_session start` keeps refusing with SESSION_OPEN until the game is launched again or the server restarts.
- Fix idea: run the health check in a try (report its error in the result) and always reach `self.live.session_state = None`.
- Resolution (fixed): session end: the health check failure is reported in the result, and the session is closed in a finally.

### B2. Watch hooks and native-module hooks are written into live code with other threads running

- Area: Native companion (C) and code injection
- Where: bridge/native/engine/fnvmcp_engine.c:787 (op_hook), :807 (op_unhook); w5_mod.c:151 (hook_remove); also w5_code.c:180-182 (`code patch`, used for fnv_code swap/unswap), which suspends threads but never checks their EIPs
- Severity: medium
- Found as: likely
- What happens: code_write memcpy's 5-16 bytes (jmp + NOPs) over a function prologue while other threads keep running. A thread preempted after the first prologue instruction (e.g. at target+1 after `push ebp`) resumes in the middle of the new rel32 or the NOPs. It then runs garbage, or skips part of the moved instructions, and the game crashes. A thread at `target` can also see a half-written jmp. probe arm and inpcfg already suspend all threads and check probe_thread_inside(); hook, unhook, hook_remove (mod unload) and `code patch` do not. `code patch` suspends but writes even when a suspended EIP is inside the patched range. That matters for fnv_code swap's 5-byte entry jmp on hot, multi-thread functions.
- Fix idea: write through probe_suspend(), refuse "busy" when probe_thread_inside(target, target+len) (as inpcfg does), then probe_resume().
- Resolution (fixed): code_write_frozen: hook, unhook, mod hook removal and code patch write with other threads suspended and refuse "busy" when a thread is inside the bytes. Engine suites pass.

### B3. fnv_code shadow replays fastcall functions with every argument shifted

- Area: Native companion (C) and code injection
- Where: bridge/geck_mcp/fnv_code.py:530-541 (_shadow) together with fnv_live.engine_call's fastcall mapping (fnv_live.py:963-966)
- Severity: medium
- Found as: likely (likely (from reading both sides))
- What happens: for conv == 'fastcall', _shadow passes this=r[3] (ecx) and args=r[12:] (the stack words). engine_call then builds regs=[this]+words, so it sends ecx=r[3], edx=stack word 0 and the stack words from 1 on. The recorded edx (r[4]) is never used, and every later argument is off by one slot. So each replayed call of the original and the new code gets wrong inputs. The result is false "differ" rows, false agreement, or faults. This is the same family as the SetFilter bug.
- Fix idea: for fastcall, call with convention 'cdecl'-style raw registers: ecx=r[3], edx=r[4], stack=r[12:]. Or pass this=None and args=[r[3], r[4]] + stack.
- Resolution (fixed): shadow replays fastcall with ecx=r[3], edx=r[4] and the stack words after them.

### B4. fnv_code swap/compare reuse a blob placed from an older build

- Area: Native companion (C) and code injection
- Where: bridge/geck_mcp/fnv_code.py:388 (`self.placed.pop(address, None) or self._place(...)`), :492-495 (compare), :215-239 (build never clears placed)
- Severity: medium
- Found as: likely
- What happens: if you run compare, then edit, then build, then compare again, the second compare runs the first build's code. A swap after that redirects the game to the old code, and the result shows the new diff. The user sees "swapped" while the game runs code that does not match the source shown.
- Fix idea: key `placed` by the link hash (or drop placed[address] in build()) and re-place when link.json changes.
- Resolution (fixed): build() drops the blob placed from the previous build.

### B5. unswap forgets the swap before restoring it; failures leave the game patched with no record

- Area: Native companion (C) and code injection
- Where: bridge/geck_mcp/fnv_code.py:469-476
- Severity: medium
- Found as: likely
- What happens: it runs swaps.pop() and _save_swaps() first, and only then patches back the call sites and the entry. Site restores ignore `ok`. If the entry restore fails (WRITE_FAILED raised) or the pipe drops mid-loop, the game stays redirected, but swaps.json no longer lists the swap. Neither `unswap` nor `list` can see or undo it. _redirect_sites (:418-452) has the same problem: sites already patched are lost if a later call raises.
- Fix idea: restore first and check every `ok`. Remove the record only when everything is back; on partial failure keep the remaining sites in the record.
- Resolution (fixed): unswap restores first, checks every ok, forgets only what came back; _redirect_sites puts back already patched sites when a later one fails. UnswapTests.

### B6. Coverage states are not moved with the addresses when `cov add` re-sorts the table

- Area: Native companion (C) and code injection
- Where: bridge/native/engine/w6_cov.c:241-263
- Severity: medium
- Found as: confirmed (confirmed (cov_sort.c: add hi, arm, hit hi, disarm, add lo. The table shows lo=state 2, hi=state 0. After arm, lo is never trapped and hi is trapped again.))
- What happens: qsort and the de-dup loop permute cov_addr only. cov_state (and cov_orig) keep their old indexes. After any arm/hit cycle, a later `cov add` without `cov reset` marks the wrong lines as hit. Never-run lines show as covered, and covered lines are armed again. Coverage results are wrong.
- Fix idea: sort index records {addr, state, orig} together (or forbid add once anything was armed until reset).
- Resolution (fixed): cov add sorts {addr, state, orig} rows together.

### B7. `call` reads argv[2] before checking argc

- Area: Native companion (C) and code injection
- Where: bridge/native/engine/fnvmcp_engine.c:1817
- Severity: medium
- Found as: likely
- What happens: `bool guarded = strlen(argv[2]) == 2 && ...` runs before `argc < 5`. A request op of just "call" or "call 401000" calls strlen on an uninitialized stack slot (serve_op's argv[40] is not zeroed). The main thread faults inside a served request, so the game crashes.
- Fix idea: move the argc check first (or zero-initialize argv in serve_op).
- Resolution (fixed): call checks argc before reading argv[2].

### B8. Exported loader for a function shorter than 5 bytes overwrites the next function

- Area: Native companion (C) and code injection
- Where: bridge/geck_mcp/fnv_code.py:574-651 (export / _loader_source; the jmp is written at :642-647)
- Severity: medium
- Found as: likely
- What happens: swap() handles spec['size'] < 5 through call sites and vtable slots (:390). export() and the generated loader always write a 5-byte E9 at TARGET. For a 1-4 byte function (e.g. `ret` / `xor eax,eax; ret`), the jump overwrites the first bytes of the following function. The `expect` check covers only the function's own bytes, so it does not catch this. Loading the module (or the plugin at start) then corrupts an unrelated function.
- Fix idea: refuse export when expectedSize < 5 (or emit the site-redirect form).
- Resolution (fixed): export refuses functions shorter than 5 bytes (TOO_SHORT). test_export_refuses_functions_shorter_than_a_jump.

### B9. A guarded call that faults leaves fs:[0] pointing into the abandoned stack

- Area: Native companion (C) and code injection
- Where: bridge/native/engine/w1_guard.c:78-90 (on_guard), with the resume label in fnvmcp_invoke_guarded (:50-55)
- Severity: medium
- Found as: possible
- What happens: MSVC engine functions with C++ objects or __try push SEH records and link them into fs:[0]. on_guard moves esp and ebp back to the invoke frame but never restores fs:[0]. So the SEH chain head points below esp until some enclosing frame's epilogue restores it. That window includes the rest of the request, and every request served from gate_park or stop_serve_wait while the gate is held. Any exception in that window that reaches SEH dispatch walks overwritten records: a C++ throw, or a fault not handled by VEH. The result is a crash (STATUS_INVALID_EXCEPTION_HANDLER, or an unhandled exception).
- Fix idea: save fs:[0] in GuardFrame at invoke entry and write it back (e.g. at label 1, or in on_guard via __writefsdword) on the fault path.
- Resolution (fixed, verified live): The guarded invoke links its own SEH record and the resume label restores fs:[0] to its value at entry. Live: two faulting guarded calls returned CALL_FAULTED and the game kept serving.

### B10. flow-call module cannot call fastcall functions (edx = this)

- Area: Native companion (C) and code injection
- Where: bridge/native/modules/fnvmcp_flowcall.c:29 (`movl %edx, %ecx`), with the slot layout at :10-12 and fnv_flow._arm_call
- Severity: medium
- Found as: likely
- What happens: the slot has only `self` (ecx). edx keeps the same value as ecx, and fnv_flow `call` takes no convention. For a fastcall target, the second register argument is the `this` value instead of the caller's argument, and all stack arguments are those after it. The call runs with wrong inputs, and the trace then shows the wrong path. This is the same family as the SetFilter bug.
- Fix idea: add an `edx` word to FlowSlot (and a convention to fnv_flow call that maps fastcall like engine_call does).
- Resolution (fixed): Flow-call slot version 2 has an edx word; fnv_flow maps fastcall like engine_call; an old loaded module is unloaded and a stale build rebuilt. FlowFastcallTests.

### C4. fnv_record_users crashes ("too many SQL variables") on popular records

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/fnv_records.py:234-236
- Severity: medium
- Found as: confirmed (confirmed (t3.py on a copy of the live cache: `record_users('falloutnv.esm:01C487')` (WastelandShrub01, 18,650 referencing records) -> `OperationalError: too many SQL variables`))
- What happens: the EditorID/name lookup binds 2 parameters per user in one `IN ((?, ?), ...)`; above 16,383 users this goes past SQLite's 32,766 limit. Common bases (trees, rocks, clutter, caps in a large load order) fail completely instead of returning a page.
- Fix idea: look names up only for the page being returned (`out[offset:offset+limit]`), or in chunks of up to 500 pairs, or join refs with records in SQL.
- Resolution (fixed): record_users looks names up in chunks of 500 pairs.

### C5. record_users misses references held in subrecords listed in STRING_TAGS (e.g. NPC_ RNAM race)

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/fnv_records.py:96-98 (scan_references skips every tag in fnv_data.STRING_TAGS, whatever the record type)
- Severity: medium
- Found as: confirmed (confirmed (t10.py: `record_users('Caucasian')` -> 0 users, but NPC_ records hold the race in RNAM (DocMitchell RNAM -> CaucasianOld 000042C3, which record_show decodes as a form)))
- What happens: STRING_TAGS has tags that are strings in some record types and form ids in others (RNAM, TNAM, ONAM, NNAM...). The ref scanner never looks at them, so "what uses this race / this form" answers "nothing" for those links and gives a false sense of safety before an edit.
- Fix idea: make the string skip depend on the record type (e.g. {(rtype, tag)}), or apply the same printable-text check decode_subrecord uses before skipping a 4-byte field.
- Resolution (fixed): Text-or-form tags (RNAM, TNAM, ONAM, NNAM...) are skipped only when the field reads as text; REFS_VERSION 3 rebuilds the refs index.

### C6. DLC override records resolve to a form id that does not exist (builder Resolver)

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/esp/formids.py:194-209 (Resolver.resolve -> _from_master); the same wrong mapping in esp/validate.py:105-107
- Severity: medium (high once a DLC index is used; today only FalloutNV.esm is indexed)
- Found as: confirmed (confirmed (t8/t9.py: built a GunRunnersArsenal.esm index in the scratchpad; 38 EditorIDs exist in both indexes; with masters [FalloutNV.esm, GunRunnersArsenal.esm], `@VLootSackVendorWastelandT2RS` resolves to 01167ACD instead of 00167ACD))
- What happens: an index of a DLC master holds its overrides of FalloutNV.esm records (raw top byte 00). The resolver picks the latest master and always uses that master's position as the top byte, ignoring the raw form id's own master byte. So the built plugin points at a form that does not exist (a broken reference, missing base object), and validate checks against the same wrong key, so it passes.
- Fix idea: map the hit through that master's own MAST list (raw top byte < master count means a record of that master's master), or drop index rows whose top byte is not the master's own index.
- Resolution (fixed): MasterIndex leaves out rows whose top byte is not the master's own (its overrides of an earlier master); the original is found through its own master. test_master_index_leaves_out_overrides.

### C7. fnv_script_lint keeps the first load order and file ids forever

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/fnv_data.py:565-571 (`_edid_lookup` caches `_lint_entries` on the long-lived DataTools)
- Severity: medium
- Found as: likely (likely (from reading; the DataTools in fnv_live_tools.register lives for the whole server))
- What happens: the first lint call stores the entries with their fileIds and never runs `_active()` again. After a plugin is rebuilt (its file id changes unless it is the highest id), the plugin is added or removed, or the load order changes, lookups use dead ids: EditorIDs from the user's own plugin are reported as UNKNOWN_EDITOR_ID / UNKNOWN_NAME, and new records are never indexed for lint. The cache is also written without a lock.
- Fix idea: call `self._active()` once per `script_lint` call and pass the entries to the lookup (a closure), not an attribute.
- Resolution (fixed): script_lint reads the load order once per call and passes it to the lookup.

### C8. Save namer maps quest form ids through the current load order, not the save's

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/fnv_save.py:361-371 (Namer.variables -> data.record_show('%08X' % form))
- Severity: medium
- Found as: likely
- What happens: change-form ids in a save carry the save's own plugin index (Namer.edid maps them correctly through `save.plugins`). `variables()` passes the raw id to `record_show`, which resolves the top byte through the current MO2 load order. When the active plugin list differs from the one the save was made with (a plugin added or removed since), quest script variables in fnv_save_inspect/diff are named from a different quest, or not at all, with no error.
- Fix idea: build the ref as `'%s:%06X' % (save.plugins[form >> 24], form & 0xFFFFFF)` (the plugin:local form that record_show already accepts).
- Resolution (fixed): Namer.variables maps the quest through the save's own plugin list (plugin:local).

### D4. hide_hud toggles `tm` without reading the state: an already hidden HUD is shown for the photo

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_eyes.py:696-705
- Severity: medium
- Found as: likely
- What happens: `_hud_off` sends `tm` before the capture and `tm` after. If the menus were already hidden (the user ran tm, or `fnv_render toggle menus`), the first `tm` SHOWS the HUD, so every photo includes it, and the second hides it again. The flag address is known (fnv_render.py:26 TOGGLES['menus'] = 0x0118C6F0), but eyes does not read it.
- Fix idea: read byte 0x0118C6F0 first and toggle only when the menus are visible. Restore only if this call toggled.
- Resolution (fixed): HUD state is read first; a hidden HUD is left alone.

### D5. world_edit enable/disable journals an undo that does not depend on the state before

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_world.py:364-369
- Severity: medium
- Found as: likely
- What happens: `disable` on a reference that was already disabled (for example a quest object kept off by its enable parent) journals `Enable`. fnv_undo / fnv_revert_all then enables it: the game ends in a state it was never in. The same is true for `enable` on an enabled reference, whose undo journals `Disable` and so removes a visible object. Before the call, `raw` holds the flags (+0x08, bit 0x800), but the code does not check them.
- Fix idea: read flags & 0x800 before the call. Journal the opposite only when the state changed, and journal nothing otherwise.
- Resolution (fixed): enable/disable read the flag first and journal the opposite only when it changed.

### D6. world_edit weather: the undo loses the original override and has no pid

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_world.py:417-424
- Severity: medium
- Found as: likely (likely (Sky::ForceWeather 0x0063D0E0 checked in the decomp))
- What happens: the undo is `ForceWeather <old current>` with no override flag. In ForceWeather, b=false clears m_pOverrideWeather. So if a quest or script override was active before (sky+0x1C non-zero), undo removes it. If there was no current weather, nothing is journaled, so the override set by this call cannot be undone. The weather and imagespace entries (line 436) also have no `pid`, so revert_all applies them in a later game process.
- Fix idea: record both +0x10 and +0x1C. Undo with ForceWeather <override> 1 when there was one, else ReleaseWeatherOverride (+ ForceWeather current). Add the pid.
- Resolution (fixed): Weather undo restores the previous override (or releases it); pid via the journal stamp.

### D7. fnv_asset reload leaves references disabled when the purge fails

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_create.py:177-193 (_refresh)
- Severity: medium
- Found as: likely
- What happens: `_refresh` disables every user of the model, then `_purge()` can raise NO_MODEL_LOADER / CALL_FAULTED / CALL_REJECTED. In that case the `Enable` loop never runs, so all those references stay disabled. No journal entry exists to undo it (the asset_reload entry is added only after success, with undo None). A `.dds` reload may cover dozens of refs, and the same failure hides all of them.
- Fix idea: wrap the purge in try/finally and always re-enable `live_refs`. Optionally journal each Disable with an Enable undo first.
- Resolution (fixed): Asset reload re-enables references in a finally.

### D8. Save backup runs on every change, or never, depending on a pid written only by fnv_launch

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_game.py:693-710 (backup_saves), 233-243 (session cache)
- Severity: medium
- Found as: likely
- What happens: the guard is `savesBackedUpFor == session.pid and session.pid`. session.pid is written only by `_launch` and cleared by `quit`. - Game started outside the bridge (MO2, or after fnv_quit): pid is None, so every act tool call (`live.changing`) copies the whole saves folder. After 10 calls the rotation `backups[:-10]` deletes the one backup made before the session changed anything. - Game crashed and was restarted outside the bridge: the stale pid still equals savesBackedUpFor, so no backup is made for the new process. - `session()` is cached in memory, so a second bridge process (another client session on the same state dir) keeps a stale dict. Its `save_session` writes that stale dict back over the first process's pid/launchedAt/ready.
- Fix idea: key the backup on the live process pid (`self.process()[0]`), not on session.json, and reread session.json before each write.
- Resolution (fixed, verified live): Save backup keys on the live process pid; session.json is re-read before writing. Live: the first change in a new game backed up the saves once (savesBackedUpFor = the new pid); a second change made no new backup.

### D9. geck_actor_photo is macOS-only but runs on Windows: it loads the studio plugin, then always fails

- Area: World editing, creation, camera and photos
- Where: geck_mcp/photo.py:199, 218, 240 -> imaging.py:24-45, 62-70; exposed unconditionally at server.py:435-453
- Severity: medium
- Found as: likely
- What happens: on Windows `_capture` runs after the build, install and GECK load of GeckPhotoStudio.esp (FalloutNV.esm only, so the user's own plugin is no longer loaded in GECK). It then calls `imaging.render_window` -> `_lister_command`, which needs swiftc/swift: NO_SWIFT. Even with swift present, `capture_window` runs `/usr/sbin/screencapture`. So every call on this machine changes GECK's loaded files and then fails. service.render_capture already branches on os.name to wincapture; photo.py does not.
- Fix idea: on os.name == 'nt' use wincapture.render_window()/capture() (with the client size from GetClientRect, no title-bar crop), or refuse before the build/install/load.
- Resolution (fixed): geck_actor_photo refuses with UNSUPPORTED_PLATFORM before loading anything on Windows.

### D10. Screenshot and contact-sheet file names collide (one-second stamps, fixed default names)

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_eyes.py:756, 787, 807, 819, 845 (names passed to agent.screenshot, which adds %Y%m%d-%H%M%S); photo.py:216-225
- Severity: medium
- Found as: likely
- What happens: two fnv_photo calls in the same second (parallel calls, or two quick calls without `name`) produce the same `photo-front-<stamp>.png` and `photo-sheet-<epoch>.png`. The engine capture first `unlink`s the path, then waits for the BMP, so one call can delete the other's file mid-capture (ENGINE_CAPTURE_FAILED), or one sheet shows the other call's images. Custom angles that differ only in fractions (e.g. [10.2,5] and [10.7,5]) get the same tag `yaw10_pitch5` inside one call. `_photo_compare` with the default name can overwrite a baseline named `compare-<same second>`. The GECK actor photos use `<EditorID>-%H%M%S`, and `work/fit-N.png` / `final-raw.png` are shared paths.
- Fix idea: add milliseconds plus a short uuid (or a per-call counter) to every capture and sheet name.
- Resolution (fixed): Photo, sheet, GECK actor photo and work-file names get a per-call token.

### E7. Breaks from a previous server process are orphaned and cannot be cleared, and their pred slots get reused

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_debugger.py:396-397 (pred slot choice from self.breaks only), 532-547 (clear only knows self.breaks), 199-200 (reset on pid change only); fnv_scriptdbg.py:81-83,347 (EvalHook.hook_id/holding)
- Severity: medium
- Found as: likely
- What happens: after a server restart, `bp` breakpoints, `dbg N` hook holds and `faulthold on` stay armed in the companion. Debugger.breaks is empty, so `break list` shows nothing and `break clear` clears nothing. bp ids can only be cleared by a bp clear that no tool sends, so these traps keep stopping the game. A new break with `when` takes pred slot 32 again and overwrites the condition that an orphan breakpoint still evaluates. In the script debugger, hook_id is None after a restart, so `_ours` is false for its own hold stops. status says "stopped elsewhere" and its breaks list is empty while the hold still parks the game. After a game restart (new pid) ScriptDebugger keeps `breaks` and a stale `hook_id`. `_ours` can then match a different tool's hook stop that got the same id, and its args are read as script/runner pointers.
- Fix idea: on first use per (pid, start), reconcile with `bp list`, `hooks` (labels `dbg `, `script_line_dbg`) and `pred list`. Add a `break clear all` that sends `bp clear ff`, `faulthold off` and unhooks those labels. Reset ScriptDebugger on pid change.
- Resolution (fixed): Breaks restored from the state file; break list shows orphans; break clear sweeps them.

### E8. Step out / frames / return are wrong inside a function prologue (writes an int3 into the stack)

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_debugger.py:992-996 (_return_address), 598-602 (frames, not-ready path), 1048-1068 (return_ epilogue path)
- Severity: medium
- Found as: likely (likely (companion `resume to` = isite_add int3 at any address >= 0x10000, w2_bp.c:486-495))
- What happens: `_frame_ready` is false for every offset below debug_start, but only offset 0 has the return address at [esp]. After `step instruction` past `push ebp`, [esp] holds the caller's ebp. `step out` then sends `resume to <that stack address>`, and the companion writes 0xCC into the caller's saved-ebp slot on the stack. The site is never executed, so the corruption stays, and the step reports STOP_TIMEOUT. frames() shows the saved ebp as the caller. `return` at offset 1..debug_start-1 takes the epilogue path (`mov esp,ebp`/pops with a half-built frame). That sets esp to the caller's ebp or pops garbage into ebx/esi/edi.
- Fix idea: in the prologue, decode the instructions run so far (pushes, mov ebp,esp, sub esp) to find the return slot, or refuse out/return until the frame is ready. Never send `to` for an address outside the exe's code.
- Resolution (fixed, verified live): Return address by prologue offset; non-standard prologue refuses; resume to refuses addresses outside the exe. Live: a stop after push ebp in GetExtraData gave the same caller (0x00418533) as at the entry, and step out landed there.

### E9. fnv_wait_for fails for long waits: `until` max is 0x2710, and its timeout is not enforced

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_time.py:342,354 (limit = frames + 2), 363-365 (seconds estimate unbounded), 410 (batch timeout = timeout + 5)
- Severity: medium
- Found as: confirmed (confirmed by reading (w1_gate.c:177-179 rejects max > GATE_FRAMES_MAX 0x2710 with bad_arguments))
- What happens: frames=9999 or 10000 (both pass the STEP_MAX check) gives limit 10001/10002. seconds=200 at 60 fps gives an estimate of 12000 frames, and a small multiplier inflates it further. The companion answers bad_arguments, reported as WAIT_FAILED "did not run the wait". Separately, timeout_seconds does not bound the wait: `until` runs up to `limit` frames, so a 3600-frame predicate wait at 30 fps takes 120 s. The link then times out at 65 s and reports COMPANION_TIMEOUT (outcome_unknown) with the pipe closed, instead of WAIT_TIMEOUT. The same applies to `loaded` rounds of 600 frames when only a few seconds remain.
- Fix idea: clamp limit to 0x2710 and loop in rounds (as `loaded` does) until frames, seconds or timeout_seconds is reached. Size each round from the measured fps and the time left.
- Resolution (fixed, verified live): Long waits run in rounds of at most 0x2710 frames and timeout is enforced; WAIT_TIMEOUT says whether time or frames ran out and names the right argument (timeout). Live: frames=10000 (170 s) and seconds=200 real (12,038 frames) both met, no link timeout.

### E10. Watch hooks silently remove other tools' hooks on the same function

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_deep.py:259-261 (ensure: "installed under another label: replace")
- Severity: medium
- Found as: likely
- What happens: fnv_script_trace (ensure('script_line')) removes the script debugger's `script_line_dbg` hook, and with it the hold, so every script break silently stops working. ScriptDebugger still lists the breaks and holding stays True. fnv_script_profile and fnv_crash_context (Script::Run...) remove a `dbg N` hook hold that fnv_debug set there. Debugger.breaks still lists it, and a later `break clear` unhooks by the old id, which can now belong to the new watch hook. After that, `fnv_script_debug break clear` raises HOOKED_ELSEWHERE from EvalHook.remove(), even though the breaks were already deleted from self.breaks.
- Fix idea: replace only hooks with a watch label (or none), and raise ALREADY_HOOKED for `dbg `/`script_line_dbg`/`act:`/`tp:` labels, as hook_at does.
- Resolution (fixed): A watch replaces only a watch hook; another tool's hook gives ALREADY_HOOKED.

### E11. fnv_script_profile stop keeps the baseline: the next read is empty and crash_context disable never unhooks

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_deep.py:400-421, 631
- Severity: medium
- Found as: likely
- What happens: `stop` removes the hooks but leaves `_baseline['profile']`. A later `read` re-installs fresh, empty hooks and skips the clear-and-sleep step because a baseline exists. It reads the tables right away and divides by the frames since the old baseline, so it returns almost no scripts and per-frame numbers that are far too low. crash_context(action='disable') removes the Script::Run hooks only when `_baseline.get('profile')` is falsy, so after any profile run the four table hooks stay installed for the rest of the session.
- Fix idea: pop `_baseline['profile']` on stop. In disable, check whether profiling is active, not whether a baseline ever existed.
- Resolution (fixed): profile stop drops the baseline; crash_context disable unhooks once no profile runs.

### E12. Parallel calls fight over bp ids and predicate slots

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_debugger.py:396-405 and 465-472 (_free_bp_id), fnv_flow.py:177-184, fnv_scriptdbg.py:59/125 (single slot 56), fnv_flow.py:38/171 (single slot 57)
- Severity: medium
- Found as: likely (likely (companion `bp set` on a used id drops the old one: w2_bp.c:596))
- What happens: there is no lock around Debugger/FlowTools (LiveService.run is lock-free, and tool calls run on worker threads). Two concurrent `fnv_debug break` calls, or a break plus `fnv_flow trace`, both read `bp list` and pick the same free id. The second `bp set` silently replaces the first breakpoint, and both records claim the id. Two `when` breaks pick the same pred slot, and the second overwrites the first condition. Two traces with `when` share slot 57. A ScriptDebugger.step rewriting slot 56 races a parallel break set.
- Fix idea: one threading.RLock per Debugger (shared by flow, blame and script debug) around id and slot allocation and the set.
- Resolution (fixed): One RLock per Debugger shared by flow, blame and script debug; predicate slot pool.

### E13. fnv_blame field and fnv_field_watch always use debug-register slot 0

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_blame.py:115-119, fnv_watch.py:110-113
- Severity: medium
- Found as: likely (likely (op_dbg: `if (dbg_bp_slots & (1u << slot)) fail("no_debug_register")`; bp set takes the first free DR: w2_bp.c:598))
- What happens: the first hardware fnv_debug break, or a fnv_flow trace on hw, takes DR0. After that, every blame field / field watch / fnv_memory refcount fails with no_debug_register while three registers are free. Blame says "the debug registers may be full (four in use)", which is wrong. Both tools also end with `dbg clear f`, which clears every watch slot, so a parallel watch loses its watchpoint.
- Fix idea: read `dbg info` and pick a free slot not in bpSlots; clear only that slot.
- Resolution (fixed): Field watch/blame take a free debug register and clear only theirs.

### E14. Debugger waits accept any newer stop as the result of a step

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_debugger.py:252-262 (_await), used by step/until/over/out and _step_line
- Severity: medium
- Found as: likely
- What happens: `_await(after_seq)` returns the newest stop with seq > after_seq on any thread and of any kind. A break with thread=any on a worker, a script-debugger hold, or a fault on another thread that parks during a step is reported as where the step ended. _step_line then keeps stepping that other thread's stop, and the original thread stays parked.
- Fix idea: match the new stop by thread (stop['thread']) and expected kind, and leave other stops listed.
- Resolution (fixed): Step waits match the new stop by thread.

### E15. A STOP_TIMEOUT leaves the temporary `to` site armed; recursion stops at the wrong frame

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_debugger.py:260-261, 981-990 (_run_to / _over), 1028-1035 (until), 963-964 (out)
- Severity: medium
- Found as: likely
- What happens: `until`, `step over` across a slow call (a load) or `step out` sends `resume to <addr>`. If the thread does not get there within 10 s, the tool reports STOP_TIMEOUT, but the int3 site stays armed. Later the game parks there with no tool waiting, and the main thread stays frozen while any tool keeps contacting the companion. `to <return address>` also has no esp check, so stepping over or out of a recursive call stops in the inner recursion at the same address.
- Fix idea: on timeout, report the pending `to` site (or remove it with a new op). Compare esp at the `to` stop and re-run when it is a deeper frame.
- Resolution (fixed, verified live): STOP_TIMEOUT names the pending to site; break clear removes temporary to sites (and reports how many: toSites); recursion compares esp; status lists sites still armed (kept across server restarts); until says when another stop came first. Live: until a never-called function timed out naming 0x008D0570, status listed it, break clear removed it (toSites 1, the original byte back).

### E16. Script-debugger `when` with a hex number is treated as a script-variable condition

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_scriptdbg.py:856-859 (var_names), 216-226
- Severity: medium
- Found as: confirmed (confirmed (offline: var_names('arg0.[0x0C]:u32 == 5') -> {'x0C'}; var_names('frame >= 0x10') -> {'x10'}))
- What happens: the identifier regex matches the `x0C` inside `0x0C`. Any predicate-language `when` with a hex literal or address is refused: "needs the script", or NO_SUCH_VARIABLE x0C.
- Fix idea: strip number tokens (`0[xX][0-9A-Fa-f]+|\d+(\.\d+)?`) before collecting names.
- Resolution (fixed): Number literals are not variable names in script when.

### E17. fnv_blame keeps `seen` sequence numbers across hook re-creation and game restarts

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_blame.py:363-366 (_collect), 78-79
- Severity: medium
- Found as: likely
- What happens: `seen[label]` stores the last ring sequence. If the `blame.*` hook is removed (fnv_hooks remove) and armed again, or the game restarts, the new hook's sequence starts at 0. `records(h, since=old_seq)` then returns nothing until the new ring passes the old number, so `report` silently misses every new hit. `hits` from the old game process also stay in the report.
- Fix idea: key `seen` by hook id (and pid/start) and reset it in ref() when a new hook is installed.
- Resolution (fixed): Blame seen keyed by (hook id, seq); a new game process resets it.

### F5. Bisect counts "game did not start" as "scenario fails", and never checks the final culprit

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_suite.py:64-66, 147-159
- Severity: medium
- Found as: likely
- What happens: `_passes` is `bool(report['passed'])`. A run whose prepare failed has `passed=False`. Examples are a launch failure because a master was switched off while a dependent stayed on, or a GAME_EXITED at load. Bisect reads that as "the culprit is in the half that is on". Half splits often separate a master from its dependents, so the narrowing goes to the wrong half. The last step is never confirmed either. With suspects [A,B], `on=[A]` passes and the code returns "culprit B" without running B alone. The verdict text claims "With only the culprit's half on, the scenario fails", which was never observed. Interaction failures (A+B together) are reported as a single wrong culprit.
- Fix idea: treat a report whose failures are only phase `prepare`/`run` as inconclusive (or keep each half closed under masters). Run a final round with only the culprit on, and check that it fails.
- Resolution (fixed): Runs that never started are inconclusive; the final culprit is re-run alone to confirm.

### F6. Spec JSON is read with the Windows locale encoding (cp1252)

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/authoring.py:69 (also :96, :233; compilation.py:359)
- Severity: medium
- Found as: verified
- Checked: read authoring.py:69: read_text() with no encoding
- What happens: `builder.load_spec(self.spec_path.read_text())` has no encoding. Specs written by agents or editors are UTF-8. Any non-ASCII FULL name or dialogue text in the spec is built into the plugin as mojibake, or the build and install crash with UnicodeDecodeError. photo.py writes and reads the spec the same way.
- Fix idea: `read_text(encoding='utf-8')` for spec, receipt and history (json.dumps output is ASCII anyway).
- Resolution (fixed): Spec, receipt and history JSON read as UTF-8.

### F7. Compile checkpoint gets stuck "blocked" forever if the process dies between install and the 'installed' write

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/compilation.py:310-316 together with :163
- Severity: medium
- Found as: likely
- What happens: in phase `rebuilt`, `self.auth.install()` replaces Data/<plugin> (and appends install history) before `self.write(cp, 'installed')`. If the server is killed or the sqlite write fails between these two steps, the next resume reads phase `rebuilt`. The disk hash is now the rebuilt sha, while `expectedDiskSha256` is still the saved sha. Line 163 returns 'blocked: Installed artifact changed since checkpoint' on every resume, and the reservation on the editor lock namespace is never released.
- Fix idea: write an `install_requested` phase (with builtSha256) before installing. On resume in that phase, accept disk == builtSha256 as completion.
- Resolution (fixed): A resume in phase rebuilt completes when Data already holds the rebuilt file.

### F8. Scenario runner: exceptions other than Game/Link/Scenario errors skip all cleanup

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_scenario.py:376-447 (except clause at 425)
- Severity: medium
- Found as: likely
- What happens: `prepare()` (`live.formid_live`), `mark()` (`live.script_errors`), `_deterministic_on()` (`agent.rng` -> `live.ready`) and the trace start can raise LiveError, HookError or ValueError, which are not subclasses of the caught types. The exception leaves `_run_once` without running teardown, `_settle()` or `stop_task(unpause)`, and without saving a report. With `deterministic: true`, the 60 fps fixed step set by `control.fixed()` stays on in the user's game. The unpause task keeps closing message boxes for timeout+120 s. Under bisect, the error aborts the whole bisect.
- Fix idea: catch Exception in `_run_once` as a 'prepare'/'run' failure, and put `_settle`/`stop_task` in a `finally`.
- Resolution (fixed): Scenario cleanup runs in finally for any exception.

### F9. Experiment battery can leave an engine function permanently skipped

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_experiment.py:220-227, 366-379
- Severity: medium
- Found as: possible
- What happens: `_arm` installs the hook with `action='skip'`, which is live at once, then looks it up (`deep.hook(...)['id']`) and switches the skip off. If the lookup returns None (TypeError) or `_switch` raises (HOOK_SWITCH_FAILED or LinkError), the exception happens before the `try/finally: _disarm` in `blind_battery`. The candidate (for example TESObjectREFR::SetScale) stays skipped in the running game with no record. Also, any non-LinkError raised mid-battery (HookError from hook_set, ValueError from ret_pop) propagates and discards every result of a run that can take hours.
- Fix idea: install with the skip off (or disarm inside `_arm` on any error), and catch Exception per candidate so the partial results are kept.
- Resolution (fixed): Experiment arm removes its hook on any failure; disarm always runs.

### F10. fnv_spawn: refs placed before a later PlaceAtMe failure are never journaled

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_agent.py:796-808
- Severity: medium
- Found as: likely
- What happens: with `count=5`, if the 3rd PlaceAtMe fails, LiveError SPAWN_FAILED is raised. The two refs already made are listed only in the error evidence. Journal entries are added only after the loop, so fnv_undo and fnv_revert_all cannot remove them, and they stay in the user's save. The same pattern exists in fnv_move: the journal is written after `wait_in_game`, so a timeout leaves the move unrecorded.
- Fix idea: add each ref's journal entry right after its PlaceAtMe succeeds.
- Resolution (fixed): spawn journals each ref right after placing it; move journals first.

### F11. fnv_crawl items computes wrong form ids for override records

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_crawl.py:401-408
- Severity: medium
- Found as: likely
- What happens: `iter_records` returns every WEAP/ARMO/... record in the file, overrides included. The ref is built as `<plugin's load index> + local id`. For an override of a FalloutNV.esm item (file index 00), that points into the plugin's own namespace, where no such form exists. So a rebalance mod's 200 overridden weapons each show `added: false` (+5 problems) and are never tested.
- Fix idea: skip records whose file index is below the master count, or map the master index through the plugin's MAST list to the load order.
- Resolution (fixed): crawl items maps form ids through the plugin masters.

### L1. Replies say game null after a server reload until something pings

- Area: Live checks (second pass)
- Where: geck_mcp/fnv_live.py (LiveService.identity), geck_mcp/fnv_link.py
- Severity: medium
- Found as: verified
- What happens: The envelope game field came only from the last ping; a fresh server child (the supervisor reloads on every code change) that only sends batches (fnv_wait_for) answered with game null although it talked to the game.
- Fix idea: Name the game from the process when the link is connected without a ping.
- Resolution (fixed, verified live): identity() falls back to the one game process (pid, start time = the companion's started) and the frame of the last reply. Live: fnv_wait_for right after a reload names the game. IdentityTests.

### L2. fnv_native forgets loaded modules after a server reload

- Area: Live checks (second pass)
- Where: geck_mcp/fnv_native.py (call, load)
- Severity: medium
- Found as: verified
- What happens: A module loaded before a reload could not be called (NO_EXPORT, exports empty), and load would put a second copy in the game.
- Fix idea: Read the export from the DLL in game memory; check the companion's module list before loading.
- Resolution (fixed, verified live): call reads the export table of a module the companion lists; load refuses ALREADY_LOADED for a module an earlier server loaded. Live: both after a reload. NativeAfterRestartTests.

### L4. Crawl and file log report present files as missing

- Area: Live checks (second pass)
- Where: geck_mcp/fnv_create.py (file_log)
- Severity: medium
- Found as: verified
- What happens: Texture names with a leading data\ got textures\ put in front (textures\data\textures\...); a sound the engine asks for as .ogg before using the .wav counted as missing. A clean interior cell had 15 problem points.
- Fix idea: Drop the data\ prefix; a sound miss counts only when no audio extension of it exists.
- Resolution (fixed, verified live): _data_rel and the audio-extension check (foundAs names the file used). Live: the same cell now has 0 problems. FileLogPathTests.

### S2. fnv_spawn ignores distance: every spawned ref lands on the player

- Area: Stress tests and live use
- Where: geck_mcp/fnv_agent.py (spawn)
- Severity: medium
- Found as: verified live
- What happens: fnv_spawn form=Barrel03static count=3 distance=300 returned three refs at the player's exact position (distanceToPlayer 0).
- Fix idea: Pass distance/direction to PlaceAtMe (or move each ref after placing) and check the returned position.
- Resolution (fixed, verified live): spawn places refs at distance/direction from the anchor heading (moves them when PlaceAtMe did not). Live: two barrels at 300 units, exactly along the player heading (direction 0 = front); undo removed both.

### S3. fnv_actor_values on a non-actor calls actor-only engine code

- Area: Stress tests and live use
- Where: geck_mcp/fnv_inspect.py / fnv_agent.py (actor_values)
- Severity: medium
- Found as: verified live
- What happens: fnv_actor_values ref=000CDB95 (a campfire static) ran MagicTarget::GetEffectList and failed with CALL_FAILED "not_executable". Calling actor methods on other forms can crash the game.
- Fix idea: Check the ref is an Actor (form type 42/43 or RTTI) first and fail with NOT_AN_ACTOR.
- Resolution (fixed, verified live): actor_values and effects refuse non-actors (NOT_AN_ACTOR) before any actor code runs. Live: the campfire static is refused, the player works.

### S4. The game quit during the audit and the cause is unknown

- Area: Stress tests and live use
- Where: unknown (game exit at 11:16:15, CrashLogger.log: access violation in nvse_1_4 exit handlers)
- Severity: medium
- Found as: open, not reproduced
- What happens: The running game (pid 15192) exited at 11:16:15 while the full test suite and six read-only audit agents ran; the crash log shows the normal exit path (onexit table), so something asked it to quit. No quit is in the operation journal and this session's supervisor did nothing then. A second full suite run with the game up (11:25-11:30) left it running.
- Fix idea: Log WM_CLOSE/quit requests in the companion (who sent them), and keep a process watcher during the next audit to catch it.
- Resolution (fixed (diagnosis), old cause unknown): Every bridge quit is logged with its caller (state/fnv/quit-log.jsonl); the companion writes exit-<pid>.json from the game's exit path (console vs window/menu, serving a request or not); fnv_status shows lastExit with askedByBridge and forLastLaunch, and says when the last game left neither a note nor a crash log (terminated from outside: killed, or its MO2 job closed). Every bridge quit path goes through Game.quit. Live: two quits traced as console + asked by the bridge. The 11:16 exit predates this and cannot be explained after the fact; the next one will say how it ended.

### S5. The Claude app does not refetch tools after the supervisor's change notice (respawned connections)

- Area: Stress tests and live use
- Where: geck_mcp/supervisor.py / the Claude app
- Severity: medium
- Found as: open
- What happens: The app speaks 2026-07-28 and opens subscriptions/listen; the supervisor sends correctly stamped notifications/tools/list_changed, but on connections the app re-made after a kill it never sent tools/list (it keeps a saved tool list). Not checked on a session's first connection.
- Fix idea: Reconnect geck once, add a throwaway tool, watch state/supervisor.log for "client sent tools/list". If the app never refetches, report it to the app; the code itself still reloads.
- Resolution (open (client side)): Not fixable in the server: the supervisor sends list_changed (log: sent for every reload), but the Claude app asks for tools/list only on its first connection. Calls are checked by the server against its current schema, so changed arguments work at once (live: fnv_time_control action=force_resume ran through the old tool list); only new tool names and new descriptions need a reconnect of geck in /mcp. Code changes reload by themselves.

## Low

### A9. Lazy EditorID index in LiveService: a concurrent call sees a half-loaded index

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/fnv_live.py:300-310 (`editor_id`)
- Severity: low
- Found as: confirmed (confirmed (second thread saw 9 of 12 masters while the first was loading))
- What happens: thread A sets `self._indexes = {}` and spends seconds loading the gz indexes. Thread B passes `hasattr` and walks the partial dict. It then: - picks a hit from the wrong master, or - falls back, or reports REF_NOT_FOUND, or - raises "dictionary changed size during iteration". Any fnv_* call that resolves an EditorID is affected on first use (fnv_ref, fnv_run_script substitution, and others).
- Fix idea: build the dict in a local variable under a lock, then publish it.
- Resolution (fixed): EditorID index built in a local dict under the lazy lock, then published.

### A10. Supervisor: a crashed child replaced before `child_exited` runs leaves its calls unanswered

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/supervisor.py:131-137 (`alive=False` set before `_on_exit`), 240-246 (early return when `child is not self.child`), 261
- Severity: low
- Found as: possible
- What happens: the child crashes with call A in flight. A client message arrives after `alive=False` is set but before `child_exited` takes `route`. `ensure_child` starts and installs a new child, and `child_exited(old)` then returns early. A is never answered (the client hangs), and `in_flight` keeps it, so reload is also disabled.
- Fix idea: in `child_exited`, always fail the `in_flight` entries owned by that child, whatever `self.child` is.
- Resolution (fixed): child_exited fails every in-flight call owned by that child, even after a new child replaced it. test_calls_of_a_replaced_child_still_fail.

### A11. `fnv_session start` leaves changes behind when it fails partway; two parallel starts both run

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/fnv_session.py:61-98
- Severity: low
- Found as: possible
- What happens: these steps run before `session_state` is set: warning capture on, `control.fixed(DETERMINISTIC_FPS)`, rng seed. If `control.snapshot`, `runner.mark()` or `_refs` then raises, they stay in force with no session, and `end` refuses with NO_SESSION. - The `self.state is None` check is also not atomic, so two parallel `start` calls can both run `runner.prepare` (two launches).
- Fix idea: hold a lock around start/end, and undo the steps already done if start fails.
- Resolution (fixed): session start/end hold one lock; a start that fails after the deterministic step / warning capture undoes them.

### A12. `fnv_actor_control set_position` skips the save backup and journals only after all three moves

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/fnv_live.py:1133-1141
- Severity: low
- Found as: likely
- What happens: this branch never calls `self.changing(...)`, so it bypasses the "back up saves before the first change of a game session" rule. If the 2nd or 3rd `SetPos` call fails, the axes already moved are not journaled and cannot be undone.
- Fix idea: call `self.changing('actor_control')` first, and journal before the moves (or per axis).
- Resolution (fixed): set_position backs up saves and journals before the moves.

### A13. Project file read with the locale code page (cp1252 here)

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/config.py:117 (`path.read_text()`)
- Severity: low
- Found as: confirmed (confirmed (`locale.getpreferredencoding()` is cp1252 in the venv, Python 3.12))
- What happens: a project JSON saved as UTF-8 with a BOM fails to parse. A UTF-8 path with non-ASCII characters (a user folder like `José`) decodes to mojibake, and every configured path is wrong.
- Fix idea: `read_text(encoding='utf-8-sig')`.
- Resolution (fixed): Project file read as utf-8-sig. test_project_file_with_bom.

### A14. One-shot helper output decoded with the locale, the persistent worker's as UTF-8

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/transport.py:35 (`subprocess.run(..., text=True)`) vs worker.py:186
- Severity: low
- Found as: possible
- What happens: on the oneshot path (fallback or forced), helper JSON containing non-ASCII text (window titles, paths, model paths) is decoded as cp1252. Values then differ from the persistent path, so the exact-match checks on titles and models fail. Bytes undefined in cp1252 raise UnicodeDecodeError, which is reported as INVALID_ARGUMENT.
- Fix idea: `encoding='utf-8', errors='replace'`, matching the worker.
- Resolution (fixed): One-shot helper output decoded as UTF-8 (errors replaced), as the worker's.

### A15. Write timeout on the companion pipe closes the handle with the overlapped write still pending

- Area: Server core, supervisor, pipe link, journal
- Where: geck_mcp/fnv_link.py:100-102, then `_exchange` close at 137-138
- Severity: low
- Found as: possible
- What happens: on a write timeout, `ov.cancel()` is called but the cancel is not waited for (the read path does wait). Then `CloseHandle` runs. When the Overlapped object is freed, CancelIoEx fails on the closed handle and CPython warns "I/O operations still in flight while destroying Overlapped object, the process may crash".
- Fix idea: after `ov.cancel()`, call `ov.GetOverlappedResult(True)` inside try/except before raising, as the read path does.
- Resolution (fixed): Write timeout waits for the cancel (GetOverlappedResult) before the handle is closed.

### B11. Export lookup reads AddressOfFunctions as the name count; flow call loads a second module copy

- Area: Native companion (C) and code injection
- Where: bridge/geck_mcp/fnv_native.py:316 (`count = struct.unpack_from('<II', raw, 24)[1]`)
- Severity: low
- Found as: confirmed (confirmed (exp.py on a zig build of fnvmcp_flowcall.c: true NumberOfNames 2, value used 197325, a read of 789300 bytes, which is above READ_MAX 65536))
- What happens: `[1]` at offset 24 is AddressOfFunctions, not NumberOfNames. The rd is over READ_MAX, so reads() gives None, struct.unpack raises TypeError, and the function returns None. As a result, flow_slot() never adopts a flow module that an earlier bridge loaded, and it `mod load`s it again. LoadLibrary of the same path returns the same HMODULE. The second init's on_frame is then attributed by mod_of_address to the first Mod (same base), which ends up with two frame callbacks. Each reload leaks a mod slot (MODS=16).
- Fix idea: `count = struct.unpack_from('<I', raw, 24)[0]`.
- Resolution (fixed): Export lookup reads NumberOfNames. ExportLookupTests.

### B12. `hookctl <id> 1 clear` on a hook without a call table dereferences NULL

- Area: Native companion (C) and code injection
- Where: bridge/native/engine/fnvmcp_engine.c:818-823
- Severity: low (latent: today the bridge sends clear only to the PROFILE hooks, which have HF_TABLE)
- Found as: likely
- What happens: h->table is allocated only with HF_TABLE. memset(h->table=NULL, 0, 2048*24) on the main thread crashes the game for any record-only or native hook.
- Fix idea: `if (h->table)` around the clear.
- Resolution (fixed): hookctl clear only touches a call table that exists.

### B13. Stop skip stub slot can be rewritten before the parked thread runs it

- Area: Native companion (C) and code injection
- Where: bridge/native/engine/w2_stop.c:217-218 (hook_stop: stop_skip_stub, then stop_free)
- Severity: low
- Found as: possible
- What happens: the slot's stub (mov eax,v; ret pop) is written, then the slot is freed before the thread actually returns into it through enter_common. Another thread can claim the same slot, be resumed with skip, and overwrite the 16 bytes first. The first thread then returns a different value or pops the wrong byte count, which corrupts its stack.
- Fix idea: free the slot only after the stub has run (e.g. per-thread stubs or a generation counter), or give each Hook its own skip stub.
- Resolution (fixed): Skip stubs come from a 256-entry ring, not the stop slot, so a freed slot can no longer have its stub rewritten before it runs.

### B14. Guarded calls abort on first-chance faults the callee would handle itself

- Area: Native companion (C) and code injection
- Where: bridge/native/engine/w1_guard.c:64-68 (on_guard is the first VEH)
- Severity: low
- Found as: possible
- What happens: VEH runs before SEH. Any AV inside a guarded call is turned into a "fault" and the call is abandoned, even when engine code would catch it with its own __try/catch (probe reads, IsBadReadPtr-style checks). The result is a spurious fault and stateSuspect, with locks or allocations of the abandoned frames left behind.
- Fix idea: hard to avoid with VEH. Document it, or catch only when no SEH frame newer than the guard exists (compare fs:[0] with the saved guard esp).
- Resolution (fixed, verified live): The vectored handler only takes a guarded fault when no SEH frame is newer than the guard; otherwise the code's own handlers run first and the guard's SEH record is the backstop (crash-context files are not written for guarded calls). Live: a module export with its own SEH frame that reads address 0 returned 42 through a guarded call (its handler ran once); a plain fault in the same module came back as CALL_FAULTED; the game kept running.

### B15. dbg_apply reports STILL_ACTIVE (259) as the thread count after its 5 s timeout

- Area: Native companion (C) and code injection
- Where: bridge/native/engine/fnvmcp_engine.c:1240-1243
- Severity: low
- Found as: likely
- What happens: WaitForSingleObject times out and GetExitCodeThread returns 259. `dbg set` then answers ok:true with threads:259 while the registers may not be applied to every thread yet.
- Fix idea: check the wait result; report a timeout as not ok.
- Resolution (fixed): dbg_apply reports a timeout as ok:false, error "timeout".

### B16. `cov read` with since above the hit count emits 8000 bogus addresses

- Area: Native companion (C) and code injection
- Where: bridge/native/engine/w6_cov.c:290-291
- Severity: low
- Found as: likely
- What happens: `upto - from` wraps when from > upto (e.g. after `cov reset`). upto becomes from+8000, and next goes forward past real hits.
- Fix idea: `if (from > upto) from = upto;`.
- Resolution (fixed): cov read clamps since to the hit count.

### B17. write_bytes restores the first page's protection on every page it touched

- Area: Native companion (C) and code injection
- Where: bridge/native/engine/fnvmcp_engine.c:189-192
- Severity: low
- Found as: likely
- What happens: VirtualProtect returns only the first page's old protection. A `wr` that crosses from a read-only page into a RW page (end of .rdata into .data) leaves the RW page read-only. The game's next write there faults.
- Fix idea: VirtualQuery per region and restore each region's own protection (as probe_writable/probe_protect_back do per page).
- Resolution (fixed): write_bytes restores each memory region's own protection.

### C9. Reference change forms with changed form flags never decode (missing spacer)

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/fnv_save.py:274-277 (Save.decode, REFR/PGRE branch)
- Severity: low
- Found as: confirmed (confirmed (t6/t7.py over the 54 real saves: 12,301 SaveFormatError, all REFR/PGRE. In one save, refs with initial data (kind 4/5) and flag 0x1 failed 385 times and decoded 18 times; the body has one extra byte + '|' before the u32 form flags: `...27 bytes| 00| 29 00 00 00|`))
- What happens: fnv_save_diff/inspect return `decodeError` instead of `formFlags`/`scale` for references whose flags changed. Enable/disable is exactly what a diff is usually asked about.
- Fix idea: read the extra byte after the initial-data block for kinds 4-6 (check the decomp's writer order), and test against the real saves.
- Resolution (fixed, verified live): The Havok state before the form flags is read: spaced single bytes, then the 12/16-byte float blocks a moving body saves (velocity and angular velocity, or position and quaternion), for references with initial data and for projectiles. Live: a bottle disabled mid-flight saved |78|03|04|<velocity>|<angular velocity>|0b080000| and its form flags in memory were 0x80B, as decoded. All 51 real saves and the dev saves now decode with no SaveFormatError (12,301 before the fix pass). test_moving_reference_flags_after_havok_blocks_decode, test_projectile_flags_after_its_state_byte_decode.

### C10. Save parser crashes with raw IndexError/struct.error instead of SaveFormatError

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/fnv_save.py:136 (`(1,2,4)[b & 3]`), 195 (`(1,2,4)[tbyte >> 6]`), 154-178 (Save.__init__ offsets)
- Severity: low
- Found as: likely
- What happens: a count byte with low bits 3, a change-form type byte >= 0xC0, or a truncated save (an autosave still being written) raise IndexError/struct.error. summarise_change catches only SaveFormatError, so the whole inspect/diff call fails instead of marking one body.
- Fix idea: check the selector value and the buffer bounds and raise SaveFormatError, and wrap Save() parsing errors.
- Resolution (fixed): Bad size selectors and truncated saves raise SaveFormatError. test_truncated_save_is_a_format_error.

### C11. Record search treats "_" in the query as a wildcard

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/fnv_data.py:291-292
- Severity: low
- Found as: confirmed (confirmed (t3.py: `record_find('Caps_01')` returns Caps001))
- What happens: `%` is stripped but `_` (common in EditorIDs) matches any character, so the results include unrelated records.
- Fix idea: escape `\`, `%`, `_` and add `ESCAPE '\'` (fnv_symdb._like already does this).
- Resolution (fixed): Search escapes \, % and _ with ESCAPE.

### C12. Shared per-instance caches are published before they are filled (thread race)

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/fnv_naming.py:232-245 (StaticIndex.claims), 269-276 (statics); geck_mcp/fnv_records.py:150-162 (`_condition_params`); geck_mcp/fnv_oracle.py:465 (`self._formids`, read at 935)
- Severity: low
- Found as: possible
- What happens: `self._claims = defaultdict(list)` / `self._statics = {}` / `self._cond = {}` are assigned and then filled, so a parallel call sees an empty or partial dict (a dossier with no fnvsym claims; a refs index built with no condition-parameter kinds, then cached for good by REFS_VERSION). `OracleTools.match` keeps per-call form ids on the shared instance, so two parallel oracle runs can compare form oracles against the other run's pointers. If the engine map fails once, `_cond` stays `{}` for the life of the process.
- Fix idea: build into a local variable and assign it at the end (under a lock); keep `_formids` local to match() and pass it to `_candidate`.
- Resolution (fixed): StaticIndex claims/statics and the condition-parameter table are built privately then published; the oracle keeps form ids per call. A failed engine map load is not cached.

### C13. Strict cp1252 decoding crashes on plugin strings with undefined bytes

- Area: Offline data: record index, references, saves, plugin builder
- Where: geck_mcp/esp/codec.py:145-149 (cstring); callers fnv_data.py:101/121 (catches only FormatError), fnv_records.py:524 (_plugin_assets), esp/validate.py:23,146
- Severity: low
- Found as: possible (possible (no vanilla plugin hits it; a translated plugin with UTF-8 text does: 0x81/0x8D/0x8F/0x90/0x9D are common UTF-8 continuation bytes))
- What happens: UnicodeDecodeError is not a FormatError, so fnv_loadorder (a MAST name), fnv_asset_check (a MODL/ICON path) or validate fail entirely instead of reporting the one bad plugin.
- Fix idea: decode with `errors='replace'` for display paths, or catch UnicodeDecodeError next to FormatError.
- Resolution (fixed): cp1252's undefined bytes decode to the same code point and encode back (error handler cp1252-bytes). test_codec_keeps_cp1252_undefined_bytes.

### D11. fnv_photo sequence opens a frame gate the user had closed

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_eyes.py:795-813
- Severity: low
- Found as: likely
- What happens: `control.run('hold')`, then in the finally `control.run('release')`. release always sends `hold off` (fnv_time.py:212-217). A user who held the game with fnv_time_control hold before taking a sequence finds it running afterwards.
- Fix idea: read `('gate',)` first, and release only if the gate was open before.
- Resolution (fixed): Photo sequence reads the gate first and releases only a gate it closed.

### D12. vats_odds uses the agent's shared scratch buffer across three separate requests

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_world.py:728, 762-764
- Severity: low
- Found as: possible
- What happens: write base chance -> call ApplyPerkModifiers(buf) -> read buf are three pipe round trips. fnv_engine_ai uses `agent._buffer()` too (5 uses), so a parallel raycast or perk call can overwrite buf in between, and afterPerks/shownPercent come out wrong.
- Fix idea: put the write, call and read in one link.batch, or use a per-call scratch allocation.
- Resolution (fixed): vats_odds uses its own scratch slot with a lock.

### D13. wincapture.capture reports the wrong method when it falls back to a blank PrintWindow image

- Area: World editing, creation, camera and photos
- Where: geck_mcp/wincapture.py:225-243
- Severity: low
- Found as: likely
- What happens: if PrintWindow returns a blank image and the screen copy is skipped because the window is covered, the saved image is the PrintWindow one, but `method` is `attempts[-1]['method']` = 'screen'. Also, GetDIBits runs while the bitmap is still selected into memdc (wincapture.py:276), which MSDN says not to do.
- Fix idea: keep the method that produced `image` together with `stats`. Select `old` back into memdc before GetDIBits.
- Resolution (fixed, verified live): wincapture reports the method that produced the saved image and selects the old bitmap back before GetDIBits; fnv_screenshot now passes the method on ("window capture (screen)"). Live: window captures are not blank and name their method.

### D14. SystemsTools.sweep divides by zero for steps=0

- Area: World editing, creation, camera and photos
- Where: geck_mcp/fnv_inspect.py:987-988
- Severity: low
- Found as: likely
- What happens: `k / float(steps)` with steps 0 raises ZeroDivisionError, and an `index` past the end of args raises IndexError. Both come back as a generic error.
- Fix idea: validate steps >= 1 and 0 <= index < len(args).
- Resolution (fixed): sweep refuses steps < 1 (BAD_ARGUMENTS).

### E18. `set` on a double variable writes integer bytes

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_debugger.py:932-935
- Severity: low
- Found as: likely
- What happens: only `float` with size 4 is packed as a float. A `double` local (size 8) set to 1.5 gets int(1.5)=1 written as an 8-byte integer, which reads back as a denormal (about 4.9e-324).
- Fix idea: pack '<d' when the type is double and the size is 8.
- Resolution (fixed): set on a double writes 8 bytes as a double.

### E19. Blame drops the exact script statement when the statement hook has id 0

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_blame.py:168
- Severity: low
- Found as: likely
- What happens: `if len(h) >= 10 and h[8] and h[9]` treats hook id 0 as "no mark". The EvaluateLine hook is often the first hook of a session (id 0), so every hit falls back to the approximate context.
- Fix idea: test only `h[9]` (mark_seq) for presence.
- Resolution (fixed): Statement hook id 0 is valid.

### E20. Watch-hook status check uses 'class proven' but the engine map says 'class_proven'

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_deep.py:240, fnv_blame.py:241 (engine_map/generate.py:239-240 writes 'class_proven')
- Severity: low
- Found as: confirmed (confirmed by reading)
- What happens: watch hooks (fnv_deep._function) and blame presets refuse class_proven functions as "unproven", and blame lists the preset as skipped.
- Fix idea: accept both spellings, as fnv_control._function does.
- Resolution (fixed): Both 'class proven' and 'class_proven' accepted.

### E21. Predicate compiler edge cases

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_pred.py:207-214 (negative float), 300-306 (absolute address + offset), 28 (compare semantics)
- Severity: low
- Found as: confirmed (confirmed (offline runs))
- What happens: (1) `-1999...9.0 > 0` (more than about 3.4e38) raises a raw OverflowError, not BAD_PREDICATE (the positive branch catches it). (2) `[0x100+0x80000000]:u32` is refused ("offset does not fit in 32 bits") although the combined address is valid; any absolute base plus an offset of 0x80000000 or more fails. (3) `u32` loads are compared as signed ints by the companion, so `this.[0x08]:u32 > 0x10000000` is false for pointers at or above 0x80000000. Those are common with the 4 GB (LAA) patch. The compiler gives no warning.
- Fix idea: catch OverflowError in the negative branch. Fold absolute addresses into `value` mod 2^32. Document that compares are signed, or add an unsigned compare op.
- Resolution (fixed): Predicate compiler: overflow, large offsets folded, signed-compare warning.

### E22. `step` with fps leaves the fixed frame step on after the step

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_time.py:228-229
- Severity: low
- Found as: likely
- What happens: `fnv_time_control step frames=N fps=30` calls BSTimer_ClampAt(30) and never restores it. After release, the game keeps running at a fixed 33 ms game step regardless of real time. The verdict does not say that the setting persists.
- Fix idea: read TimeGlobal+4 before, and restore it in the same request after `step`, or state clearly that it persists.
- Resolution (fixed): step with fps restores the previous fixed step.

### E23. Blame field leaves the EvaluateLine statement hook installed

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_blame.py:110-114
- Severity: low
- Found as: likely
- What happens: `statement_hook.ensure()` installs the record hook on every script statement and never removes it. ScriptDebugger._arm removes it only when its own break list changes, so after a field watch every statement of every script is recorded for the rest of the session.
- Fix idea: remember whether field installed it, and remove it in the finally block when no break or log needs it.
- Resolution (fixed): Blame field removes the statement hook it installed.

### E24. Function-entry breaks with thread=main still park worker threads

- Area: Debugger, script debugger, time control, profiling
- Where: fnv_debugger.py:419-424, 486-487
- Severity: low
- Found as: possible
- What happens: the hook-hold path is taken regardless of `thread`, and the `hook` op has no main-only flag, so a worker thread that calls the function parks too. The record still says thread 'main'.
- Fix idea: add `main` to the hold predicate (`main and (...)`) when thread == 'main'.
- Resolution (fixed, verified live): Function breaks with thread=main hold on the main predicate. Live: a hook hold on GetExtraData (called by worker threads too) parked only the main thread (one stop among 88 threads).

### F12. Scenario YAML subset: apostrophes break comment stripping; some scalars mis-typed

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_scenario.py:55-90
- Severity: low
- Found as: confirmed (confirmed (ran parse_yaml))
- What happens: `_strip_comment` treats a `'` anywhere as an opening quote. `- run: MessageBox Mitchell's  # note` parses as the console line `"MessageBox Mitchell's  # note"`, which is sent to the game. `{message_box: Doc Mitchell's house}  # x` raises "text after a flow value". `1E5` becomes the float 100000.0, so an item/form id written like that resolves wrongly. A double-quoted Windows path (`"C:\Mods\x"`) raises a raw JSONDecodeError instead of ScenarioError.
- Fix idea: only start a quote at the beginning of a scalar or after `: `/`- `/`[{,`, and keep e-notation as a string unless it contains a `.`.
- Resolution (fixed): Scenario YAML: quotes only at scalar starts, 1E5 stays text, bad escapes raise.

### F13. fnv_crawl cells leaves the warning capture hook and the file log running

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_crawl.py:156-190
- Severity: low
- Found as: likely
- What happens: `capture.start()` installs the Warning record hook and nothing stops it at the end. When a cell raises after `file_log('start')`, the file hooks stay on after the last cell. Both keep recording in the user's game after the crawl.
- Fix idea: track what the crawl started and stop it in a `finally`.
- Resolution (fixed, verified live): crawl cells stops the capture and file log it started in a finally. Live: after two crawls the companion has no hooks armed.

### F14. Engine screenshot treats a half-written BMP as done

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_agent.py:569-571, 533-537
- Severity: low
- Found as: possible
- What happens: `_engine_capture` returns ok as soon as the file is larger than 54 bytes. `Image.open(bmp).load()` then runs outside the try, so a truncated image raises and the tool fails without falling back to the window capture.
- Fix idea: wait until the size equals the BMP header's bfSize (or is stable), and wrap the open in the fallback.
- Resolution (fixed, verified live): Engine screenshot waits for the full BMP and falls back to window capture. Live: method=engine returns the back buffer (SaveScreenshot 0x00878860).

### F15. Records bisect quits the user's game before validating its arguments

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_suite.py:122-130
- Severity: low
- Found as: confirmed (confirmed (reading order))
- What happens: `level=records` with a running game calls `self.quit_game()` at line 124, before `steps` is checked (125) and before plugin/candidates are validated. A call with empty steps or a wrong plugin name closes the game and then fails with BAD_ARGUMENTS or PLUGIN_NOT_FOUND.
- Fix idea: validate steps, plugin and candidates first, then quit.
- Resolution (fixed): Records bisect validates arguments and backs up before quitting the game.

### F16. fnv_crawl items changes the player's equipment and inventory beyond the item under test

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_crawl.py:418-431
- Severity: low
- Found as: likely
- What happens: `EquipItem` replaces whatever armor or weapon the player wore. `UnequipItem` afterwards does not put it back. `RemoveItem <count>` removes every copy, including ones the player had before the crawl. None of this is journaled.
- Fix idea: record the equipped slots and the count before, then restore both.
- Resolution (fixed): crawl items restores worn items and removes only added copies.

### F17. fnv_coverage writes outside its folder for a run name with path parts

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_coverage.py:73, 120-121
- Severity: low
- Found as: likely
- What happens: `name` from the tool call goes straight into `self.folder / ('%s.json' % name)`. A name like `..\..\x` writes the coverage JSON outside state/fnv/coverage.
- Fix idea: sanitize with the same `[^A-Za-z0-9_-]` rule used for reports.
- Resolution (fixed): coverage run names with path parts refused.

### F18. engine_wait leaves the player's wait counter changed if a middle batch fails

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_agent.py:1467-1476
- Severity: low
- Found as: possible
- What happens: player+0x654 is set to n+2 in the first chunk and restored only by the last op of the last chunk. A LinkError on a middle `batch` raises with the counter left at n+2, and no journal entry is written.
- Fix idea: restore the counter in a `finally` (a separate write), and journal before the calls.
- Resolution (fixed): engine_wait journals first and restores the counter in a finally.

### F19. Report and screenshot files are keyed by whole seconds

- Area: Agents, scenarios, crawl, bisect, GECK authoring and compile
- Where: bridge/geck_mcp/fnv_scenario.py:1037, 1047; fnv_agent.py:523
- Severity: low
- Found as: likely
- What happens: `repeat: N` runs of a fast scenario, and the aggregate report, finish in the same second with the same name, so `save_report` overwrites earlier reports. Each run's `report` path then points at the last file written. The same applies to screenshots taken in the same second with the same name.
- Fix idea: add a uuid or a counter to the file name. Note, outside this area but hit by the scenario runner: fnv_live.Journal.mark_undone (fnv_live.py:68-74) rewrites the whole journal file while `add` appends from other worker threads. A parallel tool call's journal entry can be lost, and then cannot be undone.
- Resolution (fixed): Report and screenshot names get a per-process counter.

### L3. A native module fault is CALL_FAILED with a raw dict, and a silent build failure has no message

- Area: Live checks (second pass)
- Where: geck_mcp/fnv_native.py (call, build)
- Severity: low
- Found as: verified
- What happens: A guarded module call that faulted came back as CALL_FAILED with a Python dict in the message (engine calls say CALL_FAULTED); a linker that stopped without output gave "zig cc failed:" and nothing else.
- Fix idea: Map the fault like engine_call; report the exit code and a hint.
- Resolution (fixed, verified live): CALL_FAULTED with address, access and code; BUILD_FAILED gives the exit code, stdout and a hint (a .safeseh directive in inline asm makes lld stop silently). The fault checked live, the build message by NativeAfterRestartTests.

### L5. until says "Reached" when another stop came first

- Area: Live checks (second pass)
- Where: geck_mcp/fnv_debugger.py (until)
- Severity: low
- Found as: verified
- What happens: until reported "Reached <location>" for a stop at a different break; the temporary site at the location stayed armed with nothing saying so.
- Fix idea: Compare the stop with the location.
- Resolution (fixed): reached false and a verdict naming the stop that came first; the site is listed in status (pendingTo) until it is hit or break clear removes it (the listing and the sweep checked live; the verdict by UntilAndTemporarySiteTests).

### L6. Companion ping counts removed hooks

- Area: Live checks (second pass)
- Where: native/engine/fnvmcp_engine.c (ping)
- Severity: low
- Found as: verified
- What happens: hooks in the ping counted every slot used since launch, so it stayed above 0 after all hooks were removed.
- Fix idea: Count armed hooks only.
- Resolution (fixed, verified live): Counts hooks still stolen. Live: 0 after crawls and breaks were cleared.

### L7. WAIT_TIMEOUT names an argument the tool does not have

- Area: Live checks (second pass)
- Where: geck_mcp/fnv_time.py (WaitFor._run)
- Severity: low
- Found as: verified
- What happens: The message said to raise timeout_seconds; fnv_wait_for's argument is timeout, and it did not say whether time or frames ran out.
- Fix idea: Say which ran out and name the right argument.
- Resolution (fixed): ranOutOf time|frames in the evidence; the message names timeout or max_frames. WaitTimeoutMessageTests.

### S6. Test runs write into the real state/supervisor.log

- Area: Stress tests and live use
- Where: geck_mcp/supervisor.py main()
- Severity: low
- Found as: verified
- What happens: Every stdio test that starts run_mcp.py logs to bridge/state/supervisor.log, mixing test supervisors (other pids) into the live log.
- Fix idea: Take the log path from the project's state_dir, or an env var the tests set to a temp folder.
- Resolution (fixed): Supervisor log path: GECK_SUPERVISOR_LOG, else the project's state_dir, else <root>/state. test_log_goes_to_the_projects_state_folder.

### S7. fnv_record_show 0xFF000001 says "No record None:000001"

- Area: Stress tests and live use
- Where: geck_mcp/fnv_data.py (record lookup)
- Severity: low
- Found as: verified (stress)
- What happens: A runtime (0xFF) form id is resolved through the load order and the missing plugin prints as None.
- Fix idea: Say that 0xFF ids are runtime references with no plugin record (use fnv_ref).
- Resolution (fixed, verified live): Runtime (0xFF) ids give RUNTIME_FORM; an unknown load-order index says so. Live checked.

### S8. fnv_func_code does not accept Class::Method names

- Area: Stress tests and live use
- Where: geck_mcp/fnv_engine.py (function lookup)
- Severity: low
- Found as: verified (stress)
- What happens: function="TESObjectREFR::GetPos" -> NOT_FOUND, while "GetPos" -> AMBIGUOUS (2 functions). The qualified name is the only way to pick one without an address.
- Fix idea: Match qualified names against the class-qualified names in the map/symbol database.
- Resolution (fixed, verified live): Class::Method names from class proofs resolve; an unknown class lists the candidates. Live: Actor::GetPos resolved to 0x008AE4C0.

### S9. fnv_look reports the prompt as "Button Text" when nothing is targeted

- Area: Stress tests and live use
- Where: geck_mcp/fnv_eyes.py (look)
- Severity: low
- Found as: verified live
- What happens: With no crosshair ref the prompt field holds the UI tile's placeholder text.
- Fix idea: Return null when the crosshair ref is null or the prompt tile is hidden.
- Resolution (fixed): No crosshair ref: prompt is null.

### S10. fnv_wait_for on unreadable memory just times out

- Area: Stress tests and live use
- Where: geck_mcp/fnv_pred.py / fnv_time.py
- Severity: low
- Found as: verified live
- What happens: predicate "[0x00000000]:u8 == 1" waited out its frames and returned WAIT_TIMEOUT with no word that the address cannot be read.
- Fix idea: Check the predicate's addresses once before waiting and fail with MEMORY_UNREADABLE.
- Resolution (fixed): Predicate addresses are read once before waiting: MEMORY_UNREADABLE.

### S11. The envelope's "game" field is stale after the game exits

- Area: Stress tests and live use
- Where: geck_mcp/fnv_live_tools.py (envelope)
- Severity: low
- Found as: verified live
- What happens: After the game exited, every error envelope still carried game {pid 15192, frame 20329} while fnv_status said not_running.
- Fix idea: Clear the cached game info when the process is gone, or mark it stale.
- Resolution (fixed, verified live): The link forgets its hello on close and on disconnect. Live: the envelope after a quit says game null.

# Round 2: open bugs (2026-11-12)

A second hunt the same day, after the fix pass was committed (133ed0e). Nothing in this part is fixed yet; every entry is open. IDs start with the way the bug was found:

- G: live stress test in the running game
- R: review of the fix-pass diff (036b4c8..133ed0e)
- T: tool-contract sweep (every tool called offline with odd arguments)
- P: parsers run over all real data

The parser sweep ran 16 plugins (about 630,000 records), the record index (866,000 references), 3,719 scripts, 58 saves (1.4 million change forms), 21 BSAs and the engine map; everything not listed here checked clean. Findings that did not hold up when checked were dropped (fnv_window persist writes the dev profile's Fallout.ini, as designed, with a backup; fnv_hook_set is gated). R entries that name an earlier ID are fixes from the first pass that are incomplete or wrong.

Totals: 66 bugs, 5 high, 19 medium, 42 low. N1-N12 came from the stabilization research (PLAN-3, `fnv-mcp/research/stabilize/`), found by reading the code; they were added on 2026-11-12 with the S0 baseline.

Baseline (S0, 2026-11-12): the full offline suite, `.venv\Scripts\python.exe -m unittest discover -s tests` from `bridge`, ran 1,011 tests: all passed, 4 skipped, 0 failures. That includes the 54 engine companion tests, which now find Zig from the project files instead of being skipped without `GECK_MCP_ZIG`. Before S0 the same run with `GECK_MCP_ZIG` set was 1,002 tests, 4 skipped. No C test failed, so no N13.

| ID | Severity | Status | Bug | Where |
|---|---|---|---|---|
| R1 | high | fixed (live check pending) | Coverage: two threads that reach the same coverage line at once crash the game (B1 fix) | `native/engine/w6_cov.c:31-55` |
| R2 | high | open | The F5 fix makes bisect unable to find a plugin that crashes the game while it loads (F5) | `geck_mcp/fnv_suite.py:75-83` |
| R3 | high | open | The plugin backup and `restore.json` are written but never read: a server that dies mid-bisect leaves the plugin pruned, and the next bisect makes that permanent (F3) | `geck_mcp/fnv_suite.py:131-146` |
| R4 | high | open | Hot reload kills a cancelled call that is still running in the old server (A5 fix) | `geck_mcp/supervisor.py:331-338` |
| T1 | high | open | The engine_control gate is bypassed by tools registered as control or read | `fnv_live.py:167` |
| G1 | medium | open | Undoing a nudge/rotate/scale leaves the export pose at the undone value | `geck_mcp/fnv_create.py:1109-1119` |
| G4 | medium | open | fnv_revert_all lists every non-undoable entry ever journaled (57 KB reply, over the client's limit) | `geck_mcp/fnv_live.py:1419` |
| G5 | medium | open | fnv_wait_for seconds with clock=game says "met" while the game clock is paused | `geck_mcp/fnv_time.py` |
| G9 | medium | open | fnv_globals rounds short/long globals that hold fractions (GameHour), and its undo restores the rounded value | `geck_mcp fnv_globals read and set` |
| R5 | medium | open | `fnv_flow call` leaves its breakpoint armed (and its predicate slot freed) when the call cannot be armed (E4 incomplete; new path from the B10 fix) | `geck_mcp/fnv_flow.py:171-191 arms the breakpoint` |
| R6 | medium | open | `fnv_audio subtitles` drops every second subtitle (new code from the subtitles rewrite) | `geck_mcp/fnv_inspect.py:671-681` |
| R7 | medium | open | After a server restart the camera journal entry still undoes with a blind `tfc`, so fnv_revert_all turns the fly camera ON (D2 incomplete) | `geck_mcp/fnv_eyes.py:271-292` |
| R8 | medium | open | Journal: `mark_undone`'s `os.replace` fails while any other handle has `journal.jsonl` open, so an undo that already ran is not recorded and undo/revert_all stops (A1 fix, A7 again) | `geck_mcp/fnv_live.py:104-115` |
| R9 | medium | open | C5 is only partly fixed: some form-id references are still skipped as text (C5) | `geck_mcp/fnv_records.py:96` |
| R10 | medium | open | `save_session` rewrites `session.json` with no lock: parallel act tools lose changes or corrupt it, and a corrupt file then breaks every act tool in a new server (D8 incomplete) | `geck_mcp/fnv_game.py:239-253` |
| R11 | medium | open | Step over, step out and `step line` leave their temporary `to` site armed and unrecorded when another stop of the same thread comes first (E15 / L5 incomplete) | `geck_mcp/fnv_debugger.py _run_to` |
| T2 | medium | open | fnv_input: invalid key_name raises outside invoke() | `fnv_live_tools.py:618` |
| P2 | medium | open | Vanilla compressed LAND with bad Adler-32 breaks full parse | `esp/codec.py decompress` |
| P5 | medium | open | record_find reports the wrong version's name and editor ID | `fnv_data.py record_find` |
| G2 | low | open | fnv_spawn with a reference spec (player, crosshair, a ref form id) fails with a script compile error | `geck_mcp/fnv_agent.py:808` |
| G3 | low | open | A placed ref whose SetPos/SetAngle fails is never journaled | `geck_mcp/fnv_create.py:1005-1019` |
| G6 | low | open | fnv_wait_for seconds with clock=real uses the fixed step as the real frame rate | `geck_mcp/fnv_time.py:450-455` |
| G7 | low | open | fnv_time_control status says "The game is running" while the clock is paused | `geck_mcp/fnv_time.py status verdict` |
| G8 | low | open | The envelope's game.frame is the frame of the last ping, minutes old | `geck_mcp/fnv_live.py:190-192` |
| G10 | low | open | fnv_globals set returns the whole unfiltered list, with the values from before the write | `geck_mcp fnv_globals` |
| G11 | low | open | fnv_hook_set: with a limit, actionHits counts every call and read says every call was changed | `native/engine/fnvmcp_engine.c:580-581` |
| G12 | low | open | fnv_mem_write: an out-of-range value is FNV_ERROR, and setting types show as mangled names | `geck_mcp fnv_mem_write` |
| G13 | low | open | After break clear, a stop that is still parked is described as "break 0" | `geck_mcp/fnv_debugger.py` |
| G14 | low | open | fnv_console runs qqq (and other quit/load lines) unguarded; the quit is then traced as not asked by the bridge | `geck_mcp/fnv_live.py:235` |
| G15 | low | open | fnv_status fails with SEVERAL_GAMES while a game is exiting | `geck_mcp/fnv_game.py` |
| G16 | low | open | fnv_engine_get ignores extra arguments | `geck_mcp/fnv_control.py:396-404` |
| R12 | low | open | `fnv_session start` rollback leaves prepare's crash-context hooks on, and stops a warning capture it did not start (A11 incomplete) | `geck_mcp/fnv_session.py:77-117` |
| R13 | low | open | `fnv_debug thaw` with nothing on record resumes every thread of the game once (E1/E6 fix) | `geck_mcp/fnv_debugger.py thaw()` |
| R14 | low | open | C2 is only partly fixed: a failed refs index build keeps the SQLite write lock (C2) | `geck_mcp/fnv_records.py:175-208` |
| R15 | low | open | C3 is fixed within one process only: two server processes still collide (C3) | `geck_mcp/fnv_data.py:168-210` |
| R16 | low | open | C13 is only partly fixed: strict cp1252 decodes remain in other readers (C13) | `geck_mcp/fnv_create.py:64, 71, 745, 751, 767, 818` |
| R17 | low | open | The F16 fix leaves its own error path open: an item that fails mid-test stays in the player's inventory (F16) | `geck_mcp/fnv_crawl.py:483-517` |
| R18 | low | open | The new "terminated from outside" note in `fnv_status` is wrong with an older companion and when the bridge itself force-quit (S4 diagnosis) | `geck_mcp/fnv_game.py:305-313` |
| R19 | low | open | Journal entries made with no hello on the link carry `pid: None` and can never be undone (A3 fix) | `geck_mcp/fnv_live.py:89-96` |
| R20 | low | open | E6 state file: one save with a different `start` drops every other section (ProcState) | `geck_mcp/fnv_pred.py:400-421` |
| T3 | low | open | fnv_name_export writes a trace file even when it has nothing to export | `fnv naming export tool` |
| T4 | low | open | Inconsistent error codes for "game not running" | `fnv_live.py ready()/in_game(), fnv_link.py EngineLink.connect, fnv_game.game_pro` |
| T5 | low | open | Raw int(...,16) ValueError text in fnv_probe and fnv_events arguments | `fnv_live_tools.py` |
| T6 | low | open | Short form ids (no leading zeros / no load-order prefix) not accepted consistently | `fnv_live.py resolve()` |
| T7 | low | open | fnv_asset_check accepts arbitrary paths and surfaces a raw parse error | `fnv_asset_check registration` |
| T8 | low | open | Parameters documented/required but ignored | `several fnv_* registrations` |
| T9 | low | open | geck_compile_status vs geck_compile_resume return different codes for unknown operation | `geck_* compile tools in server.py` |
| P3 | low | open | Corrupt input raises raw exceptions rather than FormatError | `esp/codec.py lines 207 and 224` |
| P4 | low | open | XXXX round trip not byte exact | `esp/codec.py encode_subrecords` |
| P6 | low | open | `_form` off-by-one | `fnv_data.py:430 high >= len(own) + 1` |
| P7 | low | open | Index and lookup gaps | `fnv_data.py SKIP_EDID` |
| P8 | low | open | Lint false positives on vanilla scripts | `fnv_data.py _split_args` |
| P9 | low | open | Spec validation regexes accept a trailing newline | `esp/records.py:14 EDID_RE` |
| P10 | low | open | Save parser: FLT_MAX rotation sentinel passed through | `fnv_save.py ACHR/ACRE initial block decode` |
| N1 | medium | open | An unknown undo op is marked undone without running | `geck_mcp/fnv_live.py:1347-1382` |
| N2 | medium | open | A console undo is marked undone even when the game rejected the line | `geck_mcp/fnv_live.py:1356-1357, 1380-1381` |
| N3 | medium | open | After an undo the camera keeps its saved state, so later camera changes are not journaled | `geck_mcp/fnv_eyes.py:273-274` |
| N4 | low | open | `form_delete` does not supersede `form_create`, so export keeps deleted forms | `geck_mcp/fnv_create.py:962` |
| N5 | low | open | Undoing a probe only disarms it: the stubs stay patched and `probe_session.json` still says armed | `geck_mcp/fnv_probe.py:432` |
| N6 | low | open | The `dbg_clear` undo clears every debug register, including other tools' | `geck_mcp/fnv_watch.py:170` |
| N7 | low | open | The rng seed undo does not restore the index at +0x9C8 | `geck_mcp/fnv_agent.py:1386, 1409` |
| N8 | medium | open | About a dozen tools change the game with no journal entry at all | `fnv_deep.py, fnv_debugger.py, fnv_time.py, fnv_naming.py, fnv_agent.py and others (see details)` |
| N9 | low | open | actor_control enable, disable, unconscious, restrain and add_item have blind undos | `geck_mcp/fnv_live.py:1245` |
| N10 | medium | open | 8 tool families journal after the change, so a failure leaves an unjournaled change | `fnv_live.py, fnv_code.py, fnv_probe.py, fnv_watch.py, fnv_create.py, fnv_world.py, fnv_eyes.py (see details)` |
| N11 | low | open | The game's identity is chosen six different ways, and `tasklist` runs on every `game.process()` call | `geck_mcp/fnv_game.py:~33-52; fnv_code.py:89 and four other pickers` |
| N12 | low | open | Nothing under `state/fnv` is pruned, and none of it has a cross-process lock | `state/fnv/*` |

## Round 2: High

### R1. Coverage: two threads that reach the same coverage line at once crash the game (B1 fix)

**Where:** `native/engine/w6_cov.c:31-55` (`cov_on_breakpoint`, the new `else if (*at == 0xCC) return false;` branch) and `native/engine/w6_cov.c:64-77` (`cov_release_range`).
- **Severity:** high (crash).
- **Confidence:** likely (from reading the code; there is no offline harness for the companion).
- **What happens:**
  - Thread A traps on an armed coverage line. Its `InterlockedCompareExchange(&cov_state[i], 2, 1)` wins, then it calls `VirtualProtect` (a system call) before it writes the original byte back.
  - Thread B traps at the same address inside that window. Its exchange fails because the state is already 2, and `*at` is still 0xCC. The new branch then returns false ("not ours").
  - `on_trap` then tries `bp_on_breakpoint`, which finds no site, and the probe table, which finds nothing. It returns `EXCEPTION_CONTINUE_SEARCH`, so the int3 is unhandled and the game crashes.
  - Before the fix, thread B set `Eip = address` and ran the trap again until A had restored the byte. That loop was harmless.
  - `cov_release_range` has the same window in the other order. It sets the state from 1 to 0 first, then calls `VirtualProtect`, then restores the byte. It is called without the other threads suspended, from `op_hook` (`fnvmcp_engine.c:751`) and from `op_code patch` (`w5_code.c:56`). Any thread that traps in between sees state 0 with 0xCC and crashes the same way.
  - Code that worker threads share (Havok, audio, task threads) makes a simultaneous first hit realistic.
- **Fix idea:** Add a transient "restoring" state (3). The hit path claims it with an exchange from 1 to 3, restores the byte, then sets 2. The release path also goes through 3 (or restores the byte before it changes the state). In the handler, state 3 means "in flight: set Eip = address and run it again". Only state 0 or 2 with 0xCC should be treated as foreign.
- Status: fixed (S1.1), live check pending. Coverage lines now have a fourth state, 3 (restoring): the hit and the release both claim a line 1 -> 3, so the winner owns the byte until it is back, and any other trap there re-runs the int3 meanwhile. A release (hook, patch, breakpoint, disarm) does not wait for a hit in flight, since its owner may be suspended by the caller; it puts the byte back itself. The restore goes through WriteProcessMemory, which fails instead of faulting when two threads change the same page's protection at once. `cov add` and `cov reset` answer `busy` while a hit is in flight. Tests: `tests/test_engine_races.py` (4, seam build), which reproduced the 0x80000003 crash before the fix.

### R2. The F5 fix makes bisect unable to find a plugin that crashes the game while it loads (F5)

**Where:** `geck_mcp/fnv_suite.py:75-83` (`_passes` sets `inconclusive`) and `:213-216` (round 0 returns "Inconclusive"). The failure is tagged at `geck_mcp/fnv_scenario.py:441-446`.
- **Severity:** high (wrong result in normal use; "the game crashes on load" is the usual reason to bisect).
- **Confidence:** confirmed by repro (`repro_bisect_crash_at_load.py`).
- **What happens:**
  - A culprit that makes the game exit at startup or load raises `GameError('GAME_EXITED', ..., {'crash': ...})` inside `prepare`.
  - `_run_once` records that as a phase-'prepare' failure, and `_passes` calls any run whose failures are all 'prepare' inconclusive.
  - So round 0 (every mod on) returns "Inconclusive" and stops. Any half that contains the culprit stops the same way.
  - In the repro (4 mods, M2 crashes at load) the result is culprit None after 1 round.
- **Fix idea:** Count GAME_EXITED, or any prepare failure whose evidence has `crash`, as a fail. Call a run inconclusive only when a master is missing: keep each half closed under its masters, or check `missingMasters` in the load order before launching.
- Status: open

### R3. The plugin backup and `restore.json` are written but never read: a server that dies mid-bisect leaves the plugin pruned, and the next bisect makes that permanent (F3)

**Where:** `geck_mcp/fnv_suite.py:131-146` (`_backup_original`), and `:96-101` with `:161` (`_original` is reset and the file on disk is taken as the original). Nothing reads `*.restore.json` or the `.orig` backup.
- **Severity:** high (data loss in the user's plugin). It needs the server to die mid-bisect; a supervisor reload can cause that (see finding 4) and so can a client restart.
- **Confidence:** confirmed by repro (`repro_bisect_dead_server.py`).
- **What happens:**
  1. The server dies between rounds, so A.esp stays a pruned variant (1 of 4 records in the repro). Nothing restores it on restart and nothing warns.
  2. The next `fnv_bisect level=records` on that plugin reads the variant as the original and backs it up.
  3. It then points `A.esp.restore.json` at that backup, which loses the only pointer to the real one.
  4. At the end it "restores" the variant, so the plugin is left with 1 of 4 records.
- **Fix idea:**
  - At bisect start (and at server start), check for an existing `restore.json`. If the plugin's sha differs from the recorded one and the backup's sha matches, restore the backup, or refuse with RESTORE_PENDING.
  - Delete `restore.json` after a successful restore.
  - Never overwrite an existing `restore.json` whose backup differs from the current file.
- Status: open

### R4. Hot reload kills a cancelled call that is still running in the old server (A5 fix)

**Where:** `geck_mcp/supervisor.py:331-338` (the cancel handling pops `in_flight`), `:420-426` (`watch_loop` reloads when `in_flight` is empty), and `:186-199` (`Child.close`, grace 10 s, then `kill()`).
- **Severity:** high. The game or the user's files are left half changed. It needs a cancel and then a code change, but with the supervisor that is ordinary development.
- **Confidence:** confirmed by repro (`regress\sup\repro_cancel_reload.py`).
- **What happens:**
  - Tool calls are shielded from cancellation: `server.py:139-141` and `fnv_live_tools.py:307-308` use `CancelScope(shield=True)` and `abandon_on_cancel=False`. The service docstring says that killing a running call is unsafe.
  - The A5 fix drops a cancelled request from `in_flight` at once, although the old child is still running it.
  - The next code change then reloads: `old.close()` closes stdin, waits 10 s and kills the process.
  - In the repro, a 14 s call that the client cancelled after 1 s was killed (exit code 1 after 10.8 s), and its work never finished.
  - Calls that can be cut this way include `fnv_wait_for` (gate held between rounds), `fnv_crawl` (hooks and file log on), `fnv_bisect records` (the user's plugin left pruned; see finding 3) and any debugger or act tool mid-change. A `notifications/cancelled` from the client is enough.
- **Fix idea:** Keep cancelled requests in a separate "draining" set, owned by the child, and don't reload while it is non-empty. Or reload without killing: when the old child still has draining calls, let it exit on its own once they finish, with no 10 s kill (or a kill only after a much longer bound). Clear the drained id when the child's late response arrives or when it exits.
- Status: open

### T1. The engine_control gate is bypassed by tools registered as control or read

- Where: fnv_live.py:167 (only kind == 'act' is gated); fnv_live_tools.py (fnv_probe, fnv_name_oracle, fnv_name_test, fnv_field_watch, fnv_atlas invoked as 'control'; fnv_probe uses 'read' for plan/status/read/harvests and 'control' otherwise); fnv_deep.py:628 crash_context and :676 hooks_tool
- Severity: high
- Confidence: confirmed by a call (project with engine_control="read": these tools were not refused with CONTROL_DISABLED; they proceeded to the not-running/pipe stage, while true act tools returned CONTROL_DISABLED)
- What happens: the docs say "Act tools change the running game and need fnv.engine_control full", but tools that install/remove hooks or write game memory are registered with kind 'control' or 'read' (and annotations=read), so they skip the gate. With the game running and engine_control="read", they would install hooks. Also read-only annotations mislead clients that auto-approve read tools.
- Fix idea: gate kind in ('act','control') for mutating actions, or reclassify these tools as 'act'; set annotations to non-read for hook-installing tools.
- Checked: fnv_hook_set is not affected (its changing actions are invoked as act). The hook-installing actions of fnv_probe, fnv_atlas, fnv_name_oracle, fnv_name_test and fnv_field_watch are invoked as 'control', and fnv_hooks remove as 'read'; LiveService.run gates only kind == 'act' (fnv_live.py:167).
- Status: open

## Round 2: Medium

### G1. Undoing a nudge/rotate/scale leaves the export pose at the undone value

- Where: geck_mcp/fnv_create.py:1109-1119 (adjust, _update_placed; place_pose entries have undo None)
- Severity: medium
- Confidence: confirmed live
- What happens: place array -> nudge FF001319 by +50 z -> fnv_undo that nudge. The ref is back at z 6364.31 in the game (fnv_ref), but fnv_place list still says z 6414.31: the place_pose entry written by the nudge is not undone with it, and the export (fnv_author) reads the newest place_pose.
- Fix idea: tie the place_pose to its place_adjust entry (store the adjust id, skip poses whose adjust is undone), or write a new place_pose with the before pose when a place_adjust is undone.
- Status: open

### G4. fnv_revert_all lists every non-undoable entry ever journaled (57 KB reply, over the client's limit)

- Where: geck_mcp/fnv_live.py:1419 (skipped ignores same_process and since)
- Severity: medium
- Confidence: confirmed live
- What happens: after 8 changes in this game, fnv_revert_all reverted 8 and returned notUndoable with 3,761 ids from every earlier game process (journal.jsonl has 5,291 lines). The reply was 56,996 characters, too big for the client, so the agent cannot see the result without reading a dump file. fnv_session end (revert since) has the same list.
- Fix idea: filter skipped with the same same_process/since test as the undo list, and cap it (count plus the newest 50).
- Status: open

### G5. fnv_wait_for seconds with clock=game says "met" while the game clock is paused

- Where: geck_mcp/fnv_time.py (seconds -> estimatedFrames from the real frame rate; the clock counter is not consulted)
- Severity: medium
- Confidence: confirmed live
- What happens: fnv_time_control pause (clock counter 1), then fnv_wait_for seconds=1 clock=game -> "Condition met at frame 441880 after 60 frames", although no game time passed. The description promises game seconds scaled by the time multiplier; a paused clock (multiplier effectively 0) should never meet it, or the tool should refuse/warn.
- Fix idea: when the clock counter is above 0 (or the multiplier is 0), refuse with CLOCK_PAUSED, or wait on the game clock itself (a predicate over the game-time global) instead of a frame estimate.
- Status: open

### G9. fnv_globals rounds short/long globals that hold fractions (GameHour), and its undo restores the rounded value

- Where: geck_mcp fnv_globals read and set (the type char decides rounding; the engine stores every global as a float at +0x24)
- Severity: medium
- Confidence: confirmed live
- What happens: GameHour is FNAM short in FalloutNV.esm, but the engine keeps the fraction (console GetGlobalValue GameHour -> 9.12). fnv_globals shows 9; set {"GameHour": 9.5} writes 9 and reports old 9 new 9. The journaled old value is the rounded one, so fnv_undo cannot restore 9.12. Time of day cannot be set to a half hour with this tool.
- Fix idea: read and journal the raw float; round only on write, and not for GameHour/GameDaysPassed (or only when the value is integral), and say when a value was rounded.
- Status: open

### R5. `fnv_flow call` leaves its breakpoint armed (and its predicate slot freed) when the call cannot be armed (E4 incomplete; new path from the B10 fix)

**Where:** `geck_mcp/fnv_flow.py:171-191` arms the breakpoint, then `_trace_armed` at `:193-195` calls `_arm_call`. `_arm_call` (`:258-291`) can raise `OLD_MODULE` (new, from `_slot_has_edx(required=True)`), `BAD_ARGUMENTS` (more than 12 words) or a resolve error for an argument name.
- **Severity:** medium (the game hangs in a less common case).
- **Confidence:** confirmed by repro (`regress\flow\repro_flow_bp_left.py`: OLD_MODULE raised, `bp set` sent, no `bp clear`).
- **What happens:**
  - The exception propagates out of `trace()`. Its `finally` frees and clears the `when` predicate slot, but nothing clears the FLAG_ONCE breakpoint.
  - The next time the game calls that function, the thread parks at a breakpoint stop that no tool waits for. On the main thread this freezes the game.
  - Any later tool call resets the dead-man clock, so the stop stays parked while the agent keeps working.
  - The freed slot can then be claimed by another break, and the stale breakpoint would then use that break's condition.
  - The OLD_MODULE message says "fnv_native unload ..., then call again", which arms a second breakpoint on a new id.
- **Fix idea:** Call `_arm_call` before the breakpoint is armed, or wrap everything after `_set_bp` in try/except that sends `bp clear <id>` (and zeroes the call slot) before re-raising. Run the argument checks (12 words, resolving names, `_slot_has_edx`) before anything is armed.
- Status: open

### R6. `fnv_audio subtitles` drops every second subtitle (new code from the subtitles rewrite)

**Where:** `geck_mcp/fnv_inspect.py:671-681`: `seen = max(seen, rec[0] + 1)` with `self.deep.records(hook, since=seen)`. `DeepTools.records` (`fnv_deep.py:303-327`) returns records in (since, current], so `since` is exclusive.
- **Severity:** medium (wrong result in normal use of that action).
- **Confidence:** confirmed by repro (`regress\subtitle\repro_subtitles_skip.py`: 8 subtitles shown, 4 caught, lines 1, 3, 5 and 7).
- **What happens:** After reading record N, the next poll asks for records above N+1, so record N+1 is never read. When subtitles arrive one per poll (0.15 s), every second line is lost. Lines that arrive within one poll are all kept, apart from the one after the last of each batch.
- **Fix idea:** Use `seen = max(seen, rec[0])`, or use the sequence number that `records()` returns (as `fnv_blame` does).
- Status: open

### R7. After a server restart the camera journal entry still undoes with a blind `tfc`, so fnv_revert_all turns the fly camera ON (D2 incomplete)

**Where:** `geck_mcp/fnv_eyes.py:271-292` (`_remember` journals `{'op': 'console', 'line': 'tfc'}`) and `:405-421` (`restore`). The entry is marked undone only when `self.saved`, which lives in server memory, still holds the journal id.
- **Severity:** medium (the game is left changed in normal use: the supervisor restarts the server on every code change).
- **Confidence:** confirmed by repro (`regress\camera\repro_camera_tfc_after_restart.py`: after restart, restore, then revert, the fly camera is on).
- **What happens:**
  1. Server 1 turns the fly camera on, and the journal gets the undo `tfc`.
  2. After a reload, server 2 has no saved state. `restore()` takes the "No saved state: turned fly mode off" path and does not touch the journal.
  3. `fnv_revert_all` (or `fnv_session end`) then runs the blind `tfc` and turns the fly camera back on.
  - The same happens when the user turns the fly camera off by hand, or another tool does.
- **Fix idea:** Make the undo depend on the current state, for example a new undo op `fly_off` that runs `tfc` only while `fly_state()['fly']` is on. Also mark open `camera` journal entries of this game process as undone in the no-saved-state branch of `restore()`.
- Status: open

### R8. Journal: `mark_undone`'s `os.replace` fails while any other handle has `journal.jsonl` open, so an undo that already ran is not recorded and undo/revert_all stops (A1 fix, A7 again)

**Where:** `geck_mcp/fnv_live.py:104-115` (`mark_undone`: write the tmp file, then `os.replace`) and `:1395-1408` (`_apply_undos`, where the `try` covers only `_apply_undo`, not `mark_undone`).
- **Severity:** medium. It needs a second reader: another server process on the same state folder (two sessions on one game, as now), or an indexer or antivirus scan.
- **Confidence:** confirmed by repro (`regress\journal\repro_mark_undone_sharing.py`).
- **What happens:**
  - On Windows, `os.replace` onto a file that another handle has open without FILE_SHARE_DELETE fails with `PermissionError [WinError 5]`. Python's own `open()` handles are like that.
  - The A1 lock is per process, so another server reading `entries()` at that moment, or a scanner, makes it fail.
  - By then the undo has already run in the game. The error escapes `_apply_undos`, so that entry is not marked and the remaining undos do not run.
  - A retry applies the same undo twice, which is exactly A7. In the repro the undo was applied in the game and nothing was marked undone.
  - The old in-place `write_text` did not fail this way.
- **Fix idea:** Retry `os.replace` briefly on PermissionError and fall back to an in-place rewrite. Catch and report mark failures in `_apply_undos` per entry rather than aborting. For two processes, use an OS file lock (for example `msvcrt.locking` on a lock file) around read-modify-write, and give each process its own tmp name.
- Status: open

### R9. C5 is only partly fixed: some form-id references are still skipped as text (C5)

**Where:** `geck_mcp/fnv_records.py:96` with `_is_text` at `:128-130`. `geck_mcp/fnv_data.py:760` (`decode_subrecord`) uses the same check.
- **Severity:** medium (as C5).
- **Confidence:** confirmed by repro on the real FalloutNV.esm (`regress\data\c5_text_formids.py`).
- **What happens:**
  - A 4-byte field counts as text when it ends in 0x00 and its other bytes are printable.
  - FalloutNV.esm ids 0x0009xxxx, 0x000Axxxx and 0x000Dxxxx with printable low bytes pass that test.
  - The scan drops 81 real references in FalloutNV.esm alone: NPC_ RNAM races 000987DC to 000987DF (31), SCOL ONAM (29), TERM TNAM (11) and others.
  - `fnv_record_users` misses them, and `fnv_record_show` shows them as garbage text.
- **Fix idea:** Treat a 4-byte field as a form id when that id exists as a record, and check that before the text test. Or key the skip by record type and field. Apply the same change in `decode_subrecord`, then bump `REFS_VERSION`.
- Also found by the parser sweep: NPC_ RNAM on NVDLC01Elijah (01001300) decodes as text, so fnv_record_users misses that race reference.
- Status: open

### R10. `save_session` rewrites `session.json` with no lock: parallel act tools lose changes or corrupt it, and a corrupt file then breaks every act tool in a new server (D8 incomplete)

**Where:** `geck_mcp/fnv_game.py:239-253` (`save_session`) and `:232-236` (`session()` calls `json.loads` unguarded).
- **Severity:** medium.
- **Confidence:** confirmed by repro (`repro_save_session_race.py`, 3 runs).
- **What happens:**
  - Two threads each read the file, `clear()` and `update()` the shared in-memory dict, and write back only their own change.
  - In 1 of 3 runs `savesBackedUpFor` was lost. In 2 of 3 runs the two unsynchronised writes interleaved and left invalid JSON.
  - A fresh server process (after any reload) then raises inside `session()`, so `fnv_status`, `changing()`/`backup_saves` (every act tool) and `save_session` all fail until the file is deleted.
- **Fix idea:** Hold a lock around the read-modify-write, write through a temp file and `os.replace` (see finding 8 for the sharing caveat), and treat a corrupt file as {} with a log line.
- Status: open

### R11. Step over, step out and `step line` leave their temporary `to` site armed and unrecorded when another stop of the same thread comes first (E15 / L5 incomplete)

**Where:** `geck_mcp/fnv_debugger.py` `_run_to` (about `:1171-1190`): when `_addr(new['eip']) != address` it returns at once. `pending_to` is added only on STOP_TIMEOUT (`_await`) and in `until()`. Callers are `step` mode out (`:1153`), `_over` (`:1193-1199`) and `_step_line` (`:1226-1231`).
- **Severity:** medium (a surprise stop parks the thread, possibly the main thread, with nothing in status to say why).
- **Confidence:** likely (from reading the code; the companion keeps a SITE_TO until that thread reaches it, `w2_bp.c:404-414`).
- **What happens:**
  - A user steps over a call (or steps out), and a break inside the called code fires first.
  - The debugger shows that break. The `to` site after the call stays armed for that thread, but it is not in `pending_to` or the state file.
  - After `continue`, the thread parks again when the call returns, as a "step" stop the user did not ask for.
  - `fnv_debug status` does not list the site, so only "break op=clear" with no id removes it.
- **Fix idea:** In `_run_to`, when the stop that comes back is not at `address`, add the address to `pending_to` and persist it (as `until` does), and say so in the verdict. Or send `bp clear` for that site.
- Status: open

### T2. fnv_input: invalid key_name raises outside invoke()

- Where: fnv_live_tools.py:618 (key = scan_code(key_name) before invoke)
- Severity: medium
- Confidence: confirmed by a call (fnv_input with key_name="nokey")
- What happens: scan_code raises ValueError before the envelope wrapper, so the client gets a raw "Error executing tool" text instead of {ok:false, error.code:INVALID_ARGUMENT}; no operationId or game identity.
- Fix idea: move the scan_code call inside the lambda passed to invoke.
- Status: open

### P2. Vanilla compressed LAND with bad Adler-32 breaks full parse

- Where: esp/codec.py `decompress` (line 171-175, strict zlib.decompress).
- Severity: medium (high if FalloutNV.esm full parse is ever needed)
- Confidence: confirmed
- What happens: FalloutNV.esm LAND 00150FC0 (declared 4385, payload 4088) has a valid deflate stream but a wrong checksum. parse(lazy=False), validate.validate, diff.diff on FalloutNV.esm and record_show('FalloutNV.esm:150FC0') raise `Error -3 ... incorrect data check`. RecordIndex and scan_plugin are fine (decompressobj, fnv_data.py:252).
- Fix idea: inflate raw (`decompressobj(-15)` on payload[6:]) or tolerate checksum failure; wrap zlib.error in FormatError.
- Checked: zlib.decompress on that record payload fails with "incorrect data check".
- Status: open

### P5. record_find reports the wrong version's name and editor ID

- Where: fnv_data.py `record_find` (line 331, about 355-372); same pattern in record_users names (setdefault) and conflicts EDID.
- Severity: medium
- Confidence: confirmed
- What happens: `max(file)` with SQLite bare columns picks the highest file id, not the load-order winner. After FalloutNV.esm was re-indexed (new, higher file id), record_find('0015EDAC') returned `VDialogueVeronicaAbeMsgRefuse` while the winner is DeadMoney.esm ("It's better you don't...").
- Fix idea: select fields from the row of the file with the highest load-order index.
- Status: open

### N1. An unknown undo op is marked undone without running

- Where: geck_mcp/fnv_live.py:1347-1382 (_apply_undo).
- Severity: medium
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: `_apply_undo` handles 7 ops and has no else branch: an entry whose op it does not know falls through and is marked undone, so fnv_undo and fnv_revert_all report a change as reverted that was never touched.
- Fix idea: Raise on an unknown op (UNDO_UNKNOWN_OP) and leave the entry pending; one registry of undo ops (PLAN-3 S3.1).
- Evidence: `fnv-mcp/research/stabilize/CONTRACT-MAP.md (Executor, item 17)`.
- Status: open

### N2. A console undo is marked undone even when the game rejected the line

- Where: geck_mcp/fnv_live.py:1356-1357 (console), 1380-1381 (console_ref loop).
- Severity: medium
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: `console` and `console_ref` undo lines are sent and the entry is marked undone without checking `accepted` or `compileError` in the reply, so a rejected or non-compiling undo line counts as a successful undo.
- Fix idea: Check the console reply; a rejected undo leaves the entry pending with the reason.
- Evidence: `fnv-mcp/research/stabilize/CONTRACT-MAP.md (Executor, item 2)`.
- Status: open

### N3. After an undo the camera keeps its saved state, so later camera changes are not journaled

- Where: geck_mcp/fnv_eyes.py:273-274.
- Severity: medium
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: `fnv_undo`/`fnv_revert_all` run the camera entry's undo but leave `CameraTools.saved` in place. `_remember` returns early while `saved` is set, so the next set/orbit/pov/photo makes a camera change with no journal entry.
- Fix idea: Undo of a camera entry clears `saved` (the camera entry and `saved` are one record, PLAN-3 S3.3).
- Evidence: `fnv-mcp/research/stabilize/CONTRACT-MAP.md row 36; STATE-MAP.md (EyesTools.saved)`.
- Status: open

### N8. About a dozen tools change the game with no journal entry at all

- Where: fnv_deep.py (trace, tracepoint, profile hooks), fnv_debugger.py:615-624 (hold hooks), fnv_time.py (pause, gate), anim/audio/physics, fnv_naming.py (name_test, name_oracle), fnv_agent.py:1559-1572 (leveled_roll seed), `fnv_hooks remove all`.
- Severity: medium
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: These tools install hooks, hold gates, pause the clock or overwrite game state (leveled_roll with a seed rewrites the RNG while its docstring says it changes nothing) without a journal entry, so fnv_undo and fnv_revert_all cannot see or revert them.
- Fix idea: Journal every installed thing as an act change (D2, PLAN-3 S3.6).
- Evidence: `fnv-mcp/research/stabilize/CONTRACT-MAP.md (unjournaled list, tool kinds table)`.
- Status: open

### N10. 8 tool families journal after the change, so a failure leaves an unjournaled change

- Where: memory and patch writes (fnv_live.py, fnv_code.py), probe install, field watch, form delete, world_edit, camera, script_set.
- Severity: medium
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: The journal entry is written after the game was changed. A timeout or exception between the change and the journal write (for example COMPANION_TIMEOUT on a memory write) leaves a change that undo cannot see; the tool reports outcome_unknown.
- Fix idea: Journal the intent as pending before the change and confirm after (D5, PLAN-3 S3.2).
- Evidence: `fnv-mcp/research/stabilize/CONTRACT-MAP.md (journal sites table, before/after column; item 9)`.
- Status: open

## Round 2: Low

### G2. fnv_spawn with a reference spec (player, crosshair, a ref form id) fails with a script compile error

- Where: geck_mcp/fnv_agent.py:808 (base_form does not exist, so live.resolve(form) returns the reference itself)
- Severity: low
- Confidence: confirmed live
- What happens: fnv_spawn form=player -> SPAWN_FAILED "PlaceAtMe did not return a reference" with "Could not parse this line" output. The schema advertises player | crosshair | ref ids for form. A ref should map to its base form (copy of that object) or be refused with a clear BAD_FORM error. The `hasattr(self.live, 'base_form')` branch is dead code.
- Fix idea: if the resolved form is a reference (formType 58-60 etc.), use its base (GetBaseObject) or refuse.
- Status: open

### G3. A placed ref whose SetPos/SetAngle fails is never journaled

- Where: geck_mcp/fnv_create.py:1005-1019 (_place_one journals after _set, _pose and GetParentCell)
- Severity: low
- Confidence: likely (code reading; same shape as F10 for spawn)
- What happens: PlaceAtMe creates the ref first; any failure in _set/_pose/GetParentCell (a link timeout, a load starting) raises before journal.add, so fnv_undo / revert_all / place remove cannot remove that ref.
- Fix idea: journal right after PlaceAtMe (as spawn does since F10), then update the entry's pose.
- Status: open

### G6. fnv_wait_for seconds with clock=real uses the fixed step as the real frame rate

- Where: geck_mcp/fnv_time.py:450-455 (rate returns fixedFps first, for both clocks)
- Severity: low
- Confidence: likely (code reading)
- What happens: with fnv_time_control fixed fps=30 on a machine that renders 60 fps, seconds=10 clock=real waits 300 frames, which take 5 real seconds. The fixed step sets game time per frame, not frames per real second; only the game clock should use it.
- Fix idea: clock=real -> measured real fps (or wall time); clock=game -> fixed fps when set, else measured.
- Status: open

### G7. fnv_time_control status says "The game is running" while the clock is paused

- Where: geck_mcp/fnv_time.py status verdict
- Severity: low
- Confidence: confirmed live
- What happens: after pause, status gave verdict "The game is running at frame 10319" with clockCounter 1 and pausedByUs true. The verdict only looks at the gate.
- Fix idea: verdict "The game clock is paused (frames still run)" when clockCounter > 0.
- Status: open

### G8. The envelope's game.frame is the frame of the last ping, minutes old

- Where: geck_mcp/fnv_live.py:190-192 (identity prefers link.hello, whose frame is only refreshed by a ping)
- Severity: low
- Confidence: confirmed live
- What happens: after one fnv_player call every reply's envelope said frame 441444 for several minutes, while data.frame moved to 442345. Agents read the envelope frame as "now".
- Fix idea: report max(hello frame, link.last_frame), or name the field pingFrame.
- Status: open

### G10. fnv_globals set returns the whole unfiltered list, with the values from before the write

- Where: geck_mcp fnv_globals (set without query)
- Severity: low
- Confidence: confirmed live
- What happens: set {"GameHour": 13} with no query returned 200 of 258 globals (a large reply), and a listed value is the one read before the write (set 9 -> globals[] still says 13 while changed says new 9).
- Fix idea: with set and no query, list only the changed globals; re-read after writing.
- Status: open

### G11. fnv_hook_set: with a limit, actionHits counts every call and read says every call was changed

- Where: native/engine/fnvmcp_engine.c:580-581 (act_hits incremented before the limit test); geck_mcp/fnv_control.py:168 (resultNote)
- Severity: low
- Confidence: confirmed live
- What happens: hook_set result value=7 limit=5 on Actor::GetLevel. The limit itself works (console player.GetLevel after it -> 1.00), but the hook then reports actionHits 3272 with actionLimit 5, and read's resultNote says "callers got 0x00000007 instead" over a list of 30 calls that were all past the limit. An agent reading it concludes the change is still active on every call.
- Fix idea: count only applied actions (or report applied = min(hits, limit)), flag each record whether the action applied, and word the note from that.
- Status: open

### G12. fnv_mem_write: an out-of-range value is FNV_ERROR, and setting types show as mangled names

- Where: geck_mcp fnv_mem_write (struct.pack errors not mapped; type name from RTTI not demangled)
- Severity: low
- Confidence: confirmed live
- What happens: as_type u8 value 300 -> FNV_ERROR "'B' format requires 0 <= number <= 255" (not INVALID_ARGUMENT); writing a game setting by address without as_type -> "Cannot write a value of type '?$SettingT@VGameSettingCollection@@'".
- Fix idea: range-check per type and raise INVALID_ARGUMENT; demangle or name the class (Setting).
- Status: open

### G13. After break clear, a stop that is still parked is described as "break 0"

- Where: geck_mcp/fnv_debugger.py (status: why text from the freed bp slot)
- Severity: low
- Confidence: confirmed live
- What happens: break set on Actor::GetLevel (break 1), stopped, break op=clear, status -> "Stopped in Actor::GetLevel ...: break 0 (trap)". Break 0 does not exist.
- Fix idea: keep the break number in the stop record when it is taken, or say "a cleared break".
- Status: open

### G14. fnv_console runs qqq (and other quit/load lines) unguarded; the quit is then traced as not asked by the bridge

- Where: geck_mcp/fnv_live.py:235 (console has no deny list; geck_mcp/console.py has one for the GECK console)
- Severity: low
- Confidence: confirmed live
- What happens: fnv_console line=qqq quit the game ("Bye."). fnv_status lastExit then says how=console, servingRequest=true, askedByBridge=false, although the bridge sent it, so the S4 diagnosis points away from the bridge. Game.quit (session bookkeeping, quit-log) is bypassed.
- Fix idea: refuse qqq/quitgame/load/coc-style lines in fnv_console with a pointer to fnv_quit / fnv_move / fnv_launch, or route them through those; at least log a quit-log entry when the line is a quit.
- Status: open

### G15. fnv_status fails with SEVERAL_GAMES while a game is exiting

- Where: geck_mcp/fnv_game.py (process() refuses when two FalloutNV.exe exist)
- Severity: low
- Confidence: confirmed live (seen once)
- What happens: right after qqq, fnv_status returned SEVERAL_GAMES with pids [17324, 28984]; a second later both were gone and status worked. A short-lived second FalloutNV.exe appears during shutdown (xNVSE/CrashLogger exit path; unknown which). Any tool called in that window fails.
- Fix idea: when one of the pids is the session's game and it is exiting (or the other one is younger than a few seconds), report not_running/exiting instead of failing; retry once after a short wait.
- Status: open

### G16. fnv_engine_get ignores extra arguments

- Where: geck_mcp/fnv_control.py:396-404 (engine_get types args by position and never compares the count with the signature)
- Severity: low
- Confidence: confirmed live (extra args); too few by code reading
- What happens: Actor::GetLevel (no parameters) with args [1,2,3] returned 1, ok. With too few args the callee reads its missing parameters from whatever lies on the stack above the pushed ones (the companion restores esp afterwards, so no corruption, but the answer is computed from garbage and reported as a result).
- Fix idea: refuse when len(args) differs from the signature's parameter count (TOO_MANY_ARGS).
- Status: open

### R12. `fnv_session start` rollback leaves prepare's crash-context hooks on, and stops a warning capture it did not start (A11 incomplete)

**Where:** `geck_mcp/fnv_session.py:77-117`; the rollback is at `:105-117`. `crash_context('enable')` is in `fnv_scenario.py:539-547`.
- **Severity:** low.
- **Confidence:** confirmed by repro (`repro_session_start_rollback.py`).
- **What happens:**
  - If `snapshot`, `mark` or `_refs` raise, the rollback stops only the capture and the fixed step. The script hooks that `prepare` turned on stay on, with no session to end them.
  - `warnings_state['capture']` is also True when the capture was already running ("Already capturing.", `started` False), so the rollback stops the user's own earlier capture.
- **Fix idea:** Call `crash_context('disable')` in the rollback, and stop the capture only when `start()` reported `started: True`.
- Status: open

### R13. `fnv_debug thaw` with nothing on record resumes every thread of the game once (E1/E6 fix)

**Where:** `geck_mcp/fnv_debugger.py` `thaw()` (about `:1390-1404`): `proc.resume_all(proc.threads())` when `self.frozen` is empty.
- **Severity:** low.
- **Confidence:** possible.
- **What happens:**
  - This also runs on a plain double thaw, or a thaw when nothing is frozen.
  - `ResumeThread` decrements the suspend count of every thread. A thread the game, a plugin or the companion holds suspended at that moment is released.
  - The companion's own `probe_suspend` is one case. It now runs for every hook, unhook and code patch (`code_write_frozen`, B2 fix) and every coverage arm, so a thread could be released while code is half written.
  - The comment "a thread that was not suspended is unaffected" holds only for threads with suspend count 0.
- **Fix idea:** Force-resume only when the game is actually stalled (for example every thread suspended, or no frame advance), and only threads whose suspend count is at least 1 and that `suspend_all` would have frozen (skip the companion's pipe thread). Or require an explicit `force=true`.
- Status: open

### R14. C2 is only partly fixed: a failed refs index build keeps the SQLite write lock (C2)

**Where:** `geck_mcp/fnv_records.py:175-208` (`_ensure_refs`: delete at `:202`, commit at `:208`, no rollback). Also `geck_mcp/fnv_data.py:177-178`: when the plugin is already up to date, `ensure` returns before its rollback.
- **Severity:** low (the triggers are rare: disk full, an I/O error, "database is locked" at commit).
- **Confidence:** the mechanism is confirmed by repro (`regress\data\c2_refs_no_rollback.py`; the failure is forced).
- **What happens:** If `_ensure_refs` fails after its DELETE, that thread's connection keeps an open transaction holding the write lock. Every other thread's write then fails with "database is locked", and a later `ensure()` on the same thread does not clear it.
- **Fix idea:** Put the delete, insert and commit in `with db:`, and roll back any leftover transaction at the start of `_ensure_refs` and in `ensure`'s up-to-date path.
- Status: open

### R15. C3 is fixed within one process only: two server processes still collide (C3)

**Where:** `geck_mcp/fnv_data.py:168-210`. `_ensure_lock` is a threading lock, and the re-check at `:182` can run before the other process commits.
- **Severity:** low (one call fails; calling again works).
- **Confidence:** confirmed by repro (`regress\data\c3_two_processes.py`).
- **What happens:** Two MCP clients share `state/fnv/records.sqlite`, as with two sessions on one game. After a plugin rebuild, both processes rebuild it, and the second insert fails with `IntegrityError: UNIQUE constraint failed: files.path`.
- **Fix idea:** Check again inside the write transaction, once the lock is held, and return the existing id. Or delete by path rather than by the old id.
- Status: open

### R16. C13 is only partly fixed: strict cp1252 decodes remain in other readers (C13)

**Where:** `geck_mcp/fnv_create.py:64, 71, 745, 751, 767, 818`; `geck_mcp/compilation.py:25, 39`; `geck_mcp/authoring.py:126`; `geck_mcp/fnv_data.py:511`; `esp/assets.py` and `esp/scripts.py`. Only `codec.cstring` was changed.
- **Severity:** low.
- **Confidence:** likely.
- **What happens:** A plugin string with an undefined cp1252 byte still raises UnicodeDecodeError, for example from `fnv_form reload` reading FULL/MODL/MAST, from BSA folder names, or from compiling an in-game script.
- **Fix idea:** Use the same `cp1252-bytes` error handler (or `errors='replace'` for display) at these sites.
- Status: open

### R17. The F16 fix leaves its own error path open: an item that fails mid-test stays in the player's inventory (F16)

**Where:** `geck_mcp/fnv_crawl.py:483-517` (`_items_rows`).
- **Severity:** low.
- **Confidence:** likely.
- **What happens:** After `player.AddItem`, an exception from EquipItem, UnequipItem or GetItemCount goes to the per-item `except`. The count-based RemoveItem never runs, so the test copy stays.
- **Fix idea:** Wrap the AddItem to RemoveItem(count - had) step in its own try/finally.
- Status: open

### R18. The new "terminated from outside" note in `fnv_status` is wrong with an older companion and when the bridge itself force-quit (S4 diagnosis)

**Where:** `geck_mcp/fnv_game.py:305-313`.
- **Severity:** low (the diagnosis text is wrong).
- **Confidence:** likely.
- **What happens:**
  - The note appears whenever there is neither an exit note nor a crash log since launch.
  - A companion built before this commit never writes `exit-*.json`, so every normal quit is blamed on "outside".
  - `quit(force=True)` (TerminateProcess) is blamed on "outside" too, although `quit-log.jsonl` records that the bridge did it.
- **Fix idea:** Show the note only when the companion is new enough to write exit notes, and check `quit-log.jsonl` for a bridge quit first.
- Status: open

### R19. Journal entries made with no hello on the link carry `pid: None` and can never be undone (A3 fix)

**Where:**
  - `geck_mcp/fnv_live.py:89-96`: `Journal.add` stamps a pid only when `'pid' not in entry`.
  - The callers that pass `'pid': (self.link.hello or {}).get('pid')`: `fnv_live.py:1003`, `fnv_control.py:239, 295`, `fnv_world.py:398, 402`, `fnv_data.py:546, 612, 708`.
  - `fnv_link.py:95, 75, 152`: `close()` and connect errors now null `hello`.
- **Severity:** low.
- **Confidence:** possible (needs the link to close between the tool's ping and its journal write).
- **What happens:**
  - Since the S11 fix, any `close()` clears `hello`. If a change is journaled after a reconnect with no ping, the entry has an explicit `pid: None`.
  - `add()` then skips its own stamping, and `same_process` returns False for an entry without a pid. The change shows as "made in another game process (unknown)" and `fnv_undo` / `fnv_revert_all` never revert it.
  - Before the fix, entries without a pid were undone.
- **Fix idea:** In `add()`, stamp when `not entry.get('pid')` (drop a None pid). Or have the callers use `self.live.identity()`, which falls back to the process.
- Status: open

### R20. E6 state file: one save with a different `start` drops every other section (ProcState)

**Where:** `geck_mcp/fnv_pred.py:400-421` (`ProcState.save` keeps only records whose pid and start equal the caller's), with `fnv_game.process_started` returning None when `OpenProcess` fails.
- **Severity:** low.
- **Confidence:** possible.
- **What happens:**
  - All sections share `debug_state.json`: time pause, script freeze, debugger breaks, frozen threads and pending `to` sites.
  - If one caller sees `start=None` for the same pid (a transient `OpenProcess`/`GetProcessTimes` failure, or a `tasklist` row read during process start), its save deletes every other section.
  - That loses the pause, freeze and break records E6 meant to keep, so a restarted server can no longer undo them (E6 again).
  - The write is also not atomic, and a torn file reads as {}.
- **Fix idea:** Drop other sections only when their pid differs, or their start differs and both starts are known. Write through a temp file.
- Status: open

### T3. fnv_name_export writes a trace file even when it has nothing to export

- Where: fnv naming export tool (fnv_live_tools.py, fnv_name_export registration)
- Severity: low
- Confidence: confirmed by a call (offline call created fnvsym/traces/fnvmcp-<date>.json with an empty payload)
- What happens: a read-looking export tool creates a file in the decomp tree with zero entries, and does not report that nothing was exported; repeated calls overwrite.
- Fix idea: return changed:false / NO_DATA without writing when there are no entries.
- Status: open

### T4. Inconsistent error codes for "game not running"

- Where: fnv_live.py ready()/in_game(), fnv_link.py EngineLink.connect, fnv_game.game_processes users
- Severity: low
- Confidence: confirmed by a call (offline sweep)
- What happens: depending on tool, the same condition returns different codes (a LiveError not-running code, COMPANION_* connect failure, or generic FNV_ERROR). Agents cannot branch on one code. Non-ValueError unexpected exceptions also map to FNV_ERROR with retrySafe true even for act/control kinds, which may claim retry is safe after a possible change.
- Fix idea: one GAME_NOT_RUNNING code from ready(); treat FNV_ERROR in act/control kinds as outcome unknown (retrySafe false).
- Status: open

### T5. Raw int(...,16) ValueError text in fnv_probe and fnv_events arguments

- Where: fnv_live_tools.py (fnv_probe, fnv_events address/id parsing)
- Severity: low
- Confidence: confirmed by a call (non-hex string input)
- What happens: error message is Python's "invalid literal for int() with base 16: 'zz'" with no mention of which parameter; code is INVALID_ARGUMENT.
- Fix idea: wrap with a helper naming the parameter and expected format.
- Status: open

### T6. Short form ids (no leading zeros / no load-order prefix) not accepted consistently

- Where: fnv_live.py resolve(); record tools in fnv_tools.py
- Severity: low
- Confidence: likely (some tools accept "0x14", others require 8 hex digits or an EditorID)
- What happens: the same form id string works in one tool and gives not-found in another; docs do not state the accepted forms.
- Fix idea: one shared parser; document the accepted forms.
- Status: open

### T7. fnv_asset_check accepts arbitrary paths and surfaces a raw parse error

- Where: fnv_asset_check registration (fnv_live_tools.py) and its asset reader
- Severity: low
- Confidence: confirmed by a call (malformed/non-asset path)
- What happens: the path is not restricted to the game Data directory, and a malformed file returns a parser exception string rather than a structured BAD_ASSET code.
- Fix idea: confine to Data/, catch parse errors into a named code.
- Status: open

### T8. Parameters documented/required but ignored

- Where: several fnv_* registrations (fuzz found calls where the parameter changed nothing)
- Severity: low
- Confidence: possible
- What happens: some parameters are accepted and ignored, or required by the schema but unused by the implementation, so an agent cannot tell its argument had no effect.
- Fix idea: drop them or reject when set.
- Status: open

### T9. geck_compile_status vs geck_compile_resume return different codes for unknown operation

- Where: geck_* compile tools in server.py
- Severity: low
- Confidence: likely
- What happens: the same unknown/finished-operation condition gives different error codes in status and resume.
- Fix idea: share one code.
- Status: open

### P3. Corrupt input raises raw exceptions rather than FormatError

- Where: esp/codec.py lines 207 and 224 (`tag.decode('ascii')` -> UnicodeDecodeError), `iter_records` (struct.error on truncated group/record header), `decompress` (zlib.error), `Group.label_text` (non-ASCII type-0 label).
- Severity: low
- Confidence: confirmed by fuzz
- What happens: validate, which is documented never to raise for content problems, raised UnicodeDecodeError / zlib.error / struct.error on mutated plugins.
- Fix idea: catch and convert to FormatError, or decode with latin-1 as fnv_data does.
- Status: open

### P4. XXXX round trip not byte exact

- Where: esp/codec.py `encode_subrecords` (line 293).
- Severity: low
- Confidence: confirmed (synthetic; no real plugin triggers it)
- What happens: a subrecord preceded by XXXX but with size <= 0xFFFF (even XXXX=0) is re-encoded without the XXXX, so write(parse(b)) != b despite the docstring claiming exactness.
- Fix idea: remember that the XXXX was present, or document the normalisation.
- Status: open

### P6. `_form` off-by-one

- Where: fnv_data.py:430 `high >= len(own) + 1`.
- Severity: low
- Confidence: likely
- What happens: a top byte equal to len(own) (invalid, one past the master count) resolves to the plugin itself, so non-form 4-byte values can show as forms.
- Fix idea: `high > len(own)` is the self index; check the intended boundary (masters count = len(own), self index = len(own), so anything above must be rejected, but `>= len(own)+1` is right only if self is len(own); verify against 0-based masters).
- Status: open

### P7. Index and lookup gaps

- Where: fnv_data.py `SKIP_EDID` (LAND, NAVM, PGRE, PMIS, NAVI) and `record_find`; lint `_check_call` (line 989).
- Severity: low
- Confidence: confirmed
- What happens: named placed grenades/mines/NAVM are not found by EditorID. `FiendMineRef` (PGRE) is not in the index, so lint warns UNKNOWN_REFERENCE for `FiendMineRef.enable` in FiendAmbushTriggerScript. NAVM `NVDLC04ElevNav02REF` (LonesomeRoad) is also missing. Hard-coded forms such as PlayerREF (0x14) are not in the index. Huge offset/limit raise OverflowError; limit=-1 returns unlimited results.
- Fix idea: index EDIDs for PGRE/PMIS (only skip LAND/NAVI if cost matters); seed engine forms; clamp offset/limit.
- Status: open

### P8. Lint false positives on vanilla scripts

- Where: fnv_data.py `_split_args` (line 969), `_check_call` (line 989).
- Severity: low to medium
- Confidence: confirmed (3,719 SCPT; 12 scripts report errors)
- What happens:
  - UNKNOWN_COMMAND '' error for `VStripSS1SecuritronBREF. StopLook X` (VStripSS1ControlScript, lines 183-184); the game accepts a space after the dot.
  - UNKNOWN_REFERENCE for `PlayerREF` (8 hits); `_check_call` does not consult KNOWN_NAMES.
  - TOO_MANY_ARGUMENTS 237 warnings: ShowMessage is variadic (66 false positives, e.g. `ShowMessage Msg rank` in ChallengeBugSCRIPT); the game ignores trailing tokens on zero-parameter commands (RemoveScriptPackage 89, StopCombat 62, StopLook 11, EvaluatePackage 5).
  - DUPLICATE_VARIABLE / VARIABLE_SHADOWS_COMMAND on declarations followed by prose (weak; possibly stale source).
- Fix idea: trim whitespace after `.`; consult KNOWN_NAMES in `_check_call`; mark ShowMessage variadic; downgrade extra args on zero-parameter commands to info.
- Status: open

### P9. Spec validation regexes accept a trailing newline

- Where: esp/records.py:14 `EDID_RE` (`^...$` with re.match); build.py `check_spec` for project_id, plugin, REF_ID_RE.
- Severity: low
- Confidence: confirmed
- What happens: `'x.esp\n'` passes check_spec; pydantic `config.Record` correctly rejects it. model_path accepts NUL, newline, `*`, tab and `con.nif`; `text()` accepts an embedded NUL.
- Fix idea: use `fullmatch` or `\Z`; reject control characters and reserved device names.
- Status: open

### P10. Save parser: FLT_MAX rotation sentinel passed through

- Where: fnv_save.py ACHR/ACRE initial block decode.
- Severity: low
- Confidence: likely
- What happens: 25,228 occurrences across all saves of `rotation.x = 3.4028234663852886e+38` (bytes ffff7f7f), e.g. autosave.fos ACHR 0200CFF3 and 0011253F. It appears unflagged and shows in save diffs as noise.
- Fix idea: map the sentinel to None or flag it as "unset". Also theoretical: `global_variables`/`inspect` with a namer would hit `int(None, 16)` if a global form were unresolved (never occurred in the data).
- Status: open

### N4. `form_delete` does not supersede `form_create`, so export keeps deleted forms

- Where: geck_mcp/fnv_create.py:962.
- Severity: low
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: Deleting a created form only removes it from `FormTools.created`; the `form_create` journal entry is not marked undone, so `export` (which reads the journal) still lists the deleted form.
- Fix idea: A `form_delete` entry supersedes its `form_create` entry (PLAN-3 S3.4).
- Evidence: `fnv-mcp/research/stabilize/CONTRACT-MAP.md row 31`.
- Status: open

### N5. Undoing a probe only disarms it: the stubs stay patched and `probe_session.json` still says armed

- Where: geck_mcp/fnv_probe.py:432.
- Severity: low
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: The probe journal entry's undo is `probe_disarm`. The patched call sites and stubs stay in the game, and `probe_session.json` is not updated, so the next server reads the probe as armed.
- Fix idea: A `probe_remove` undo op that unpatches the sites and updates the session file (PLAN-3 S3.4).
- Evidence: `fnv-mcp/research/stabilize/CONTRACT-MAP.md row 44`.
- Status: open

### N6. The `dbg_clear` undo clears every debug register, including other tools'

- Where: geck_mcp/fnv_watch.py:170.
- Severity: low
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: A field-watch entry undoes with `dbg clear f`, which clears all four hardware debug registers, so an fnv_debug hardware break or another watch is removed too.
- Fix idea: Record the slot and clear only that slot (PLAN-3 S3.4).
- Evidence: `fnv-mcp/research/stabilize/CONTRACT-MAP.md rows 37 and 48`.
- Status: open

### N7. The rng seed undo does not restore the index at +0x9C8

- Where: geck_mcp/fnv_agent.py:1386, 1409.
- Severity: low
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: fnv_rng seed writes the 624-word state and the index at instance+0x9C8; the undo restores only the state array, so the generator resumes at the wrong position.
- Fix idea: Journal the old index with the state and restore both (PLAN-3 S3.4).
- Evidence: `fnv-mcp/research/stabilize/CONTRACT-MAP.md row 17, item 15`.
- Status: open

### N9. actor_control enable, disable, unconscious, restrain and add_item have blind undos

- Where: geck_mcp/fnv_live.py:1245.
- Severity: low
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: The undo is a fixed console line (`enable`/`disable`, unconscious off, restrain off, RemoveItem) that does not look at the state before the change: undoing a disable of an actor that was already disabled enables it.
- Fix idea: Record the prior state and undo to it (PLAN-3 S3.3).
- Evidence: `fnv-mcp/research/stabilize/CONTRACT-MAP.md row 9`.
- Status: open

### N11. The game's identity is chosen six different ways, and `tasklist` runs on every `game.process()` call

- Where: geck_mcp/fnv_game.py:~33-52; fnv_code.py:89 and four other pickers.
- Severity: low
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: Start time is an int in some pickers, a string in others and absent in several; match rules range from strict to pid-only. `game.process()` shells out to `tasklist` on every call, and `CodeTools._game_id` calls it on every property access.
- Fix idea: One GameIdentity(pid, start) with one match rule and a 1 s cache (D4, PLAN-3 S2.2).
- Evidence: `fnv-mcp/research/stabilize/STATE-MAP.md (identity pickers, item 8)`.
- Status: open

### N12. Nothing under `state/fnv` is pruned, and none of it has a cross-process lock

- Where: state/fnv/* (journal.jsonl, quit-log.jsonl, code/, session.json, probe files, oracle and experiment outputs).
- Severity: low
- Confidence: confirmed by reading the code (stabilization research, not yet reproduced)
- What happens: Six projects share `state/fnv`. The writers use per-process locks only (or none), and the journal (5,314 entries), quit log, crash contexts and experiment outputs grow without bound.
- Fix idea: One state module with a cross-process lock and atomic writes, plus pruning (PLAN-3 S2.1, S2.6).
- Evidence: `fnv-mcp/research/stabilize/STATE-MAP.md`.
- Status: open
