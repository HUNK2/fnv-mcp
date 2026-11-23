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
    """FNV Data folder: $FNV_DATA, else the usual Steam location (Windows only, decision D5)."""
    if os.environ.get('FNV_DATA'):
        return Path(os.environ['FNV_DATA'])
    return Path(r'C:\Program Files (x86)\Steam\steamapps\common\Fallout New Vegas\Data')


DEFAULT_DATA = default_data()
SKIP = index_module.SKIP
build_index = index_module.build_index


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('masters', nargs='*', default=['FalloutNV.esm'])
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA)
    parser.add_argument('--out', type=Path, default=ROOT / 'state' / 'index')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for name in args.masters:
        target, index = index_module.write_index(args.data / name, args.out)
        summary = {k: v for k, v in index.items() if k != 'entries'}
        summary['out'] = str(target)
        print(json.dumps(summary))


if __name__ == '__main__':
    main()
