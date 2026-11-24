"""The steps fnv_asset calls: make a NIF, KF or DDS under a Data folder, with the output path checked first.

`out` is a Data-relative path (meshes\\...\\x.nif, meshes\\...\\x.kf, textures\\...\\x.dds): no drive, no "..", the
file type's own top folder. The caller picks the Data folder (a mod folder of the MO2 profile, or the game's Data
when direct install changes are allowed)."""
import re
from pathlib import Path, PureWindowsPath

from . import dds, kf, mesh
from .errors import AssetMakeError

__all__ = ['AssetMakeError', 'FOLDER', 'data_path', 'target', 'make']

FOLDER = {'.nif': 'meshes', '.kf': 'meshes', '.dds': 'textures'}
_PART = re.compile(r'[A-Za-z0-9_][A-Za-z0-9 _.\-()]*\Z')


def data_path(root, rel, what='out'):
    """root / rel when rel is a relative path of plain parts (no drive, no "..", no device trick)."""
    if not isinstance(rel, str) or not rel.strip():
        raise AssetMakeError('%s is a Data-relative path such as meshes\\mymod\\thing.nif' % what)
    p = PureWindowsPath(rel.replace('/', '\\'))
    if p.drive or p.root or p.anchor:
        raise AssetMakeError('%s is relative to Data (meshes\\..., textures\\...), not %s' % (what, rel))
    if any(part in ('.', '..') or not _PART.match(part) or part.endswith(('.', ' ')) for part in p.parts):
        raise AssetMakeError('%s %r: each part is a plain name (letters, digits, space, _ - . ( )), no ".."'
                             % (what, rel))
    return Path(root).joinpath(*p.parts)


def target(root, rel, ext):
    """root / rel when rel is a safe Data-relative path of type ext under its top folder; else AssetMakeError."""
    data_path(root, rel)
    p = PureWindowsPath(rel.replace('/', '\\'))
    parts = p.parts
    if p.suffix.lower() != ext:
        raise AssetMakeError('out %r must end in %s' % (rel, ext))
    if len(parts) < 2 or parts[0].lower() != FOLDER[ext]:
        raise AssetMakeError('a %s goes under %s\\ (out %r)' % (ext, FOLDER[ext], rel))
    return Path(root).joinpath(*parts)


def make(kind, src, root, rel, options=None, skeleton_bytes=None):
    """kind nif, kf or dds: make Data\\rel under root from the source file src. Returns the maker's report plus
    `out` (rel) and `path` (the file written)."""
    ext = {'nif': '.nif', 'kf': '.kf', 'dds': '.dds'}[kind]
    out = target(root, rel, ext)
    if not Path(src).is_file():
        raise AssetMakeError('the source %s is not a file' % src)
    options = dict(options or {})
    if kind == 'nif':
        report = mesh.make(src, out, options, rel=str(PureWindowsPath(rel.replace('/', '\\'))))
    elif kind == 'kf':
        report = kf.make(src, out, options, skeleton_bytes)
    else:
        report = dds.make(src, out, options)
    report['out'] = str(PureWindowsPath(rel.replace('/', '\\'))).lower()
    report['path'] = str(out)
    return report
