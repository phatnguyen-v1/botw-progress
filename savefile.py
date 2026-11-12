"""Read a Breath of the Wild game_data.sav and work out which map objects and quests are done.

Switch format (game 1.6 and later): u32 version, u32 0xFFFFFFFF, u32 1, then (u32 hash, u32 value) pairs to the
end of the file, sorted by hash. The hash is the CRC32 of the flag name. Vectors, strings and arrays are
several pairs with the same hash in a row (one u32 each).
"""
import struct
import zlib
from pathlib import Path

TABLE_START = 0x0C
VERSIONS = {0x24E2: '1.0', 0x24EE: '1.1', 0x2588: '1.2', 0x29C0: '1.3', 0x2A46: '1.3.3', 0x2F8E: '1.4',
            0x3EF8: '1.5', 0x3EF9: '1.5', 0x471A: '1.6', 0x471B: '1.6', 0x471E: '1.6', 0x4730: '1.6+'}

# category key -> (map_data.json key, how it is completed)
CATEGORIES = {
    'korok':          ('koroks', 'nonzero'),
    'shrine':         ('shrines', 'nonzero'),
    'tower':          ('towers', 'nonzero'),
    'divine_beast':   ('divine_beasts', 'nonzero'),
    'location':       ('locations', 'nonzero'),
    'great_fairy':    ('great_fairies', 'nonzero'),
    'hinox':          ('hinoxes', 'nonzero'),
    'talus':          ('taluses', 'nonzero'),
    'molduga':        ('moldugas', 'nonzero'),
    'lynel':          ('lynels', 'none'),     # not tracked: Lynels come back after a Blood Moon
    'armor_chest':    ('armor_chests', 'nonzero'),
    'treasure_chest': ('treasure_chests', 'nonzero'),
}


class SaveError(Exception):
    pass


def crc(name):
    return zlib.crc32(name.encode('utf-8'))


PLAYTIME_HASH = crc('PlayReport_PlayTime')   # seconds
SAVE_POS_HASH = crc('PlayerSavePos')
SAVE_MAP_HASH = crc('PlayerSavePosMapName')
SAVE_MAPTYPE_HASH = crc('PlayerSavePosMapType')


def read_table(buf):
    """{hash: [values]} (one value for plain flags, several for vectors, strings and arrays)."""
    if len(buf) < TABLE_START + 8 or struct.unpack_from('<I', buf, 4)[0] != 0xFFFFFFFF:
        raise SaveError('not a BotW game_data.sav (bad header)')
    n = (len(buf) - TABLE_START) // 8
    vals = struct.unpack_from(f'<{n * 2}I', buf, TABLE_START)
    table = {}
    for i in range(0, n * 2, 2):
        h = vals[i]
        lst = table.get(h)
        if lst is None:
            table[h] = [vals[i + 1]]
        else:
            lst.append(vals[i + 1])
    if len(table) < 1000:
        raise SaveError('save is incomplete')
    return table


def text(table, h):
    v = table.get(h)
    if not v:
        return None
    return b''.join(struct.pack('<I', x) for x in v).split(b'\0', 1)[0].decode('utf-8', 'replace')


def player_position(table):
    """Where Link was when the game saved (PlayerSavePos), only on the map of Hyrule (not inside a shrine)."""
    v = table.get(SAVE_POS_HASH)
    if not v or len(v) < 3:
        return None
    if text(table, SAVE_MAPTYPE_HASH) not in (None, 'MainField'):
        return None
    x, y, z = struct.unpack('<3f', struct.pack('<3I', *v[:3]))
    if not all(abs(c) < 20000 for c in (x, y, z)) or (x, y, z) == (0, 0, 0):
        return None
    return {'x': round(x, 1), 'y': round(y, 1), 'z': round(z, 1)}


def _first(table, h):
    v = table.get(h)
    return v[0] if v else None


def save_info(path):
    buf = Path(path).read_bytes()
    table = read_table(buf)
    return {'version_id': struct.unpack_from('<I', buf, 0)[0], 'playtime': _first(table, PLAYTIME_HASH) or 0}


def parse_quests(table, quests):
    """{quest id: [status, log step, None, target step]}; status is locked / available / active / done.

    Each step has a flag (NextFlag) the game sets when it reaches that step: the first step's is
    <quest>_Activated (the quest started), the last one's means the quest is complete. The adventure log shows
    the text of the last step reached; the map points at the places of the step after it (the one to do).
    Steps whose flag isn't in the save are side branches and are skipped.
    """
    out = {}
    for q in quests:
        steps = [(s['name'], _first(table, s['flag'])) for s in q['steps'] if s.get('flag') is not None]
        steps = [(n, v) for n, v in steps if v is not None]
        last = max((i for i, (_, v) in enumerate(steps) if v), default=-1)
        if steps and last == len(steps) - 1:
            out[q['id']] = ['done', steps[-1][0], None, None]
        elif last >= 0:
            target = next((n for n, v in steps[last + 1:] if not v), None)
            out[q['id']] = ['active', steps[last][0], None, target]
        elif q.get('ready') is None or _first(table, q['ready']):
            out[q['id']] = ['available', steps[0][0] if steps else 'Ready', True, None]
        else:
            out[q['id']] = ['locked', 'Ready', False, None]
    return out


def porch(table):
    """The inventory: [(item actor, value)] in order. PorchItem is an array of 64-byte strings; PorchItem_Value1 the
    count for materials (durability for weapons, nothing much for armor)."""
    raw = table.get(crc('PorchItem')) or []
    blob = b''.join(struct.pack('<I', x) for x in raw)
    names = [blob[i:i + 64].split(b'\0', 1)[0].decode('utf-8', 'replace') for i in range(0, len(blob), 64)]
    values = table.get(crc('PorchItem_Value1')) or []
    return [(n, values[i] if i < len(values) else 0) for i, n in enumerate(names) if n]


def parse_outfits(table, outfits):
    """{piece id: [best level ever had (-1: never), best level in the inventory now (-1: not carried)]} and the
    counts of the materials in the inventory ({item actor: count}).

    Each upgrade level is its own actor with its own IsGet_ flag (set when Link first gets it, kept when sold).
    """
    inv = porch(table)
    carried = {n for n, _ in inv}
    mats = {}
    for n, v in inv:
        if not n.startswith(('Armor_', 'Weapon_')) and v < 0x80000000:
            mats[n] = mats.get(n, 0) + v
    out = {}
    for s in outfits:
        for p in s['pieces']:
            ever = max((i for i, lv in enumerate(p['levels']) if lv['flag'] is not None and _first(table, lv['flag'])),
                       default=-1)
            now = max((i for i, lv in enumerate(p['levels']) if lv['actor'] in carried), default=-1)
            out[p['id']] = [max(ever, now), now]
    return out, mats


def parse(path, map_data, quests=None, outfits=None):
    """{done: {category: [bool per object, in map_data order]}, ...} plus metadata."""
    buf = Path(path).read_bytes()
    table = read_table(buf)

    done = {}
    for cat, (key, rule) in CATEGORIES.items():
        flags = []
        for obj in map_data.get(key, []):
            if rule == 'none' or obj.get('completion_hash') is None:
                flags.append(False)
            else:
                flags.append(bool(_first(table, obj['completion_hash'])))
        done[cat] = flags
    # shrines found (their location discovered) but not finished
    found = {'shrine': [bool(o.get('found_hash') is not None and _first(table, o['found_hash']))
                        for o in map_data.get('shrines', [])]}
    result = {
        'version_id': struct.unpack_from('<I', buf, 0)[0],
        'playtime': _first(table, PLAYTIME_HASH) or 0,
        'player': player_position(table),
        'done': done,
        'found': found,
        'korok_seeds': _first(table, crc('HiddenKorok_Number')),
    }
    if quests:
        result['quests'] = parse_quests(table, quests)
    if outfits:
        result['outfits'], result['materials'] = parse_outfits(table, outfits)
    return result
