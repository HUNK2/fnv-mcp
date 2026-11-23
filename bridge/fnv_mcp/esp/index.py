"""The EditorID index of a master file (FalloutNV.esm, a DLC .esm): what esm_index.py writes to
state/index/<master>.json.gz and formids.MasterIndex reads.

  {"master": name, "size": bytes, "sha256": ..., "entries": {"edid": [type, "FORMID", full_name, model_path], ...}}

Duplicated EditorIDs map to a list of such entries. Since tool gaps 1a (2026-11-23) the builder makes the index of a
master that has none the first time a spec names it (authoring.Authoring.index), so esm_index.py is no longer a step an
agent has to run by hand. Standard library only.
"""
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

from . import codec

# Bulk geometry records never carry useful EditorIDs; skipping them avoids decompression.
SKIP = {b'LAND', b'NAVM', b'NAVI', b'PGRD'}


def build_index(path):
    path = Path(path)
    started = time.monotonic()
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    entries, counts, total = {}, {}, 0
    for rec, _groups in codec.iter_records(data):
        total += 1
        if rec.type.encode() in SKIP:
            continue
        counts[rec.type] = counts.get(rec.type, 0) + 1
        try:
            edid = rec.editor_id
        except (codec.FormatError, ValueError, UnicodeDecodeError):
            continue
        if not edid:
            continue
        full = rec.get('FULL')
        model = rec.get('MODL')
        row = [rec.type, '%08X' % rec.form_id,
               codec.cstring(full) if full else None,
               codec.cstring(model) if model else None]
        if edid in entries:
            existing = entries[edid]
            entries[edid] = (existing if isinstance(existing[0], list) else [existing]) + [row]
        else:
            entries[edid] = row
    return {'master': path.name, 'size': len(data), 'sha256': digest, 'records': total,
            'indexed': len(entries), 'types': counts,
            'seconds': round(time.monotonic() - started, 1), 'entries': entries}


def write_index(path, out_dir):
    """Build the index of the master at `path` into out_dir/<name>.json.gz (written whole, then moved in place)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = build_index(path)
    target = out_dir / (Path(path).name + '.json.gz')
    fd, tmp = tempfile.mkstemp(dir=out_dir, prefix='.' + target.name + '.')
    try:
        with os.fdopen(fd, 'wb') as raw, gzip.open(raw, 'wt', encoding='utf-8') as stream:
            json.dump(index, stream, separators=(',', ':'))
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target, index
