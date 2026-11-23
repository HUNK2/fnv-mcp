# Make records
Summary: Write the records of your plugin in the spec: start from a master record, change master records, set fields by name, fill lists, place objects indoors and outdoors.
Words: record, records, spec, plugin, esp, armor, armour, ammo, key, note, book, weapon mod, leveled list, loot, recipe, door, terminal, perk, race, idle, body part, override, template, field, fields, exterior, outdoors, worldspace

The spec is the project's JSON file that `fnv_spec` turns into a plugin. Building is offline: no game, no GECK.
Records are keyed by EditorID under `"records"`.

## Steps

1. **Find what to start from.** `fnv_spec action=lookup query=<words> record_type=<TYPE>` lists master records with
   the `"@EditorID"` to use. `fnv_record action=show` shows one, with its fields by name.
2. **See the named fields.** `fnv_spec action=fields record_type=ARMO` lists the fields a type takes in `"set"`
   (value, weight, flags...) and its lists. Without `record_type` it lists the types that have named fields:
   ARMO AMMO KEYM NOTE BOOK IMOD LVLI LVLN LVLC RCPE DOOR TERM PERK RACE BPTD IDLE.
3. **Write the record.**
   - A new record from a master one: `{"type": "ARMO", "template": "@ArmorLeather", "set": {"name": "Road
     Leathers", "value": 120, "damage_threshold": 8}}`. The template is read from the master file itself.
   - A new record from nothing (small types): `{"type": "KEYM", "set": {"name": "Shed key", "value": 0,
     "weight": 0, "model": "Clutter\\Key01.nif"}}`.
   - A change to a master record, keeping its FormID: `"ArmorLeather": {"type": "ARMO", "override": true,
     "set": {"value": 200}}`. Under another key: `"override": "@ArmorLeather"` or a FormID.
   - What has no name yet: `"fields"` with subrecord tags and byte offsets (`{"tag": "DATA", "patch": [[0, "<i",
     5]]}`). `"set"` is applied after `"fields"`.
4. **Fill lists.** Each replaces the template's list.
   - Leveled lists: `"entries": [{"form": "@Ammo10mm", "level": 1, "count": 12}]`.
   - Recipes: `"ingredients": [{"form": "@Tin", "count": 2}]`, `"outputs": [...]`, `"skill": "repair"`,
     `"skill_level": 25`.
   - Terminals: `"menu": [{"text": "Read log", "result": "Day 1.", "note": "@MyNote", "conditions": [...]}]`.
     A menu item's script is left empty.
   - Perks: `"effects": [{"entry_point": "Calculate Weapon Damage", "function": "multiply", "value": 1.1,
     "conditions": [[{"function": "GetIsID", "param1": "@Varmint"}]]}]`, or `{"ability": "@Spell"}`, or
     `{"quest": "@Q", "stage": 10}`. `fnv_perk_entries` names the entry points.
   - Body parts: `"parts": [{"part": "Head", "damage_mult": 2}]` changes an existing part's data.
   - Conditions of a recipe, perk or idle: `"conditions": [{"function": "GetIsSex", "param1": 0}]`.
5. **Place objects.** Indoors: `"cells"` (a new interior cell). Outdoors: `"worlds": {"WastelandNV": {"cells":
   {"MyCamp": {"grid": [-19, 1], "objects": [{"ref_id": "crate", "base": "@MyCrate", "position": [x, y, z]}]}}}}`.
   The grid is the cell's `[floor(x / 4096), floor(y / 4096)]`; `fnv_player` gives the player's position.
6. **Build and check.** `fnv_spec action=validate`, then `build`; the receipt lists the FormIDs, the overrides and
   where each came from. Install with `fnv_install` (game closed), launch, and read the records back in the game
   with `fnv_form action=bytes` and from the load order with `fnv_record`.

## What the answers mean

- `fnv_spec build` receipt: `formIds` are this plugin's new records; `overrides` names each changed master record,
  its FormID and the master file its starting version came from (the last listed master that has it).
- `fnv_spec action=fields`: `value` says what a field takes (a number, text, "@EditorID", a flag list or one name of
  a list); `lists` are the list fields of the type.
- `fnv_record show`: a subrecord with a layout comes back by the names `"set"` takes, so a value read there can be
  written back unchanged; `lists` holds the type's lists (entries, menu, effects...).

## Known traps

- A template or override from a DLC needs the DLC's masters first, in order (FalloutNV.esm, then DeadMoney.esm):
  its FormIDs are copied as they are.
- A list in `"set"` replaces the template's whole list, it does not add to it.
- `"set"` on a type without named fields is refused: use `"fields"` for those.
- A grid with no exterior cell in the master is refused; new exterior cells are not made.
- A master with no EditorID index gets one the first time a spec names it (a few seconds, once).

## Checks that prove the fix

- The record is in the game as built: `fnv_form action=bytes form=<FormID>` is the live form written back by the
  engine (SaveForm), field by field with the same names, after the plugin is loaded.
- The fields are the ones written: `fnv_record action=show record=@MyArmor` shows them by name.
- An item works: `fnv_console line="player.AddItem <FormID> 1"`, then the inventory (`fnv_inventory`).
- An outdoor object is there: `fnv_refs_near` at its position.
- Placing objects by hand first and keeping them: the `build-a-place` guide.
