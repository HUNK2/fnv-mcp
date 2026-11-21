# Symbol database

Source lines, named locals and compiler-exact type layouts for the retail game (Fallout: New Vegas 1.4.0.525),
built on your own machine from your own decomp checkout. Nothing in it is shipped or committed: it lives in
`bridge/state/symdb.sqlite` (ignored by git), and neither it nor any compiled object may leave the machine.

## 1. Why it works

The decomp reproduces the game byte for byte, unit by unit. Compiling a unit with `/Z7` added to its recipe
flags leaves every function's bytes and relocations identical (checked on 2026-11-10: 4 units, 209 functions,
compilers sp1 and rtm, `/Od` and `/O2`) and adds CodeView C13 debug sections to the object. So the debug
records describe the real game code: which source line each instruction comes from, where each parameter and
local lives, and how the compiler laid out every class the unit uses.

Not always: the full build (2026-11-11) found 343 functions in 60 sp1 `/Od /GS` units whose code changes with
`/Z7` (the frame grows: `sub esp, 8` instead of `push ecx`, 9 bytes longer), while all of them match the game
without it. The build checks every function, so these get no lines or locals.

### Measured (decomp `db4568bf`, `--image`, 10 jobs, 2026-11-11)

| What | Count |
|---|---|
| Units | 2,988: 2,927 ok, 60 partial (the `/Z7` change above), 1 no-compiler (`src/staging/blk_009e0000_o2.cpp`, vc8) |
| Functions | 68,657: 68,314 verified, 67,944 with a procedure record, 63,111 with lines |
| Verified, no lines | 4,701 with a record, nearly all compiler-generated `atexit` destructors in `src/generated/*initializers*.cpp`; 502 with no record (adjustor thunks `?...@@W...`, deleting destructors `??_G...`) |
| Locals | 176,608 (147,187 `bprel`, 16,730 `regrel`, 12,378 `register`, 313 `static`) |
| Lines | 644,766 rows, 2,897 files |
| Types | 904,009 distinct type records, 125,569 distinct named class, struct, union and enum names |
| Build | 133 s wall clock; database 249 MB |

Every unit is built with `/Gy`. `bprel` locals also appear in 11,005 `/O2` functions: `bprel` means "relative to
EBP", not "built without optimisation".

## 2. Build

```
python -m engine_map.symdb --decomp PATH --out state/symdb.sqlite [--image EXE] [--jobs N]
                           [--only TEXT]... [--limit N]
```

| Option | Meaning |
|---|---|
| `--decomp` | fnv_decomp checkout (has `build/units.json`, `tools/msvc.py`, `tools/compare.py`, `include/`, `src/`) |
| `--out` | Database to write. Built as `<out>.building`, then moved over `<out>`, so readers never see half a database |
| `--image` | Your unpacked FalloutNV.exe. With it, each function is checked against the game itself; without it, each unit is compiled twice (with and without `/Z7`) and the two are compared |
| `--jobs` | Worker processes (default: CPU count minus 2, at least 1) |
| `--only` | Only units whose source path contains TEXT (repeatable). The result holds only those units: for tests and development |
| `--limit` | Stop after N units (development) |

Compiler roots come from the environment as in the decomp: `FNV_MSVC9_ROOT` (sp1), `FNV_MSVC9_RTM_ROOT`
(rtm; falls back to `FNVCHECK_RTM_ROOT`), `FNV_MSVC8_ROOT` (vc8). A unit whose compiler is not available is
recorded as failed and the build goes on.

### Per unit (one worker process each)

1. Make a fresh temporary folder under the system temp folder (never inside the decomp or this repository).
2. Compile: `python <decomp>/tools/msvc.py --compiler <unit.compiler> /c /nologo <unit.flags...> /Z7
   /I<decomp>/include <decomp>/<unit.source> /Fo<temp>/unit.obj`, working directory = the temporary folder.
3. Check that the code is unchanged, per function of the unit. Objects are read with
   `engine_map.codeview.ObjectFile`, not the decomp's `Coff`: `Coff` refuses sections with more than 65,535
   relocations, and `compare()` only needs `named()`, `sections` and `symbols`, which `ObjectFile` has.
   - with `--image`: `compare.compare(ObjectFile(obj), Pe(image), manifest)` from the decomp's
     `tools/compare.py` with `manifest = {symbols, functions, literal_symbols}` of the unit (as
     `tools/build_and_match.py` does); a function is verified when its verdict is `match`. If `compare()`
     raises for the unit, fall back to the next method for that unit (counted as `fallbacks` in the summary).
   - without it: compile again without `/Z7` and compare each function's bytes with the 4 bytes of every
     relocation masked, and the relocation lists (offset and kind) equal.
4. Read the object with `engine_map.codeview.ObjectFile` and parse it with `engine_map.codeview.parse_object`.
5. Delete the temporary folder (also on errors).
6. Send the result to the parent process, which is the only writer of the database.

A function that is not verified gets a row in `functions` (with `verified = 0`) but no lines and no locals.
Compiler-generated functions such as scalar deleting destructors (`??_G...`) have no procedure record: they can
be verified and still have `has_debug = 0` and no lines.

The summary printed on stdout has every count of `meta`, plus `checks` (units per check method), `fallbacks`,
the first 20 failed or partial units and `replaced`. A dying worker process does not stop the build: the units
it held are recorded as `compile-failed` and a new pool takes over.

On Windows a file that any process holds open with SQLite (for example a running MCP server, before it stopped
keeping the file open) cannot be replaced. The builder retries the final move for 30 seconds; if it still fails
it keeps `<out>.building`, says which process to stop and how to rename the file, prints the summary with
`replaced: false` and exits with code 3.

## 3. Parser: `engine_map/codeview.py`

```python
obj = ObjectFile(path)      # COFF object reader
data = parse_object(obj)    # plain dicts and lists only (it crosses a process boundary)
```

`ObjectFile` exposes what the decomp's `Coff` class exposes, so the parser also accepts a `Coff`:

- `sections`: list, index 0 is `None`, then one dict per section: `name`, `data` (bytes), `flags`,
  `relocs` (list of `(offset, symbol_index, kind)`).
- `symbols`: dict from symbol table index to `{'name', 'value', 'section', 'type', 'storage'}`.
- `by_name`: dict from name to the list of those symbol dicts.

Unlike `Coff`, `ObjectFile` must accept sections with more than 65,535 relocations (section flag
`0x01000000`: the real count is the `VirtualAddress` of the first relocation, which is then skipped).

`parse_object` returns:

```python
{
  'compiler': str | None,       # version text from S_COMPILE2
  'files': [str, ...],          # source paths exactly as the compiler recorded them; lines refer to them by index
  'functions': [Function, ...],
  'globals': [Global, ...],
  'types': {hash: Type, ...},
  'warnings': [str, ...],       # each unknown record kind once, truncated records, unresolved relocations
}
Function = {
  'symbol': str | None,         # COFF symbol of the code (the decorated name), None when it cannot be resolved
  'name': str,                  # name in the record, e.g. 'MersenneTwister::Seed'
  'isGlobal': bool,             # S_GPROC32 (True) or S_LPROC32 (False)
  'length': int, 'debugStart': int, 'debugEnd': int, 'procFlags': int,
  'type': hash | None,          # the function's LF_PROCEDURE / LF_MFUNCTION
  'frameBytes': int | None, 'frameFlags': int | None,     # from S_FRAMEPROC
  'locals': [Local, ...],       # parameters and locals in record order
  'lines': [[offset, file_index, line], ...],             # offset from the function start, ascending
}
Local = {
  'name': str,
  'kind': 'bprel' | 'regrel' | 'register' | 'static',
  'reg': int | None,            # CodeView register number (regrel, register); None for bprel, which is always ebp
  'offset': int | None,         # signed, for bprel and regrel
  'type': hash | None,
  'scope': [lo, hi] | None,     # function-relative byte range [lo, hi) of the enclosing S_BLOCK32; None = whole function
  'symbol': str | None,         # static locals only: COFF symbol of the data
}
Global = {'name': str, 'symbol': str | None, 'type': hash | None, 'isGlobal': bool}
Type = {
  'kind': str,                  # one of symdb_schema.TYPE_KINDS
  'name': str | None, 'size': int | None,
  'target': hash | None,        # ptr/ref/modifier/array/enum: the type it is built on; proc/mfunc: return type
  'attrs': dict,                # small, JSON-able; see section 5
  'members': [{'name': str, 'offset': int, 'type': hash, 'bitPos': int | None, 'bitLen': int | None}, ...],
  'bases': [{'type': hash, 'offset': int | None, 'virtual': bool}, ...],
  'methods': [{'name': str, 'type': hash | None, 'virtual': bool, 'static': bool, 'vtOffset': int | None}, ...],
  'enumerators': [[name, value], ...],
  'args': [hash, ...],          # proc/mfunc parameter types in order
}
```

Empty lists may be left out. `types` holds every type reachable from a function, a local or a global, and
every complete (not forward-declared) named class, struct, union and enum of the object. The dict is closed: every
hash that appears anywhere in the result (in a function, a local, a global or inside another type) is a key
of `types`.

## 4. CodeView C13 reference

Authoritative source: Microsoft's `cvinfo.h`. Where a real object disagrees with this section, the object
wins: fix the parser and correct this section.

### 4.1 `.debug$S`

An object has one main `.debug$S` and, for units built with `/Gy`, one more per COMDAT function. Each starts
with `u32 signature = 4`, then subsections `{u32 kind, u32 length}` + data, each padded to 4 bytes.
The string table and the file checksums exist once, in the main section; line blocks in every section refer
to them.

| Kind | Content |
|---|---|
| `0xF1` | Symbol records |
| `0xF2` | Lines of one function: `{u32 offCon, u16 segCon, u16 flags, u32 cbCon}` then blocks `{u32 fileOffset, u32 nLines, u32 cbBlock}` + `nLines` x `{u32 offset, u32 lineStart:24, deltaLineEnd:7, isStatement:1}`; when `flags & 1`, `nLines` x `{u16 colStart, u16 colEnd}` follow. `fileOffset` is a byte offset into the `0xF4` data. `cbBlock` includes the 12-byte block header. The function is named by the relocation on `offCon` |
| `0xF3` | String table: NUL-terminated strings |
| `0xF4` | File checksums: entries `{u32 nameOffset (into 0xF3), u8 checksumBytes, u8 checksumKind}` + checksum, padded to 4 |
| `0xF5` | Frame data (not stored in schema 1) |

Line numbers `0xFEEFEE` and `0xF00F00` mean "no source": drop them.

Symbol record: `{u16 length (bytes after this field), u16 kind}` + body. Offsets below are into the body.

| Kind | Name | Body |
|---|---|---|
| `0x1101` | S_OBJNAME | `u32 signature`, name |
| `0x1116` | S_COMPILE2 | `u32 flags, u16 machine, u16 feMajor, feMinor, feBuild, u16 major, minor, build`, version text |
| `0x1110` / `0x110F` | S_GPROC32 / S_LPROC32 | `u32 parent, end, next` (0, 4, 8), `u32 length` (12), `u32 debugStart` (16), `u32 debugEnd` (20), `u32 type` (24), `u32 offset` (28, relocated), `u16 segment` (32, relocated), `u8 flags` (34), name (35) |
| `0x1012` | S_FRAMEPROC | `u32 frameBytes` (0), `u32 padBytes`, `u32 padOffset`, `u32 savedRegBytes`, `u32 handlerOffset`, `u16 handlerSection` (20), `u32 flags` (22) |
| `0x110B` | S_BPREL32 | `i32 offset, u32 type`, name |
| `0x1111` | S_REGREL32 | `i32 offset, u32 type, u16 register`, name |
| `0x1106` | S_REGISTER | `u32 type, u16 register`, name |
| `0x1103` | S_BLOCK32 | `u32 parent, end` (0, 4), `u32 length` (8), `u32 offset` (12, relocated), `u16 segment` (16), name (18) |
| `0x0006` | S_END | closes the innermost open procedure, block or thunk |
| `0x110C` / `0x110D` | S_LDATA32 / S_GDATA32 | `u32 type, u32 offset (relocated), u16 segment`, name. Inside a procedure it is a static local |
| `0x1112` / `0x1113` | S_LTHREAD32 / S_GTHREAD32 | as S_LDATA32 |
| `0x1102` | S_THUNK32 | `u32 parent, end, next, u32 offset, u16 segment, u16 length, u8 ordinal`, name. Opens a scope |
| `0x1105` | S_LABEL32 | `u32 offset, u16 segment, u8 flags`, name |
| `0x1107` | S_CONSTANT | `u32 type`, numeric leaf, name |
| `0x1108` | S_UDT | `u32 type`, name |
| `0x1124` | S_UNAMESPACE | name |
| `0x1139` / `0x113A` | S_CALLSITEINFO / S_FRAMECOOKIE | In `/GS` objects. Not stored |

`S_LDATA32` records with an empty name inside a procedure (jump tables, labels such as `$LN13`) are not static
locals: the parser drops them.

Names are NUL-terminated. The code a procedure record (or a lines subsection, or a block) belongs to is given
by the relocation at its `offset` field: `(offset, symbol_index, kind)` with kind `0x0B` (SECREL), next to a
kind `0x0A` (SECTION) relocation on the segment field. When the relocation's symbol is a function symbol
(`type & 0x20`), that is the function. When it is a section symbol, the function is the function symbol in
the same section whose `value` equals the 4 bytes stored in the field.

A block's function-relative range starts at (the block's stored offset field minus the procedure's stored
offset field) when both relocations name the same symbol, and is `length` bytes long.

VC9 has no `S_LOCAL`/`S_DEFRANGE` records. In `/O2` code a `S_REGISTER` local claims its register for the
whole function, which is often not true: consumers treat `register` locals of optimised units as hints.

### 4.2 `.debug$T`

`u32 signature = 4`, then records `{u16 length, u16 leaf}` + body, numbered from type index `0x1000`.
Indexes below `0x1000` are primitive types: low byte = kind, bits 8-11 = mode (`0x4` = 32-bit pointer to it).

| Index | Type | Index | Type |
|---|---|---|---|
| `0x0003` | void | `0x0040` | float |
| `0x0008` | HRESULT | `0x0041` | double |
| `0x0010` | signed char | `0x0042` | long double (80-bit) |
| `0x0011` | short | `0x0068` / `0x0069` | __int8 / unsigned __int8 |
| `0x0012` | long | `0x0070` | char |
| `0x0013` | __int64 | `0x0071` | wchar_t |
| `0x0020` | unsigned char | `0x0072` / `0x0073` | short / unsigned short (int2) |
| `0x0021` | unsigned short | `0x0074` / `0x0075` | int / unsigned int |
| `0x0022` | unsigned long | `0x0076` / `0x0077` | __int64 / unsigned __int64 (int8) |
| `0x0023` | unsigned __int64 | `0x0030` | bool |

A numeric leaf is a `u16`: below `0x8000` it is the value; otherwise it selects what follows: `0x8000` i8,
`0x8001` i16, `0x8002` u16, `0x8003` i32, `0x8004` u32, `0x8009` i64, `0x800A` u64.

| Leaf | Name | Body |
|---|---|---|
| `0x1001` | LF_MODIFIER | `u32 type, u16 attr` (1 const, 2 volatile, 4 unaligned) |
| `0x1002` | LF_POINTER | `u32 type, u32 attr`: bits 0-4 pointer kind, bits 5-7 mode (0 pointer, 1 reference, 2 data member pointer, 3 member function pointer), bit 9 volatile, bit 10 const, bits 13-18 size. Member pointers add `u32 class, u16 format` |
| `0x1008` | LF_PROCEDURE | `u32 return, u8 call, u8 attr, u16 paramCount, u32 argList` |
| `0x1009` | LF_MFUNCTION | `u32 return, u32 class, u32 this, u8 call, u8 attr, u16 paramCount, u32 argList, i32 thisAdjust` |
| `0x1201` | LF_ARGLIST | `u32 count`, `count` x `u32 type` |
| `0x1203` | LF_FIELDLIST | sub-records (below) |
| `0x1205` | LF_BITFIELD | `u32 type, u8 length, u8 position` |
| `0x1206` | LF_METHODLIST | entries `{u16 attr, u16 pad, u32 type}` + `u32 vtableOffset` when the entry introduces a virtual |
| `0x000A` | LF_VTSHAPE | `u16 count`, 4 bits per entry |
| `0x1503` | LF_ARRAY | `u32 element, u32 index`, numeric size in bytes, name |
| `0x1504` / `0x1505` | LF_CLASS / LF_STRUCTURE | `u16 count, u16 property, u32 fieldList, u32 derived, u32 vshape`, numeric size, name |
| `0x1506` | LF_UNION | `u16 count, u16 property, u32 fieldList`, numeric size, name |
| `0x1507` | LF_ENUM | `u16 count, u16 property, u32 underlying, u32 fieldList`, name |

`property & 0x80` = forward reference (declared, not defined here). Calling conventions: 0 cdecl, 2 pascal,
4 fastcall, 7 stdcall, 11 thiscall.

Field list sub-records start with a `u16` leaf and are padded to 4 bytes with bytes `0xF1`-`0xF3` (a byte
`>= 0xF0` where a leaf is expected is padding: skip `byte & 0x0F` bytes).

| Leaf | Name | Body |
|---|---|---|
| `0x1400` | LF_BCLASS | `u16 attr, u32 type`, numeric offset |
| `0x1401` / `0x1402` | LF_VBCLASS / LF_IVBCLASS | `u16 attr, u32 type, u32 vbptrType`, numeric vbptr offset, numeric vbtable index |
| `0x1404` | LF_INDEX | `u16 pad, u32 type`: the list continues in that LF_FIELDLIST |
| `0x1409` | LF_VFUNCTAB | `u16 pad, u32 type`: the class has a vtable pointer |
| `0x1502` | LF_ENUMERATE | `u16 attr`, numeric value, name |
| `0x150D` | LF_MEMBER | `u16 attr, u32 type`, numeric offset, name |
| `0x150E` | LF_STMEMBER | `u16 attr, u32 type`, name |
| `0x150F` | LF_METHOD | `u16 count, u32 methodList`, name |
| `0x1510` | LF_NESTTYPE | `u16 pad, u32 type`, name |
| `0x1511` | LF_ONEMETHOD | `u16 attr, u32 type`, then `u32 vtableOffset` when the method introduces a virtual, name |

Method `attr`: bits 0-1 access, bits 2-4 kind: 0 plain, 1 virtual (override), 2 static, 3 friend, 4 introducing
virtual, 5 pure virtual (override), 6 pure introducing virtual. Kinds 4 and 6 carry `vtableOffset` (byte
offset of the slot in the vtable: slot = offset / 4).

## 5. Type model

Every type reference in the parser's output is a hash string.

| Kind | Hash | Fields |
|---|---|---|
| `prim` | `prim:%04x` of the index with the pointer mode removed | `name` (C spelling), `size`, `attrs = {'cv': index}`. A primitive index with pointer mode `0x4` is a `ptr` whose target is the `prim` |
| `udtref` | `udtref:<name>` | `name` only. A reference to a named class, struct, union or enum by name |
| everything else | first 20 hex digits of SHA-1 over a canonical JSON of the type (kind, name, size, target hash, attrs, members, bases, methods, enumerators, args; keys sorted) | |

Rules:

1. Inside another type (pointer target, modifier target, array element, member type, base class, argument,
   return type, method class), a named class/struct/union/enum is always referred to through its `udtref`.
   This breaks every cycle and keeps a type's hash independent of how other classes are defined.
2. A function's own `type`, a local's `type` and a global's `type` refer directly to the definition when the
   object has a complete one, else to the `udtref`.
3. Unnamed types (name contains `<unnamed` or `__unnamed`) are never referred to by name: they are hashed by
   content and referenced directly.
4. Bit fields are flattened into the member: `type` = the underlying type, `bitPos` and `bitLen` set.
5. A forward-declared type produces no definition, only `udtref` references.

`attrs` by kind: `ptr`/`ref`: `{'const', 'volatile'}`; `memptr`: `{'class': hash, 'mode': 'data'|'function'}`;
`modifier`: `{'const', 'volatile', 'unaligned'}`; `array`: `{'count': elements or None}`;
`struct`/`class`/`union`: `{'vtable': bool}`; `proc`: `{'call': name}`; `mfunc`: `{'call': name, 'class':
hash, 'this': hash or None, 'thisAdjust': int}`; `other`: `{'leaf': number}`. Only true flags need be stored.

The same class is defined in many units, sometimes with different detail. Every distinct definition is kept
(`types.seen` counts the units that produced it). Lookups by name take the definition with the most members,
then the highest `seen`, and report the other sizes as conflicts.

## 6. Database

Schema: `engine_map/symdb_schema.py` (`DDL`, `create(connection)`).

| Table | Notes |
|---|---|
| `meta` | `schema`, `gameBuild`, `decompPath`, `decompCommit`, `builtAt` (UTC ISO), `mode` (`image` or `double-compile`), `imageSha256` (left out when unknown, as is `decompCommit`), and the counts in `symdb_schema.META_KEYS`. `unitsFailed` counts units with no usable result; `partial` units are counted in `unitsPartial`, not as failed. `unitsDoubleCompile` counts units checked by the second compile |
| `units` | One row per unit tried. `status`: `ok`, `partial` (some functions not verified), `compile-failed`, `parse-error`, `no-compiler`. `flags` is the recipe flags joined by spaces. `units.json` lists some source files twice with different flags and functions: the second recipe is stored as `src/a.cpp#2` |
| `files` | `path` relative to the decomp root with forward slashes and the real case (`src/bscore/bsrandom.cpp`). The compiler records lower-cased absolute paths: map them back through a lower-case index of the decomp tree. Paths outside the decomp stay absolute, forward slashes |
| `functions` | One row per function of `units.json` (`addr`, `size`, `decorated` from there). `name`, `type_id`, `proc_flags`, `frame_*`, `debug_*` from the procedure record; `has_debug = 1` when one was found. When two units list the same address, the first verified one wins |
| `lines` | Only for verified functions. `offset` is from the function start; rows with `offset >= size` are dropped; one row per offset (when several records share an offset, the last one is kept) |
| `locals` | Only for verified functions. `seq` keeps record order (parameters come first in VC9 output). `reg` is NULL for `bprel` (always EBP). `scope_lo`/`scope_hi` are offsets from the function start, `scope_hi` exclusive; both NULL = the whole function |
| `types` | `hash` unique; `attrs` is JSON text or NULL; `target_id` resolved from the hash |
| `members`, `bases`, `methods`, `enums`, `args` | Children of a type, `seq` in declaration order |
| `globals` | Data symbols outside procedures and static locals' data; `addr` from the unit's `symbols` map in `units.json` when the decorated name is there, else NULL |

## 7. Reader: `fnv_mcp/fnv_symdb.py`

```python
db = fnv_symdb.load(path)            # cached; opens read-only; None when the file does not exist
db.identity()                        # path, schema, decompCommit, builtAt, mode, counts
db.function(addr)                    # row of the function that starts at addr, or None
db.line_at(address)                  # {'functionAddress', 'offset', 'file', 'line', 'lineOffset', 'verified'} or None
db.lines_of(func_addr)               # [(offset, file, line), ...]
db.addresses_of(file, line)          # [(address, func_addr), ...]; file matched by case-insensitive path suffix
db.locals_of(func_addr, offset=None) # [{'name', 'kind', 'location', 'reg', 'offset', 'type', 'size', 'param'}, ...]
db.type_text(type_id)                # C spelling: 'const char *', 'TESObjectREFR *', 'float [3]'
db.layout(name)                      # best definition by name: size, members, bases, methods, variants, conflicts
fnv_symdb.default_path(project)      # project setting, then env FNV_SYMDB, then <state>/symdb.sqlite
```

`line_at` answers for any address inside a function with lines: the row with the greatest `offset` not above
the address's offset. Tools that have no database say so with the build command as the next step and keep
working without it.

The reader opens the file only while an operation runs (one read-only connection per thread, closed when the
operation ends), so a rebuild can replace the file while the MCP server runs; `load()` notices the new file by
its size and time.

A caller frame's address is a return address: the instruction after the `call`. `EngineMap.symbolize(address,
return_address=True)` looks up the function, the line and the locals' scope at `address - 1` and still reports
the real address and offset. The crash doctor uses it for every call-trace frame except the first (the fault
address); the hang doctor for every frame except the `eip` frame, and for the stack-scan entries.
