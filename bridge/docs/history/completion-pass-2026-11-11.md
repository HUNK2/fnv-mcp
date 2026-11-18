## Plan 2 completion pass (2026-11-11 late night)

Everything the waves left as "not built" was built or decided against; each item below was run in the game
(Prospector Saloon, a spawned Trudy, the Hello plugins) unless it says otherwise. Tool count at the time: 209 (165 `fnv_`,
44 `geck_`).

**Inspectors**
- `fnv_anim groups` (the group map at animation +0xDC, values are AnimSequenceBase: a single one holds its
  BSAnimGroupSequence at +4): 1,057 groups for the player with their KF files. `biped` (Character +0x1B4, slots of 16
  bytes at +0x2C): the player's upper body, hands, Pip-Boy; Trudy's NCR outfit. `face` (process +0x178, keyframe
  blocks with values at +0xC and count at +0x10): counts 15/15/16/1/17/16/1 as the decomp says; Trudy's mood
  expression 5 = 1.0. `idle_tree`: the roots come from IdleAnimationManager::GetIdleArray 0x00600690 **on the base
  form** (the manager's own lookup by reference returned no folder); each root is asked with
  TESIdleForm::FindIdleFormForObject 0x005FF2E0 in order. Approximate: replay delays and the caller's filters
  (HighProcess::StartIdleAnimation) are not applied, so the LOOSE root idles with no conditions answer first.
  `events`: record hook on Actor::SetAnimAction 0x008A73E0 (no actions in a quiet saloon).
- `fnv_audio playing` (BSAudioManager 0x011F6EF0 +0x54 map; BSGameSound flags +8, state +0x10, volume +0x24, file
  +0x36, sound form +0x134): 12 sounds, 10 playing. The position at +0x17C read as garbage, so positions are not
  reported. `stop` by form sends message 4 through 0x00ADB610 (two clock loops stopped). `music`: the state block
  0x011DD0F0; `file=` plays a track as special media (type 6) and `stop` fades it (both seen). `radio`: one station,
  radio off. `hear`: CanPlaySoundAt 0x0082ECA0 (static, cdecl) says yes next to the player, no 90,000 units away.
  `subtitles`: record hook on Interface::ShowSubtitle 0x00705210 (not run with a speaker). `voice_check` = `fnv_crawl
  dialogue`: Trudy's 60 lines all found with the corrected naming rule.
- Voice file names (fixed in fnv_crawl): quest and topic EditorIDs share 25 characters; when they do not fit, the
  quest keeps at most 10 and the topic the rest; the id is the record's own 6 hex digits with a `00` prefix in every
  plugin (DLCs with one master too); then the TRDT response number. Checked on every FalloutNV.esm response that has
  a file in Fallout - Voices1.bsa (28,767 of 28,768). The old rule (always 10/15, prefix = number of masters) matched
  67% of vanilla and every mod plugin wrongly.
- `fnv_physics body` adds the velocity (hkpRigidBody +0x1B0, Havok units x 6.9991); `set`: bhkRigidBody
  SetLinearVelocity 0x005615D0 takes an NiPoint3 **in game units** and converts it itself (a bottle jumped 12 units).
  `controller` adds the state (+0x3F0: on ground ...). `triggers`: record hook on the script event raiser 0x005AC750
  (action ref, the trigger's extra list = trigger + 0x44, flag): OnTrigger runs merged per frame span, two actors in
  two scripted triggers seen. `projectiles` (ProcessLists 0x011E0E80 +0x70, kept by RTTI class), `query` (six rays),
  `layers`.
- `fnv_systems perk`: perk DATA at +0x38 plus its conditions ("Educated: needs level 4, player 1"). `barter`:
  GetSkillPriceMultipliers 0x00649050 with scratch out-floats: Barter 15 gives x1.482 / x0.517, a 9mm pistol (value
  100) 148 / 52; not compared with an open barter menu. `GetValue` returns 0 for ingestibles (their value is in
  ENIT). `zone`: GetCalculatedLevel 0x00526190 returns an unsigned short (mask it): Vault11Zone level 6. `crime`:
  karma; New Vegas keeps no bounty.
- `fnv_object referrers`: every writable region read from outside (VirtualQueryEx), 601 MB in 0.7 s; a Havok body
  pointer was held by its bhkCollisionObject and its native body. `fnv_memory refcount`: a write watchpoint on +4
  through `fnv_blame field`.

**Creation loop**
- `fnv_asset nif` (offline): header, root, the child links of every node block (NiAVObject flags are a u32 in New
  Vegas files, user version 2 = 34), collision, skin, texture paths. It names the S2 crash mesh's bad link (root child
  10 -> NiTriStripsData) without the game and passes vanilla meshes, the skeleton included. `dds`: size, format, mip
  chain (read from the archive). `users path`: the records that name a file (the S2 culprit HelloCrashCrate from its
  mesh path). One FalloutNV.esm record (00150FC0) has compressed data zlib refuses; it is listed as unreadable.
- `file_log` gains source `texture` (NiSourceTexture::Create 0x00A5FD70; textures never pass FileFinder::GetFile):
  the crate's diffuse and normal map seen when it was placed.
- `reload` of a menu XML (`menu=HUDMainMenu`): RemoveMenuEntries 0x00A1CA20, then MenuManager::ReloadMenu 0x00720AA0
  with the code of `&HUDMainMenu;` from 0x00A01860 (1004), as the console's `reload <menu>` does. `.kf`: ClearUnused-
  Models also drops unused KF files; `ref=` rebuilds an actor that holds one.
- `fnv_form bytes`: vtable slot 0xB on the live form, the record buffer 0x011C54CC read, freed (FormHeap_Free) and the
  globals restored. WEAP has a writer (the research had found none in the decomp): the 9mm pistol came back as 820
  bytes. `delete`: flags 0x20 | 0x4000 on a form this session created (no engine function to unlist it was found).
- `fnv_place check` adds overlaps: bound spheres compared, a candidate confirmed by a ray between the centres.

**Debugger and code**
- `fnv_flow call`: a small module (native/modules/fnvmcp_flowcall.c) makes the call from its frame callback, where
  breakpoints stop (they are ignored while the companion serves a request); the bridge fills its slot. XP for level
  5 and CalculateSkillValue(15, 5, 5) traced in the game. A module left loaded by an earlier bridge is found through
  its export table in memory. A call that times out disarms the slot (a stale armed slot once fired later into a
  breakpoint).
- `fnv_flow depth 1`: companion op `trace ranges <hex>` (up to 16 extra ranges, cleared when the trace ends); the
  bridge passes the direct callees with source lines from the engine map's call table. Each line names its function.
- `fnv_blame form`: record hook on BGSSaveLoadGame::NotifyFormIDChanged 0x0084A690 filtered to the form; flags named
  by GetChangeFlagName 0x0083FEF0 ("Moved", "Havok Moved" for a thrown bottle, 67 calls in 4 s, summarised).
- `fnv_code`: `fnv_orig_<8 hex digits>` (C name or a block-scope C++ declaration's mangled name) binds to a trampoline
  (the original's first instructions, then a jump back): new = original + 1 gave 29 for 28 while swapped. `export`
  writes a loader module (checks the expected bytes, places the code, binds relocations, jumps the entry): loaded as
  a hot module it applied the change (38), unloaded it put it back (28). `compare mode=shadow`: record hook on the
  original, each live call replayed on the new code (no live calls of the formula in an idle game). Functions under
  five bytes: their E8 call sites (engine map call table) and vtable slots (exe read-only data) are redirected (fake
  tested only).
- `fnv_script_debug step over|out`; `fnv_script_coverage` (statement ring read every 0.2 s in the background:
  4,500 statements in 11 s, 55 lost; HelloBug's scripts 75% and 89% with the lines never run);
  `fnv_script_decompile` (xNVSE's DecompileScript command, the file read back and removed: GSTrudyScript).

**Eyes, hands, time**
- `fnv_render toggle` (the console toggles; flag before and after where known: borders 0 -> 1 -> 0), `imagespace`,
  `shaders`, `lights` (the world shadow scene node's list: nodes of next, prev, ShadowSceneLight; its NiPointLight at
  +0xF8): 26 lights with positions.
- `fnv_overlay text` (DebugText::Print 0x00A0F8B0 on DebugText_Instance 0x00A0D9E0): shows once the debug text layer
  is on (`fnv_render toggle debug_text`); `marker`, `clear`.
- `fnv_replay`: two held controls recorded with their frames from a save, replayed under the frame gate: the player
  ended within 2 units of the recorded position.
- `fnv_game launch start=cell:<interior EditorID>` (SStartingCell in the INI): run 2026-11-12, crashed the game at startup
  in every cell tried (HelloWasteland, GSProspectorSaloonInterior): TES::LoadInterior -> LoadingMenu setup 0x00788AB0
  -> Tile::Set on InterfaceManager's root tile, still null before the main menu builds it. Removed; `coc:` goes
  through the main menu. Intros were already skipped (SIntroSequence blank).
- `fnv_ui_do type`: not run (no name entry menu could be opened without a new game).

**Whole-plugin checks**
- `fnv_crawl quest` (HelloCourier, stages 10 and 20: stage reached, objective shown, no unresolved target) and
  `items` (HelloCourier's token added, model found, taken back; the console takes the load-order form id, not the
  EditorID). `fnv_bisect level=records plugin=`: variants written with the codec (fake tested); `fnv_bisect` itself
  is still not run in the game (it needs the MCP server's launch, which the in-process runner cannot do).
