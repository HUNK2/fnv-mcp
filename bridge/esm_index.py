"""Build a read-only lookup index of a master file (FalloutNV.esm, DLC .esm).

    python3 esm_index.py [--data DIR] [--out DIR] FalloutNV.esm [DeadMoney.esm ...]

Writes state/index/<master>.json.gz with every record that has an EditorID:
  {"edid": [type, "FORMID", full_name, model_path], ...}
Duplicated EditorIDs map to a list of such entries. The master is never
modified; its size and SHA-256 are recorded so a game update invalidates the index.
Standard library only.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fnv_mcp.esp import index as index_module  # noqa: E402

ROOT = Path(__file__).resolve().parent
def default_data():
    """FNV Data folder: $FNV_DATA, else the found game's Data (fnv_mcp.locate), else None (pass --data)."""
    from fnv_mcp.locate import game_data
    return game_data()


DEFAULT_DATA = default_data()
SKIP = index_module.SKIP
build_index = index_module.build_index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('masters', nargs='*', default=['FalloutNV.esm'])
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--out', type=Path, default=ROOT / 'state' / 'index')
    args = parser.parse_args()
    if args.data is None:
        parser.error('no Fallout: New Vegas install found: pass --data <game folder>\\Data or set FNV_DATA')
    args.out.mkdir(parents=True, exist_ok=True)
    for name in args.masters:
        target, index = index_module.write_index(args.data / name, args.out)
        summary = {k: v for k, v in index.items() if k != 'entries'}
        summary['out'] = str(target)
        print(json.dumps(summary))


if __name__ == '__main__':
    main()
