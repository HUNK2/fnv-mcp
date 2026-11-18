# An NPC does the wrong thing
Summary: See which AI package an actor runs and why, why it does not fight, see, reach or hit its target, and try a change you can undo.
Words: npc, actor, ai, package, packages, schedule, sandbox, follow, combat, fight, attack, hostile, faction, detection, sneak, see, pathing, path, stuck, navmesh, damage, hit, vats

Use this when an NPC or creature stands still, goes to the wrong place, does not attack or attacks the wrong one,
does not notice the player, or takes the wrong damage.

## Steps

1. **What it does now.** `fnv_actor_ai ref=<actor>`: the current package, process level, combat target, detection
   both ways and its state (dead, sitting, sleeping...).
2. **Why this package.** `fnv_ai_packages actor=<actor>`: every package on its list in order, with schedule,
   location and each condition's current value, and whether it runs, could run, is hidden by an earlier one, or
   why not. `fnv_condition_eval form=<package>` shows one package's conditions in detail.
3. **Make it choose again.** `fnv_actor_control action=evaluate_package ref=<actor>`, then `fnv_actor_ai` again.
4. **It does not fight, or fights the wrong one.** `fnv_reaction actor=<a> other=<b>`: hostile or not and the rule
   that decided it (factions, disposition, crime). `fnv_combat actor=<actor>`: target, procedure, weapon, combat
   style and the combat group. `fnv_detection observer=<actor> target=player`: does it see the target, and with
   `recompute=true` the inputs of the engine's own detection formula.
5. **It does not get there.** `fnv_pathing actor=<actor>`: the path, the request (start, goal, door rules) and the
   reason it fails.
6. **Hits are too hard or too soft.** `fnv_damage_log action=start`, let the fight run, `action=read`, then
   `action=stop`: each hit broken down. `fnv_actor_values` shows the values, their modifiers and the effects
   behind them; `fnv_vats_odds` the VATS hit chances.
7. **Try a change, then make it real.** `fnv_actor_control` (start_combat, add_package, set_av, move_to...) and
   `fnv_form_edit` (package or NPC data) change the running game. Both are journaled; `fnv_form_edit` changes are
   undoable, and for `fnv_actor_control` the answer's `undoable` says whether `fnv_undo` can reverse it. When the
   change works, put it in the plugin. For a fight test, `fnv_place action=at export=false` places an
   enemy near the player that is not exported.

## What the answers mean

- A package that is eligible but not running: an earlier package on the list wins, or the actor has not
  re-evaluated yet (step 3).
- "Shadowed": an earlier package's conditions pass, so the later one is never reached. Move it or tighten the
  earlier package's conditions.
- `fnv_reaction` hostile false: the factions, disposition or aggression keep it calm; the answer names the faction
  pair that decides.
- Detection "unseen" with line of sight false: walls or the view cone; with light low and sneak high: by design.

## Known traps

- `fnv_detection` with `recompute` and `fnv_damage_log` install a hook while they run; `fnv_damage_log action=stop`
  removes it.
- Changes from `fnv_actor_control` last only in the running game; a restart or a load starts from the plugin and
  the save again.
- Most `fnv_actor_control` actions (kill, resurrect, start_combat, evaluate_package, packages, idles) cannot be
  undone; enable, disable, add_item and set_av can. Start from a test save (`fnv_test_save`) to try them again.
- An actor in another cell has little AI running (process level): go there with `fnv_move` before judging it.

## Checks that prove the fix

- `fnv_ai_packages`: the package you want runs, with every condition passing.
- `fnv_actor_ai` after `evaluate_package`: the actor does it now.
- A scenario test (`fnv_test`) that reaches the place or starts the fight, passing with `repeat`.
