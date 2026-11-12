"""Decode the in-game map tiles (UI/MapTex/MainField/*.sbmaptex) from your own BotW files into WebP tiles.

The map is a 12 x 10 grid of tiles of 1000 x 1000 world units (x -6000..6000, z -5000..5000), in four detail
levels: 3000, 840, 300 and 135 px. Each .sbmaptex is Yaz0-compressed BFRES holding one BC1 (sRGB) texture
in the Switch's block-linear layout.
"""
import struct

import texture2ddecoder

ASTC_BLOCKS = {0x2D: (4, 4), 0x2E: (5, 4), 0x2F: (5, 5), 0x30: (6, 5), 0x31: (6, 6), 0x32: (8, 5),
               0x33: (8, 6), 0x34: (8, 8), 0x35: (10, 5), 0x36: (10, 6), 0x37: (10, 8), 0x38: (10, 10),
               0x39: (12, 10), 0x3A: (12, 12)}


def _div_up(a, b):
    return (a + b - 1) // b


def deswizzle(data, width_blocks, height_blocks, bpp, block_height_log2):
    """Tegra X1 block-linear -> linear, in units of compressed blocks of `bpp` bytes."""
    block_height = 1 << block_height_log2
    gobs_wide = _div_up(width_blocks * bpp, 64)
    row_bytes = width_blocks * bpp
    out = bytearray(row_bytes * height_blocks)
    mv = memoryview(data)
    for y in range(height_blocks):
        y_base = (y // (8 * block_height)) * 512 * block_height * gobs_wide + (y % (8 * block_height) // 8) * 512 \
            + ((y % 8) // 2) * 64 + (y % 2) * 16
        o = y * row_bytes
        for x in range(width_blocks):
            xb = x * bpp
            addr = y_base + (xb // 64) * 512 * block_height + ((xb % 64) // 32) * 256 + ((xb % 32) // 16) * 32 + (xb % 16)
            out[o:o + bpp] = mv[addr:addr + bpp]
            o += bpp
    return bytes(out)


# Block-compressed formats besides ASTC: kind -> (bytes per 4x4 block, decoder)
BC_FORMATS = {0x1A: (8, 'decode_bc1'), 0x1C: (16, 'decode_bc3'), 0x1D: (8, 'decode_bc4'),
              0x1E: (16, 'decode_bc5'), 0x20: (16, 'decode_bc7')}


def textures(data):
    """Every texture in a BNTX file: [(name, BRTI offset)]."""
    out, i = [], data.find(b'BRTI')
    while i >= 0:
        name_at = struct.unpack_from('<Q', data, i + 0x60)[0]
        n = struct.unpack_from('<H', data, name_at)[0]
        out.append((data[name_at + 2:name_at + 2 + n].decode('utf-8', 'replace'), i))
        i = data.find(b'BRTI', i + 4)
    return out


def decode_texture(data, i):
    """Decode the BRTI texture at offset i (first mip level) -> (width, height, BGRA bytes, kind)."""
    fmt = struct.unpack_from('<I', data, i + 0x1C)[0]
    width, height = struct.unpack_from('<II', data, i + 0x24)
    layout = struct.unpack_from('<I', data, i + 0x34)[0]
    image_size = struct.unpack_from('<I', data, i + 0x50)[0]
    ptrs = struct.unpack_from('<Q', data, i + 0x70)[0]
    mip0 = struct.unpack_from('<Q', data, ptrs)[0]
    brtd = data.find(b'BRTD')
    if 0 <= brtd and mip0 < brtd:   # not relocated (BNTX inside BFRES): relative to the BRTD data
        mip0 += brtd + 0x10
    kind = fmt >> 8
    if kind in ASTC_BLOCKS:
        bw, bh = ASTC_BLOCKS[kind]
        bpp, decode = 16, lambda raw: texture2ddecoder.decode_astc(raw, width, height, bw, bh)
    elif kind in BC_FORMATS:
        bw = bh = 4
        bpp, fn = BC_FORMATS[kind]
        decode = lambda raw: getattr(texture2ddecoder, fn)(raw, width, height)
    elif kind == 0x0B:   # R8G8B8A8, uncompressed (the inventory icons, UI/StockItem/*.sbitemico)
        bw = bh = 1
        bpp = 4

        def decode(raw):
            bgra = bytearray(raw)
            bgra[0::4], bgra[2::4] = raw[2::4], raw[0::4]
            return bytes(bgra)
    else:
        raise ValueError(f'unsupported texture format 0x{fmt:04x}')
    raw = deswizzle(data[mip0:mip0 + image_size], _div_up(width, bw), _div_up(height, bh), bpp, layout & 7)
    return width, height, decode(raw), kind


def decode_bntx(data):
    """First texture of a BNTX file -> (width, height, RGBA bytes)."""
    i = data.find(b'BRTI')
    if i < 0:
        raise ValueError('no BRTI texture')
    width, height, bgra, _ = decode_texture(data, i)
    rgba = bytearray(bgra)
    rgba[0::4], rgba[2::4] = bgra[2::4], bgra[0::4]
    return width, height, bytes(rgba)


LAYERS = ('surface',)
# Bump when the tile choice or drawing changes: installs then redraw their tiles.
TILES_VERSION = 1
# detail level folder -> file prefix: MapTex = 3000 px, MapTex1 = 840 px, MapTex2 = 300 px, MapTex3 = 135 px
LOD_PREFIX = {'00': 'MapTex', '01': 'MapTex1', '02': 'MapTex2', '03': 'MapTex3'}
COLS = 'ZABCDEFGHIJK'   # Z is the westmost column


def build_tiles(romfs, out_dir, lods=('02', '01'), progress=None):
    """Decode the map tiles to out_dir/surface/<lod>/<col>-<row>.webp. Returns the number of tiles.

    UI/MapTex/MainField/<prefix>_<C>-<R>.sbmaptex: column letter Z, A-K (x -6000..6000), row 0-9 (z -5000..5000).
    The file without a suffix is the fully revealed map; `_0` is the unexplored blueprint and the numbered
    ones are partly revealed versions (bit masks of tower regions).
    """
    import json
    from pathlib import Path
    out_dir = Path(out_dir)
    report = progress or (lambda f, m: None)
    try:
        old = json.loads((out_dir / 'meta.json').read_text(encoding='utf-8')).get('version')
    except (OSError, ValueError):
        old = TILES_VERSION
    if old != TILES_VERSION:
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
    todo = [(lod, col, row, f'UI/MapTex/MainField/{LOD_PREFIX[lod]}_{COLS[col]}-{row}.sbmaptex')
            for lod in lods for col in range(12) for row in range(10)]
    from PIL import Image
    for i, (lod, col, row, path) in enumerate(todo):
        dest = out_dir / 'surface' / lod / f'{col}-{row}.webp'
        if not dest.is_file() and romfs.exists(path):
            dest.parent.mkdir(parents=True, exist_ok=True)
            w, h, px = decode_bntx(romfs.read(path, dlc=False))
            tmp = dest.with_suffix('.part')
            Image.frombytes('RGBA', (w, h), px).convert('RGB').save(tmp, 'WEBP', quality=80, method=4)
            tmp.replace(dest)
        report((i + 1) / len(todo), f'Drawing the surface map… {i + 1}/{len(todo)}')
    (out_dir / 'meta.json').write_text(json.dumps({
        'version': TILES_VERSION, 'built': int(__import__('time').time()), 'source': 'game', 'format': 'webp',
        'lods': list(lods), 'cols': 12, 'rows': 10, 'tile_world': 1000, 'origin': [-6000, -5000],
        'layers': list(LAYERS)}))
    return len(todo)
