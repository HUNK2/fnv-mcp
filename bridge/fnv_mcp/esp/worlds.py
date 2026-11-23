"""Exterior cells of a master's worldspace, for placing objects outdoors (tool gaps 1c).

A plugin that places a reference in an exterior cell overrides the world record, the cell at that grid and, for a
persistent reference, the world's persistent cell, inside the same groups the master uses:

  GRUP WRLD (top) > WRLD > GRUP world children (1, world FormID)
      > [persistent CELL > GRUP cell children (6) > GRUP persistent (8) > REFR...]
      > GRUP exterior block (4, y x) > GRUP exterior sub-block (5, y x) > CELL > GRUP cell children (6)
          > GRUP temporary (9) > REFR...

The block and sub-block labels are the master's own (read from its groups, not computed), so the plugin's cells sit
where the master's are. The world record is copied without its offset table (OFST), which describes the master's
own file. Written from the user's robot project (rotf-fnv tools/master.py world_cells) and its esp.py.
"""
from pathlib import Path
import struct
import threading

from . import codec

_GROUP = struct.Struct('<4sI4si')
_CACHE = {}
_LOCK = threading.Lock()


class WorldError(ValueError):
    pass


def world_layout(path, world_form):
    """{'persistent': raw CELL FormID or None, 'cells': {(x, y): (raw CELL FormID, block label, sub-block label)}} of
    the world whose raw FormID (as the file numbers it) is world_form. Cached per file size and time."""
    path = Path(path)
    st = path.stat()
    key = (str(path.resolve()).lower(), st.st_size, st.st_mtime_ns, world_form)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
    data = path.read_bytes()
    found = _scan(data, world_form)
    if found is None:
        raise WorldError('no world %08X in %s' % (world_form, path.name))
    with _LOCK:
        _CACHE[key] = found
    return found


def _scan(data, world_form):
    pos = 24 + struct.unpack_from('<I', data, 4)[0]
    end = len(data)
    label = struct.pack('<I', world_form)
    while pos + 24 <= end:
        tag, size, glabel, gtype = _GROUP.unpack_from(data, pos)
        if tag != b'GRUP':
            raise WorldError('expected a top group at %d' % pos)
        if gtype == 0 and glabel == b'WRLD':
            p, gend = pos + 24, pos + size
            while p + 24 <= gend:
                tag, size2, glabel2, gtype2 = _GROUP.unpack_from(data, p)
                if tag == b'GRUP':
                    if gtype2 == 1 and glabel2 == label:
                        out = {'persistent': None, 'cells': {}}
                        _walk(data, p + 24, p + size2, out, None, None)
                        return out
                    p += size2
                else:
                    p += 24 + size2
            return None
        pos += size
    return None


def _walk(data, p, end, out, block, subblock):
    while p + 24 <= end:
        tag, size, label, gtype = _GROUP.unpack_from(data, p)
        if tag == b'GRUP':
            if gtype == 4:
                _walk(data, p + 24, p + size, out, label, None)
            elif gtype == 5:
                _walk(data, p + 24, p + size, out, block, label)
            p += size
            continue
        if tag == b'CELL':
            rec, _ = codec._parse_record(data, p, end, lazy=False)
            if block is None:
                if out['persistent'] is None:
                    out['persistent'] = rec.form_id
            else:
                xclc = rec.get('XCLC')
                if xclc and len(xclc) >= 8:
                    out['cells'][struct.unpack_from('<ii', xclc)] = (rec.form_id, block, subblock)
        p += 24 + size
