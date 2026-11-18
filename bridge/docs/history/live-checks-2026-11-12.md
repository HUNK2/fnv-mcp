## Live checks of the leftovers (2026-11-12)

Every item the completion pass left as "built, checked offline" was run in the game (Prospector Saloon, the Hello
plugins). Five were wrong and are fixed; one was removed. Tests: 858 pass.

- `fnv_game launch start=cell:` **removed**. SStartingCell in the INI crashes the retail game at startup in every cell
  tried (a plugin interior and GSProspectorSaloonInterior): TES::LoadInterior -> LoadingMenu setup 0x00788AB0 ->
  Tile::Set on InterfaceManager's root tile, still null because the main menu builds it. `coc:` (through the main
  menu) reaches a cell in 11 s. A `cell:` start now answers CELL_START_UNSUPPORTED with the `coc:` to use.
- `fnv_ui_do type` **fixed**: calling PlayerNameEntryMenu::Finish 0x007AB9A0 on the TextEditMenu crashed the game
  (Finish belongs to the name entry menu object, whose +0x28 is the player's NPC). The TextEditMenu is generic: its
  Done handler 0x007E66A0 copies its string (+0x34) to 0x011DAED8 and closes it, and the name entry menu takes the
  text on its next update (PlayerNameEntryMenu::UpdateDialog 0x007AB7E0). Run: the menu opened through the engine's
  queue (9 written to 0x011D8950, HandleQueuedMenuOpen builds "Player Name Entry Menu"), `type` "Courier Six", the
  menu closed and the player's base form is named Courier Six.
- `fnv_anim idle_tree` **fixed** (was approximate and wrong: it named SpecialIdle_Shrug for every actor). The answer
  now is the engine's own IdleAnimationManager::FindIdleFormForObject 0x00600950 (what HighProcess::StartIdleAnimation
  0x008DAB40 asks), with the actor's replay delays (animation +0x134). The walk uses the same folder as the engine:
  TESObjectREFR::GetModelFilePath 0x005715D0 -> BuildIdlePath 0x006007F0 -> GetFormArrayForIdleDir 0x00600560 (20
  roots for a male NPC, not the one-entry array GetIdleArray gives for the base form). Seen: Easy Pete (sitting) 0006F8C1
  from engine and walk alike; Sunny and a settler "no idle": the walk reaches a group with no file of its own, which
  the engine's last check drops (reported as walkAnswer with a note).
- `fnv_systems barter` **fixed** and compared with open barter menus: 28 of 28 prices equal (14 the player sells to
  Sunny, 14 Trudy sells). The value now comes from the player's inventory entry (ExtraContainerChanges via
  GetByType 0x00410220; entry value 0x004BD400 with condition and weapon mods: a worn 10mm pistol is 1,197, not
  1,200) or, for items not carried, the form (0x0048E8A0, which also prices ingestibles; the console's GetValue
  gives 0 for them). Rounding is half up (0x004BD510; Python's round() is not), and the menu holds a buy price at or
  over the value and a sell price at or under it (0x0072ED00).
- `fnv_code compare mode=shadow` **fixed** and run: FUN_00404010 (max of two floats): 200 live calls in 3 s, 39
  distinct, all agree; with max turned into min, 37 of 38 differ (original 5, new 1 for 1 and 5). The replay passed
  the recorded ecx as `this` to every function, which a cdecl function takes as an extra first argument (the
  originals then read 32916.06 for max(1, 5)); `this` now goes only to thiscall and fastcall functions.
- Short-function swaps run: FUN_00a62660 (4 bytes, an NiTexture getter): 19 call sites and 3 vtable slots
  redirected, frames kept coming, unswap put all 22 back (checked byte by byte). **Fixed**: the swap list lived only in
  the bridge process, so a restarted MCP server could not undo a swap the game still had. It is now kept in the
  bridge state (`fnv/code/swaps.json`) with the game's pid and start time; a new game process starts empty.
- `fnv_script_debug step over|line|out` run on HelloBugQuestScript and VCG01SCRIPT. `out` **fixed**: the script
  runner is a stack object, so scripts the game runs one after another reuse its address and "a different runner"
  never came (4,096 statements, then STEP_LIMIT). A run now also ends when another script runs or the same script
  starts again (its offset goes back). Also **fixed**: after the last break was cleared the EvaluateLine hook stayed,
  recording every statement of every script for the rest of the session (1.46 million records seen); it is now
  removed unless `fnv_script_log commands` started it. Breaks and the stop live in the server process: a restarted
  server drops its hooks (a held game resumes). Inside a block the runner skips (a MenuMode block outside that menu)
  the stop's opcode field showed the previous statement's opcode; the line mapping was right.
- `fnv_audio subtitles`: a spoken line outside dialogue gets a subtitle only when General Subtitles is on
  (bGeneralSubtitles 0x011D8928, value at +4, off by default; VoiceHost::PlayVoiceTopic 0x00578A80 checks it). The
  result now says when it is off, and `enable=true` turns it on for the recording only (seen on, then back to 0). No
  subtitle was caught: after Sunny's first Say (her greeting voice file played), no NPC spoke again on Say or SayTo
  (PlayVoiceTopic was never called; the hooks themselves recorded 752 calls of a test function), so the capture of a
  subtitle is still not seen.
- `fnv_asset reload .kf` with `ref=`: Sunny's animation object was rebuilt (0x1C5D2888 -> 0x1B293158) with her 287
  groups; without `ref` only unused copies are dropped.
- **Rays were broken everywhere** (found while building surface alignment): bhkPickData::SetFilter 0x004A3F70 takes
  its filter on the stack (`mov [ebp-4],ecx`, the filter read at `[ebp+8]`, `ret 4`), but both ray callers
  (fnv_hands, fnv_engine_ai) passed it in edx, so the filter was whatever the stack held (seen: 0x05EAC25D, layer
  93) and rays missed indoors and out. Fixed: a downward ray now meets the saloon floor at the player's feet (3,456)
  and a sight line to Sunny stops on the wall between (NVGSWallONLY). `fnv_raycast`, `fnv_place snap/check`, the walk
  planner and `fnv_physics query` all use these rays. Outdoor Havok coordinates have no origin shift (a crate's
  body is its position / 6.9991).
- `fnv_place snap align=true` built: the slope from five rays 24 units apart (the pick output has no normal), then
  SetAngle X and Y. Measured from the 3D's world rotation (+0x68; translation +0x8C): the up axis is
  (-sin Y, sin X cos Y, cos X cos Y) whatever the heading, so X = asin(n_y / cos Y), Y = -asin(n_x). Three crates
  on a 22 to 46 degree slope at headings 0, 37 and 200: up axis = surface normal (dot 1.0). A side that rises
  more than 96 units over the 48 is a step or a wall and is left out (a 71 degree rock face was laid flat that way).
- `fnv_script_debug` **fixed**: the script's variable list is embedded in the Script at +0x4C (its first node), not a
  pointer there, so every variable name came back empty (vars, set by name). Types come from ScriptVariableInfo
  +0x10 (0 float, 1 integer); a local variable is 16 bytes and has no type byte (the old "string" flag read past
  it). **Built**: a `when` that names script variables (`fTimer >= 0 and bChooseSex == 0`), checked by the bridge at
  each stop because the predicate language cannot read doubles; a stop whose condition is false is passed on like
  the refused save/load stops (VCG01SCRIPT: false condition, 2 then 4 refused stops and no stop; true condition,
  a stop with the 12 variables named; an unknown name lists the script's variables). The stop's opcode is read
  from the compiled script at the stop's offset - 4 (it equals arg1). In VCG01SCRIPT's inactive MenuMode 1036
  block the statement counter still runs one ahead of the source mapping; not resolved (that block was not
  running, and no stop in an active block of that script came while checking).
- `fnv_audio subtitles` caught live: "Evening." from a spawned Trudy greeting the player, through the tool with
  `enable=true`. The text is a temporary string, so the tool now reads it while recording (records polled every
  0.15 s); read after the recording it was garbage. The call carries no speaker form (argument 1 is the sound id,
  argument 2 part of the voice handle), so the result is text, frame and sound id. `Say` on an NPC that has already
  spoken does not reliably speak again; a fresh NPC's own greeting does.
- MCP protocol: an error the SDK writes itself (arguments failing the input schema, an unknown tool) now carries the
  same structured result as the tools' errors (BridgeServer in server.py), so every result matches the declared
  output schema (checked over raw stdio, 8 calls including errors). `fnv_globals`, `fnv_hud`, `fnv_physics` are
  annotated as acting on the game.
- Code reloads without a restart: `run_mcp.py` is now a supervisor that reloads the server when `geck_mcp/`
  changes and sends the client a tool-list change (`run_mcp.py --reload`). Checked
  in the Claude app (Claude Code 2.1.321): the app speaks 2026-07-28 (`server/discover`, then `subscriptions/listen`
  for tools, prompts and resources), the server declares `tools.listChanged`, reloads swap the process between calls,
  and the stamped notices reach the listen stream. The app never sends `tools/list` itself: it keeps a saved tool
  list, which is why new tools needed a reconnect. On connections the app re-made after the server process was
  killed, it did not refetch on the notice; whether it refetches on a session's first connection (the normal case
  now) is not yet checked. The supervisor writes its events to `state/supervisor.log` (the app shows any server
  stderr as an error, so routine events go only to that file).
- Still to run: `fnv_bisect` (mods, then records), from the MCP server (the in-process runner cannot launch through
  MO2).
