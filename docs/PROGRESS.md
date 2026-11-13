# Progress log

Where the project stands, what was learned along the way, and what's next. Update it with each stage.

## Status (2026-11-13)

| | |
|---|---|
| Version | 0.3.1: map tiles sent as `image/webp` (download managers such as IDM no longer grab them). Built by GitHub Actions on the `v0.3.1` tag |
| 0.3.1 tested | Local exe: tiles, outfit pictures, JS and HTML come back with the right Content-Type |
| 0.3.0 | Outfits tab (every armor piece, its picture, upgrades and where to get it) |
| 0.3.0 tested | From source in the browser and a local exe against the owner's save, and the outfit reader against the public 100% save (83/107 pieces, 46 fully upgraded) |
| 0.2.0 | Live position (Savage Wind's control port), ▶ Play, follow Link |
| 0.2.0 tested | Against the real game (`pos` from title screen to walking, Play from the map, a second start refused, the map closed with the game still running) and a stand-in TCP server; Windows only |
| Tested on | Savage Wind (game 1.9.0 + DLC, English) with saves in `F:\Games\SavageWind`: from source in the browser, and the exe (app window, setup, save) from a Desktop shortcut |
| Also in 0.1.0 | Spoiler-free mode and new-version notice, ported from totk-progress `cb4377f`; README screenshots taken with headless Edge from the running app |
| Started from | [totk-progress](https://github.com/phatnguyen-v1/totk-progress) v1.6.0 (server, page, setup, phone view) |

### Next up
1. Check more saves (later in the game: Divine Beasts, Great Fairies, bosses, quest steps with branches).
2. Try the Linux binary on a real desktop / Steam Deck (CI only runs a start-up smoke test).
3. Hyrule Compendium tab (385 entries, works like the outfits). Korok puzzle types (the hidden Korok links to its puzzle through `LinkTagAnd` / `LinkTagOr` actors), memories
   (`IsGet_MemoryPhoto_NNN`, positions not found yet), Hestu.
4. Interface translations (`web/i18n/<code>.json`; only English so far, game text is already in every language).

## v0.3.1 (2026-11-13): map tiles as image/webp

- `send_file` takes the type from a fixed table (`CONTENT_TYPES`) for the files the page loads, then
  `mimetypes`: on Windows that asks the registry, which often has no `.webp` (Python itself only knows it from
  3.13 on), so tiles went out as `application/octet-stream` and IDM took them for downloads. Same fix as
  totk-progress PR #4 (eUnisk).

## v0.3.0 (2026-11-12): Outfits tab

- `build_outfits.py` -> `assets/outfits.json` (`DATA_VERSION` 3 rebuilds it on existing installs, ~15 s with the
  shop scan). Armor = `Actor/ActorInfo.product.sbyml` actors with profile `ArmorHead/Upper/Lower`; each Great Fairy
  level is its own actor (`armorNextRankName`), with the materials for that level in `normal0ItemName0N/Num0N`.
  107 pieces once upgrades, hood-down `_B` variants, the unnamed `Armor_5xx` test pieces and the second Snow Boots
  (`Armor_140_Lower`) are left out. Sets: `seriesArmorSeriesType` (names hand-written; the game has none).
- Pictures: `UI/StockItem/<actor>.sbitemico` (Yaz0 BFRES, one 144 px R8G8B8A8 sRGB texture, format 0x0B06, now
  decoded by `maptiles.decode_texture`; dyed colours are `<actor>.NN`, left out) -> `assets/outfiticons/<id>.webp`
  at 96 px, 700 KB for all 107. `DATA_VERSION` 4.
- Save: `IsGet_<actor>` per level (kept after selling), inventory `PorchItem` (64-byte strings) with counts in
  `PorchItem_Value1`.
- Where to get it: the `.bshop` shop lists (AAMP, new reader `gamefiles.aamp`; `ItemLookGetFlg` = sold only once
  found, e.g. Rhondson, Granté, Natie), armor shop mannequins (`Mannequin_*` `EquipArmorName1`, `ForSale` link to the
  shopkeeper), `TBox_*` chests on the map and in `Pack/DungeonNNN.pack` shrines. `Npc_DressFairy_0N` shop = ★N+1.
  Quest gifts, amiibo and DLC hints in `build_outfits.HINTS`. No source found for Zora Greaves and Sheik's Mask.
- Checked against the owner's slot 5: 5 pieces found (= the armor in the inventory), material counts as in game.
  Public 100% save, slot 0: 83 of 107 found, 46 of 71 fully upgraded; missing only the amiibo sets, the Divine
  Helms, the Lynel Mask and Sheik's Mask (none needed for 100%). Slot 6 (26 h): 19.

## v0.2.0 (2026-11-12): live position

- `server.LiveGame`: Savage Wind started with `SAVAGEWIND_CONTROL=<port>` answers text commands on 127.0.0.1, one JSON
  line each; a connection that starts with an HTTP request is closed. Only `pos` is ever sent (the port also takes
  developer commands). 4 Hz while a page polls `/api/live`, stops 6 s after the last one; 1.5 s timeout; backoff 1 to
  10 s. A reply counts only with a `swr:` place line, and only the exact error `No world loaded` means no save yet:
  TotK's recomp speaks the same protocol on the same default port.
- `pos` gives Link's x/y/z (same frame as the save position: live == SavePos right after loading) and, in its
  `place` line, the camera's yaw (0 along +z, 90 along +x). heading = 180 - yaw, clockwise from map north.
- `server.Game`: ▶ Play starts the game with the variable in its environment only. The file is savagewind.exe in the
  game folder or one picked in the file window (exe, .cmd/.bat, AppImage); the page never sends a path. Refused while
  a Savage Wind runs; started detached.
- Page: marker moved in place, glide off while the map moves; Link button follows; game chip (Play / Starting / Load
  a save / Live).

## v0.1.0 (2026-11-11): first version

### Game files (`gamefiles.py`)
- Savage Wind keeps the romfs extracted as plain files: `<data>/extracted/romfs`, DLC in `extracted/aoc1` (aoc2
  only has `System`). `Romfs` prefers the DLC copy of a path.
- `.s*` files are Yaz0 (pure Python decoder; the map tiles take ~13 s in all). `.pack` / `.ssarc` / `.sblarc` are
  SARC, little endian. BYML v2+ little endian. MSBT as in TotK.
- `Pack/Bootup.pack`: `GameData/gamedata.ssarc` (45,027 flag names), `Map/MainField/Static.smubin`,
  `Layout/Common.sblarc`. `Pack/TitleBG.pack`: the 80 `_Static` map sections, `Quest/QuestProduct.sbquestpack`.
  `Pack/Bootup_<lang>.pack` -> `Message/Msg_<lang>.product.ssarc`: `StaticMsg/LocationMarker`, `StaticMsg/Dungeon`
  (shrine subtitles `DungeonNNN_sub`), `ActorType/*` (`<actor>_Name`), `QuestMsg/QL_<quest>` (`_Name`, step texts).
- DLC: `aoc1/Map/MainField/Static.smubin` (adds the 16 DLC shrines), loose `_Static` / `_Dynamic` sections and
  `aoc1/Pack/AocMainField.pack`.

### Map tiles (`maptiles.py`)
- `UI/MapTex/MainField/MapTex{,1,2,3}_<C>-<R>[_<n>].sbmaptex`: columns `Z, A-K` (west to east), rows `0-9`
  (north to south), 1000 world units each, same grid as TotK. 3000 / 840 / 300 / 135 px.
- No suffix = fully revealed map; `_0` = unexplored blueprint; other numbers = partly revealed (tower region bits).
- Yaz0 -> BFRES with an embedded BNTX; BC1 sRGB, block linear. The BNTX isn't relocated: mip pointers are 0 and
  relative to the `BRTD` data (`BRTD + 0x10`); reading from 0 shifted every tile by a few GOB columns.

### Save (`savefile.py`)
- `game_data.sav`: u32 version (0x4730 on 1.9.0), 0xFFFFFFFF, u32 1, then (u32 CRC32(name), u32 value) pairs
  sorted by hash. Vectors / strings / arrays: several pairs with the same hash.
- Link: `PlayerSavePos` (3 floats), only when `PlayerSavePosMapType` is `MainField`. Play time:
  `PlayReport_PlayTime` (seconds).
- Saves: `<data>/save/save_0/<slot>/game_data.sav` (slots 0-5, newest file wins).

### Quests (`build_quests.py`)
- 170 quests in the pack, 152 with an adventure log name. `Type`: `Main` (20), `Mini` = side quests (90, with the
  DLC's EX quests), `Sub` = shrine quests (42).
- Each step's `NextFlag` is set when the game reaches the step (the first one is `<quest>_Activated`). The log shows
  the text of the last step reached (`QL_<quest>_<MessageName>`); the map target is the next step's
  `IndicatorActors`. Done = the last saved step flag set. Not started = `<quest>_Ready`; otherwise not available.

### Checked against the owner's save (slot 2, 3 h 22 min)
5 Koroks (= `HiddenKorok_Number`), 4 shrines (the Great Plateau's), Great Plateau tower, 2 main quests done and
"Seek Out Impa" in progress with its target at Impa's house.

### Checked against a public 100% save (2026-11-11)
[Torphedo/BOTW-CompletedSaveFile](https://github.com/Torphedo/BOTW-CompletedSaveFile) release 1.0, `Switch-100.zip`
(game 1.6 format, version 0x471b; kept out of the repo). Slots 0-5, 362 h: Koroks 900/900 (= `HiddenKorok_Number`),
shrines 136/136, towers 15/15, Divine Beasts 4/4, locations 164/164, Great Fairies 4/4, Hinoxes 40/40, Taluses 40/40,
Moldugas 4/4, quests 151 done with only "Destroy Ganon" open (the game reloads to before the final fight).
- Fixed: the Champions' Ballad's Igneo Talus Titan (`Enemy_Golem_Fire_R`) and Molduking (`Enemy_SandwormR`) have
  an `IsDefeat` flag that stays 0 even at 100%, so they're left out (the medal counts are 40 Taluses, 4 Moldugas).
- Chests: 372 of 1,281 opened, armor chests 30/31 (the Great Plateau's Warm Doublet chest wasn't opened). 100%
  doesn't need chests; every chest flag is a never-reset one (`ResetType` 0), so this is the player's real count.
- Slots 6-7 (an earlier run, 26 h): sensible current steps for 39 active quests, the DLC's EX quests all open at
  once as in the game.
