"""Read and write New Vegas NIF and KF files (Gamebryo 20.2.0.7, user version 11, Bethesda version 34).

Blocks of the types in `schema` are parsed into fields; any other block is kept as its raw bytes, so a vanilla file
can be read, changed and written back without knowing every block it holds. New blocks are appended (indices of the
old ones never move), and new strings are appended to the string table.

    f = nif.read(data)          # NifFile
    f.blocks[i].type, f.blocks[i].fields['Name']
    i = f.add('NiNode', Name='x', ...)   # defaults for every field not given
    data = f.write()
"""
from .file import NifError, NifFile, Block, read, new_file  # noqa: F401
from . import schema  # noqa: F401
