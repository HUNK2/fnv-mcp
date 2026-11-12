# PLAN-3 exit gate, round-3 audit: file parsers, record data, plugin writing

Auditor scope: geck_mcp/esp/ (codec, validate, records, build, generic, dialogue, formids, assets), fnv_data.py,
fnv_records.py, fnv_save.py, fnv_naming.py, authoring.py. Read-only; findings checked against the code and, where
marked, with small offline snippets (scratch files in %TEMP%, removed).

Counts: high 1, medium 2, low 7.

---

## HIGH

### H1. fnv_name_export writes to any path the call names, truncating whatever is there, under a read-only annotation
- **Where:** geck_mcp/fnv_naming.py:547-549 (`path = Path(path) if path else ...; path.write_text(...)`);
  registration geck_mcp/fnv_live_tools.py:1447-1453 (`@server.tool(annotations=read)`; `read` is `live_read`,
  `readOnlyHint=True`, fnv_tools.py:932).
- **What happens:** `fnv_name_export(path="<game>\\Data\\MyMod.esp")` (or FalloutNV.esm, a save, a profile file,
  any file the process can write) replaces that file with the fnvsym-trace JSON. There is no folder check, no
  extension check and no refusal of an existing file; `write_text` truncates. The only precondition is that some
  naming data exists (a harvest, oracle result, experiment or watch), which is normal once the naming tools were
  used. Because the tool is declared read-only, a client may run it without asking the user.
- **Why confident:** the `path` argument goes from the tool straight to `naming.export(path)` and on to
  `Path(path).write_text`; nothing in between (invoke, export) inspects it. The docstring says the file goes "into
  fnvsym/traces". Not in BUG-LOG (T3 is only about empty exports).
- **Severity note:** needs an agent-supplied path (the default path is safe). It matches two bug classes of the
  gate: a write that escapes the allowed folder, and data loss in the user's files under a read-only hint.
- **Fix idea:** accept only a file name (or a path under FNVSYM/traces or state_dir), require a `.json` suffix, and
  refuse to overwrite a file that is not an earlier fnvsym-trace; mark the tool `control`, not `read`.

---

## MEDIUM

### M1. fnv_asset_check's T7 folder check is lexical: `Data\..\..` escapes it
- **Where:** geck_mcp/fnv_records.py:403-405 (`spots = {path.absolute(), path.resolve()}` and
  `any(spot.is_relative_to(root.absolute()) ... for spot in spots ...)`).
- **What happens:** `Path.absolute()` does not collapse `..`, and `is_relative_to` compares path parts. For
  `plugin = r"C:\Games\FNV\Data\..\..\Users\me\other.esp"` the absolute spot starts with `C:\Games\FNV\Data`, so the
  check passes even though `resolve()` is outside every allowed root (the `any` over both spots lets the unresolved
  one win). The file is then read and parsed (`_plugin_assets`), and its asset strings are returned. Also,
  `path.is_file()` runs before the folder check (line 393), so PLUGIN_NOT_FOUND vs BAD_PATH tells whether any path
  exists.
- **Why confident:** `PureWindowsPath(r'C:\Game\Data\..\..\Users\x.esp').is_relative_to(PureWindowsPath(r'C:\Game\Data'))`
  returns True (run with the bridge venv). T7's fix is therefore incomplete.
- **Impact limit:** read-only; only files that parse as TES4 give content back; others give BAD_ASSET.
- **Fix idea:** compare only `path.resolve()` against `root.resolve()` (keep the symlinked-mod case by also
  resolving the root), and do the folder check before `is_file()`.

### M2. Record index never drops rows of a plugin path that left the load order; several lookups do not filter by file and serve them
- **Where:** geck_mcp/fnv_data.py:197-203 (rows are deleted only for the same `path`), with unfiltered lookups at
  fnv_data.py:445 (`_form`), fnv_records.py:191 (`known` ids for `exists()` in the refs scan), fnv_records.py:257
  (user names), :287 (target type/EditorID), :351 (conflict EditorIDs), fnv_save.py:423 (`Namer.edid`).
- **What happens:** the same plugin name can live at two paths: geck_plugin_install writes `Data\<plugin>`
  (authoring.py:235) and fnv_install writes `mods\<stem>\<plugin>` (fnv_game.py:811); load_order prefers the enabled
  mod copy, and disabling that mod switches back to Data. Each path gets its own `files` row and its records stay
  under the same `owner` name forever. Then:
  - `_form` (`... where owner = ? and local = ? limit 1`) can return the other copy's type/EditorID, so
    fnv_record_show labels a form with a stale EditorID, or treats a 4-byte field as a form that the active copy no
    longer has (is_form in decode_subrecord).
  - `_ensure_refs` builds `known` from every row of the owner, so `exists()` accepts form ids that exist only in the
    stale copy, and fnv_record_users lists references to deleted records.
  - record_users / conflicts / save namer show EditorIDs from whichever row SQLite returns first.
- **Why confident:** no code deletes `files`/`records` rows except the per-path rebuild (grep for `delete from
  files`); the listed queries have no `file in (...)` clause, unlike record_find and `_resolve`, which do.
- **Fix idea:** filter these lookups by the active file ids (as `_edid_lookup` does), or delete other rows with the
  same `name` when a path is (re)indexed.

---

## LOW

### L1. records.text accepts U+0080..U+009F and writes them as bytes that read back as other characters
- **Where:** geck_mcp/esp/codec.py:157-158 (`cp1252-bytes` encode branch) used by records.text (records.py:60-62),
  zstring, generic `text`.
- **What happens:** only 0x81, 0x8D, 0x8F, 0x90, 0x9D round-trip. `text('A\x80B\x9fC')` is accepted and writes
  bytes 80 and 9F, which read back as `'A\u20acB\u0178C'` (euro sign and Y-diaeresis); confirmed with the venv.
  The "representable in Windows-1252" check therefore passes text that the game shows differently.
- **Fix idea:** in the encode branch, allow only the five undefined code points (0x81, 0x8D, 0x8F, 0x90, 0x9D).

### L2. P9 is fixed only in records.text: generic `text`/`texts` fields and dialogue responses still write an embedded NUL, and a non-cp1252 character is a raw UnicodeEncodeError
- **Where:** geck_mcp/esp/generic.py:89-92 (`zstring(v)`), dialogue.py:146 (`zstring(resp['text'])`).
- **What happens:** `{"tag": "FULL", "text": "Hi\u0000there"}` builds `b'Hi\x00there\x00'`; the game and GECK read
  "Hi". A snowman character raises UnicodeEncodeError (reported as INVALID_ARGUMENT, no `where`). Confirmed with the
  venv.
- **Fix idea:** route these through records.text (NUL check, SpecError with the field path).

### L3. codec.parse raises RecursionError on deeply nested groups (P3 incomplete)
- **Where:** geck_mcp/esp/codec.py:271-287 (`_parse_items` recursion); validate.py:18-21 catches only FormatError.
- **What happens:** a 28 KB file of TES4 plus 1,200 nested GRUP headers makes `validate()` raise RecursionError
  (confirmed); `iter_records` handles the same file. Callers: authoring.inspect / is_generated on the installed
  plugin, fnv_suite.
- **Fix idea:** parse groups with an explicit stack, or cap the depth (FNV needs at most about 6) with FormatError.

### L4. scan_references raises raw struct.error / IndexError on truncated subrecords, which breaks fnv_record_users for the whole load order
- **Where:** geck_mcp/fnv_records.py:93 (`u32(body, p + 6)` after XXXX), :99 (`u32(body, start)` at ssize 4),
  :110-122 (CTDA and form offsets read past a short body).
- **What happens:** a record body ending in `XXXX\x04\x00`, or an NPC_ with `RNAM` size 4 and only 2 bytes left,
  raises `struct.error` (confirmed). `_ensure_refs` scans every active plugin before answering, so one damaged
  plugin makes every fnv_record_users call fail with FNV_ERROR until it is fixed.
- **Fix idea:** bound every read by `n` (skip the rest of a record whose subrecord overruns) and report the plugin
  as unreadable instead of failing.

### L5. Build-time asset check ignores BSAs inside enabled MO2 mods (the loose-file fix of 2026-11-11 is half done)
- **Where:** geck_mcp/authoring.py:189 (`AssetIndex(self.data_dir, self.mod_roots())`) with esp/assets.py:134-135
  (only `Data\*.bsa` are read when `bsas` is None).
- **What happens:** a model that ships in a mod's BSA (`mods\SomeMod\SomeMod - Main.bsa`) is "model missing", so
  validation fails and geck_plugin_build writes nothing; fnv_asset_check (fnv_records.py:447-450) does read mod
  BSAs, so the two tools disagree. Fails safe, but there is no way past it from the tool.
- **Fix idea:** pass the BSAs of the mod roots as fnv_records._asset_index does (and read them from their own
  folder in asset_manifest, which now joins `self.data_dir / name`).

### L6. BSA readers raise raw struct.error/IndexError on a truncated archive, outside the BAD_ASSET mapping
- **Where:** geck_mcp/esp/assets.py:110-116 (`struct.unpack('<QII', stream.read(16))`, `stream.read(1)[0]`);
  called from fnv_records.py:412 (before the try at :413) and authoring.py:189.
- **What happens:** one truncated or zero-length `.bsa` in Data or an enabled mod makes fnv_asset_check fail with
  FNV_ERROR and geck_plugin_build fail with BRIDGE_ERROR, for every plugin.
- **Fix idea:** check read lengths and raise AssetError; skip (and report) an unreadable archive.

### L7. fnv_profile_set writes any plugin name into plugins.txt/loadorder.txt unchecked
- **Where:** geck_mcp/fnv_records.py:509-512 (`active.append(name)`, `order.append(name)`), written at :526.
- **What happens:** `plugins={"A.esp\nB.esp": true}` adds two lines, and a name without .esp/.esm or of a file
  that does not exist is added as active; a mod name `..` passes the `is_dir` check (:490) and is written as `+..`.
  The backup makes it recoverable.
- **Fix idea:** require a plain `.esp`/`.esm` file name with no path or control characters, and a mod name with no
  path parts.

---

## Checked and found sound (no finding)
- codec: XXXX read/write (P4 fix), raw-inflate fallback (needs eof and declared size), group/record overrun checks,
  no loops on size 0 (records always advance 24 bytes); HEDR count = records + groups; TES4/MAST/DATA layout;
  interior block/sub-block; CTDA 28 and TRDT 24 bytes; INFO DATA flag bytes; 0x2000 result-script flag.
- RecordIndex.ensure: stat before scan, re-check inside BEGIN IMMEDIATE, new file id per rebuild with refs dropped
  (C1, R14, R15 fixes hold); REFS_VERSION 4 gating; INDEX_VERSION 2 gating.
- fnv_save: every parse error inside the constructor becomes SaveFormatError; loops are bounded by the data size.
  (The P10 side note, `int(None, 16)` for an unresolved global form, at fnv_save.py:505/573, is still present but
  was logged there as theoretical.)
- authoring.install: foreign/edited checks, verified backup before replace, atomic write, re-check before write.
- AssetIndex._loose: `..` parts cannot match a listdir entry, so loose lookups stay under their roots.
