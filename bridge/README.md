# Plugin builder (`fnv_spec`): a JSON spec to a plugin, offline

`fnv_spec` builds a Fallout: New Vegas plugin (`.esp`) from a JSON *spec*: records, interior cells and placed
objects. It writes a valid plugin with stable FormIDs, checks every `@EditorID` reference against your own masters
and every mesh and texture against the Data folder, and needs neither the GECK nor the game. Installing the build
is `fnv_install`; testing it is `fnv_test`. The rest of the server is in the
[setup guide](docs/FNV-MCP-SETUP.md) and the tool references
([FNV-ENGINE-TOOLS.md](docs/FNV-ENGINE-TOOLS.md), [FNV-LIVE-TOOLS.md](docs/FNV-LIVE-TOOLS.md)).

The builder is 2Shay's work from [geck-mcp-nv](https://github.com/2Shay96/geck-mcp-nv). geck-mcp-nv's GECK editor
tools, its 32-bit helper and its legacy runtime and console tools were removed from fnv-mcp on 2026-11-18: the
`fnv_*` tools do those jobs in the running game.

## Setup

1. Set up the server with [docs/FNV-MCP-SETUP.md](docs/FNV-MCP-SETUP.md).
2. **Build the master index** (read-only, once per game update). Set `FNV_DATA` or pass `--data` if your Data folder
   is not the Steam one:
   ```powershell
   .venv\Scripts\python.exe esm_index.py FalloutNV.esm
   ```
3. **Name the plugin and its spec in the project file** (paths relative to the project file);
   `projects/example-plugin.json` is a complete example:
   ```json
   "plugin": "HelloWasteland.esp",
   "spec": "../examples/hello-wasteland.spec.json"
   ```
   Without them `fnv_spec` answers `NO_SPEC`; the other tools do not need them.
4. Ask your agent: *"Validate the spec, build the plugin and show me what it contains."* That is `fnv_spec`
   `action=validate`, then `build`, then `inspect`, and touches nothing in the game.

## fnv_spec

| Action | What it does |
| --- | --- |
| `validate [spec]` | Dry run of the project spec, or of an inline spec object: structure, references through the master index, meshes and textures. Writes nothing |
| `build` | Writes the plugin and a receipt (FormIDs, references, validation) into the build folder next to the state folder. FormIDs stay stable across builds. Refuses when validation fails |
| `inspect [which=build\|installed]` | Masters, record counts and cells of the build, or of the installed copy (the MO2 dev profile mod, else Data), and whether it matches the build |
| `lookup query [record_type] [limit]` | Records by EditorID in the spec and the indexed masters, with the `@EditorID` reference to use in the spec |

Scripts: the builder emits a script compiled once `fnv_scripts_compile` has cached its bytecode from the running
game, uncompiled before that. [examples/hello-quest](examples/hello-quest/README.md) is the whole loop.

## FormIDs in the game console

Build receipts and plugin inspection show **file-local FormIDs**, not the game's current load order. A plugin with
one master commonly stores its own records with prefix `01`. In the game, replace that prefix with the plugin's
actual runtime load index. For example, file ID `0100080F` becomes `0A00080F` when the plugin loads at index `0A`.
Do not change the plugin's stored IDs to match a particular player's load order; `fnv_formid_live` converts them.

## Limits

- Richer records (creatures, quests, scripts, weapons ...) are made through the spec; there is no record editor.
- Scripts with `Quest.variable` references cannot be compiled in the running game (it keeps no quest EditorIDs),
  so they stay uncompiled.
