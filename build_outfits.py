"""Build assets/outfits.json from your own Breath of the Wild game files (Savage Wind's extracted folder).

    python build_outfits.py --extracted <Savage Wind data folder>\\extracted [--lang USen]

Every armor piece (Actor/ActorInfo.product.sbyml, profiles ArmorHead / ArmorUpper / ArmorLower) with its upgrade
levels (armorNextRankName: each Great Fairy upgrade is its own actor), the materials each upgrade takes, its set
and effect, and where to get it, all from the game files:
  - shops: the shop lists (Actor/ShopData/*.bshop in the NPC actor packs) and the armor shop mannequins placed on
    the map (Mannequin_*, EquipArmorName1, linked ForSale to the shopkeeper);
  - treasure chests on the map (TBox_*, DropActor) and inside shrines (Pack/DungeonNNN.pack).
What the files don't say (quest gifts, amiibo) comes from a short hand-written list (HINTS).
The save has an IsGet_<actor> flag per piece and level, and the inventory (PorchItem) with counts.
"""
import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path

import gamefiles
from build_mapdata import crc, game_flags, messages, Text, placed_actors, static_doc
from paths import ASSETS

OUT = ASSETS / 'outfits.json'
ICON_SIZE = 96   # px; shown at 40-48 css px in the list
SLOTS = {'ArmorHead': 'head', 'ArmorUpper': 'body', 'ArmorLower': 'legs'}
# the four Great Fairies' upgrade shops: Npc_DressFairy_<n> upgrades to ★n+1 (and needs n+1 fairies restored)
FAIRY_SHOPS = {f'Npc_DressFairy_0{i}' for i in range(4)}
# places a shop is named after when it's close to one (else the nearest place of any kind)
TOWN_ICONS = {'Village', 'Hatago', 'Labo', 'CheckPoint'}

# set names (the game has no text for them), by seriesArmorSeriesType
SETS = {
    'Old': 'Old clothes', 'Hero': "Champion's Tunic", 'Hylia': 'Hylian set', 'HyliaArmor': "Soldier's set",
    'Amber': 'Gerudo jewelry', 'Ruby': 'Gerudo jewelry', 'Sapphire': 'Gerudo jewelry', 'Topaz': 'Gerudo jewelry',
    'Opal': 'Gerudo jewelry', 'Dia': 'Gerudo jewelry', 'Winter': 'Warm Doublet', 'Rito': 'Snowquill set',
    'Gerudo': 'Desert Voe set', 'GerudoCloth': 'Gerudo set', 'Rubber': 'Rubber set', 'Goron': 'Flamebreaker set',
    'Zora': 'Zora set', 'Sheikah': 'Stealth set', 'SheikMask': "Sheik's Mask", 'Thunder': 'Thunder Helm',
    'Climb': 'Climbing set', 'Power': 'Barbarian set', 'AmiiboMajorasMask': 'Fierce Deity set',
    'Stalfos': 'Radiant set', 'Ancient': 'Ancient set', 'Desert': 'Boots', 'Snow': 'Boots',
    'Bokoblin': 'Monster masks', 'Moriblin': 'Monster masks', 'Lizalfos': 'Monster masks', 'Lynel': 'Monster masks',
    'Black': 'Dark set', 'AmiiboOcarinaOfTime': 'Set of Time', 'AmiiboWindWaker': 'Set of the Wind',
    'AmiiboTwilightPrincess': 'Set of Twilight', 'AmiiboSkywardSword': 'Set of the Sky',
    'AmiiboFirstHero': 'Set of the Hero', 'Korok': 'Set of the Wild', 'Majora': "Majora's Mask",
    'Tingle': "Tingle's set", 'Midna': "Midna's Helmet", 'Phantom': 'Phantom set',
    'WindWaker': 'Island Lobster Shirt', 'Ravio': "Ravio's Hood", 'Zant': "Zant's Helmet",
    'RoyalGuard': 'Royal Guard set', 'PhantomGanon': 'Phantom Ganon set', 'Collaboration': 'Salvager set',
}
DIVINE_HELMS = {'Armor_181_Head', 'Armor_182_Head', 'Armor_183_Head', 'Armor_184_Head'}
EFFECTS = {
    'ResistCold': 'Cold resistance', 'ResistHot': 'Heat resistance', 'ResistElectric': 'Shock resistance',
    'ResistBurn': 'Flame guard', 'SwimSpeed': 'Swim speed up', 'Quietness': 'Stealth up',
    'ResistLightning': 'Lightning proof', 'ClimbSpeed': 'Climb speed up', 'AttackUp': 'Attack up',
    'ResistAncient': 'Guardian resist up', 'SandMove': 'Sand speed up', 'SnowMove': 'Snow speed up',
    'ResistFreeze': 'Unfreezable', 'BeamPowerUp': 'Master Sword beam up', 'WakeWind': 'Wind (Island Lobster)',
}
AMIIBO = {
    'AmiiboOcarinaOfTime': 'Link (Ocarina of Time)', 'AmiiboWindWaker': 'Toon Link (The Wind Waker)',
    'AmiiboTwilightPrincess': 'Link (Twilight Princess)', 'AmiiboSkywardSword': 'Link (Skyward Sword)',
    'AmiiboFirstHero': '8-bit Link', 'AmiiboMajorasMask': "Link (Majora's Mask)",
}
# How to get what the shop lists and chests don't cover. `place`: a location marker (MessageID) to show on the map.
HINTS = {
    'Armor_116_Upper': ("Impa in Kakariko Village gives it to you during the main quest Captured Memories.", 'Kakariko'),
    'Armor_006_Upper': ("A gift in Zora's Domain during the main quest Divine Beast Vah Ruta.", 'WhiteZora'),
    'Armor_115_Head': ("Taken back from the Yiga Clan Hideout in the main quest The Thunder Helm; Riju lets you "
                       "keep it once Divine Beast Vah Naboris is freed.", 'Assassin'),
    'Armor_053_Head': ("Vilia at Kara Kara Bazaar sells the whole set (you need it to get into Gerudo Town).", 'Oasis'),
    'Armor_053_Upper': ("Vilia at Kara Kara Bazaar sells the whole set (you need it to get into Gerudo Town).", 'Oasis'),
    'Armor_053_Lower': ("Vilia at Kara Kara Bazaar sells the whole set (you need it to get into Gerudo Town).", 'Oasis'),
    'Armor_005_Head': ("The chest appears at the Forgotten Temple once you've completed every shrine.", None),
    'Armor_005_Upper': ("The chest appears at the Forgotten Temple once you've completed every shrine.", None),
    'Armor_005_Lower': ("The chest appears at the Forgotten Temple once you've completed every shrine.", None),
}
DLC1 = "The Master Trials (DLC pack 1): its EX quest in the Adventure Log leads to the chest."
DLC2_HELM = "The Champions' Ballad (DLC pack 2): reward for that Divine Beast's Champion song quest."


def clean(s):
    """Game text without control and private-use characters (icons, ruby tags)."""
    return ''.join(c for c in (s or '') if unicodedata.category(c) not in ('Cc', 'Co', 'Cs')).strip()


def p3(t):
    t = t if isinstance(t, (list, tuple)) else [t['X'], t['Y'], t['Z']]
    return [round(t[0], 1), round(t[1], 1), round(t[2], 1)]


def effect_label(kind):
    if not kind or kind == 'None':
        return ''
    parts = re.split(r'And(?=[A-Z])', kind)
    return ' · '.join(EFFECTS.get(p, p) for p in parts)


def write_icons(rf, pieces, out_dir, log):
    """The inventory picture of each piece (UI/StockItem/<actor>.sbitemico: BFRES with one RGBA texture; dyed
    colours are separate <actor>.NN files and left out) as <piece id>.webp. Sets piece['icon'] when written."""
    import io
    from PIL import Image
    import maptiles
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in pieces:
        for actor in dict.fromkeys([p.get('iconActor'), p['id']]):
            rel = f'UI/StockItem/{actor}.sbitemico' if actor else None
            if not rel or not rf.exists(rel):
                continue
            try:
                data = rf.read(rel)
                bntx = data[data.find(b'BNTX'):]
                w, h, bgra, _ = maptiles.decode_texture(bntx, maptiles.textures(bntx)[0][1])
                img = Image.frombytes('RGBA', (w, h), bgra, 'raw', 'BGRA')
                img.thumbnail((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, 'WEBP', quality=88, method=6)
                (out_dir / f"{p['id']}.webp").write_bytes(buf.getvalue())
                p['icon'] = True
                n += 1
                break
            except Exception as e:   # the list still works without the picture
                log(f'outfits: no icon for {actor}: {type(e).__name__}: {e}')
    log(f'outfits: {n} icons -> {out_dir}')


def shop_lists(rf, log):
    """{shop actor: [(item, look_get_flag, adjust_price), ...]} from every NPC's .bshop."""
    names = {crc(s): s for s in ('Header', 'TableNum', 'ColumnNum')}
    out = {}
    packs = sorted(rf.base.glob('Actor/Pack/Npc_*.sbactorpack')) + sorted(rf.base.glob('Actor/Pack/NPC_*.sbactorpack'))
    for a in rf.aoc:
        packs += sorted((a / 'Actor' / 'Pack').glob('Npc_*.sbactorpack'))
    seen = set()
    for p in packs:
        actor = p.name[:-len('.sbactorpack')]
        if actor.lower() in seen:
            continue
        seen.add(actor.lower())
        try:
            files = gamefiles.sarc(p.read_bytes())
        except Exception:
            continue
        shop = next((d for n, d in files.items() if n.endswith('.bshop')), None)
        if shop is None:
            continue
        objs = gamefiles.aamp(shop)
        header = objs.get(crc('Header'), {})
        items = []
        for t in range(1, (header.get(crc('TableNum')) or 0) + 1):
            table = header.get(crc(f'Table{t:02d}'))
            o = objs.get(crc(table or ''), {})
            for i in range(1, (o.get(crc('ColumnNum')) or 0) + 1):
                item = o.get(crc(f'ItemName{i:03d}'))
                if isinstance(item, str) and item.startswith('Armor_'):
                    items.append((item, bool(o.get(crc(f'ItemLookGetFlg{i:03d}'))), o.get(crc(f'ItemAdjustPrice{i:03d}')) or 0))
        if items:
            out[actor] = items
    log(f'outfits: {len(out)} shops sell armor')
    return out


def shrine_chests(rf):
    """[(shrine id DungeonNNN, armor actor)] of the chests inside shrines (DLC shrines included)."""
    packs = {f'Pack/{p.name}' for p in rf.base.glob('Pack/Dungeon*.pack')}
    for a in rf.aoc:
        packs |= {f'Pack/{p.name}' for p in (a / 'Pack').glob('Dungeon*.pack')}
    out = []
    for rel in sorted(packs):
        did = rel.rsplit('/', 1)[1][:-len('.pack')]
        for n, data in rf.pack(rel).items():
            if n.endswith(('_Static.smubin', '_Dynamic.smubin')):
                for o in gamefiles.byml(data).get('Objs', []):
                    drop = (o.get('!Parameters') or {}).get('DropActor') or ''
                    if drop.startswith('Armor_'):
                        out.append((did, drop))
    return out


def build(extracted, out=OUT, lang='USen', log=print, objs=None):
    rf = gamefiles.Romfs(extracted)
    raw, lang = messages(rf, lang)
    text = Text(raw)
    names = text.actors()
    places = text('StaticMsg/LocationMarker')
    dungeons = text('StaticMsg/Dungeon')
    flags = game_flags(rf)
    info = {a['name']: a for a in gamefiles.byml(rf.read('Actor/ActorInfo.product.sbyml'))['Actors']}
    armor = {n: a for n, a in info.items() if a.get('profile') in SLOTS}
    if objs is None:
        objs = placed_actors(rf, log=lambda m: None)

    def name(actor):
        return clean(names.get(f'{actor}_Name')) or ''

    # ---- places: the location markers, to say where a shop or chest is and to show it on the map
    static = static_doc(rf)
    markers, shrine_pos = [], {}
    for m in static.get('LocationMarker', []) + static.get('LocationPointer', []):
        mid = m.get('MessageID')
        if not mid or 'Translate' not in m:
            continue
        pt = p3(m['Translate'])
        if m.get('Icon') == 'Dungeon':
            shrine_pos.setdefault(mid, pt)
        elif places.get(mid):
            markers.append((mid, places[mid], pt, m.get('Icon') in TOWN_ICONS))
    marker_pos = {mid: pt for mid, _, pt, _ in markers}

    def place(pt):
        d2 = lambda m: (m[2][0] - pt[0]) ** 2 + (m[2][2] - pt[2]) ** 2
        towns = [m for m in markers if m[3] and d2(m) < 350 ** 2]
        best = min(towns or markers, key=d2, default=None)
        return best[1] if best else ''

    # ---- pieces and their upgrade chains
    upgraded = {a['armorNextRankName'] for a in armor.values() if a.get('armorNextRankName')}
    pieces, by_actor, seen_names = [], {}, set()
    for base in sorted(armor, key=lambda n: armor[n].get('sortKey', 0)):
        a = armor[base]
        nm = name(base)
        if base in upgraded or f'IsGet_{base}' not in flags or not nm or nm in seen_names:
            continue   # an upgrade, a hood-down variant, an unused test piece, or a second copy of a name
        seen_names.add(nm)
        chain = [base]
        while armor.get(chain[-1], {}).get('armorNextRankName') in armor and len(chain) < 6:
            chain.append(armor[chain[-1]]['armorNextRankName'])
        levels = []
        for i, act in enumerate(chain):
            mats = []
            if i:
                b = armor[act]
                for j in range(1, 6):
                    item = b.get(f'normal0ItemName{j:02d}')
                    if item and not item.startswith('Armor_'):
                        mats.append([item, name(item) or item, b.get(f'normal0ItemNum{j:02d}', 1)])
            levels.append({'actor': act, 'flag': crc(f'IsGet_{act}') if f'IsGet_{act}' in flags else None,
                           'mats': mats})
        series = a.get('seriesArmorSeriesType') or ''
        set_name = 'Divine Helms' if base in DIVINE_HELMS else SETS.get(series) or nm
        piece = {
            'id': base, 'name': nm, 'slot': SLOTS[a['profile']], 'set': set_name, 'series': series,
            'sort': a.get('sortKey', 0), 'effect': effect_label(a.get('armorEffectEffectType')),
            'bonus': bool(a.get('seriesArmorEnableCompBonus')),
            'price': a['itemBuyingPrice'] if a.get('itemBuyingPrice', -1) > 0 else None,
            'levels': levels, 'sources': [], 'iconActor': a.get('itemUseIconActorName') or base,
        }
        pieces.append(piece)
        for act in chain:
            by_actor[act] = piece

    def add(actor, src):
        p = by_actor.get(actor)
        if p is None:
            return
        key = (src['type'], src.get('who'), src.get('place'), tuple(src.get('points', [[]])[0]))
        if all((s['type'], s.get('who'), s.get('place'), tuple(s.get('points', [[]])[0])) != key for s in p['sources']):
            p['sources'].append(src)

    # ---- shops: shopkeepers' lists and the mannequins on display
    npc_at = {}
    for o in objs:
        u = o['UnitConfigName']
        if u.lower().startswith('npc_') and 'Translate' in o:
            npc_at.setdefault(u.lower(), []).append(p3(o['Translate']))

    def shop(actor, item, rebuy=False, adjust=0, points=None):
        # a second copy of a shopkeeper for later in the game (Npc_oasis050_2: Rhondson in Tarrey Town) has no
        # name of its own; copies without any name are left out
        who = name(actor) or name(re.sub(r'_\d+$', '', actor))
        if not who:
            return
        # (Kilton's shop moves around: all of its spots)
        pts = points or npc_at.get(actor.lower(), [])[:8]
        price = (armor.get(item, {}).get('itemBuyingPrice') or 0) + (adjust or 0)
        where = list(dict.fromkeys(place(p) for p in pts))
        add(item, {'type': 'shop', 'who': who, 'place': ' · '.join(w for w in where if w), 'points': pts,
                   'price': price if price > 0 else None, 'rebuy': rebuy})

    for actor, items in shop_lists(rf, log).items():
        if actor in FAIRY_SHOPS:
            continue
        for item, look_get, adjust in items:
            # ItemLookGetFlg: on sale only once you've found it yourself (Rhondson, Grant, Natie, ...)
            shop(actor, item, rebuy=look_get, adjust=adjust)
    by_hash = {o.get('HashId'): o for o in objs}
    for o in objs:
        if not o['UnitConfigName'].startswith('Mannequin_'):
            continue
        item = (o.get('!Parameters') or {}).get('EquipArmorName1')
        keeper = next((by_hash.get(l.get('DestUnitHashId')) for l in o.get('LinksToObj') or []
                       if l.get('DefinitionName') == 'ForSale'), None)
        if item and keeper:
            shop(keeper['UnitConfigName'], item, points=npc_at.get(keeper['UnitConfigName'].lower(), [])[:3] or [p3(o['Translate'])])

    # ---- treasure chests: on the map (DLC ones included) and inside shrines
    for o in objs:
        drop = (o.get('!Parameters') or {}).get('DropActor') or ''
        if o['UnitConfigName'].startswith('TBox_') and drop.startswith('Armor_') and 'Translate' in o:
            pt = p3(o['Translate'])
            add(drop, {'type': 'chest', 'place': place(pt), 'points': [pt]})
    for did, drop in shrine_chests(rf):
        pt = shrine_pos.get(did)
        add(drop, {'type': 'shrine', 'place': clean(dungeons.get(did)) or did, 'points': [pt] if pt else []})

    # ---- what the files don't tell
    for p in pieces:
        hint = HINTS.get(p['id'])
        if hint:
            pt = marker_pos.get(hint[1]) if hint[1] else None
            p['sources'].append({'type': 'hint', 'text': hint[0], 'place': places.get(hint[1], '') if hint[1] else '',
                                 'points': [pt] if pt else []})
        elif p['series'] in AMIIBO:
            p['sources'].append({'type': 'amiibo', 'text': AMIIBO[p['series']]})
        elif p['id'] in DIVINE_HELMS:
            p['sources'].append({'type': 'hint', 'text': DLC2_HELM})
        elif re.match(r'Armor_1(7\d|80|85)_', p['id']) and any(s['type'] == 'chest' for s in p['sources']):
            p['sources'].append({'type': 'hint', 'text': DLC1})
        # first-time sources first; shops that only restock what you've found go last
        p['sources'].sort(key=lambda s: ({'hint': 0, 'amiibo': 0, 'chest': 1, 'shrine': 1, 'shop': 2}[s['type']]
                                         + (2 if s.get('rebuy') else 0)))

    write_icons(rf, pieces, Path(out).parent / 'outfiticons', log)
    for p in pieces:
        p.pop('iconActor', None)
    sets = {}
    for p in pieces:
        sets.setdefault(p['set'], []).append(p)
    data = {'lang': lang, 'built': int(time.time()), 'sets': [{'name': k, 'pieces': v} for k, v in sorted(sets.items(), key=lambda kv: kv[1][0]['sort'])]}
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix('.part')
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    tmp.replace(out)
    nosrc = [p['name'] for p in pieces if not p['sources']]
    log(f'outfits: {len(pieces)} pieces in {len(sets)} sets; no source for {len(nosrc)}: {", ".join(nosrc)}')
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--extracted', required=True)
    ap.add_argument('--out', default=str(OUT))
    ap.add_argument('--lang', default='USen')
    args = ap.parse_args()
    if not (Path(args.extracted) / 'romfs').is_dir():
        sys.exit('Not a Savage Wind extracted folder (no romfs inside).')
    build(args.extracted, args.out, args.lang)


if __name__ == '__main__':
    main()
