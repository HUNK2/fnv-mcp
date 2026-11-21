# Start here: set up and the first calls
Summary: Check the machine, start or attach to the game, and learn the working loop before changing anything.
Words: start, getting started, first, first call, begin, new, setup, set up, ready, launch, attach, steam, direct, mo2, mod organizer, companion, rules, loop

Use this at the start of a session, or when you do not know where things stand. The tools work with two kinds of
install: an MO2 dev profile (a portable Mod Organizer 2 instance with a profile of its own), or the direct install,
the game as Steam installed it and started with Steam's Play button or `nvse_loader.exe`.

## Steps

1. **Where are we?** Call `fnv_status`. It works with nothing started. Read `phase` (not_running, starting,
   main_menu, loading, in_game, in_menu, paused_menu), `install.kind` (`mo2` or `direct`), the loaded plugins with
   their load-order index, and the engine companion's ping. Call it again after every launch or load.
2. **Is the machine ready?** When the companion does not answer or the game cannot start, call `fnv_setup_check`.
   Each check (game build, xNVSE, the MO2 instance, JIP LN NVSE, the crash logger, Zig, the engine companion, the
   engine map) says what to do when it fails. Fix them with the user: what goes into their game is their choice.
3. **The engine companion.** With the game closed, `fnv_companion_install`. With MO2 it becomes its own MO2 mod; on
   the direct install pass `target=game`, which puts it into the game folder's `Data\NVSE\Plugins` with a receipt
   and a backup of any file it replaces. It loads at the next start.
4. **Start the game.** `fnv_game action=launch` with `start` menu, continue, `save:<name>` or `coc:<cell>` (for
   example `coc:GSProspectorSaloonInterior`). `via=auto` uses MO2 when the project names an MO2 instance, and
   otherwise runs `nvse_loader.exe` as Steam's Play button does. A game the user started needs no launch: the tools
   attach to it. `fnv_session action=start` does the same with a setup check first, waits until the game is
   loaded, turns on the capture of script errors and engine warnings, and opens a journal scope that
   `fnv_session action=end` reverts.
5. **Look again.** `fnv_status`: phase `in_game` and your plugins in the list.
6. **Pick the guide for the job.** `fnv_guide action=list` lists the guides, `topic` takes the problem in your own
   words, `find` answers "which tool does X". To make things: `build-a-place`, `test-a-mod`, `quest-dialogue`,
   `npc-ai-combat`, `safety-undo`. For problems: `crash`, `script`, `slow`, `check-plugin`, `change-engine`.

The working loop, which the other guides fill in: build (`fnv_spec`), install (`fnv_install`, game closed), launch
(`fnv_game`), test (`fnv_test`), look (`fnv_photo`, `fnv_look`), fix, and again.

## What the answers mean

- `phase` not_running: there is no game. Launch it, or wait for the user to start it.
- `install.kind` direct: this is the user's real game. Tools that would change its files (plugins in `Data`,
  `plugins.txt`, `Fallout.ini`, saves) refuse with `DIRECT_INSTALL_PROTECTED` unless
  `fnv.allow_direct_install_changes` is true in the project file. Only the user turns it on.
- A refusal that names `fnv.engine_control`: the project is at `"read"` (the default). `"full"` lets the tools
  change the running game and run code in it, as the user's Windows account. Ask the user before they change it.
- Every answer has `ok`, `outcome`, `data` and `error`. A failure carries `error.code` and `error.recovery`: do
  what the recovery line says, do not repeat the same call.
- `fnv_guide find` names a tool that is off in this server: the project's `toolsets` setting turns it on, and the
  client must connect again to see it.

## Known traps

- The launch hangs before the first frame (`COMPANION_NOT_READY`) when the monitors are asleep. Keep the display
  on while testing (`docs/KNOWN-ISSUES.md` has a helper), then `fnv_game action=quit force=true` and launch again.
- `fnv_install` and `fnv_companion_install` need the game closed, and what they install loads only at the next
  start: `fnv_game action=restart`.
- The client keeps the tool list it saw when it connected; after the server is updated, connect again.
- On the direct install a save start loads the user's real saves. Start at the menu or with `coc:` unless the user
  said otherwise.
- `fnv_game action=quit` closes only a game the bridge started, unless `fnv.engine_control` is `"full"`.

## Checks that prove the fix

- `fnv_setup_check`: every required check is ok.
- `fnv_status` twice: phase `in_game`, the companion's frame counter rising between the calls, `install.kind` as
  expected and your plugins loaded.
