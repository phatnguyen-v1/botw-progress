'use strict';

// ---------- the app's own words ----------
// In the same language as the game text (Settings -> Language; follows Savage Wind by default). The server
// merges web/i18n/<code>.json over English (USen.json), the source and the fallback for anything missing.
const I18N = (() => {
  try {
    const x = new XMLHttpRequest();
    x.open('GET', '/api/i18n', false);
    x.send();
    if (x.status === 200) return JSON.parse(x.responseText);
  } catch {}
  return { lang: 'USen', locale: 'en-US', strings: {} };
})();
const LOCALE = I18N.locale || 'en-US';
const STR = I18N.strings || {};
const fill = (s, vars) => (vars ? s.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? vars[k] : m)) : s);
// t('key', {n: 3}): the text for `key` with {n} filled in. Values may hold markup (only from our own files);
// callers escape what they fill in when the result goes into innerHTML.
const t = (key, vars) => fill(typeof STR[key] === 'string' ? STR[key] : key, vars);
// tn('badge.slots', 2): the plural form for that number (`key.one`, `key.few`, ... `key.other`)
const plural = new Intl.PluralRules(LOCALE);
const tn = (key, n, vars) => {
  const k = `${key}.${plural.select(n)}`;
  return t(k in STR ? k : `${key}.other`, { n, ...vars });
};
// Messages the server sends in English (setup notes, progress, errors): looked up by their English text.
const SERVER_PATTERNS = [
  [/^Drawing the (\w+) map… (\d+)\/(\d+)$/, m => t('msg.drawing', { layer: t('layer.' + m[1]), i: m[2], n: m[3] })],
  [/^Quest list not built: (.*)$/s, m => t('msg.noQuests', { error: m[1] })],
  [/^Could not open port (\d+) on ([^:]+): (.*)$/s, m => t('msg.port', { port: m[1], ip: m[2], error: m[3] })],
  [/^RuntimeError: (.*)$/s, m => tMsg(m[1])],
  [/^map data: /, () => tMsg('Finding shrines, Koroks and everything else in your game files…')],
  [/^outfits: /, () => tMsg('Finding every outfit and where to get it…')],
  [/^could not read slot (\d+): (.*)$/s, m => t('msg.slot', { n: m[1], error: m[2] })],
];
function tMsg(s) {
  if (!s) return s;
  const table = STR.server || {};
  if (typeof table[s] === 'string') return table[s];
  for (const [rx, f] of SERVER_PATTERNS) { const m = rx.exec(s); if (m) return f(m); }
  return s;
}
// Labels the map data has in English (Korok hints, fallback names): see build_mapdata.py.
function tData(s) {
  if (!s) return s;
  const table = STR.data || {};
  return typeof table[s] === 'string' ? table[s] : s;
}
function applyI18n() {
  document.documentElement.lang = LOCALE;
  document.querySelectorAll('[data-i18n]').forEach(el => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll('[data-i18n-html]').forEach(el => { el.innerHTML = t(el.dataset.i18nHtml); });
  for (const a of ['title', 'placeholder', 'aria-label']) {
    document.querySelectorAll(`[data-i18n-${a}]`).forEach(el => el.setAttribute(a, t(el.getAttribute(`data-i18n-${a}`))));
  }
}
applyI18n();
const layerName = l => t('layer.' + l);

// key must match savefile.CATEGORIES; data = map_data.json key. `on`: shown the first time (all ~2,500
// markers at once are slow); the rest is a click away in the legend, or "Show all". `phone`: the even smaller
// first set on phones and tablets opening the map over the network.
const CATS = [
  { key: 'korok', data: 'koroks', label: t('cat.korok'), icon: 'korok', group: t('group.collectibles'), size: 20, on: true },
  { key: 'shrine', data: 'shrines', label: t('cat.shrine'), icon: 'shrine', group: t('group.collectibles'), on: true, phone: true },
  { key: 'tower', data: 'towers', label: t('cat.tower'), icon: 'tower', group: t('group.places'), on: true, phone: true },
  { key: 'divine_beast', data: 'divine_beasts', label: t('cat.divine_beast'), icon: 'divine_beast', group: t('group.places'), on: true, phone: true },
  { key: 'great_fairy', data: 'great_fairies', label: t('cat.great_fairy'), icon: 'great_fairy', group: t('group.places'), on: true },
  { key: 'location', data: 'locations', label: t('cat.location'), icon: 'location', group: t('group.places') },
  { key: 'hinox', data: 'hinoxes', label: t('cat.hinox'), icon: 'hinox', group: t('group.bosses') },
  { key: 'talus', data: 'taluses', label: t('cat.talus'), icon: 'talus', group: t('group.bosses') },
  { key: 'molduga', data: 'moldugas', label: t('cat.molduga'), icon: 'molduga', group: t('group.bosses') },
  // Lynels come back after every Blood Moon, so the game keeps no lasting record: where they are, not tracked
  { key: 'lynel', data: 'lynels', label: t('cat.lynel'), icon: 'lynel', group: t('group.bosses'), track: false },
  { key: 'armor_chest', data: 'armor_chests', label: t('cat.armor_chest'), icon: 'armor_chest', group: t('group.chests'), on: true },
  // ~1,300 chests: off unless asked for
  { key: 'treasure_chest', data: 'treasure_chests', label: t('cat.treasure_chest'), icon: 'treasure_chest', group: t('group.chests'), size: 20 },
];
const SIZE = 1500;
const POLL_MS = 4000;

// View settings (legend, filters, layer, tab, ...) are kept by the app in config.json (/api/prefs), so they
// survive restarts and a port change, and are the same in the app window and the browser. Read once,
// synchronously, before the page sets itself up; localStorage is only a fallback.
// Opened from another device on the network (Settings -> Open on your phone): a read-only view.
const REMOTE = !['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);
const PREFS = REMOTE ? {} : (() => {
  try {
    const x = new XMLHttpRequest();
    x.open('GET', '/api/prefs', false);
    x.send();
    return x.status === 200 ? JSON.parse(x.responseText) || {} : {};
  } catch { return {}; }
})();
let prefsTimer = null, prefsPending = {};
// A phone keeps its own settings in its browser, under their own names (never mixed with the PC's).
const LS = REMOTE ? 'botwp.phone.' : 'botwp.';
const store = {
  get(k, d) {
    if (k in PREFS) return PREFS[k];
    try { const v = localStorage.getItem(LS + k); return v == null ? d : JSON.parse(v); } catch { return d; }
  },
  set(k, v) {
    PREFS[k] = v;
    try { localStorage.setItem(LS + k, JSON.stringify(v)); } catch {}
    if (REMOTE) return;   // a phone keeps its own view settings
    prefsPending[k] = v;
    clearTimeout(prefsTimer);
    prefsTimer = setTimeout(() => {
      const body = JSON.stringify(prefsPending);
      prefsPending = {};
      fetch('/api/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-BotW-Progress': '1' }, body, keepalive: true })
        .catch(() => {});
    }, 300);
  },
  flush() {   // closing the window right after a click: send what's still waiting
    if (!Object.keys(prefsPending).length) return;
    clearTimeout(prefsTimer);
    const body = JSON.stringify(prefsPending);
    prefsPending = {};
    fetch('/api/prefs', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-BotW-Progress': '1' }, body, keepalive: true })
      .catch(() => {});
  },
};
addEventListener('pagehide', () => store.flush());

const state = {
  data: null,
  progress: null,
  slot: store.get('slot', 'latest'),
  layer: 'surface',
  mode: store.get('mode', 'missing'),
  off: new Set(store.get('off', CATS.filter(c => !(REMOTE ? c.phone : c.on)).map(c => c.key))),
  known: new Set(store.get('known', CATS.filter(c => !c.added).map(c => c.key))),
  search: '',
  lastMtime: null,
  tab: store.get('tab', 'map'),
  qmode: store.get('qmode', 'active'),
  qtargets: store.get('qtargets', true),
  qstarts: store.get('qstarts', false),
  // Spoiler-free: everything not found yet is a plain "?" (no type, name or Korok route), see render()
  mystery: store.get('mystery', false),
  quests: null,      // definitions from /data/quests.json
  openQuest: null,
  omode: store.get('omode', 'missing'),
  outfits: null,     // definitions from /data/outfits.json
  openOutfit: null,
};
// Layers added after the legend was saved start the way a new user would see them (most are on, the
// ~1,200 treasure chests are off), instead of all showing because they aren't in the saved "off" list.
if (CATS.some(c => !state.known.has(c.key))) {
  for (const c of CATS) if (!state.known.has(c.key) && !(REMOTE ? c.phone : c.on)) state.off.add(c.key);
  store.set('off', [...state.off]);
  store.set('known', CATS.map(c => c.key));
}
const QCATS = ['main', 'side_quest', 'shrine'].map(k => [k, t('qcat.' + k)]);
const QSTATUS = Object.fromEntries(['active', 'available', 'queued', 'locked', 'done'].map(k => [k, t('qs.' + k)]));

const $ = s => document.querySelector(s);
const layerOf = () => 'surface';   // BotW's Hyrule is one map (shrine interiors aren't on it)
const toLatLng = p => [-(SIZE / 2 + p.z / 8), SIZE / 2 + p.x / 8];
const fmtTime = s => { const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60); return h ? t('time.hm', { h, m }) : t('time.m', { m }); };
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

// ---------- map ----------
const map = L.map('map', { crs: L.CRS.Simple, minZoom: -1.5, maxZoom: 3, zoomSnap: 0.25, attributionControl: false, zoomControl: true });
const bounds = [[-SIZE, 0], [0, SIZE]];
const overlays = {};
// Map built from the game's own map tiles: 12 x 10 tiles of 1000 world units = 125 units in this 1500 px space,
// rows start 125 px down (z -5000). Two detail levels: 300 px tiles zoomed out, 840 px tiles zoomed in.
const GameTiles = L.TileLayer.extend({
  // ?v=: a new URL after the tiles are redrawn, so the browser doesn't keep showing cached old tiles
  getTileUrl(c) { return `/assets/tiles/${this.options.layerName}/${this.options.lod}/${c.x}-${c.y - 1}.webp?v=${state.tileStamp}`; },
});
// The 840 px tiles are added on top from this zoom on. Switched here, not with the layers' minZoom/maxZoom:
// Leaflet rounds the zoom before checking those, so with quarter-step zooms both layers could be off at
// once (e.g. 1.25 rounds to 1) and the map went black in places. The 300 px layer always stays underneath,
// so there's no gap while detailed tiles load.
const DETAIL_ZOOM = 1.25;
function gameTileLayer(name) {
  const common = { layerName: name, tileSize: 125, minNativeZoom: 0, maxNativeZoom: 0, minZoom: -10, maxZoom: 10,
    noWrap: true, bounds: L.latLngBounds([[-1375, 0], [-125, 1500]]), keepBuffer: 4 };
  const far = new GameTiles('', { ...common, lod: '02', zIndex: 1 });
  const near = new GameTiles('', { ...common, lod: '01', zIndex: 2 });
  const group = L.layerGroup([far]);
  group.syncDetail = () => {
    const want = map.getZoom() >= DETAIL_ZOOM;
    if (want && !group.hasLayer(near)) group.addLayer(near);
    else if (!want && group.hasLayer(near)) group.removeLayer(near);
  };
  return group;
}
map.on('zoomend', () => { const o = overlays[state.layer]; if (o && o.syncDetail) o.syncDetail(); });
async function setupBaseMaps() {
  let meta = null;
  try { meta = await getJSON('/assets/tiles/meta.json'); } catch {}
  state.tileStamp = meta ? (meta.built || meta.version || 1) : 1;
  // tiles drawn from the player's game files at setup; without them the map stays blank but markers still work
  for (const l of ['surface']) overlays[l] = gameTileLayer(l);
  state.mapSource = meta ? 'game' : 'none';
  // Icons taken from the player's own game files (build_icons.py); the app's own SVGs otherwise.
  try {
    const m = await getJSON('/assets/gameicons/meta.json');
    state.gameIcons = new Set(m.icons);
    state.iconStamp = m.built || 0;   // new URL after a rebuild, so the browser doesn't keep old cached icons
  } catch {}
  document.querySelectorAll('#layers img[data-icon]').forEach(img => { img.src = iconSrc(img.dataset.icon); });
}
const iconSrc = name => (state.gameIcons && state.gameIcons.has(name)
  ? `/assets/gameicons/${name}.png?v=${state.iconStamp}` : `/icons/${name}.svg`);
map.fitBounds(bounds);
map.setMaxBounds(L.latLngBounds(bounds).pad(0.2));
const markerLayer = L.layerGroup().addTo(map);
const questLayer = L.layerGroup().addTo(map);
const scaleIcons = () => {
  const z = map.getZoom(); // -1.5 (whole map) .. 3
  const s = z <= -1 ? 0.5 : z <= 0 ? 0.65 : z <= 1 ? 0.85 : z <= 2 ? 1 : 1.15;
  map.getContainer().style.setProperty('--s', s);
};
map.on('zoomend', scaleIcons);
scaleIcons();
map.on('zoomend', () => { const r = unkRadius(); markerLayer.eachLayer(m => m instanceof UnkMarker && m.setRadius(r)); });
const pathLayer = L.layerGroup().addTo(map);
const iconCache = {};
// In spoiler-free mode all ? markers are shown or hidden together, under this key in state.off: one filter per
// category would tell which ? is a Korok and which a chest. The category filters then only apply to what's found.
const UNK = 'undiscovered';
// Drawn on one canvas, not as ~2,600 HTML markers (those made zooming drop to ~5 fps on the surface).
const unkRenderer = L.canvas({ padding: 0.3 });
const unkRadius = () => 10 * (parseFloat(map.getContainer().style.getPropertyValue('--s')) || 1);
const UnkMarker = L.CircleMarker.extend({
  _updatePath() {
    const r = this._renderer;
    r._updateCircle(this);
    if (!r._drawing || this._empty()) return;
    const ctx = r._ctx, p = this._point;
    ctx.fillStyle = '#f1ead6';
    ctx.font = `800 ${Math.round(this._radius * 1.25)}px system-ui, sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('?', p.x, p.y + 1);
  },
});
const unkMarker = latlng => new UnkMarker(latlng, { renderer: unkRenderer, radius: unkRadius(), color: '#c9c2ae', weight: 1.5,
  fillColor: '#3a3f46', fillOpacity: 1 });
const unkPopup = o => `<div class="pop"><h3>${esc(t('mystery.label'))}</h3>
  <div class="row">${esc(t('mystery.pop'))}</div>
  <div class="row">x ${Math.round(o.position.x)} · y ${Math.round(o.position.y)} · z ${Math.round(o.position.z)}</div></div>`;

function icon(cat, done, found) {
  const k = cat.key + (done ? ':d' : found ? ':f' : '');
  if (!iconCache[k]) {
    const s = cat.size || 24;
    iconCache[k] = L.divIcon({
      className: 'mk' + (done ? ' done' : found ? ' found' : ''),
      html: `<img src="${iconSrc(cat.icon)}" alt="">`,
      iconSize: [s, s], iconAnchor: [s / 2, s / 2], popupAnchor: [0, -s / 2],
    });
  }
  return iconCache[k];
}

// one of a kind, for names of markers without one of their own (Korok seed: Lift the rock, ...)
const SINGULAR = Object.fromEntries(['korok', 'hinox', 'talus', 'molduga', 'lynel', 'armor_chest', 'treasure_chest']
  .map(k => [k, t('one.' + k)]));
// what "done" means, per layer (default: Done / Not yet)
const DONE_WORDS = Object.fromEntries(['korok', 'tower', 'divine_beast', 'location', 'great_fairy', 'hinox', 'talus', 'molduga',
  'armor_chest', 'treasure_chest'].map(k => [k, [t(`done.${k}.yes`), t(`done.${k}.no`)]]));
function objName(cat, o) {
  if (o.drop) return SINGULAR[cat.key] || cat.label;   // chests: named by what's inside, in the popup
  if (o.display_name) return tData(o.display_name);
  const one = SINGULAR[cat.key] || cat.label;
  return o.type ? t('name.typed', { name: one, type: tData(o.type) }) : one;
}

function popupHtml(cat, o, done, found) {
  const p = o.position;
  const [yes, no] = DONE_WORDS[cat.key] || [t('done.yes'), t('done.no')];
  const status = cat.track === false
    ? `<div class="row">${esc(t('pop.lynel'))}</div>`
    : `<div class="row">${esc(t('pop.status'))} <span class="st ${done ? 'yes' : found ? 'found' : 'no'}">${esc(done ? yes : found ? t('pop.found') : no)}</span></div>`;
  const extra = o.drop ? `<div class="row">${esc(t('pop.contains', { item: o.display_name || o.drop }))}</div>`
    : o.subtitle ? `<div class="row"><i>${esc(o.subtitle)}</i></div>` : '';
  return `<div class="pop"><h3>${esc(objName(cat, o))}</h3>
    <div class="row">${esc(cat.label)}</div>${extra}
    ${status}
    <div class="row">x ${Math.round(p.x)} · y ${Math.round(p.y)} · z ${Math.round(p.z)}</div></div>`;
}

// ---------- render ----------
function render() {
  markerLayer.clearLayers();
  pathLayer.clearLayers();
  if (!state.data) return;
  const q = state.tab === 'map' ? state.search.trim().toLowerCase() : '';
  const done = state.progress ? state.progress.done : {};
  const foundAll = (state.progress && state.progress.found) || {};
  const unkOff = state.off.has(UNK);
  for (const cat of CATS) {
    const catOff = state.off.has(cat.key);
    if (catOff && (!state.mystery || unkOff)) continue;
    const list = state.data[cat.data] || [];
    const flags = done[cat.key] || [];
    const found = foundAll[cat.key] || [];
    list.forEach((o, i) => {
      const isDone = !!flags[i];
      const isFound = !isDone && !!found[i];
      const hidden = state.mystery && !isDone && !isFound;
      if (hidden ? unkOff : catOff) return;
      if (state.mode === 'missing' && isDone) return;
      if (state.mode === 'done' && !isDone) return;
      if (layerOf(o.position.y) !== state.layer) return;
      // a ? never matches a search: typing "Korok" would show which ones are Koroks
      if (q && (hidden || !`${objName(cat, o)} ${o.display_name || ''}`.toLowerCase().includes(q))) return;
      const m = hidden ? unkMarker(toLatLng(o.position))
        : L.marker(toLatLng(o.position), { icon: icon(cat, isDone, isFound), riseOnHover: true, keyboard: false });
      m.bindPopup(() => (hidden ? unkPopup(o) : popupHtml(cat, o, isDone, isFound)));
      markerLayer.addLayer(m);
      const path = o.path && (o.path.flowers ? o.path.flowers.map(f => f.position) : o.path.start && [o.path.start, o.path.end]);
      if (path && !isDone && !hidden) {
        pathLayer.addLayer(L.polyline(path.map(toLatLng), { color: cat.key === 'carry_korok' ? '#e3c27a' : '#6fd3c2', weight: 2, opacity: 0.8, dashArray: '4 4', interactive: false }));
        if (o.path.flowers) for (const f of path) pathLayer.addLayer(L.circleMarker(toLatLng(f), { radius: 2.5, color: '#f5a3d0', weight: 1, fillOpacity: 1, interactive: false }));
      }
    });
  }
  renderPlayer();
}

// Link's position: live from a running Savage Wind when its control port answers (/api/live, see pollLive), else
// where the game last saved (PlayerStatus.SavePos).
const playerLayer = L.layerGroup().addTo(map);
let playerMarker = null, playerMarkerKey = '';
const playerPos = () => state.live || (state.progress && state.progress.player) || null;
function playerPopup(p) {
  const when = !state.live && state.progress && state.progress.mtime ? new Date(state.progress.mtime * 1000).toLocaleString(LOCALE) : '';
  return `<div class="pop"><h3>${esc(t('link'))}</h3><div class="row">${esc(t(state.live ? 'player.whereLive' : 'player.where'))}</div>
    ${when ? `<div class="row">${esc(t('player.saved', { when }))}</div>` : ''}<div class="row">x ${Math.round(p.x)} · y ${Math.round(p.y)} · z ${Math.round(p.z)}</div></div>`;
}
function renderPlayer() {
  const p = playerPos();
  $('#find-link').hidden = !p;
  const key = p && layerOf(p.y) === state.layer ? (state.live ? 'live' : 'saved') : '';
  if (!state.live) setFollow(false);
  if (key && key === playerMarkerKey && playerMarker) {   // same marker: just move it (live updates, 4 a second)
    playerMarker.setLatLng(toLatLng(p));
    playerMarker.setPopupContent(playerPopup(p));
    turnPlayer(p);
    if (state.follow) map.panTo(toLatLng(p), { animate: true, duration: 0.25, easeLinearity: 1, noMoveStart: true });
    return;
  }
  playerLayer.clearLayers();
  playerMarker = null;
  playerMarkerKey = key;
  if (!key) return;
  const s = 30;
  playerMarker = L.marker(toLatLng(p), {
    icon: L.divIcon({ className: 'mk player' + (state.live ? ' live' : ''), html: `<img src="${iconSrc('player')}" alt="">`,
      iconSize: [s, s], iconAnchor: [s / 2, s / 2], popupAnchor: [0, -s / 2] }),
    zIndexOffset: 10000, keyboard: false,
  });
  playerMarker.bindPopup(playerPopup(p));
  playerLayer.addLayer(playerMarker);
  playerTurn = null;
  turnPlayer(p);
}
// The arrow points up (north) in the icon. Live, it turns the way the camera looks (heading: degrees clockwise from
// north, from the yaw of Savage Wind's `pos`), which while playing is the way Link goes. The angle is kept unwrapped
// so the short glide between updates never spins the long way round (359 -> 1 turns by 2 degrees). A save has no
// facing: the arrow then points north.
let playerTurn = null;
function turnPlayer(p) {
  const img = playerMarker && playerMarker.getElement() && playerMarker.getElement().querySelector('img');
  if (!img) return;
  if (typeof p.heading !== 'number') { img.style.transform = ''; playerTurn = null; return; }
  if (playerTurn === null) playerTurn = p.heading;
  else playerTurn += ((p.heading - playerTurn) % 360 + 540) % 360 - 180;
  img.style.transform = `rotate(${playerTurn}deg)`;
}

// Live position. Savage Wind started with SAVAGEWIND_CONTROL=7777 answers `pos` on a local port; the server asks it
// four times a second for /api/live. Fast polls here while it answers, slow ones while it doesn't (game closed or
// started without the variable), and none while switched off.
const LIVE_MS = 250, LIVE_IDLE_MS = 2000;
state.liveOn = store.get('live', true);
state.live = null;
state.liveReason = 'missing';
let liveTimer = null;
function setLive(p, reason) {
  const was = state.live;
  state.live = p;
  state.liveReason = p ? 'live' : reason || 'missing';
  if (!!was !== !!p || (was && p && (was.x !== p.x || was.y !== p.y || was.z !== p.z || was.heading !== p.heading))) renderPlayer();
  renderLiveStatus();
}
async function pollLive() {
  clearTimeout(liveTimer);
  if (!state.liveOn) { setLive(null, 'off'); return; }
  try {
    const r = await getJSON('/api/live');
    setLive(r.live ? { x: r.x, y: r.y, z: r.z, heading: r.heading } : null, r.reason);
  } catch { setLive(null, 'missing'); }
  if (state.liveOn) liveTimer = setTimeout(pollLive, state.live ? LIVE_MS : LIVE_IDLE_MS);
}
function renderLiveStatus() {
  const el = $('#live-msg');
  if (el) el.textContent = t('live.status.' + state.liveReason);
  if (state.live) state.gameStarting = false;
  if (typeof renderGame === 'function') renderGame();
}

function renderLegend() {
  const done = state.progress ? state.progress.done : {};
  const el = $('#legend');
  el.innerHTML = `<label class="check mystery" title="${esc(t('mystery.hint'))}"><input type="checkbox" id="mystery" ${state.mystery ? 'checked' : ''}>
    <span>${esc(t('mystery.toggle'))}</span></label>`;
  el.querySelector('#mystery').onchange = e => { state.mystery = e.target.checked; store.set('mystery', state.mystery); renderLegend(); render(); };
  if (state.mystery) {
    const found = (state.progress && state.progress.found) || {};
    let left = 0;
    for (const cat of CATS) {
      const d = done[cat.key] || [], f = found[cat.key] || [];
      (state.data[cat.data] || []).forEach((o, i) => { if (!d[i] && !f[i]) left++; });
    }
    const b = document.createElement('button');
    b.className = 'cat' + (state.off.has(UNK) ? ' off' : '');
    b.title = state.off.has(UNK) ? t('legend.show') : t('legend.hide');
    b.innerHTML = `<span class="unk-ico">?</span><span>${esc(t('mystery.label'))}</span><span class="n">${left}</span>`;
    b.onclick = () => { state.off.has(UNK) ? state.off.delete(UNK) : state.off.add(UNK); store.set('off', [...state.off]); renderLegend(); render(); };
    el.appendChild(b);
    el.insertAdjacentHTML('beforeend', `<p class="mystery-note">${esc(t('mystery.note'))}</p>`);
  }
  let group = null;
  for (const cat of CATS) {
    if (cat.group !== group) { group = cat.group; el.insertAdjacentHTML('beforeend', `<h2>${esc(group)}</h2>`); }
    const total = (state.data[cat.data] || []).length;
    const n = (done[cat.key] || []).filter(Boolean).length;
    const b = document.createElement('button');
    b.className = 'cat' + (state.off.has(cat.key) ? ' off' : '') + (total && n === total ? ' complete' : '');
    b.title = state.off.has(cat.key) ? t('legend.show') : t('legend.hide');
    b.innerHTML = cat.track === false
      ? `<img src="${iconSrc(cat.icon)}" alt=""><span>${esc(cat.label)}</span><span class="n">${total}</span>`
      : `<img src="${iconSrc(cat.icon)}" alt=""><span>${esc(cat.label)}</span><span class="n">${n} / ${total}</span>
      <span class="bar"><i style="width:${total ? (100 * n / total).toFixed(1) : 0}%"></i></span>`;
    b.onclick = () => { state.off.has(cat.key) ? state.off.delete(cat.key) : state.off.add(cat.key); store.set('off', [...state.off]); renderLegend(); render(); };
    el.appendChild(b);
  }
}

function renderSummary() {
  const d = state.progress ? state.progress.done : {};
  const cnt = k => (d[k] || []).filter(Boolean).length;
  const all = k => ((state.data && state.data[k]) || []).length;
  const items = [
    [t('sum.korokSeeds'), cnt('korok'), all('koroks') || 900],
    [t('sum.shrines'), cnt('shrine'), all('shrines') || 120],
    [t('sum.towers'), cnt('tower'), all('towers') || 15],
    [t('sum.beasts'), cnt('divine_beast'), all('divine_beasts') || 4],
    [t('sum.playtime'), state.progress ? fmtTime(state.progress.playtime) : '–', null],
  ];
  $('#summary').innerHTML = items.map(([l, n, t]) =>
    `<div class="big"><b>${t == null ? esc(n) : `${n}<small style="color:var(--muted);font-size:12px"> / ${t}</small>`}</b><span>${esc(l)}</span></div>`).join('');
}

// ---------- quests ----------
const ptLatLng = p => toLatLng({ x: p[0], z: p[2] });
const rawStatus = q => (state.progress && state.progress.quests && state.progress.quests[q.id]) || ['locked', 'NotReady'];
// "queued": unlocked, but its giver hands out the previous quest of the series first
function qStatus(q) {
  const s = rawStatus(q);
  if (s[0] === 'available' && q.after) {
    const prev = state.quests.find(x => x.id === q.after);
    if (prev && rawStatus(prev)[0] !== 'done') return ['queued', s[1], false];
  }
  return s;
}
const qStep = (q, name) => q.steps.find(s => s.name === name);
// where to go for an active quest: the places of the step after the one reached
const targetPoints = q => { const s = qStep(q, rawStatus(q)[3]); return (s && s.points) || []; };
const humanize = s => s.replace(/_/g, ' ').replace(/([a-z])([A-Z0-9])/g, '$1 $2');

const questName = id => (state.quests.find(q => q.id === id) || {}).name || humanize(id);
const startPoints = q => (q.start && q.start.points) || [];
// "To start: talk to <giver> in <region>." (either part may be missing)
const startText = q => (q.giver && q.region ? t('quest.startBoth', { who: q.giver, region: q.region })
  : q.giver ? t('quest.startWho', { who: q.giver }) : q.region ? t('quest.startWhere', { region: q.region }) : '');

function questText(q) {
  const [st, step] = qStatus(q);
  const sp = startPoints(q), approx = q.start && !q.start.exact;
  if (st === 'locked') {
    const needs = ((q.requires && q.requires.quests) || []).filter(id => id !== q.id)
      .filter(id => { const s = state.quests.find(x => x.id === id); return !s || qStatus(s)[0] !== 'done'; });
    return {
      label: t('qs.locked'),
      need: needs.length ? t('quest.needs', { list: needs.map(questName).join(t('list.sep')) }) : t('quest.later'),
      points: sp, goto: sp.length ? (approx ? t('goto.area') : t('goto.giver')) : '', kind: 'start',
    };
  }
  if (st === 'queued') {
    return {
      label: t('qs.queued'),
      need: t('quest.queuedBy', { who: q.giver || t('quest.theGiver'), quest: questName(q.after) }),
      points: sp, goto: sp.length ? (approx ? t('goto.area') : t('goto.giver')) : '', kind: 'start',
    };
  }
  if (st === 'available') {
    return {
      label: q.category === 'main' ? t('quest.mainNotStarted') : t('qs.available'),
      need: startText(q),
      hint: q.category === 'main' ? '' : t('quest.appearHint'),
      points: sp, goto: sp.length ? (approx ? t('goto.startArea') : t('goto.giver')) : '', kind: 'start',
    };
  }
  const s = qStep(q, step);
  if (st === 'done') return { label: t('quest.completed'), text: s ? s.text : '' };
  const pts = targetPoints(q);
  return { label: t('quest.inProgress'), text: (s && s.text) || '', points: pts, goto: pts.length ? t('goto.step') : '', kind: 'target' };
}

function renderQuests() {
  const el = $('#quests');
  if (!state.quests) { el.innerHTML = `<div class="empty">${t('quests.noData')}</div>`; return; }
  const q = state.search.trim().toLowerCase();
  const counts = { active: 0, available: 0, queued: 0, done: 0, locked: 0 };
  for (const x of state.quests) counts[qStatus(x)[0]]++;
  $('#qsummary').innerHTML = [[t('qs.active'), counts.active], [t('qs.done'), `${counts.done}<small style="color:var(--muted);font-size:12px"> / ${state.quests.length}</small>`],
    [t('qsum.ready'), counts.available], [t('qsum.waiting'), `${counts.queued + counts.locked}`]]
    .map(([l, n]) => `<div class="big"><b>${n}</b><span>${esc(l)}</span></div>`).join('');
  const open = store.get('qopen', { main: true, side_quest: true, shrine: true });
  let html = '', shown = 0;
  for (const [cat, label] of QCATS) {
    const all = state.quests.filter(x => x.category === cat);
    const list = all.filter(x => {
      const st = qStatus(x)[0];
      if (state.qmode === 'active' && st !== 'active') return false;
      if (state.qmode === 'available' && !['available', 'queued', 'locked'].includes(st)) return false;
      if (state.qmode === 'done' && st !== 'done') return false;
      return !q || `${x.name} ${x.giver} ${x.region}`.toLowerCase().includes(q);
    });
    if (!list.length) continue;
    shown += list.length;
    const done = all.filter(x => qStatus(x)[0] === 'done').length;
    html += `<details class="qgroup" data-cat="${cat}" ${open[cat] !== false ? 'open' : ''}><summary>${esc(label)}<span class="n">${esc(t('quests.groupDone', { done, all: all.length }))}</span></summary>`;
    for (const x of list) {
      const [st] = qStatus(x);
      const t = questText(x);
      html += `<div class="quest ${state.openQuest === x.id ? 'open' : ''}" data-id="${esc(x.id)}">
        <div class="t"><b>${esc(x.name)}</b><span class="chip ${st}">${QSTATUS[st]}</span></div>
        <div class="meta">${esc([x.giver, x.region].filter(Boolean).join(' · '))}</div>
        <div class="body"><div class="step">${esc(t.label)}</div>
        ${t.need ? `<div class="need">${esc(t.need)}</div>` : ''}${t.text ? `<div class="txt">${esc(t.text)}</div>` : ''}
        ${t.hint ? `<div class="hint">${esc(t.hint)}</div>` : ''}
        ${t.goto ? `<button class="goto" data-id="${esc(x.id)}">${esc(t.goto)}</button>` : ''}</div></div>`;
    }
    html += '</details>';
  }
  el.innerHTML = shown ? html : `<div class="empty">${esc(q ? t('quests.noneSearch') : t('quests.none'))}</div>`;
}

function renderQuestTargets(focusId) {
  questLayer.clearLayers();
  if (!state.quests || !state.progress) return;
  for (const x of state.quests) {
    const [st, step] = qStatus(x);
    const focus = x.id === focusId;
    if (st === 'active' && (state.qtargets || focus)) {
      for (const p of targetPoints(x)) {
        if (layerOf(p[1]) !== state.layer) continue;
        const m = L.marker(ptLatLng(p), { icon: L.divIcon({ className: 'qmk' + (focus ? ' focus' : ''), iconSize: [26, 26] }), zIndexOffset: 1000 });
        m.bindPopup(`<div class="pop"><h3>${esc(x.name)}</h3><div class="row">${esc(t('quest.inProgress'))}</div>
          <div class="row">${esc([x.giver, x.region].filter(Boolean).join(' · '))}</div></div>`);
        questLayer.addLayer(m);
      }
    } else if ((st === 'available' && state.qstarts) || (['available', 'queued', 'locked'].includes(st) && focus)) {
      const approx = x.start && !x.start.exact;
      for (const p of startPoints(x).slice(0, approx ? 1 : 4)) {
        if (layerOf(p[1]) !== state.layer) continue;
        const m = L.marker(ptLatLng(p), { icon: L.divIcon({ className: 'smk' + (approx ? ' approx' : '') + (focus ? ' focus' : ''), html: '!', iconSize: [22, 22] }), zIndexOffset: 900 });
        m.bindPopup(`<div class="pop"><h3>${esc(x.name)}</h3>
          <div class="row">${QSTATUS[st]}${x.giver ? ` · ${esc(x.giver)}` : ''}${x.region ? ` · ${esc(x.region)}` : ''}</div>
          <div class="row">${esc(approx ? t('quest.approx') : t('quest.giverSpot'))}</div></div>`);
        questLayer.addLayer(m);
      }
    }
  }
}

function showQuestOnMap(id) {
  const x = state.quests.find(q => q.id === id);
  const t = x && questText(x);
  if (!t || !t.points || !t.points.length) return;
  const p = t.points[0];
  if (layerOf(p[1]) !== state.layer) setLayer(layerOf(p[1]));
  renderQuestTargets(id);
  map.flyTo(ptLatLng(p), Math.max(map.getZoom(), 1.5), { duration: 0.6 });
  if (window.innerWidth <= 760) document.body.classList.remove('panel-open');
}

$('#quests').addEventListener('click', e => {
  const go = e.target.closest('.goto');
  if (go) { e.stopPropagation(); showQuestOnMap(go.dataset.id); return; }
  const card = e.target.closest('.quest');
  if (card) { state.openQuest = state.openQuest === card.dataset.id ? null : card.dataset.id; renderQuests(); }
});
$('#quests').addEventListener('toggle', e => {
  const d = e.target.closest('.qgroup');
  if (!d) return;
  const open = store.get('qopen', {}); open[d.dataset.cat] = d.open; store.set('qopen', open);
}, true);

// ---------- outfits ----------
// Definitions from /data/outfits.json (build_outfits.py): sets of pieces, each with its upgrade levels (one actor
// and IsGet_ flag per level, the materials each takes) and where to get it. The save gives, per piece,
// [best level ever had, level carried now] (-1: none) and the material counts in the inventory.
const outfitLayer = L.layerGroup().addTo(map);
const pieces = () => (state.outfits || []).flatMap(s => s.pieces);
const oState = p => (state.progress && state.progress.outfits && state.progress.outfits[p.id]) || [-1, -1];
const maxLevel = p => p.levels.length - 1;
const fairiesRestored = () => ((state.progress && state.progress.done.great_fairy) || []).filter(Boolean).length;
function oStatus(p) {
  const [ever, now] = oState(p);
  if (ever < 0) return 'missing';
  if (ever >= maxLevel(p) && maxLevel(p) > 0) return 'max';
  return now < 0 ? 'stored' : 'have';
}
const OSTATUS = Object.fromEntries(['missing', 'have', 'stored', 'max'].map(k => [k, t('ost.' + k)]));
const stars = (n, max) => max > 0 ? `<span class="stars">${'★'.repeat(Math.max(n, 0))}<i>${'★'.repeat(max - Math.max(n, 0))}</i></span>` : '';
// The Adventure Log quest named after the piece (the DLC's EX treasure quests), if there is one
// the piece's inventory picture from the game files (build_outfits.write_icons); ?v= changes after a rebuild
const outfitImg = (p, cls) => p.icon ? `<img class="${cls}" src="/assets/outfiticons/${encodeURIComponent(p.id)}.webp?v=${state.outfitStamp || 0}" alt="" loading="lazy">` : `<span class="${cls} noimg"></span>`;
const outfitQuest = p => (state.quests || []).find(q => q.name.includes(p.name) && q.category === 'side_quest');

function sourceHtml(p, s, i) {
  const go = s.points && s.points.length ? `<button class="goto" data-id="${esc(p.id)}" data-src="${i}">${esc(t('goto.outfit'))}</button>` : '';
  let main = '', sub = [];
  if (s.type === 'shop') {
    main = s.place ? t('outfit.shop', { who: s.who, place: s.place }) : t('outfit.shopNoPlace', { who: s.who });
    if (s.price) sub.push(t('outfit.price', { n: s.price.toLocaleString(LOCALE) }));
    if (s.rebuy) sub.push(t('outfit.rebuy'));
  } else if (s.type === 'chest') main = t('outfit.chest', { place: s.place });
  else if (s.type === 'shrine') main = t('outfit.shrine', { place: s.place });
  else if (s.type === 'amiibo') main = t('outfit.amiibo', { name: s.text });
  else main = s.text;
  return `<div class="src"><span>${esc(main)}${sub.length ? `<br><span class="sub">${esc(sub.join(' · '))}</span>` : ''}</span>${go}</div>`;
}

function upgradesHtml(p) {
  if (maxLevel(p) < 1) return '';
  const [ever] = oState(p);
  const have = (state.progress && state.progress.materials) || {};
  const fairies = fairiesRestored();
  let html = `<div class="sec">${esc(t('outfit.upgrades'))}</div>`;
  p.levels.forEach((lv, i) => {
    if (!i) return;
    const done = ever >= i;
    const mats = lv.mats.map(([id, name, n]) => {
      const c = have[id] || 0;
      return done ? `${esc(name)} ×${n}` : `<span class="${c >= n ? 'ok' : 'short'}">${esc(name)} ${c}/${n}</span>`;
    }).join(', ');
    const fairy = done ? '' : `<span class="fairy">${esc(tn('outfit.fairiesNeeded', i, { have: fairies }))}</span>`;
    html += `<div class="lv ${done ? 'done' : ''}"><b>${esc(t('outfit.level', { n: i }))}</b>${done ? `${esc(t('outfit.levelDone'))} · ` : ''}${mats}${fairy}</div>`;
  });
  return html;
}

function renderOutfits() {
  const el = $('#outfits');
  if (!state.outfits) { el.innerHTML = `<div class="empty">${t('outfits.noData')}</div>`; $('#osummary').innerHTML = ''; return; }
  const all = pieces();
  const q = state.tab === 'outfits' ? state.search.trim().toLowerCase() : '';
  const haveN = all.filter(p => oState(p)[0] >= 0).length;
  const upg = all.filter(p => maxLevel(p) > 0);
  const setsDone = state.outfits.filter(s => s.pieces.every(p => oState(p)[0] >= 0)).length;
  const small = n => `<small style="color:var(--muted);font-size:12px"> / ${n}</small>`;
  $('#osummary').innerHTML = [[t('osum.have'), `${haveN}${small(all.length)}`], [t('osum.sets'), `${setsDone}${small(state.outfits.length)}`],
    [t('osum.max'), `${upg.filter(p => oStatus(p) === 'max').length}${small(upg.length)}`], [t('osum.fairies'), `${fairiesRestored()}${small(4)}`]]
    .map(([l, n]) => `<div class="big"><b>${n}</b><span>${esc(l)}</span></div>`).join('');
  const open = store.get('oopen', {});
  let html = '', shown = 0;
  for (const set of state.outfits) {
    const list = set.pieces.filter(p => {
      const st = oStatus(p);
      if (state.omode === 'missing' && st !== 'missing') return false;
      if (state.omode === 'have' && st === 'missing') return false;
      if (state.omode === 'upgrade' && !((st === 'have' || st === 'stored') && maxLevel(p) > 0)) return false;
      if (!q) return true;
      const hay = [p.name, set.name, p.effect, ...p.sources.map(s => `${s.who || ''} ${s.place || ''} ${s.text || ''}`)].join(' ');
      return hay.toLowerCase().includes(q);
    });
    if (!list.length) continue;
    shown += list.length;
    const got = set.pieces.filter(p => oState(p)[0] >= 0).length;
    html += `<details class="qgroup" data-set="${esc(set.name)}" ${open[set.name] !== false ? 'open' : ''}><summary>${esc(set.name)}<span class="n">${esc(t('outfits.groupHave', { have: got, all: set.pieces.length }))}</span></summary>`;
    for (const p of list) {
      const st = oStatus(p), [ever, now] = oState(p);
      const quest = outfitQuest(p);
      const meta = [t('oslot.' + p.slot), p.effect, p.bonus ? t('outfit.bonus') : ''].filter(Boolean).join(' · ');
      const carried = now > 0 ? t('outfit.carried', { n: now }) : st === 'stored' && maxLevel(p) > 0 ? t('outfit.everHad', { n: ever }) : '';
      html += `<div class="quest outfit ${st} ${state.openOutfit === p.id ? 'open' : ''}" data-id="${esc(p.id)}">
        <div class="orow">${outfitImg(p, 'oimg')}<div class="ocol">
        <div class="t"><b>${esc(p.name)}</b>${stars(Math.max(ever, 0), maxLevel(p))}<span class="chip ${st}">${esc(OSTATUS[st])}</span></div>
        <div class="meta">${esc(meta)}</div></div></div>
        <div class="body">${p.icon ? outfitImg(p, 'obig') : ''}${carried ? `<div class="need">${esc(carried)}</div>` : ''}
          <div class="sec">${esc(t('outfit.howTo'))}</div>
          ${p.sources.length ? p.sources.map((s, i) => sourceHtml(p, s, i)).join('') : `<div class="src">${esc(t('outfit.unknown'))}</div>`}
          ${quest ? `<div class="src">${esc(t('outfit.quest', { quest: quest.name, status: QSTATUS[qStatus(quest)[0]] }))}</div>` : ''}
          ${upgradesHtml(p)}</div></div>`;
    }
    html += '</details>';
  }
  el.innerHTML = shown ? html : `<div class="empty">${esc(q ? t('outfits.noneSearch') : t('outfits.none'))}</div>`;
}

function showOutfitSource(id, i) {
  const p = pieces().find(x => x.id === id);
  const s = p && p.sources[i];
  if (!s || !s.points || !s.points.length) return;
  outfitLayer.clearLayers();
  const where = s.type === 'shop' ? (s.place ? t('outfit.shop', { who: s.who, place: s.place }) : t('outfit.shopNoPlace', { who: s.who }))
    : s.type === 'chest' ? t('outfit.chest', { place: s.place }) : s.type === 'shrine' ? t('outfit.shrine', { place: s.place }) : (s.place || s.text || '');
  for (const pt of s.points) {
    const m = L.marker(ptLatLng(pt), { icon: L.divIcon({ className: 'omk', html: '★', iconSize: [22, 22] }), zIndexOffset: 1100 });
    m.bindPopup(`<div class="pop opop">${outfitImg(p, 'pimg')}<div><h3>${esc(p.name)}</h3><div class="row">${esc(where)}</div></div></div>`);
    outfitLayer.addLayer(m);
  }
  if (s.points.length === 1) map.flyTo(ptLatLng(s.points[0]), Math.max(map.getZoom(), 1.5), { duration: 0.6 });
  else map.flyToBounds(L.latLngBounds(s.points.map(ptLatLng)).pad(0.3), { duration: 0.6, maxZoom: 1.5 });
  if (window.innerWidth <= 760) document.body.classList.remove('panel-open');
}

$('#outfits').addEventListener('click', e => {
  const go = e.target.closest('.goto');
  if (go) { e.stopPropagation(); showOutfitSource(go.dataset.id, Number(go.dataset.src)); return; }
  const card = e.target.closest('.outfit');
  if (card) { state.openOutfit = state.openOutfit === card.dataset.id ? null : card.dataset.id; renderOutfits(); }
});
$('#outfits').addEventListener('toggle', e => {
  const d = e.target.closest('.qgroup');
  if (!d) return;
  const open = store.get('oopen', {}); open[d.dataset.set] = d.open; store.set('oopen', open);
}, true);

function setTab(tab) {
  state.tab = tab; store.set('tab', tab);
  document.body.classList.toggle('tab-quests', tab === 'quests');
  document.body.classList.toggle('tab-outfits', tab === 'outfits');
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  $('#search').placeholder = t(tab === 'quests' ? 'search.quests' : tab === 'outfits' ? 'search.outfits' : 'search.map');
  $('#search').value = '';
  state.search = '';
  render();
  renderQuests();
  renderOutfits();
}

function setStatus(text, cls) { const s = $('#status'); s.textContent = text; s.className = 'status' + (cls ? ' ' + cls : ''); }

// ---------- data ----------
async function getJSON(url) {
  const r = await fetch(url, { cache: 'no-store' });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

async function loadSlots() {
  const s = await getJSON('/api/slots');
  const sel = $('#slot');
  const cur = state.slot;
  sel.innerHTML = `<option value="latest">${esc(t('slot.latest', { n: s.latest ?? '–' }))}</option>` +
    s.slots.map(x => `<option value="${x.slot}" ${x.error ? 'disabled' : ''}>${esc(t('slot.item', { n: x.slot,
      info: x.error ? t('slot.unreadable') : fmtTime(x.playtime), date: new Date(x.mtime * 1000).toLocaleString(LOCALE) }))}</option>`).join('');
  sel.value = [...sel.options].some(o => o.value === String(cur)) ? String(cur) : 'latest';
  state.slot = sel.value;
  return s;
}

async function loadProgress() {
  const p = await getJSON('/api/progress?slot=' + encodeURIComponent(state.slot));
  state.progress = p;
  state.lastMtime = p.mtime;
  renderSummary(); renderLegend(); render(); renderQuests(); renderQuestTargets(); renderOutfits();
  setStatus(t('status.live', { n: p.slot, time: new Date(p.mtime * 1000).toLocaleTimeString(LOCALE) }), 'live');
}

async function poll() {
  let again = true;
  try {
    const s = await loadSlots();
    const slotNo = state.slot === 'latest' ? s.latest : Number(state.slot);
    const info = s.slots.find(x => x.slot === slotNo);
    if (info && (info.mtime !== state.lastMtime || !state.progress || state.progress.slot !== slotNo)) await loadProgress();
    // background update of the map data (e.g. first start after an app update)
    if (s.job && s.job.upgrade) {
      if (s.job.running) setStatus(`${tMsg(s.job.message)} ${Math.round(s.job.progress * 100)}%`);
      else if (state.upgrading && s.job.done) { location.reload(); return; }
      state.upgrading = s.job.running;
    }
  } catch (e) {
    if (e instanceof TypeError) setStatus(t('status.notRunning'), 'err');
    else if (/setup required/.test(e.message)) { again = false; setStatus(t('status.setup')); openSetup(true); }
    else setStatus(t('status.cannotRead', { error: tMsg(e.message) }), 'err');
  } finally {
    if (again) setTimeout(poll, POLL_MS);
  }
}

// ---------- UI wiring ----------
function setLayer(l) {
  state.layer = l; store.set('layer', l);
  for (const k in overlays) map.hasLayer(overlays[k]) && map.removeLayer(overlays[k]);
  overlays[l].addTo(map);
  if (overlays[l].syncDetail) overlays[l].syncDetail();
  if (overlays[l].bringToBack) overlays[l].bringToBack();
  document.querySelectorAll('#layers button[data-layer]').forEach(b => b.classList.toggle('on', b.dataset.layer === l));
  render();
  renderQuestTargets();
}

document.querySelectorAll('#layers button[data-layer]').forEach(b => b.onclick = () => setLayer(b.dataset.layer));
// Link button: fly to Link. While the game is live it also keeps the map on him as he moves (the button stays lit),
// until it is clicked again, the map is dragged, or the live position ends.
function setFollow(on) {
  state.follow = on;
  $('#find-link').classList.toggle('on', on);
  $('#find-link').title = t(on ? 'link.following' : state.live ? 'link.titleLive' : 'link.title');
}
function focusLink() {
  const p = playerPos();
  if (!p) return;
  if (state.follow) { setFollow(false); return; }
  if (layerOf(p.y) !== state.layer) setLayer(layerOf(p.y));
  map.flyTo(toLatLng(p), Math.max(map.getZoom(), 1.5), { duration: 0.8 });
  if (state.live) map.once('moveend', () => { if (state.live) setFollow(true); });
  else setTimeout(() => playerMarker && playerMarker.openPopup(), 900);
}
$('#find-link').onclick = focusLink;
map.on('dragstart', () => setFollow(false));
// (style.css: the live arrow's glide is off while the map moves, so it doesn't trail behind during a fly or zoom;
// follow mode's own pans pass noMoveStart and keep it)
map.on('movestart zoomstart', () => map.getContainer().classList.add('map-moving'));
map.on('moveend zoomend', () => map.getContainer().classList.remove('map-moving'));
document.querySelectorAll('#mode button').forEach(b => {
  b.classList.toggle('on', b.dataset.mode === state.mode);
  b.onclick = () => {
    state.mode = b.dataset.mode; store.set('mode', state.mode);
    document.querySelectorAll('#mode button').forEach(x => x.classList.toggle('on', x === b));
    render();
  };
});
$('#slot').onchange = e => { state.slot = e.target.value; store.set('slot', state.slot); loadProgress().catch(err => setStatus(tMsg(err.message), 'err')); };
let searchTimer;
$('#search').oninput = e => { clearTimeout(searchTimer); searchTimer = setTimeout(() => { state.search = e.target.value; render(); renderQuests(); renderOutfits(); }, 150); };
document.querySelectorAll('#tabs button').forEach(b => b.onclick = () => setTab(b.dataset.tab));
document.querySelectorAll('#qmode button').forEach(b => {
  b.classList.toggle('on', b.dataset.q === state.qmode);
  b.onclick = () => {
    state.qmode = b.dataset.q; store.set('qmode', state.qmode);
    document.querySelectorAll('#qmode button').forEach(x => x.classList.toggle('on', x === b));
    renderQuests();
  };
});
document.querySelectorAll('#omode button').forEach(b => {
  b.classList.toggle('on', b.dataset.o === state.omode);
  b.onclick = () => {
    state.omode = b.dataset.o; store.set('omode', state.omode);
    document.querySelectorAll('#omode button').forEach(x => x.classList.toggle('on', x === b));
    renderOutfits();
  };
});
$('#qtargets').checked = state.qtargets;
$('#qtargets').onchange = e => { state.qtargets = e.target.checked; store.set('qtargets', state.qtargets); renderQuestTargets(); };
$('#qstarts').checked = state.qstarts;
$('#qstarts').onchange = e => { state.qstarts = e.target.checked; store.set('qstarts', state.qstarts); renderQuestTargets(); };
$('#all-on').onclick = () => { state.off.clear(); store.set('off', []); renderLegend(); render(); };
$('#all-off').onclick = () => { CATS.forEach(c => state.off.add(c.key)); state.off.add(UNK); store.set('off', [...state.off]); renderLegend(); render(); };
$('#panel-toggle').onclick = () => document.body.classList.toggle('panel-open');
// On small screens the open panel covers the ☰ button: close with ×, by tapping the map beside it, or Esc.
const closePanel = () => document.body.classList.remove('panel-open');
$('#panel-close').onclick = closePanel;
$('#panel-scrim').onclick = closePanel;
addEventListener('keydown', e => { if (e.key === 'Escape') closePanel(); });

// ---------- setup / settings ----------
async function post(url, body) {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-BotW-Progress': '1' }, body: JSON.stringify(body || {}) });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

const setup = { selected: null, candidates: [], firstRun: false, polling: null };

function badgeHtml(info) {
  const b = [];
  b.push(info.exe || info.game ? '<span class="badge ok">Savage Wind</span>' : `<span class="badge warn">${esc(t('badge.saveOnly'))}</span>`);
  b.push(info.slots ? `<span class="badge ok">${esc(tn('badge.slots', info.slots))}</span>` : `<span class="badge warn">${esc(t('badge.noSaves'))}</span>`);
  b.push(info.game ? `<span class="badge ok">${esc(t('badge.quests'))}</span>` : `<span class="badge warn">${esc(t('badge.noQuests'))}</span>`);
  return `<div class="badges">${b.join('')}</div>`;
}

function renderCandidates() {
  const el = $('#cands');
  const list = [...setup.candidates];
  if (setup.selected && !list.some(c => c.path.toLowerCase() === setup.selected.path.toLowerCase())) list.unshift(setup.selected);
  if (!list.length) {
    el.innerHTML = `<div class="muted">${t('setup.noneFound')}</div>`;
  } else {
    el.innerHTML = list.map((c, i) => `<button class="cand ${setup.selected && setup.selected.path === c.path ? 'sel' : ''}" data-i="${i}">
      <span class="radio"></span><span><div class="p">${esc(c.path)}</div>${badgeHtml(c)}</span></button>`).join('');
    el.querySelectorAll('.cand').forEach(b => b.onclick = () => { selectFolder(list[+b.dataset.i]); });
  }
}

function selectFolder(info) {
  setup.selected = info;
  renderCandidates();
  const ch = $('#choice');
  ch.hidden = !(info && info.notes && info.notes.length);
  ch.innerHTML = info && info.notes ? info.notes.map(n => `<div class="note">${esc(tMsg(n))}</div>`).join('') : '';
  $('#setup-go').disabled = !(info && info.ok);
}

// ---------- new versions (the server asks GitHub; see server.Updates)
function renderUpdate(u) {
  const v = u.latest && u.latest.version;
  $('#update').hidden = !(u.newer && store.get('updateSkip', null) !== v);
  if (v) {
    $('#update-text').textContent = t('update.available', { v });
    $('#update-link').href = u.latest.url;
  }
  $('#update-on').checked = !!u.enabled;
  $('#update-msg').textContent = !u.enabled ? ''
    : u.newer ? t('update.available', { v }) + ' ' + t('update.current', { v: u.current })
    : v ? t('update.latest', { v: u.current })
    : u.error ? t('update.failed') : t('update.checking');
}
async function checkUpdate() {
  if (REMOTE) return;
  try { renderUpdate(await getJSON('/api/update')); } catch {}
}
$('#update-skip').onclick = () => {   // hidden until a version newer than this one
  getJSON('/api/update').then(u => { if (u.latest) store.set('updateSkip', u.latest.version); $('#update').hidden = true; }).catch(() => {});
};
$('#update-on').onchange = async e => {
  try { renderUpdate(await post('/api/update', { enabled: e.target.checked })); } catch {}
  setTimeout(checkUpdate, 4000);   // the check itself runs in the background
};
if (!REMOTE) { setTimeout(checkUpdate, 4000); setInterval(checkUpdate, 30 * 60 * 1000); }

// ---------- Play: start Savage Wind with its control port (server.Game); the page never says which file
state.game = null;
async function loadGame() {
  if (REMOTE) return;
  try { state.game = await getJSON('/api/game'); } catch { state.game = null; }
  renderGame();
}
// The game chip at the top of the map, next to Link: ▶ Play (asks for savagewind.exe first when it isn't known yet),
// Starting…, Load a save (the game answers, no save loaded) or Live (following; a click centres on Link).
function gameChip() {
  if (state.live) return 'live';
  if (state.liveOn && state.liveReason === 'not_in_world') return 'open';
  if (state.gameStarting) return 'starting';
  return 'play';
}
function renderGame() {
  const g = state.game;
  const chip = gameChip(), b = $('#play-game');
  b.hidden = REMOTE;
  b.dataset.state = chip;
  b.disabled = chip === 'starting';
  b.querySelector('span').textContent = t('game.chip.' + chip);
  b.title = t('game.chipTitle.' + chip);
  $('#game-play').disabled = !g || !!state.gameStarting;
  if (state.gameMsg) $('#game-msg').textContent = state.gameMsg;
  else $('#game-msg').textContent = !g ? '' : g.exe ? t('game.exe', { path: g.exe }) : t('game.none');
}
let toastTimer = null;
function toast(msg) {
  const el = $('#toast');
  el.textContent = msg; el.hidden = false;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.hidden = true; }, 8000);
}
async function chooseGame() {
  state.gameMsg = t('setup.browseNote'); renderGame();
  try { state.game = await post('/api/game/choose', {}); state.gameMsg = ''; }
  catch (e) { state.gameMsg = tMsg(e.message); toast(state.gameMsg); }
  renderGame();
  return state.game && state.game.exe;
}
async function playGame() {
  if (!state.game) await loadGame();
  if (!(state.game && state.game.exe)) {   // not known yet: pick it once, then start
    toast(t('game.pickFirst'));
    if (!await chooseGame()) return;
  }
  state.gameStarting = true; state.gameMsg = t('game.starting'); renderGame();
  try {
    state.game = await post('/api/game/start', {});
    if (!state.liveOn) { state.liveOn = true; store.set('live', true); $('#live-on').checked = true; }
    toast(t('game.starting'));
    pollLive();
    setTimeout(() => { state.gameStarting = false; state.gameMsg = ''; renderGame(); }, 20000);   // time to load
  } catch (e) {
    state.gameStarting = false; state.gameMsg = tMsg(e.message); toast(state.gameMsg);
  }
  renderGame();
}
$('#play-game').onclick = () => {
  const chip = gameChip();
  if (chip === 'live') { if (!state.follow) focusLink(); }
  else if (chip === 'open') toast(t('game.chipTitle.open'));
  else if (chip === 'play') playGame();
};
$('#game-play').onclick = playGame;
$('#game-choose').onclick = chooseGame;
if (REMOTE) $('#game-play').closest('.game-row').hidden = true;

// ---------- Live position switch (Settings)
$('#live-on').checked = state.liveOn;
$('#live-on').onchange = e => {
  state.liveOn = e.target.checked;
  store.set('live', state.liveOn);
  pollLive();
};
renderLiveStatus();

// ---------- Open on your phone
function renderLan(st) {
  // Settings section and the floating phone button's popup show the same thing
  $('#share-btn').classList.toggle('on', !!st.enabled);
  $('#share-off').hidden = !!st.enabled;
  $('#share-box').hidden = !st.enabled;
  $('#share-qr').innerHTML = st.qr || '';
  $('#share-url').textContent = st.url || '';
  $('#share-msg').hidden = !st.error;
  $('#share-msg').textContent = tMsg(st.error) || '';
  $('#lan-on').checked = !!st.enabled;
  $('#lan-box').hidden = !st.enabled;
  $('#lan-qr').innerHTML = st.qr || '';
  $('#lan-url').textContent = st.url || '';
  $('#lan-msg').hidden = !st.error;
  $('#lan-msg').textContent = tMsg(st.error) || '';
}
async function setLan(enabled) {
  const btn = enabled ? $('#share-on') : $('#share-stop'), label = btn.textContent;
  btn.disabled = true;
  btn.textContent = enabled ? t('share.turningOn') : t('share.turningOff');
  try { renderLan(await post('/api/lan', { enabled })); }
  catch (err) { renderLan({ enabled: false, error: err.message }); }
  finally { btn.disabled = false; btn.textContent = label; }
}
$('#lan-on').onchange = e => setLan(e.target.checked);
$('#share-on').onclick = () => setLan(true);
$('#share-stop').onclick = () => setLan(false);
function showShare(open) {
  $('#share-pop').hidden = !open;
  $('#share-btn').setAttribute('aria-expanded', String(open));
  if (open) getJSON('/api/lan').then(renderLan).catch(e => renderLan({ enabled: false, error: e.message }));
}
$('#share-btn').onclick = () => showShare($('#share-pop').hidden);
$('#share-close').onclick = () => showShare(false);
addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#share-pop').hidden) showShare(false); });
$('#share-btn').hidden = REMOTE;   // the phone itself doesn't need it
if (!REMOTE) getJSON('/api/lan').then(st => $('#share-btn').classList.toggle('on', !!st.enabled)).catch(() => {});

async function openSetup(firstRun) {
  setup.firstRun = firstRun;
  $('#setup').hidden = false;
  $('#setup-close').hidden = firstRun;
  $('#setup-go').textContent = firstRun ? t('setup.go') : t('setup.save');
  $('#setup-title').textContent = firstRun ? t('setup.title') : t('settings');
  $('#setup-error').hidden = true;
  $('#job').hidden = true;
  $('#cands').innerHTML = `<div class="muted">${esc(t('setup.looking'))}</div>`;
  const dataNote = dir => { $('#data-note').innerHTML = t('setup.dataNote', { dir: `<code id="data-dir">${esc(dir)}</code>` }); };
  dataNote('');
  try {
    const st = await getJSON('/api/setup?scan=1');
    dataNote(st.data_dir);
    $('#setup-foot').textContent = t('setup.version', { v: st.version }) + (st.install ? t('setup.folder', { path: st.install.path }) : '');
    setup.candidates = st.candidates || [];
    setup.current = st.ready && st.install ? st.install.path : null;
    $('#lan-step').hidden = firstRun;
    $('#live-step').hidden = firstRun;
    if (!firstRun) loadGame();
    if (!firstRun) getJSON('/api/lan').then(renderLan).catch(() => {});
    checkUpdate();
    renderLang(st.lang);
    selectFolder(st.install && st.install.ok ? st.install : (setup.candidates[0] || null));
    if (st.job.running) watchJob();
  } catch (e) {
    $('#cands').innerHTML = `<div class="err">${esc(tMsg(e.message))}</div>`;
  }
}

// Language of the game text (names, quests): built from the game files in that language.
function renderLang(l) {
  if (!l) return;
  const name = code => (l.options.find(o => o[0] === code) || [code, code])[1];
  $('#lang-sel').innerHTML = [`<option value="auto">${esc(t('lang.auto', { name: name(l.game) }))}</option>`,
    ...l.options.map(([code, label]) => `<option value="${esc(code)}">${esc(label)}</option>`)].join('');
  $('#lang-sel').value = l.setting;
  $('#lang-msg').hidden = true;
}
$('#lang-sel').onchange = async e => {
  $('#lang-msg').hidden = true;
  try {
    await post('/api/lang', { lang: e.target.value });
    const st = await getJSON('/api/setup');
    if (st.job.running) watchJob();   // rebuilding names and quests in the new language, then reloads
    else if (st.lang && st.lang.effective !== I18N.lang) location.reload();   // nothing to rebuild (e.g. not set up yet)
  } catch (err) {
    $('#lang-msg').hidden = false;
    $('#lang-msg').textContent = tMsg(err.message);
  }
};

$('#browse').onclick = async () => {
  $('#browse-note').textContent = t('setup.browseNote');
  try {
    const r = await post('/api/setup/browse', { initial: setup.selected && setup.selected.path });
    if (r.info) selectFolder(r.info);
  } catch (e) {
    $('#browse-note').textContent = tMsg(e.message);
    return;
  }
  $('#browse-note').textContent = '';
};

function watchJob() {
  $('#job').hidden = false;
  $('#setup-go').disabled = true;
  clearInterval(setup.polling);
  setup.polling = setInterval(async () => {
    let st;
    try { st = await getJSON('/api/setup'); } catch { return; }
    const j = st.job;
    $('#job-bar').style.width = `${Math.round(j.progress * 100)}%`;
    $('#job-msg').textContent = tMsg(j.message) || '';
    if (j.running) return;
    clearInterval(setup.polling);
    if (j.error) {
      $('#setup-error').hidden = false;
      $('#setup-error').textContent = tMsg(j.error);
      $('#setup-go').disabled = false;
      $('#setup-go').textContent = t('setup.retry');
    } else if (j.done) {
      $('#job-msg').textContent = j.warning ? t('setup.readyWarn', { warning: tMsg(j.warning) }) : t('setup.ready');
      setTimeout(() => location.reload(), j.warning ? 2500 : 700);
    }
  }, 500);
}

$('#setup-go').onclick = async () => {
  if (!setup.selected) return;
  // Settings with the same, already set up folder: nothing to rebuild, just close.
  if (!setup.firstRun && setup.current && setup.selected.path === setup.current) { $('#setup').hidden = true; return; }
  $('#setup-error').hidden = true;
  try {
    await post('/api/setup/apply', { path: setup.selected.path });
    watchJob();
  } catch (e) {
    $('#setup-error').hidden = false;
    $('#setup-error').textContent = tMsg(e.message);
  }
};
$('#setup-close').onclick = () => { $('#setup').hidden = true; };
$('#open-settings').onclick = () => openSetup(false);
$('#open-settings').hidden = REMOTE;   // settings stay on the PC
// Inside the app's own window (server.run_window opens /?app=1) closing the window quits, so there's no
// Quit button; "Open in browser" shows the same map in the browser (e.g. on a second screen).
const IN_APP = new URLSearchParams(location.search).has('app');
$('#quit').hidden = IN_APP;
$('#open-browser').hidden = !IN_APP;
$('#open-browser').onclick = () => post('/api/open-browser').catch(() => {});
$('#quit').onclick = async () => {
  try { await post('/api/quit'); } catch {}
  document.body.insertAdjacentHTML('beforeend', `<div class="bye"><div><h2>${esc(t('bye.title'))}</h2><p>${esc(t('bye.text'))}</p></div></div>`);
};

(async function init() {
  try {
    const st = await getJSON('/api/setup');
    if (!st.ready && REMOTE) { setStatus(t('status.remoteNotSetUp'), 'err'); return; }
    if (!st.ready) { setStatus(t('status.setup')); await openSetup(true); return; }
    await setupBaseMaps();
    state.data = await getJSON('/data/map_data.json');
    try { state.quests = (await getJSON('/data/quests.json')).quests; } catch { state.quests = null; }
    try { const o = await getJSON('/data/outfits.json'); state.outfits = o.sets; state.outfitStamp = o.built || 0; } catch { state.outfits = null; }
    setTab(state.tab);
    setLayer(state.layer);
    renderLegend(); renderSummary();
    await loadSlots();
    await loadProgress();
  } catch (e) {
    setStatus(t('status.error', { error: tMsg(e.message) }), 'err');
  }
  setTimeout(poll, POLL_MS);
  pollLive();
  loadGame();
})();
