## Phase 2 tools (2026-11-07 night)

Built from the decomp (three Sonnet research passes over the main loop, VATS, scene graph, UI tiles, dialogue,
memory and world edits) and checked in the running game on the dev profile:

- **Behaviour hooks** (`fnv_hook_set`): the companion's `hookact` op adds an action to a watch hook. skip returns
  through a per-hook stub (`mov eax, value` or `fld dword [value]`, then `ret n` with the bytes the calling
  convention pops), result replaces eax at exit, arg rewrites a stack word or ecx/edx before the trampoline, script
  queues a console line for the main thread (at most 8 per frame). A filter and a hit limit apply to every action.
  Checked: Actor::GetLevel skipped with 42 for the player only (engine call and `player.GetLevel` gave 42, a gecko
  kept 7), result 30 with a player filter, fnv_undo removing the hook.
- **Patches** (`fnv_patch`): `B8 05 00 00 00 C3` over GetLevel's first six bytes made every actor level 5; a second
  patch with a wrong `expect` was refused; fnv_undo restored `55 8B EC 51 89 4D`.
- **Tracepoints** (`fnv_trace stack=true`): the hook record's snapshot area holds up to 24 stack words that point into the
  exe (`HF_STACK`); the bridge keeps those preceded by a call instruction. GetLevel on the gecko: 67 hits, all from
  CalculateDetectionLevel (0x008A0D10). Deeper frames can be stale stack values.
- **fnv_engine_get**: Actor::GetLevel on the player returned 1.
- **fnv_globals**: TimeScale 30 set to 60 (`GetGlobalValue` agreed) and undone.
- **fnv_dialogue**: a conversation with a spawned Sunny Smiles: GREETING, the line on screen, quest
  VFreeformGoodsprings and her choices in order.
- **fnv_memory action=address_space**: 1856 of 2048 MB of address space used after loading the test save, largest free block 14 MB; 133552
  forms; 18 high-process actors. Windows reserves the region above 2 GB for a non-large-address-aware 32-bit process,
  so the walker stops at the limit the loaded image's header gives.
- **fnv_world_edit**: transform (position, Z angle, scale 2.0), duplicate, disable on a gecko, then undo of all three;
  HVDustStorm forced (seen in a screenshot) and undone (current weather back, override cleared).
- **fnv_scene_graph**: the player's 97 nodes (BSFadeNode, NiNode, BSFaceGenNiNode, NiTriShape, NiTriStrips), skinned
  shapes with vertex and triangle counts, PPLighting texture sets (HandMale.dds, _n, _sk), locomotion sequences, BSX
  flags. Gamebryo classes have no MSVC RTTI: their names come from NiObject::GetRTTI (vtable slot 2).
- **fnv_menu tiles**: HUDMainMenu with 401 trait names read from the game's own trait map (0x011F32F4).
- **fnv_vats_odds**: 9mm pistol on a gecko at 210 units: head 0.4907 from the engine, and the recomputed terms give
  49.07% too; the torso term is 147% and caps at 95. The menu number uses single precision (Direct3D's x87 control
  word), so 0.95 shows as 95. Not yet compared with an open VATS menu: injected input did not open VATS here.
- **fnv_profile action=frame**: 60 fps, 16.4 ms frames, render 13.7 ms (vsync wait included); with iNumHWThreads 12 the AI
  and process-list children run off the main thread and are listed as not called.
- Offline: record users of GSSunnySmiles (her ACHR, 159 dialogue conditions, a package, a quest) and Caps001 (the
  first index of the whole load order takes about 10 s); conflicts in the vanilla DLC set (76 with lost changes);
  6 missing assets in HonestHearts.esm; profile_set dry run.

Hazard seen: reverting the session right after closing a conversation with the spawned Sunny Smiles copy (disable and
MarkForDelete of her and of a spawned gecko in one frame) crashed the game in
Script::RunDelayedScriptActionsOnReferences (0x005AA720) with an illegal instruction. The undo lines come from the
spawn tool; the cause is not pinned down.
