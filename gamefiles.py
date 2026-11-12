"""Minimal readers for Breath of the Wild (Switch) game files: Yaz0, SARC, MSBT, BYML.

Savage Wind keeps the game's romfs extracted as plain files in <data folder>/extracted/romfs, with the DLC
in extracted/aoc1 (and aoc2). Files with a `.s` extension (.sbyml, .smubin, .ssarc, .sbmaptex, ...) are Yaz0
compressed. `.pack` files are SARC archives whose contents may be Yaz0 compressed again.
"""
import struct
from pathlib import Path


def yaz0(data):
    """Decompress Yaz0 (returns data unchanged when it isn't Yaz0)."""
    if data[:4] != b'Yaz0':
        return data
    size = struct.unpack_from('>I', data, 4)[0]
    out = bytearray(size)
    src, dst, n = 16, 0, len(data)
    while dst < size and src < n:
        code = data[src]
        src += 1
        if code == 0xFF and src + 8 <= n and dst + 8 <= size:   # 8 literal bytes
            out[dst:dst + 8] = data[src:src + 8]
            src += 8
            dst += 8
            continue
        for bit in range(8):
            if dst >= size:
                break
            if code & (0x80 >> bit):
                out[dst] = data[src]
                dst += 1
                src += 1
                continue
            b1, b2 = data[src], data[src + 1]
            src += 2
            back = ((b1 & 0x0F) << 8 | b2) + 1
            length = b1 >> 4
            if length == 0:
                length = data[src] + 0x12
                src += 1
            else:
                length += 2
            start = dst - back
            if back >= length:
                out[dst:dst + length] = out[start:start + length]
            else:   # overlapping copy: repeat the pattern
                chunk = out[start:dst]
                out[dst:dst + length] = (chunk * (length // back + 1))[:length]
            dst += length
    return bytes(out)


class Romfs:
    """The game files: the base romfs, with the DLC (aoc) folders on top for paths they also have."""

    def __init__(self, extracted):
        self.root = Path(extracted)
        self.base = self.root / 'romfs'
        self.aoc = [p for p in (self.root / 'aoc1', self.root / 'aoc2') if p.is_dir()]
        self._packs = {}

    def path(self, rel, dlc=True):
        if dlc:
            for a in self.aoc:
                p = a / rel
                if p.is_file():
                    return p
        p = self.base / rel
        return p if p.is_file() else None

    def exists(self, rel):
        return self.path(rel) is not None

    def read(self, rel, dlc=True):
        p = self.path(rel, dlc)
        if p is None:
            raise FileNotFoundError(rel)
        return yaz0(p.read_bytes())

    def glob(self, pattern):
        """Relative paths matching a glob in the base romfs."""
        return sorted(str(p.relative_to(self.base)).replace('\\', '/') for p in self.base.glob(pattern))

    def pack(self, rel, dlc=True):
        """{name: bytes} of a .pack (cached)."""
        key = (rel, dlc)
        if key not in self._packs:
            self._packs[key] = sarc(self.read(rel, dlc))
        return self._packs[key]


def sarc(data):
    """{name: bytes} of a SARC archive."""
    data = yaz0(data)
    assert data[:4] == b'SARC', 'not a SARC'
    e = '<' if data[6:8] == b'\xff\xfe' else '>'
    data_start = struct.unpack_from(e + 'I', data, 0x0C)[0]
    sfat = 0x14
    count = struct.unpack_from(e + 'H', data, sfat + 6)[0]
    nodes = [struct.unpack_from(e + 'IIII', data, sfat + 12 + i * 16) for i in range(count)]
    names_start = sfat + 12 + count * 16 + 8
    out = {}
    for _hash, attr, start, end in nodes:
        if attr & 0x01000000:
            name_off = (attr & 0xFFFF) * 4
            name = data[names_start + name_off:data.index(b'\0', names_start + name_off)].decode('utf-8')
        else:
            name = f'{_hash:08x}'
        out[name] = data[data_start + start:data_start + end]
    return out


def _clean_msbt_text(raw):
    """UTF-16LE text with control tags (0x0E group type size data, 0x0F close) removed."""
    out = []
    i = 0
    n = len(raw) // 2
    chars = struct.unpack(f'<{n}H', raw[:n * 2])
    while i < n:
        c = chars[i]
        if c == 0:
            break
        if c == 0x0E:
            size = chars[i + 3] if i + 3 < n else 0
            i += 4 + size // 2
            continue
        if c == 0x0F:
            i += 3
            continue
        out.append(chr(c))
        i += 1
    return ''.join(out)


def msbt(data):
    """{label: text} of an MSBT message file (little endian, as on Switch)."""
    data = yaz0(data)
    assert data[:8] == b'MsgStdBn', 'not MSBT'
    e = '<' if data[8:10] == b'\xff\xfe' else '>'
    sections = struct.unpack_from(e + 'H', data, 0x0E)[0]
    off = 0x20
    labels, texts = {}, []
    for _ in range(sections):
        magic = data[off:off + 4]
        size = struct.unpack_from(e + 'I', data, off + 4)[0]
        body = off + 16
        if magic == b'LBL1':
            groups = struct.unpack_from(e + 'I', data, body)[0]
            for g in range(groups):
                cnt, loff = struct.unpack_from(e + 'II', data, body + 4 + g * 8)
                p = body + loff
                for _ in range(cnt):
                    ln = data[p]
                    name = data[p + 1:p + 1 + ln].decode('utf-8')
                    idx = struct.unpack_from(e + 'I', data, p + 1 + ln)[0]
                    labels[name] = idx
                    p += 1 + ln + 4
        elif magic == b'TXT2':
            cnt = struct.unpack_from(e + 'I', data, body)[0]
            offs = [struct.unpack_from(e + 'I', data, body + 4 + i * 4)[0] for i in range(cnt)] + [size]
            texts = [_clean_msbt_text(data[body + offs[i]:body + offs[i + 1]]) for i in range(cnt)]
        off = body + size
        off = (off + 15) & ~15
    return {name: texts[idx] for name, idx in labels.items() if idx < len(texts)}


def aamp(data):
    """Objects of an AAMP v2 parameter file (little endian; .bshop shop lists, ...) as
    {object name CRC32: {parameter name CRC32: value}}, from the root list and every list below it.
    Only the parameter types the shop lists use are read (bool, f32, int, u32, strings); others are None."""
    data = yaz0(data)
    assert data[:4] == b'AAMP', 'not AAMP'
    pio_off = struct.unpack_from('<I', data, 0x14)[0]
    out = {}

    def value(t, off):
        if t == 0:
            return bool(struct.unpack_from('<I', data, off)[0])
        if t == 1:
            return struct.unpack_from('<f', data, off)[0]
        if t == 2:
            return struct.unpack_from('<i', data, off)[0]
        if t == 17:
            return struct.unpack_from('<I', data, off)[0]
        if t in (7, 8, 15, 20):
            return data[off:data.index(b'\0', off)].decode('utf-8', 'replace')
        return None

    def plist(off):
        _crc, lists_rel, n_lists, objs_rel, n_objs = struct.unpack_from('<IHHHH', data, off)
        for i in range(n_objs):
            o = off + objs_rel * 4 + i * 8
            ocrc, params_rel, n_params = struct.unpack_from('<IHH', data, o)
            params = out.setdefault(ocrc, {})
            for j in range(n_params):
                p = o + params_rel * 4 + j * 8
                pcrc, word = struct.unpack_from('<II', data, p)
                params[pcrc] = value(word >> 24, p + (word & 0xFFFFFF) * 4)
        for i in range(n_lists):
            plist(off + lists_rel * 4 + i * 12)

    plist(0x30 + pio_off)
    return out


def byml(data):
    """Parse a BYML (v2-v4) document into Python objects."""
    data = yaz0(data)
    e = '<' if data[:2] == b'YB' else '>'
    _ver, key_off, str_off, root_off = struct.unpack_from(e + 'HIII', data, 2)
    order = 'little' if e == '<' else 'big'
    u32, i32, f32 = struct.Struct(e + 'I'), struct.Struct(e + 'i'), struct.Struct(e + 'f')

    def strtab(off):
        if not off:
            return []
        cnt = int.from_bytes(data[off + 1:off + 4], order)
        offs = struct.unpack_from(e + f'{cnt}I', data, off + 4)
        return [data[off + o:data.index(b'\0', off + o)].decode('utf-8') for o in offs]

    keys, strings = strtab(key_off), strtab(str_off)

    def value(t, raw):
        if t == 0xA0:
            return strings[raw]
        if t == 0xD0:
            return bool(raw)
        if t == 0xD1:
            return i32.unpack(u32.pack(raw))[0]
        if t == 0xD2:
            return f32.unpack(u32.pack(raw))[0]
        if t == 0xD3:
            return raw
        if t in (0xD4, 0xD5, 0xD6):
            return struct.unpack_from(e + {0xD4: 'q', 0xD5: 'Q', 0xD6: 'd'}[t], data, raw)[0]
        if t == 0xFF:
            return None
        if t in (0xC0, 0xC1):
            return node(raw)
        return raw

    def node(off):
        t = data[off]
        cnt = int.from_bytes(data[off + 1:off + 4], order)
        if t == 0xC0:
            types = data[off + 4:off + 4 + cnt]
            vals = off + 4 + ((cnt + 3) & ~3)
            return [value(types[i], u32.unpack_from(data, vals + i * 4)[0]) for i in range(cnt)]
        if t == 0xC1:
            out = {}
            for i in range(cnt):
                p = off + 4 + i * 8
                k = int.from_bytes(data[p:p + 3], order)
                out[keys[k]] = value(data[p + 3], u32.unpack_from(data, p + 4)[0])
            return out
        raise ValueError(f'unsupported BYML node 0x{t:02x}')

    return node(root_off) if root_off else None
