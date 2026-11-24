"""DDS textures for the game from PNG, JPEG, TGA or BMP files (ported from the robot project's textures.py).

Textures are DXT-compressed with a full mip chain, as vanilla New Vegas textures are: DXT1 when the image is opaque,
DXT5 when it has alpha. A normal map (`normal`) is DXT5 with the specular strength in alpha (from `specular`, an
image, or 64). Sides must be powers of two for a mip chain: an image that is not is resized to the nearest power of
two, with a warning. Needs Pillow 11.2 or later (older Pillow writes no DXT blocks; checked on 12.3)."""
import io
import struct
from pathlib import Path

import numpy as np
import PIL
from PIL import Image

from .errors import AssetMakeError

MIN_PILLOW = '11.2'
SOURCES = ('.png', '.jpg', '.jpeg', '.tga', '.bmp')

DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH = 0x1, 0x2, 0x4
DDSD_PIXELFORMAT, DDSD_MIPMAPCOUNT, DDSD_LINEARSIZE = 0x1000, 0x20000, 0x80000
DDPF_FOURCC = 0x4
CAPS_MIPMAPPED = 0x401008            # complex, texture, mipmap


def is_pow2(n):
    return n > 0 and n & (n - 1) == 0


def nearest_pow2(n):
    return 1 << max(0, int(round(np.log2(max(n, 1)))))


def _pow2(img, name, warnings):
    w, h = img.size
    if is_pow2(w) and is_pow2(h):
        return img
    size = (nearest_pow2(w), nearest_pow2(h))
    warnings.append('%s: %dx%d is not a power of two; resized to %dx%d' % (name, w, h, size[0], size[1]))
    return img.resize(size, Image.LANCZOS)


def _levels(img):
    levels = [img]
    while levels[-1].size != (1, 1):
        w, h = levels[-1].size
        levels.append(levels[-1].resize((max(1, w // 2), max(1, h // 2)), Image.BOX))
    return levels


def _dxt_level(img, fourcc):
    """One level's DXT blocks (Pillow writes a 128-byte DDS header before them)."""
    buf = io.BytesIO()
    img.save(buf, 'DDS', pixel_format=fourcc)
    data = buf.getvalue()
    if data[84:88] != fourcc.encode('ascii') or len(data) < 128:
        raise AssetMakeError('Pillow %s wrote no %s DDS: DXT encoding needs Pillow %s or later'
                             % (PIL.__version__, fourcc, MIN_PILLOW))
    return data[128:]


def write(path, rgba, fmt=None, warnings=None):
    """rgba: HxWx4 uint8. Writes a DXT1/DXT5 DDS (fmt None: DXT1 when every alpha is 255) with every mip level.
    Returns (format, width, height, levels)."""
    warnings = [] if warnings is None else warnings
    img = _pow2(Image.fromarray(np.ascontiguousarray(rgba, np.uint8), 'RGBA'), Path(path).name, warnings)
    if fmt is None:
        fmt = 'DXT1' if int(np.asarray(img)[:, :, 3].min()) == 255 else 'DXT5'
    levels = [_dxt_level(lv if fmt == 'DXT5' else lv.convert('RGB'), fmt) for lv in _levels(img)]
    w, h = img.size
    pixel_format = struct.pack('<II4sIIIII', 32, DDPF_FOURCC, fmt.encode('ascii'), 0, 0, 0, 0, 0)
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_MIPMAPCOUNT | DDSD_LINEARSIZE
    head = (struct.pack('<4sIIIIIII', b'DDS ', 124, flags, h, w, len(levels[0]), 0, len(levels)) + b'\0' * 44
            + pixel_format + struct.pack('<IIIII', CAPS_MIPMAPPED, 0, 0, 0, 0))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(head + b''.join(levels))
    return fmt, w, h, len(levels)


def open_image(src):
    try:
        return Image.open(src)
    except (OSError, ValueError) as exc:
        raise AssetMakeError('%s is not an image Pillow can read: %s' % (Path(src).name, exc)) from exc


def rgba_of(img, normal=False, specular=None):
    """HxWx4 uint8 from an image; a normal map gets the specular strength in alpha."""
    if not normal:
        return np.asarray(img.convert('RGBA'), np.uint8)
    rgb = img.convert('RGB')
    out = np.zeros((rgb.size[1], rgb.size[0], 4), np.uint8)
    out[:, :, :3] = np.asarray(rgb, np.uint8)
    if specular is not None:
        out[:, :, 3] = np.asarray(specular.convert('L').resize(rgb.size, Image.BILINEAR), np.uint8)
    else:
        out[:, :, 3] = 64
    return out


def make(src, out, options=None):
    """Make `out` (a .dds) from the image `src`. Options: format (DXT1, DXT5; default by alpha), normal (bool),
    specular (an image path for a normal map's alpha)."""
    options = options or {}
    if Path(src).suffix.lower() not in SOURCES:
        raise AssetMakeError('a texture is made from %s, not %s' % (', '.join(SOURCES), Path(src).suffix or 'this'))
    fmt = options.get('format')
    if fmt is not None and str(fmt).upper() not in ('DXT1', 'DXT5'):
        raise AssetMakeError('options.format is DXT1 or DXT5')
    normal = bool(options.get('normal'))
    spec = open_image(options['specular']) if options.get('specular') else None
    warnings = []
    rgba = rgba_of(open_image(src), normal, spec)
    fmt, w, h, levels = write(out, rgba, 'DXT5' if normal else (fmt and str(fmt).upper()), warnings)
    return {'format': fmt, 'width': w, 'height': h, 'mipLevels': levels, 'bytes': Path(out).stat().st_size,
            'warnings': warnings}


def flat_normal(dst):
    """A 4x4 flat normal map with low specular, for a mesh whose source has no normal map."""
    rgba = np.zeros((4, 4, 4), np.uint8)
    rgba[:, :] = (128, 128, 255, 32)
    return write(dst, rgba, 'DXT5')
