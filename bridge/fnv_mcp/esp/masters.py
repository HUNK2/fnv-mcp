"""Records read straight from master files, for templates and overrides (tool gaps 1a).

A spec record may start from a master record ("template": "@EditorID") or override one ("override": true). Before
this, a template had to be a dump file made by tools/esm_research.py; now the master file itself is read.

MasterRecords keeps, per master file, a map of FormID -> offset made by one walk over the record headers (FalloutNV.esm,
about 465,000 records, takes under a second). Placed references, landscape, navmesh and dialogue lines are left out of
the map: nothing builds from them yet. The map is kept per process and made again when the file's size or time
changes.

FormIDs inside a record are numbered by the file it comes from: top byte n is the file's n-th master, and the file
itself is the byte after its last master. The bytes of a record are copied as they are, so the plugin's master list
must start with the source file's own masters and then the file itself (FalloutNV.esm first, then DeadMoney.esm for
a Dead Money record): then every FormID means the same in the plugin. A record from a file listed otherwise is refused.
"""
from pathlib import Path
import struct
import threading

from . import codec

SKIP = {b'REFR', b'ACHR', b'ACRE', b'PGRE', b'PMIS', b'PBEA', b'PFLA', b'PCBE', b'LAND', b'NAVM', b'INFO'}
_HEAD = struct.Struct('<4sIII')


class MasterError(ValueError):
    pass


def scan_offsets(data):
    """(masters, {form_id: (type, offset)}) of one plugin's records, from a flat walk over the headers."""
    if data[:4] != b'TES4' or len(data) < 24:
        raise MasterError('not a TES4 plugin')
    header_size = struct.unpack_from('<I', data, 4)[0]
    masters = [codec.cstring(s.data) for s in codec.parse_subrecords(data[24:24 + header_size]) if s.tag == 'MAST']
    offsets, pos, end = {}, 24 + header_size, len(data)
    unpack = _HEAD.unpack_from
    while pos + 24 <= end:
        tag, size, _flags, form_id = unpack(data, pos)
        if tag == b'GRUP':
            pos += 24
            continue
        if tag not in SKIP:
            offsets[form_id] = (tag.decode('latin-1'), pos)
        pos += 24 + size
    return masters, offsets


class _File:
    def __init__(self, path):
        self.path = Path(path)
        st = self.path.stat()
        self.stamp = (st.st_size, st.st_mtime_ns)
        self.masters, self.offsets = scan_offsets(self.path.read_bytes())

    def read(self, offset):
        with open(self.path, 'rb') as f:
            f.seek(offset)
            tag, size, flags, form_id, vc1, version, vc2 = codec.RECORD_HEADER.unpack(f.read(24))
            body = f.read(size)
        rec = codec.Record(tag.decode('latin-1'), form_id, flags, vc1, version, vc2)
        if flags & codec.COMPRESSED:
            rec._compressed = body
        else:
            rec._subrecords = codec.parse_subrecords(body, offset + 24)
        return rec


_CACHE = {}
_LOCK = threading.Lock()


def _open(path):
    key = str(Path(path).resolve()).lower()
    st = Path(path).stat()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit and hit.stamp == (st.st_size, st.st_mtime_ns):
            return hit
    made = _File(path)
    with _LOCK:
        _CACHE[key] = made
    return made


class MasterRecords:
    """The master files a build may read records from, by file name (case-insensitive)."""

    def __init__(self, paths=()):
        self.paths = {}
        for p in paths:
            p = Path(p)
            self.paths.setdefault(p.name.lower(), p)

    def has(self, name):
        return name.lower() in self.paths and self.paths[name.lower()].is_file()

    def file(self, name):
        if not self.has(name):
            raise MasterError('master file %s not found (looked in the game Data folder and the dev profile mods)'
                              % name)
        return _open(self.paths[name.lower()])

    def winning(self, masters, owner, local, expect_type=None):
        """The last version, among the plugin's masters, of the record that `owner` defines with object id `local`.

        Returns (file name, record with subrecords decoded and its FormID as numbered in the plugin). Raises
        MasterError when no listed master holds it or the version found cannot be copied as it is."""
        found = None
        lowered = [m.lower() for m in masters]
        if owner.lower() not in lowered:
            raise MasterError('%s is not a master of this plugin' % owner)
        for name in masters[lowered.index(owner.lower()):]:
            if not self.has(name):
                if name.lower() == owner.lower():
                    self.file(name)     # raises: the defining master must be readable
                continue
            f = self.file(name)
            own = [m.lower() for m in f.masters] + [name.lower()]
            if owner.lower() not in own:
                continue
            hit = f.offsets.get((own.index(owner.lower()) << 24) | local)
            if hit:
                found = (name, f, hit)
        if not found:
            raise MasterError('no record %s:%06X in %s' % (owner, local, ', '.join(masters)))
        name, f, (rtype, offset) = found
        if expect_type and rtype != expect_type:
            raise MasterError('%s:%06X is a %s, not a %s' % (owner, local, rtype, expect_type))
        own = [m.lower() for m in f.masters] + [name.lower()]
        if lowered[:len(own)] != own:
            raise MasterError('%s from %s: its FormIDs count %s; list those masters first, in that order, to use it'
                              % ('%s:%06X' % (owner, local), name, ', '.join(f.masters + [name])))
        rec = f.read(offset)
        subs = list(rec.subrecords)
        out = codec.Record(rec.type, rec.form_id, rec.flags & ~codec.COMPRESSED, rec.vc1, rec.form_version, rec.vc2,
                           _subrecords=subs)
        return name, out
