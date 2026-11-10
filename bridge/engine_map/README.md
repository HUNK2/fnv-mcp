# engine_map

Generates `engine_map.json` (and optionally a C header) for Fallout: New Vegas 1.4.0.525 from your own
fnv_decomp checkout, fnvsym's proof table and your own unpacked exe. Nothing generated here is committed
or shipped: write it under `bridge/state/` (ignored by git).

```
python -m engine_map.generate --decomp PATH [--verified CSV] [--exe EXE] [--nvse-src PATH]
                              [--plugin-src LABEL=PATH] --out state/engine_map.json [--header H]
```

A second builder makes the symbol database (source lines, locals and compiler-exact types; see
[docs/FNV-SYMDB.md](../docs/FNV-SYMDB.md)). It compiles every decomp unit with `/Z7` added, so it needs the
compilers of the decomp (`FNV_MSVC9_ROOT`, `FNV_MSVC9_RTM_ROOT` or `FNVCHECK_RTM_ROOT`):

```
python -m engine_map.symdb --decomp PATH --out state/symdb.sqlite [--image EXE] [--jobs N]
                           [--only TEXT]... [--limit N]
```

`--image` checks each function against the game itself; without it each unit is compiled twice and the two
objects are compared. `--only` and `--limit` build a small database for tests. The summary is printed as JSON;
progress goes to stderr. The JSON also counts how units were checked (`checks`, `fallbacks`: units whose image
check failed and used the double compile). `unitsPartial` units (some function not verified) are not failures.
If a worker process dies, its units are recorded as failed and the build goes on with a new pool.

| Module | Reads |
| --- | --- |
| `generate.py` | `build/units.json` (functions, sizes, symbols), `verified.csv`, the exe (image probes, call edges, imports, settings) |
| `source.py` | `// @fnv 0x...` markers and the definition below each |
| `headers.py` | class/struct definitions, `// +0x` offsets, `// slot` comments, `FNV_STATIC_ASSERT` sizes and offsets |
| `demangle.py` | MSVC symbols (dbghelp on Windows; a name-only fallback elsewhere) |
| `commands.py` | vanilla `CommandInfo` tables in the exe; `DEFINE_COMMAND*` macros in script-extender sources |
| `pe.py` | minimal PE32 reader |
| `symdb.py` | `build/units.json`, the decomp compilers, the exe (optional); writes the symbol database |
| `symdb_schema.py` | the symbol database schema (tables, `meta` keys, type kinds, register names) |
| `codeview.py` | CodeView C13 debug sections of one object file: lines, locals, types (used by `symdb.py`) |

Used by the `fnv_*` MCP tools; see [docs/FNV-ENGINE-TOOLS.md](../docs/FNV-ENGINE-TOOLS.md).
