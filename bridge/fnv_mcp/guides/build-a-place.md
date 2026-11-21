# Build a place
Summary: Place objects in the running game, rest them on the ground, check them by looking, and keep them as records of your plugin.
Words: place, placing, build, house, room, camp, shack, object, objects, furniture, clutter, position, rotate, snap, ground, floating, sinking, slope, layout, decorate, array, row, grid, circle

Use this to lay out a camp, a room or a street in the game and keep it in your plugin. Placing changes the running
game, so it needs `fnv.engine_control` `"full"`. Every placement is journaled: `fnv_undo` takes it back.

## Steps

1. **Find the base form.** `fnv_spec action=lookup query=<words>` lists records by EditorID with the `@EditorID`
   to use in a spec. For a new object, `fnv_form action=create` (a `type`, or `clone` of a base form); it lasts
   until the game quits, and step 6 turns it into a record.
2. **Go there.** `fnv_move` takes the player to a cell or a reference. `fnv_refs_near` lists what is around,
   `fnv_cell_grid` which cells are loaded.
3. **Place.** `fnv_place action=at base=<form> near=player snap=true`, or a `position` and a `rotation` in
   degrees. For rows, grids and circles: `action=array` with `shape`, `count` and `spacing`.
4. **Adjust.** `nudge` (an `offset`; `local=true` in the object's own axes), `rotate` (degrees added), `scale`,
   and `snap` again (`align=true` also tilts it to the slope).
5. **Check by looking.** `fnv_place action=check ref=<ref>` says whether it floats or sinks over its whole
   footprint. `fnv_photo action=ref` shows it from several angles, `fnv_photo action=cell` the cell from above and
   the corners. `fnv_look` lists what is on screen; `fnv_visibility` says why a reference does not draw.
6. **Keep it.** `fnv_author action=export` writes the created forms and placed references as a spec part in the
   bridge state folder. Merge it into the project spec, then `fnv_spec action=validate` and `action=build`; with
   the game closed `fnv_install`, then `fnv_game action=restart`. Look again: the place now comes from the plugin.

## What the answers mean

- `footprint`: the ground under the object's whole base: samples, `groundMin` and `groundMax`, the spread, how
  much sank or floats, overhangs, and a `warning`.
- `rest=low` (the default) puts the bottom on the lowest ground under the footprint, so no corner floats and the
  uphill side sinks. `rest=centre` uses the ground under the origin, `rest=high` the highest ground.
- A `warning` with `suggestHeading`: the ground under it differs by more than a quarter of its height. Turning it to
  the suggested heading (0, 45, 90 or 135 degrees) gives the least difference.
- `TOO_STEEP`: the ground rises more than the object's height under it. Pick a flatter spot or a smaller object.
  `array` lists such points under `skipped` and places the rest.
- `overhangs`: part of the footprint is over a drop (a cliff edge, a gully). It is reported, not rested on.
- `fnv_place action=list` shows what this session placed. A placement with `export=false` (an enemy for a test) is
  left out of the export.

## Known traps

- Snap rests the bottom of the base form's bounds on the ground, not its origin, so objects whose origin is at
  their centre sit right.
- Clutter counts as ground, and dips narrower than the ray grid (128 units) are missed. Actors and objects under 48
  units across use one ray.
- The footprint ignores a tilt on the X and Y axes.
- `fnv_world_edit` moves, rotates and deletes any reference, the game's own included. Those changes are journaled
  and undoable but are not exported: use `fnv_place` for what you want to keep.
- A form from `fnv_form action=create` exists only until the game quits; only the export and a build keep it.
- A save made while placements are in the world keeps them. Test the built plugin from the menu or a `coc:` start.

## Checks that prove the fix

- `fnv_place action=check` on each reference: no issues.
- After the restart, `fnv_photo action=cell` (or a `ref` sheet) shows the place as you built it, now from the
  plugin.
- `fnv_author action=check plugin=<name>`: no missing files, no conflicts.
