# Test a mod
Summary: The working loop for a plugin: build, install, launch, test with a scenario, look, fix, and prove the fix with the same test.
Words: test, testing, tests, scenario, mod, plugin, verify, regression, expect, yaml, loop, install, launch, restart, flaky

Use this for any change to a plugin. A scenario test is a short YAML file that starts the game somewhere, runs
steps and checks the results; the same test proves the fix later. The format is in `docs/SCENARIOS.md`, examples
in `examples/fnv-tests`.

## Steps

1. **Build.** `fnv_spec action=validate` checks the project spec and writes nothing; `action=build` writes the
   plugin and a receipt into the build folder. FormIDs stay the same from build to build.
2. **Install.** With the game closed, `fnv_install`: its own MO2 mod in the dev profile, or on the direct install
   the real `Data` folder, only with `fnv.allow_direct_install_changes`. `fnv_loadorder` shows where it sits and
   any missing master.
3. **Launch.** `fnv_game action=launch` (or `restart` when the game runs), or `fnv_session action=start`, which
   also turns on error capture and opens a journal scope. Then `fnv_status`: your plugin is in the list.
4. **Write the test.** A YAML file in the project's tests folder (`fnv.tests_dir`) with `start`, `plugins`,
   `steps` and `teardown`. For a quick try without a file: `fnv_test action=steps` with the steps inline.
5. **Run it.** `fnv_test action=run path=<file>`; every test of the folder: `action=run_all`. It launches the game
   when needed.
6. **Look.** For a failed step read its actual value and the debug bundle (menus, message box, console tail,
   screenshot). `fnv_photo` and `fnv_look` show what the player sees; `fnv_script_errors` and `fnv_warnings` list
   errors; `fnv_health` lists what went wrong since the session mark.
7. **Fix and run the same test.** Change the spec or the script, build, install, restart, run.
8. **Shorter loops without a restart.** `fnv_form action=reload plugin=<name>` puts changed names, models,
   values, weights and health into the live forms. `fnv_script_hot_swap` replaces a running script's code.
   `fnv_asset action=reload` reads a model or texture from disk again.

## What the answers mean

- Each step comes back with its actual values; a failure says why. New script errors are listed. On a crash the
  answer has the crash doctor's diagnosis, and the game is launched again.
- `repeat: N` runs the test N times and lists the steps whose results differ. `deterministic: true` fixes the frame
  step at 60 fps and seeds the random number generator.
- `start: save:<name>` and `start: {test_save: name}` need a save. `fnv_test_save action=save name=<name>` makes
  one, kept apart from other saves by the prefix `fnvmcp_test_`.
- `teardown` with `revert_all` undoes the changes the test made through the tools.

## Known traps

- `fnv_install` refuses while the game runs, and a new plugin loads only at the next start.
- On the direct install, save starts and test saves need `fnv.allow_direct_install_changes`: start tests at the
  menu or with `coc:` instead.
- A test that passed once may pass by luck: run it with `repeat`.
- Hot swap refuses when existing variables would move or disappear, unless `force`. `fnv_form action=reload`
  covers only some fields; new records need a restart.
- `fnv_scripts_compile` needs the last build installed and loaded.
- To check a finished plugin as a whole, use the check-plugin guide (`fnv_crawl`, `fnv_bisect`).

## Checks that prove the fix

- The same test fails before the fix and passes after it.
- `fnv_test` with `repeat: 3` passes every time.
- `fnv_health`: nothing new since the session mark; `fnv_script_errors` returns no new errors.
