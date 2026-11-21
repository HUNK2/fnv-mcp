"""Schema of the symbol database (state/symdb.sqlite). One source of truth for the builder
(engine_map/symdb.py), the reader (fnv_mcp/fnv_symdb.py) and the tests. See docs/FNV-SYMDB.md."""

SCHEMA_VERSION = 1

DDL = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE files(id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE);
CREATE TABLE units(
    id INTEGER PRIMARY KEY, source TEXT NOT NULL UNIQUE, compiler TEXT, flags TEXT,
    status TEXT NOT NULL, error TEXT, functions INTEGER, verified INTEGER, seconds REAL);
CREATE TABLE functions(
    addr INTEGER PRIMARY KEY, size INTEGER NOT NULL, unit_id INTEGER NOT NULL,
    name TEXT, decorated TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 0,
    has_debug INTEGER NOT NULL DEFAULT 0, type_id INTEGER,
    proc_flags INTEGER, frame_bytes INTEGER, frame_flags INTEGER,
    debug_start INTEGER, debug_end INTEGER);
CREATE TABLE lines(
    func_addr INTEGER NOT NULL, offset INTEGER NOT NULL, file_id INTEGER NOT NULL, line INTEGER NOT NULL,
    PRIMARY KEY(func_addr, offset)) WITHOUT ROWID;
CREATE TABLE locals(
    func_addr INTEGER NOT NULL, seq INTEGER NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
    reg INTEGER, offset INTEGER, type_id INTEGER, scope_lo INTEGER, scope_hi INTEGER,
    PRIMARY KEY(func_addr, seq)) WITHOUT ROWID;
CREATE TABLE types(
    id INTEGER PRIMARY KEY, hash TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, name TEXT,
    size INTEGER, target_id INTEGER, attrs TEXT, seen INTEGER NOT NULL DEFAULT 1);
CREATE TABLE members(
    type_id INTEGER NOT NULL, seq INTEGER NOT NULL, name TEXT, offset INTEGER NOT NULL,
    type_id2 INTEGER, bit_pos INTEGER, bit_len INTEGER,
    PRIMARY KEY(type_id, seq)) WITHOUT ROWID;
CREATE TABLE bases(
    type_id INTEGER NOT NULL, seq INTEGER NOT NULL, base_id INTEGER, offset INTEGER, is_virtual INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(type_id, seq)) WITHOUT ROWID;
CREATE TABLE methods(
    type_id INTEGER NOT NULL, seq INTEGER NOT NULL, name TEXT NOT NULL, type_id2 INTEGER,
    is_virtual INTEGER NOT NULL DEFAULT 0, is_static INTEGER NOT NULL DEFAULT 0, vt_offset INTEGER,
    PRIMARY KEY(type_id, seq)) WITHOUT ROWID;
CREATE TABLE enums(
    type_id INTEGER NOT NULL, seq INTEGER NOT NULL, name TEXT NOT NULL, value INTEGER NOT NULL,
    PRIMARY KEY(type_id, seq)) WITHOUT ROWID;
CREATE TABLE args(
    type_id INTEGER NOT NULL, seq INTEGER NOT NULL, type_id2 INTEGER,
    PRIMARY KEY(type_id, seq)) WITHOUT ROWID;
CREATE TABLE globals(
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, decorated TEXT, addr INTEGER, type_id INTEGER,
    unit_id INTEGER NOT NULL, is_global INTEGER NOT NULL DEFAULT 1);
CREATE INDEX functions_name ON functions(name);
CREATE INDEX functions_unit ON functions(unit_id);
CREATE INDEX lines_file ON lines(file_id, line);
CREATE INDEX types_name ON types(name);
CREATE INDEX globals_name ON globals(name);
CREATE INDEX globals_addr ON globals(addr);
"""

# Values of locals.kind
LOCAL_KINDS = ('bprel', 'regrel', 'register', 'static')

# Values of types.kind
TYPE_KINDS = ('prim', 'ptr', 'ref', 'memptr', 'modifier', 'array', 'bitfield',
              'struct', 'class', 'union', 'enum', 'udtref', 'proc', 'mfunc', 'other')

# Keys the builder writes into meta (all values are text)
META_KEYS = ('schema', 'gameBuild', 'decompPath', 'decompCommit', 'builtAt', 'mode', 'imageSha256',
             'units', 'unitsOk', 'unitsFailed', 'unitsPartial', 'unitsDoubleCompile', 'functions', 'functionsWithLines', 'functionsVerified',
             'types', 'seconds')

GAME_BUILD = '1.4.0.525'

# x86 CodeView register numbers that appear in locals.reg
REGISTERS = {
    1: 'al', 2: 'cl', 3: 'dl', 4: 'bl', 5: 'ah', 6: 'ch', 7: 'dh', 8: 'bh',
    9: 'ax', 10: 'cx', 11: 'dx', 12: 'bx', 13: 'sp', 14: 'bp', 15: 'si', 16: 'di',
    17: 'eax', 18: 'ecx', 19: 'edx', 20: 'ebx', 21: 'esp', 22: 'ebp', 23: 'esi', 24: 'edi',
    128: 'st0', 129: 'st1', 130: 'st2', 131: 'st3', 132: 'st4', 133: 'st5', 134: 'st6', 135: 'st7',
    154: 'xmm0', 155: 'xmm1', 156: 'xmm2', 157: 'xmm3', 158: 'xmm4', 159: 'xmm5', 160: 'xmm6', 161: 'xmm7',
    30006: 'vframe',
}


def create(connection):
    """Create an empty database on an open sqlite3 connection."""
    connection.executescript(DDL)
    connection.execute("INSERT INTO meta(key, value) VALUES('schema', ?)", (str(SCHEMA_VERSION),))
    connection.execute("INSERT INTO meta(key, value) VALUES('gameBuild', ?)", (GAME_BUILD,))
