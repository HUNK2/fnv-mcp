# Known issues

Open problems a user can meet, one line each. None loses data in normal use; most need an unusual order of calls or a
damaged file. Most are tracked as GitHub issues.

## Client

- The Claude app does not fetch the tool list again after the server announces a change; restart the client after
  updating the server.
- With `game_root` `"auto"` and no game found, the server stops at start with one line (the client shows it in its
  MCP log), so `fnv_setup_check` cannot be asked what is missing. Set `game_root` to the game folder.

## Tool arguments

- A few `fnv_*` parameters are accepted but change nothing in some actions, so a call does not say that an argument
  had no effect.
- `fnv_move` does not turn a reference in place: a rotation with no target, cell or position is refused
  (`INVALID_ARGUMENT`). Use `fnv_world_edit rotate`.
- `fnv_place array shape=circle` gives every copy the same heading. To make them face the centre, place them one
  by one with `at` and a rotation each.

## State files

- The lock on a state file is not fair. A process that updates one state file without a pause for more than 10 s
  makes another process's update of that file fail with `STATE_LOCKED`. Seen only in a test on a slow disk.

## Undo and the journal

- An undo made between a change and its confirmation is opened again by the confirmation, for console reference
  lines, script swaps and time restores (the other repeatable operations report whether their change was seen).
- Undoing a probe removal reads and writes `probe_session.json` outside a single lock.
- The undo of a pause ignores what the resume reports.
- `fnv_undo all=true` slows down with many entries (about 22 s for 3,000).

## Debugger, hooks and traces

- A `thread=main` trap break that falls back to `int3` parks every thread, not only the main one.
- A failed unhook ends the action or hold without saying so; clearing a break drops its record anyway.
- A stop on a fault does not reload the thread's debug registers after the park.
- `fnv_flow` can leave a parked stop when the function is hit between the last poll and the break being cleared,
  and its trace reader can take the lowest new step stop of any thread.
- A slot claimed for a predicate stays claimed when setting the predicate fails.
- A main-thread stop that closed the frame gate opens it again on release, dropping a hold sent in the meantime.
- Setting a function break again on a removed hook can fall back to a trap break.
- Two traces at the same time may share one parked-stop record.
- Some read actions install a hook for the length of the call (`fnv_detection` recompute, `fnv_anim` events,
  `fnv_audio` log and subtitles).
- Two engine AI calls at once with the same hook label can lose records.
- `fnv_engine_get` checks that a getter is pure against the game file on disk, not against the running
  game's bytes, so a getter another mod has hooked is not checked.
- `fnv_script_coverage` may count a line against the wrong line number after an `if` or `begin` block that
  was skipped (not yet checked in the game).

## Not yet checked in the game

- When the bridge disconnects, the companion releases a held frame gate and a running step at once (checked in the
  game); releasing a thread stopped at a breakpoint is tested against a fake host only.

## Direct install (no MO2)

- On a direct install, `fnv_console` and `fnv_run_script` with `engine_control = "full"` can still run `save` and
  `load` console lines: `fnv.allow_direct_install_changes` guards the tools, not every console command.
- `fnv_crawl cells` and other `coc` moves change where your character is in the running game; nothing is saved
  unless the game autosaves (its own setting).
- The direct install's load order is computed as the game orders it without a mod manager (master files first, then
  file time); a load order tool that writes `loadorder.txt` or changes file times is not followed until the game
  runs, when `fnv_loadorder` uses the running game's own list.
- A game started by another mod manager that does not use MO2's file system (Vortex deploys into `Data`) counts as
  the direct install.

## Game control

- **The game hangs at startup when the monitors are asleep.** A launch while the display is off, or turning off,
  stops the game before its first frame: `fnv_game launch` ends with `COMPANION_NOT_READY`, and the Windows System
  log has a Kernel-Power event 566 (`ScMonitorPower`) in the second of the launch. Since 2026-11-21 `fnv_game
  launch` handles it: a game that answers but draws no frames for 45 s (or three times its usual start time) is
  ended and started once more (the timeline shows `startup_hung`, the answer `retried: true`), and the server keeps
  the display on while a game it launched runs. `fnv.startup_hang_seconds` changes the 45 s; 0 turns this off.
  If the second start hangs too, wake the display. Keep the display on for as
  long as you test, with a separate helper that stays running across all launches and calls
  `SetThreadExecutionState(0x80000003)` (display and system required, continuous). In PowerShell, in a window of
  its own:

  ```powershell
  Add-Type -Name Power -Namespace Keep -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags);'
  [Keep.Power]::SetThreadExecutionState(0x80000003); while ($true) { Start-Sleep -Seconds 60 }
  ```

  After a hang, end the game with `fnv_game action=quit force=true` and launch again.
- `fnv_photo view label=true` also draws a box for a reference that is behind a wall.
- `fnv_actor_control set_position` on an actor that `fnv_move` has just moved to another cell can leave the actor
  in the old cell with the new coordinates (seen once, 2026-11-27).
- The audio-cache setting can stay off when writing it back fails.
- `fnv_crawl cells` moves the player with a `coc` that is not journaled.
- A frame hold of a control (`fnv_input frames=`) holds its bound key too, so `GetControlState` and
  `IsControlPressed` see it; a control bound to nothing gets the engine's force call alone, which they do not see.
- `fnv_window background` was proven with the focus-loss message posted to the game window, not with another
  window brought to the front. With it on, the keyboard and mouse may still lose their DirectInput devices when
  the window is not in front: injected input (`frames=`) works, xNVSE `TapKey` and `HoldKey` may not.

## Plugins and data files

- When a plugin name appears more than twice, or a disabled plugin is still indexed, record lookups can name the wrong
  plugin.
- A plugin with a truncated subrecord makes the lookup of who uses a record fail with `BAD_PLUGIN`
  instead of skipping that plugin.

## Naming tools

