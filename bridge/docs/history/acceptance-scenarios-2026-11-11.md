## Acceptance scenarios S1, S2, S4, S6 (Plan 2, run in the game 2026-11-11 night)

Each scenario was run against a plugin built for it with the spec builder (`examples/hello-bug` is in the
repository; the other three needed game files it cannot ship), its scripts compiled by the running game. What failed on the way was fixed and is listed.

**S1, build a quest mod and play it as a player** (one `fnv_test steps` call): PASSED, 23 steps in 64 s, no console and no teleport. The player walks to a contact, talks,
picks "Sure, I'll look for them." (stage 20, set by the line's result script), walks to and picks up three tokens
(stage 30 from the quest script), talks again, hands them in (stage 40, the tokens taken, 100 caps given by the
result script), and a photo sheet of the contact is taken. Fixed on the way:
- The builder writes dialogue: topics (`DIAL`), lines (`INFO`) with responses, conditions (`CTDA`), choices
  (`TCLT`), flags and result scripts, and new lines on a master topic (`GREETING`) through an override copied from
  its template (`geck_mcp/esp/dialogue.py`). `fnv_scripts_compile` compiles result scripts in the game inside a
  one-block wrapper and keeps the block's statements, the layout of FalloutNV.esm's result scripts.
- A line's result script runs only when the line's record flag `0x2000` is set: all 6,277 lines of FalloutNV.esm
  with a compiled result script carry it and none of the 16,970 others (`TESTopicInfo::GetResultScript` reads the
  scripts from the file only then). Without it the line was said and its script never ran.
- `fnv_test steps` has a `dialogue` step: `{choose: [option, ...], speaker: ref}`, the call a click on a response makes.
- `fnv_play goto` waits up to 3 s for a message box that something closes (the DLC "items added" boxes, closed by
  `fnv_test steps`) and walks on, instead of stopping.

**S4, a script misbehaves** (`examples/hello-bug`): PASSED. HelloBugQuest counts the global HelloBugLevel up to
100 and never gets there. `fnv_script_debug` stopped on source line 8 (`if HelloBugLevel >= 100`), eval read 39,
step went to line 10. `fnv_blame field HelloBugLevel + 0x24` named "script 0D000803 line 8: set HelloBugLevel to
0", the janitor quest's script. `fnv_script_hot_swap` swapped in the fix; the regression test
(`tests/hello-bug-finishes.yaml`) failed before the fix and passed after, and the quest reached stage 10. Fixed on
the way:
- The game's script runner counts statements, not source lines (its "line 7" was the 7th statement, an endif on
  source line 10). `fnv_script_debug` takes and shows source lines, mapped through the script's text (the game's,
  else its plugin record's SCTX), with the statement number beside them. Code swapped in from an in-game compile
  keeps xNVSE's extra scriptname statement, so it counts one more; the mapping reads that from the code.
- `fnv_blame field` named the wrong scripts: it read "the last statement the thread ran" after the watch ended. The
  companion now stamps each main-thread watch hit with the newest hook record the main thread wrote (hit words 8
  and 9); field reads its hits every 0.2 s while watching (the statement ring wraps in about a second) and reads
  that one record: the exact statement, with its source line.
- In-game compiles replaced global EditorIDs with form ids when a first compile failed for another reason
  (`set <form id> to` is no variable). Globals keep their names (the running game keeps them).
- Quest scripts do not run while the pause menu is open (it opens when the game window loses focus): the guide says
  to close it with `fnv_ui_do`.

**S6, a new mesh looks wrong**: PASSED. A crate mesh shipped loose in its mod names a
misspelled texture (`CrateLarge0l.dds`). The photo sheet (four angles) showed it without its paint beside the
vanilla crate; `fnv_asset_check` named the missing texture and the mesh; the mesh file was fixed in the mod folder;
`fnv_asset reload` took 0.66 s with no restart; the sheet after shows both crates alike. Fixed on the way:
- `fnv_photo` of a base form (the STAT's EditorID instead of the placed reference) crashed the game in
  `Cmd_GetPos_Eval`. The eyes and hands tools refuse a base form now (`NOT_A_REFERENCE`, with the next step).
- The builder's asset check searched only Data: a mesh in the plugin's MO2 mod folder failed the build. It searches
  the enabled mods and the plugin's own mod folder now.
- Not fixed: `fnv_asset file_log` did not see the missing texture (the engine never asked `FileFinder::GetFile` for
  it); `fnv_asset_check` finds it offline.

**S2, a load order crashes on entering a cell**: PASSED. The crate mesh of HelloCrashRoom
has its root node's child pointing at its strip data. With the crash hold on (`fnv_debug break location=fault`),
`coc HelloCrashRoom` faulted on a loader thread in `NiAVObject::AttachParent` (from `NiNode::SetChildAt`,
`ReadChildListFromStream`, `NiStream::Load`, `QueuedModel::Run`); the game stayed alive, held. `fnv_debug status`
named the fault and `hellocrash\crate.nif` on the faulting thread's stack; `fnv_asset where` gave the HelloCrash mod
folder and `fnv_record action=find plugin=HelloCrash.esp` the STAT HelloCrashCrate that uses it. Fixed on the way:
- A fault on a loader thread during a load leaves the main thread waiting for it, and the companion answers only
  on the main thread: every call timed out. `fnv_debug status` and `stack` now fall back to reading the held
  fault from outside: the registers of the fault from the companion's context file (the first fault of that thread;
  a crash logger faulted again later), the stack above them named with the engine map, and file paths found on that
  stack.
- `fnv_threads action=hang` samples, besides the main thread, the threads that use the most CPU and the threads held at a
  fault, and reads a stack that sits near the top of its region in smaller pieces.
- Seen on the way: a mesh whose root claims 268 million children does not crash; the load job never finishes and the
  game hangs in `TES::LoadInterior`.

Not run: `fnv_bisect` in the game (the MCP server must launch the game; it was not reconnected with the new tools).
