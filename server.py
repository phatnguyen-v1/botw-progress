"""BotW Progress Map: a local web app showing your Breath of the Wild progress from your Savage Wind saves.

    BotWProgressMap.exe (Windows) or ./BotWProgressMap-linux-x86_64 (Linux)
    (or: python server.py [--save-dir DIR] [--port 8788] [--no-browser])

On first start the page shows a setup screen: pick the Savage Wind data folder (the one with extracted/ and
save/, found automatically when possible), then it draws the map and builds the collectibles and quest list
from your own game files.
"""
import argparse
import json
import mimetypes
import os
import re
import shutil
import socket
import string
import struct
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import gamelang
import savefile
from paths import APP_NAME, ASSETS, CONFIG, DATA, FROZEN, GAME_EXE, LOG, VERSION, WEB, WINDOWS

APP_ID = 'botw-progress-map'
# Bump when build_mapdata's output changes: existing installs then rebuild the map data (not the tiles).
DATA_VERSION = 2


def have_assets():
    """Map tiles and map data built from the game files."""
    return (ASSETS / 'tiles' / 'meta.json').is_file() and (ASSETS / 'map_data.json').is_file()


def tiles_current():
    """Map tiles drawn by the current tile code (older ones get redrawn)."""
    import maptiles
    try:
        return json.loads((ASSETS / 'tiles' / 'meta.json').read_text(encoding='utf-8')).get('version') == maptiles.TILES_VERSION
    except (OSError, ValueError):
        return False


def built_version():
    try:
        return json.loads((ASSETS / 'source.json').read_text(encoding='utf-8')).get('data_version', 1)
    except (OSError, ValueError):
        return None


def built_lang():
    """Language the map data and quests were built in (versions before 1.6 always built English)."""
    try:
        return json.loads((ASSETS / 'source.json').read_text(encoding='utf-8')).get('lang', gamelang.DEFAULT)
    except (OSError, ValueError):
        return None


# The page's own words (web/i18n/<code>.json). A language without its own file, or a key it doesn't have,
# falls back along this chain and finally to English (USen), the source.
I18N_PARENT = {'EUen': 'USen', 'USfr': 'EUfr', 'USes': 'EUes'}


def ui_strings(lang):
    chain = [lang]
    while chain[-1] != gamelang.DEFAULT:
        chain.append(I18N_PARENT.get(chain[-1], gamelang.DEFAULT))
    out = {}
    for code in reversed(chain):
        try:
            d = json.loads((WEB / 'i18n' / f'{code}.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        for k, v in d.items():
            if isinstance(v, dict):   # nested tables: messages from the server, labels in the map data
                out[k] = {**out.get(k, {}), **v}
            elif not k.startswith('_'):
                out[k] = v
    if not WINDOWS:
        out = _platform_strings(out)
    return {'lang': lang, 'locale': gamelang.LOCALES.get(lang, 'en-US'), 'strings': out}


def _platform_strings(strings):
    """The texts are written for Windows. Elsewhere they name this system's game binary (also in the keys
    of the `server` table, which are this app's own English messages), and the Windows firewall prompt becomes
    the firewall hint (`share.firewall.linux`), on its own and inside `lan.note`."""
    def fix(s):
        return s.replace('savagewind.exe', GAME_EXE)

    out = {}
    for k, v in strings.items():
        if isinstance(v, dict):
            out[k] = {fix(a): (fix(b) if isinstance(b, str) else b) for a, b in v.items()}
        else:
            out[k] = fix(v) if isinstance(v, str) else v
    prompt, hint = out.get('share.firewall'), out.get('share.firewall.linux')
    if isinstance(prompt, str) and isinstance(hint, str):
        if isinstance(out.get('lan.note'), str):
            out['lan.note'] = out['lan.note'].replace(prompt, hint)
        out['share.firewall'] = hint
    return out


class Ui:
    """How the page is shown: in the app's own window (pywebview: Edge WebView2 on Windows, GTK or Qt on
    Linux when running from source) or in the browser (always, in the packaged Linux build)."""
    mode = 'browser'
    window = None
    url = None

    @classmethod
    def focus(cls):
        """Bring the window to the front (a second launch of the exe asks for this)."""
        w = cls.window
        if not w:
            if cls.url:
                webbrowser.open(cls.url)
            return
        try:
            w.restore()
            w.show()
            w.on_top = True
            w.on_top = False
        except Exception:
            traceback.print_exc()

    @classmethod
    def quit(cls, server):
        if cls.window:
            cls.window.destroy()   # ends webview.start(); main() then stops the server
        else:
            server.shutdown()
CSRF_HEADER = 'X-BotW-Progress'   # POSTs must carry it, so other websites can't drive this local app


# ---------------------------------------------------------------- install folders

# Savage Wind keeps its game files, saves and settings in one data folder: next to savagewind.exe on Windows,
# ~/.local/share/savagewind on Linux, or SAVAGEWIND_DATA_ROOT. It has extracted/romfs (the game files),
# save/save_0/<slot>/game_data.sav and savagewind.ini.
GAME_EXES = ('savagewind.exe', 'savagewind')


def _has_game_exe(folder):
    return any((folder / name).is_file() for name in GAME_EXES)


def _is_data_folder(folder):
    return ((folder / 'extracted' / 'romfs').is_dir() or (folder / 'save').is_dir()
            or (folder / 'savagewind.ini').is_file() or _has_game_exe(folder))


def _slot_dirs(folder):
    try:
        return [d for d in Path(folder).iterdir()
                if d.is_dir() and d.name.isdigit() and (d / 'game_data.sav').is_file()]
    except OSError:
        return []


def _saves_under(folder):
    """The most recently used save_N folder (with <slot>/game_data.sav) under `folder` (or folder itself)."""
    folder = Path(folder)
    if _slot_dirs(folder):
        return folder
    try:
        users = [d for d in folder.glob('save_*') if d.is_dir() and _slot_dirs(d)]
    except OSError:
        return None
    return max(users, key=lambda d: d.stat().st_mtime) if users else None


def inspect(path):
    """Check a folder the user picked. Accepts the data folder or anything inside it (extracted, save, ...)."""
    p = Path(path).expanduser()
    info = {'input': str(p), 'path': str(p), 'game': False, 'save_dir': None, 'slots': 0, 'ok': False, 'notes': []}
    if not p.is_dir():
        info['notes'].append('This folder does not exist.')
        return info
    root = None
    for cand in [p, *list(p.parents)[:4]]:
        if _is_data_folder(cand):
            root = cand
            break
    if root:
        info['path'] = str(root)
        info['game'] = (root / 'extracted' / 'romfs' / 'Pack' / 'Bootup.pack').is_file()
        save_dir = _saves_under(root / 'save')
    else:
        save_dir = _saves_under(p) or _saves_under(p / 'save')   # a plain save folder (e.g. from an emulator)
    if save_dir:
        info['save_dir'] = str(save_dir)
        info['slots'] = len(_slot_dirs(save_dir))
    if not root and not save_dir:
        info['notes'].append(f"This doesn't look like a Savage Wind folder: no {GAME_EXE}, extracted or save "
                             "folder inside.")
    elif not info['game']:
        info['notes'].append("No game files (extracted/romfs) here. The map, the collectibles and the quests are "
                             "built from Savage Wind's game files, so pick the Savage Wind folder (the one with "
                             f"{GAME_EXE}, or its data folder with extracted and save).")
    elif not save_dir:
        info['notes'].append("No saves yet. Play until the game saves once, and the map will pick it up.")
    info['ok'] = bool(info['game'])
    info['exe'] = bool(root and _has_game_exe(root))
    return info


def recomp_last_install():
    """Savage Wind's data folder from SAVAGEWIND_DATA_ROOT, or on Linux its default ~/.local/share/savagewind."""
    if os.environ.get('SAVAGEWIND_DATA_ROOT'):
        return Path(os.environ['SAVAGEWIND_DATA_ROOT'])
    if not WINDOWS:
        return Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share') / 'savagewind'
    return None


def _subfolders(folder):
    try:
        return [d for d in folder.iterdir() if d.is_dir()]
    except OSError:
        return []


def _drives():
    """Where games get installed besides the home folder: the drive roots on Windows; on Linux /opt and the
    mounted drives (a Steam Deck's SD card is under /run/media)."""
    if WINDOWS:
        return [Path(f'{d}:/') for d in string.ascii_uppercase if os.path.exists(f'{d}:/')]
    mounts = _subfolders(Path('/mnt'))
    for parent in (Path('/media'), Path('/run/media')):   # <drive> or <user>/<drive>
        for d in _subfolders(parent):
            mounts += [d, *_subfolders(d)]
    return [Path('/opt'), *mounts]


def detect_installs():
    """Look for Savage Wind folders in the usual places. Fast: only a couple of folder levels per drive."""
    roots = []
    if os.environ.get('BOTW_DIR'):
        roots.append(Path(os.environ['BOTW_DIR']))
    last = recomp_last_install()
    if last:
        roots.append(last)
    drives = _drives()
    home = Path.home()
    bases = [*drives, *[d / 'Games' for d in drives], home, home / 'Games', home / 'Desktop', home / 'Downloads']
    if not WINDOWS:
        bases.append(Path(os.environ.get('XDG_DATA_HOME') or home / '.local' / 'share'))
    for base in bases:
        try:
            for d in base.iterdir():
                if d.is_dir() and (_has_game_exe(d) or (d / 'extracted' / 'romfs').is_dir()):
                    roots.append(d)
        except OSError:
            continue
    seen, out = set(), []
    for r in roots:
        key = str(r.resolve())
        key = key.lower() if WINDOWS else key
        if key in seen:
            continue
        seen.add(key)
        info = inspect(r)
        if info['ok']:
            out.append(info)
    out.sort(key=lambda i: (not i['save_dir'], not i['game']))
    return out


def load_config():
    try:
        return json.loads(CONFIG.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding='utf-8')


# ---------------------------------------------------------------- live position

LIVE_PORT = 7777        # Savage Wind's control port when it is started with SAVAGEWIND_CONTROL=7777
LIVE_POLL_S = 0.25      # four times a second while the game answers
LIVE_STALE_S = 2.0      # no good answer for this long: not live any more
LIVE_IDLE_S = 6.0       # stop asking the game when no page has asked /api/live for this long
LIVE_TIMEOUT_S = 1.5    # connect and reply timeout; a reply normally comes within one frame
_YAW = re.compile(r'(?:^|\s)yaw=(-?\d+(?:\.\d+)?)(?:\s|$)')


def parse_pos_reply(line):
    """One reply line of Savage Wind's `pos` command -> /api/live fields.
    {"ok":true,"data":{"x","y","z","stamina","place":"swr:1 x=.. y=.. z=.. yaw=.. pitch=.. hour=.."}} gives
    {'live': True, 'in_world': True, 'x', 'y', 'z'[, 'yaw', 'heading']}; {"ok":false,"error":"No world loaded"}
    (title screen, loading) gives {'live': False, 'in_world': False, 'reason': 'not_in_world', 'error': ...}.
    The yaw is the camera's (Savage Wind: 0 looks along +z, 90 along +x), which during play is roughly the way
    Link goes; heading turns it into degrees clockwise from map north (-z), like the TotK map's live file."""
    try:
        d = json.loads(line)
    except ValueError:
        return {'live': False, 'in_world': False, 'reason': 'unreadable'}
    if not isinstance(d, dict):
        return {'live': False, 'in_world': False, 'reason': 'unreadable'}
    # TotK's recomp speaks the same protocol and also defaults to port 7777: take only Savage Wind's own answers
    # (its error before a save is loaded, and a place line starting with "swr:").
    if not d.get('ok'):
        err = str(d.get('error') or '')[:200]
        return {'live': False, 'in_world': False, 'reason': 'not_in_world' if err == 'No world loaded' else 'unreadable',
                'error': err}
    data = d.get('data')
    if not isinstance(data, dict) or not str(data.get('place') or '').startswith('swr:'):
        return {'live': False, 'in_world': False, 'reason': 'unreadable'}
    try:
        x, y, z = (float(data[k]) for k in ('x', 'y', 'z'))
    except (KeyError, TypeError, ValueError):
        return {'live': False, 'in_world': False, 'reason': 'unreadable'}
    if not all(abs(v) < 100000 for v in (x, y, z)):   # also rejects nan and inf
        return {'live': False, 'in_world': False, 'reason': 'unreadable'}
    out = {'live': True, 'in_world': True, 'x': x, 'y': y, 'z': z}
    m = _YAW.search(str(data.get('place') or ''))
    if m:
        yaw = float(m.group(1))
        if abs(yaw) <= 720:
            out['yaw'] = yaw
            out['heading'] = (180.0 - yaw) % 360
    return out


class LiveGame:
    """Link's position right now, from a running Savage Wind's local control socket (127.0.0.1, only when the game
    was started with SAVAGEWIND_CONTROL=<port>). Text in, one JSON line out. That port also takes developer
    commands, so this only ever sends `pos`, which only reads. A thread asks four times a second while a page wants
    the live position (it polls /api/live) and nothing at all otherwise; while the game isn't there it tries again
    after 1, 2, 4 ... up to 10 seconds. A connection that stops answering is closed and opened anew."""
    REQUEST = b'pos\n'   # the one command sent, ever

    def __init__(self, port):
        self.port = port
        self.lock = threading.Lock()
        self.wanted = 0.0          # monotonic time of the last /api/live
        self.result = {'live': False, 'in_world': False, 'reason': 'missing'}
        self.good_at = None        # monotonic time of the last position
        self.seq = 0
        self.thread = None

    def status(self):
        """The /api/live answer; starts the poller when it isn't running."""
        with self.lock:
            self.wanted = time.monotonic()
            if not (self.thread and self.thread.is_alive()):
                self.thread = threading.Thread(target=self._run, name='live-pos', daemon=True)
                self.thread.start()
            out = dict(self.result)
            if out.get('live'):
                age = time.monotonic() - self.good_at
                if age > LIVE_STALE_S:
                    out = {'live': False, 'in_world': False, 'reason': 'stale'}
                else:
                    out['age_ms'] = round(age * 1000)
            out['port'] = self.port
            return out

    def _set(self, r):
        with self.lock:
            if r.get('live'):
                self.seq += 1
                r['seq'] = self.seq
                r['t'] = round(time.time() * 1000)
                self.good_at = time.monotonic()
            elif r.get('reason') in ('missing', 'unreadable') and self.result.get('live') \
                    and time.monotonic() - self.good_at < LIVE_STALE_S:
                return   # one bad poll: the last position stands until it is stale
            self.result = r

    def _run(self):
        sock, buf, backoff = None, b'', 1.0
        try:
            while True:
                with self.lock:
                    if time.monotonic() - self.wanted > LIVE_IDLE_S:
                        self.thread = None   # (status() starts a new one)
                        self.result = {'live': False, 'in_world': False, 'reason': 'missing'}
                        return
                started = time.monotonic()
                try:
                    if sock is None:
                        sock = socket.create_connection(('127.0.0.1', self.port), timeout=LIVE_TIMEOUT_S)
                        buf = b''
                    sock.sendall(self.REQUEST)
                    deadline = time.monotonic() + LIVE_TIMEOUT_S
                    while b'\n' not in buf:
                        left = deadline - time.monotonic()
                        if left <= 0 or len(buf) > 65536:
                            raise TimeoutError('no reply')
                        sock.settimeout(left)
                        chunk = sock.recv(4096)
                        if not chunk:
                            raise ConnectionError('closed')
                        buf += chunk
                    line, buf = buf.split(b'\n', 1)
                    self._set(parse_pos_reply(line.decode('utf-8', 'replace')))
                    backoff = 1.0
                    wait = LIVE_POLL_S - (time.monotonic() - started)
                except OSError:   # refused (game not running or no SAVAGEWIND_CONTROL), timed out, closed
                    if sock is not None:
                        sock.close()
                        sock = None
                    self._set({'live': False, 'in_world': False, 'reason': 'missing'})
                    wait, backoff = backoff, min(backoff * 2, 10.0)
                time.sleep(max(wait, 0.02))
        finally:
            if sock is not None:
                sock.close()


# ---------------------------------------------------------------- state

class State:
    def __init__(self, save_override=None):
        self.lock = threading.Lock()
        self.cfg = load_config()
        self.save_override = save_override
        self.install = None
        self.save_dir = None
        self.map_data = self.quests = None
        self.cache = {}
        self.job = {'running': False, 'progress': 0.0, 'message': '', 'error': None, 'done': False}
        port = self.cfg.get('savagewind_control_port', LIVE_PORT)
        self.live = LiveGame(port if isinstance(port, int) and 0 < port < 65536 else LIVE_PORT)
        self.reload()

    def reload(self):
        """Re-read config, install folder and assets."""
        self.install = inspect(self.cfg['install_dir']) if self.cfg.get('install_dir') else None
        sd = self.save_override or (self.install or {}).get('save_dir')
        self.save_dir = Path(sd) if sd else None
        mp = ASSETS / 'map_data.json'
        self.map_data = json.loads(mp.read_text(encoding='utf-8')) if mp.is_file() else None
        qfile = ASSETS / 'quests.json'
        qdata = json.loads(qfile.read_text(encoding='utf-8')) if qfile.is_file() else {}
        self.quests = qdata.get('quests')
        with self.lock:
            self.cache.clear()

    def ready(self):
        return bool(self.save_dir) and self.map_data is not None and have_assets()

    def setup_status(self, with_candidates=False):
        st = {
            'version': VERSION, 'ready': self.ready(), 'install': self.install,
            'save_override': self.save_override, 'data_dir': str(DATA),
            'game_exe': GAME_EXE, 'platform': 'windows' if WINDOWS else sys.platform,
            'assets': {'map': have_assets(), 'quests': self.quests is not None},
            'job': dict(self.job),
            'lang': self.lang_info(),
        }
        if with_candidates:
            st['candidates'] = detect_installs()
        return st

    def start_setup(self, folder):
        info = inspect(folder)
        if not info['ok']:
            raise ValueError(' '.join(info['notes']) or 'Not a Savage Wind folder.')
        if self.job['running']:
            raise ValueError('Setup is already running.')
        self.cfg['install_dir'] = info['path']
        save_config(self.cfg)
        self.job = {'running': True, 'progress': 0.0, 'message': 'Starting…', 'error': None, 'done': False}
        threading.Thread(target=self._setup_job, args=(info,), daemon=True).start()
        return info

    def needs_game_build(self):
        """True when game files are available but the map or its data is missing or from an older version
        (e.g. after an update). The rebuild skips the map tiles when they're already there."""
        return bool(self.install and self.install.get('game')) and not (
            have_assets() and built_version() == DATA_VERSION and tiles_current() and built_lang() == self.effective_lang())

    # ---- language of the game text (place, item and quest names, quest steps)
    def effective_lang(self):
        """The language picked in Settings, or else the one Savage Wind shows the game in."""
        chosen = self.cfg.get('lang')
        if chosen in gamelang.LANGUAGES:
            return chosen
        return gamelang.game_language((self.install or {}).get('path'))

    def lang_info(self):
        return {'setting': self.cfg.get('lang') if self.cfg.get('lang') in gamelang.LANGUAGES else 'auto',
                'game': gamelang.game_language((self.install or {}).get('path')),
                'effective': self.effective_lang(), 'built': built_lang(),
                'options': [[k, v] for k, v in gamelang.LANGUAGES.items()]}

    def set_lang(self, lang):
        """'auto' (follow Savage Wind) or a game language code. Rebuilds the map data and quests if needed."""
        if lang != 'auto' and lang not in gamelang.LANGUAGES:
            raise ValueError('Unknown language.')
        if self.job['running']:
            raise ValueError('The app is busy updating the map. Try again in a moment.')
        with self.lock:
            if lang == 'auto':
                self.cfg.pop('lang', None)
            else:
                self.cfg['lang'] = lang
            save_config(self.cfg)
        if self.ready() and self.needs_game_build():
            self.start_upgrade('Switching the language of names and quests…')
        return self.lang_info()

    def build_icons(self, romfs=None):
        """Map icons from the game files (a second or so). The app's own icons are used when this fails."""
        try:
            import build_icons
            import gamefiles
            rf = romfs or gamefiles.Romfs(Path(self.install['path']) / 'extracted')
            build_icons.build(rf, ASSETS / 'gameicons', log=print)
        except Exception:
            traceback.print_exc()

    UI_PREFS = {'off', 'known', 'mode', 'layer', 'tab', 'slot', 'qmode', 'qtargets', 'qstarts', 'qopen', 'mystery', 'updateSkip', 'live'}

    def set_prefs(self, values):
        """View settings of the page (legend, filters, layer, tab, ...), kept in config.json so they survive
        restarts and are the same in the app window and the browser."""
        if not isinstance(values, dict):
            raise ValueError('prefs must be an object')
        ui = dict(self.cfg.get('ui') or {})
        ui.update({k: v for k, v in values.items() if k in self.UI_PREFS})
        if len(json.dumps(ui)) > 20000:
            raise ValueError('prefs too large')
        with self.lock:
            self.cfg['ui'] = ui
            save_config(self.cfg)
        return ui

    def needs_icons(self):
        """Game files available but no icons yet, or icons from an older icon set."""
        if not (self.install and self.install.get('game')):
            return False
        import build_icons
        try:
            meta = json.loads((ASSETS / 'gameicons' / 'meta.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return True
        return meta.get('version') != build_icons.VERSION

    def start_upgrade(self, message='Updating the map from your game files…'):
        if self.install and not self.job['running']:
            self.job = {'running': True, 'progress': 0.0, 'message': message,
                        'error': None, 'done': False, 'upgrade': True}
            threading.Thread(target=self._setup_job, args=(self.install,), daemon=True).start()

    def _setup_job(self, info):
        job = self.job

        def stage(lo, hi):
            def prog(frac, msg):
                job['progress'] = round(lo + (hi - lo) * frac, 3)
                job['message'] = msg
            return prog

        try:
            if not info.get('game'):
                raise RuntimeError('No Savage Wind game files (extracted/romfs) in this folder.')
            romfs = Path(info['path']) / 'extracted'
            import gamefiles
            import maptiles
            import build_mapdata
            rf = gamefiles.Romfs(romfs)
            lang = self.effective_lang()
            if lang not in gamelang.available(rf):
                lang = gamelang.DEFAULT
            keep_tiles = job.get('upgrade') and tiles_current()
            if not keep_tiles:
                stage(0, 0.6)(0, 'Drawing the map from your game files…')
                maptiles.build_tiles(rf, ASSETS / 'tiles', progress=stage(0, 0.6))
            self.build_icons(rf)
            stage(0.6, 0.8)(0, 'Finding shrines, Koroks and everything else in your game files…')
            build_mapdata.build(romfs, ASSETS / 'map_data.json', lang=lang, log=lambda m: stage(0.6, 0.8)(0.5, m[:80]))
            stage(0.8, 1)(0, 'Reading the quest list from your game files…')
            try:
                import build_quests
                build_quests.build(romfs, ASSETS / 'quests.json', lang=lang, log=lambda m: None)
            except Exception as e:  # the map still works without quests
                traceback.print_exc()
                job['warning'] = f'Quest list not built: {e}'
            # written last: a build cut short is redone on the next start
            (ASSETS / 'source.json').write_text(json.dumps({'map': 'game', 'data_version': DATA_VERSION, 'lang': lang,
                                                            'built': time.time()}))
            job['progress'], job['message'] = 1.0, 'Done'
            self.reload()
            job['done'] = True
        except Exception as e:
            traceback.print_exc()
            job['error'] = f'{type(e).__name__}: {e}'
        finally:
            job['running'] = False

    # ---- saves
    def slots(self):
        out = []
        if not self.save_dir:
            return out
        for d in sorted(_slot_dirs(self.save_dir), key=lambda d: int(d.name)):
            f = d / 'game_data.sav'
            st = f.stat()
            entry = {'slot': int(d.name), 'mtime': st.st_mtime}
            try:
                entry.update(savefile.save_info(f))
            except (savefile.SaveError, OSError, struct.error) as e:
                entry['error'] = str(e)
            out.append(entry)
        return out

    def latest_slot(self):
        ok = [s for s in self.slots() if 'error' not in s]
        return max(ok, key=lambda s: (s['mtime'], s['playtime']))['slot'] if ok else None

    def progress(self, slot):
        f = self.save_dir / str(slot) / 'game_data.sav'
        st = f.stat()
        key = (st.st_mtime_ns, st.st_size)
        with self.lock:
            hit = self.cache.get(slot)
            if hit and hit[0] == key:
                return hit[1]
        last = None
        for _ in range(5):  # the game may be writing the file: retry on a torn read
            try:
                result = savefile.parse(f, self.map_data, self.quests)
                break
            except (savefile.SaveError, struct.error, OSError) as e:
                last = e
                time.sleep(0.3)
        else:
            raise savefile.SaveError(f'could not read slot {slot}: {last}')
        result.update(slot=slot, mtime=st.st_mtime)
        with self.lock:
            self.cache[slot] = (key, result)
        return result


# ---------------------------------------------------------------- folder picker

_dialog_lock = threading.Lock()


def _desktop_folder_dialog(start, title):
    """Linux: the desktop's own folder picker through zenity (GNOME and most others) or kdialog (KDE, Steam
    Deck). Returns (shown, path): shown is False when neither could open a window."""
    tools = [['zenity', '--file-selection', '--directory', f'--title={title}', f'--filename={start.rstrip("/")}/'],
             ['kdialog', '--title', title, '--getexistingdirectory', start]]
    if 'KDE' in os.environ.get('XDG_CURRENT_DESKTOP', '').upper():
        tools.reverse()
    for cmd in tools:
        if not shutil.which(cmd[0]):
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if r.returncode == 0:
            return True, r.stdout.strip() or None
        if r.returncode == 1 and not r.stderr.strip():   # cancelled
            return True, None
        print(f'{cmd[0]} failed ({r.returncode}): {r.stderr.strip()[:200]}')
    return False, None


def pick_folder(initial=None):
    """Open the system's folder picker (on Windows on top of everything). Returns a path or None."""
    start = initial or str(Path.home())
    title = f'Select your Savage Wind folder (the one with {GAME_EXE}, or its data folder)'
    with _dialog_lock:
        if not WINDOWS:
            shown, chosen = _desktop_folder_dialog(start, title)
            if shown:
                return chosen
        try:
            import tkinter
            from tkinter import filedialog
            root = tkinter.Tk()
        except Exception as e:   # no Tk in this Python, or no display
            if WINDOWS:
                raise
            print(f'No folder window: {e}')
            raise ValueError('Could not open a folder window here. Install zenity or kdialog, or start the app '
                             'with BOTW_DIR=/path/to/your/savagewind-data-folder.')
        root.withdraw()
        root.attributes('-topmost', True)
        try:
            chosen = filedialog.askdirectory(parent=root, mustexist=True, initialdir=start, title=title)
        finally:
            root.destroy()
        return chosen or None


# ---------------------------------------------------------------- HTTP

# ---------------------------------------------------------------- new versions

class Updates:
    """Is a newer release out? Asks GitHub for the latest release of this app when it starts and then every
    12 hours (Settings -> Updates turns it off). Only compares version numbers: nothing is downloaded or
    installed, and nothing about the player is sent; the page shows a notice with a link to the release."""
    REPO = 'phatnguyen-v1/botw-progress'
    EVERY = 12 * 3600
    lock = threading.Lock()
    latest = None   # {'version', 'url', 'name'} of the latest release, once known
    error = None
    checked = 0.0
    timer = None

    @staticmethod
    def parse(v):
        try:
            return tuple(int(x) for x in str(v).strip().lstrip('vV').split('-')[0].split('.'))
        except ValueError:
            return None

    @classmethod
    def enabled(cls, state):
        return state.cfg.get('update_check', True)

    @staticmethod
    def tls():
        """Certificates from certifi (bundled with the app): Windows only adds a missing root certificate to its
        store when Windows itself checks a site, so Python's own check of api.github.com can fail on a PC
        where that root was never needed before ('unable to get issuer certificate')."""
        import ssl
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            return ssl.create_default_context()

    @classmethod
    def check(cls):
        req = urllib.request.Request(f'https://api.github.com/repos/{cls.REPO}/releases/latest', headers={
            'Accept': 'application/vnd.github+json', 'User-Agent': f'{APP_ID}/{VERSION}'})
        try:
            with urllib.request.urlopen(req, timeout=10, context=cls.tls()) as r:
                rel = json.loads(r.read().decode('utf-8'))
            tag = rel.get('tag_name') or ''
            with cls.lock:
                cls.latest = {'version': tag.lstrip('vV'), 'url': rel.get('html_url') or
                              f'https://github.com/{cls.REPO}/releases/latest', 'name': rel.get('name') or tag}
                cls.error = None
        except Exception as e:   # offline, rate limited, GitHub down: try again next time
            with cls.lock:
                cls.error = str(e)
        cls.checked = time.time()

    @classmethod
    def start(cls, state):
        """Check now (in the background) and then every 12 hours, while checking is on."""
        if cls.timer:
            cls.timer.cancel()
            cls.timer = None
        if not cls.enabled(state):
            return

        def run():
            cls.check()
            if cls.enabled(state):
                cls.timer = threading.Timer(cls.EVERY, run)
                cls.timer.daemon = True
                cls.timer.start()
        threading.Thread(target=run, daemon=True).start()

    @classmethod
    def status(cls, state):
        with cls.lock:
            latest, error = cls.latest, cls.error
        cur, new = cls.parse(VERSION), latest and cls.parse(latest['version'])
        return {'enabled': cls.enabled(state), 'current': VERSION, 'latest': latest, 'error': error,
                'newer': bool(cls.enabled(state) and cur and new and new > cur)}

    @classmethod
    def set_enabled(cls, state, on):
        with state.lock:
            state.cfg['update_check'] = bool(on)
            save_config(state.cfg)
        cls.start(state)
        return cls.status(state)


def make_handler(state, server_ref, remote=False):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def send_json(self, obj, code=200):
            body = json.dumps(obj, separators=(',', ':')).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, path, cache=False):
            try:
                # Only files under the web or data folder, compared without resolving: on some setups (a
                # redirected or junctioned AppData, e.g. when started from a packaged app) resolve() maps the
                # file somewhere else than the folder, and every asset would answer 404.
                path = Path(path).absolute()
                inside = any(path.is_relative_to(base.absolute()) for base in (WEB, ASSETS))
                if '..' in path.parts or not inside or not path.is_file():
                    raise FileNotFoundError
            except (FileNotFoundError, OSError, ValueError):
                self.send_error(404)
                return
            data = path.read_bytes()
            ctype = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'max-age=86400' if cache else 'no-cache')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            q = parse_qs(url.query)
            p = url.path
            try:
                if remote and not self.remote_get_allowed(p):
                    self.send_json({'error': 'not available on other devices'}, 403)
                elif remote and p == '/api/setup':   # what a phone needs, without folder paths
                    self.send_json({'version': VERSION, 'ready': state.ready(), 'remote': True,
                                    'job': {'running': state.job['running']},
                                    'lang': {'effective': state.effective_lang()}})
                elif p == '/api/i18n':   # the page's own words, in the language of the game text
                    self.send_json(ui_strings(state.effective_lang()))
                elif remote and p == '/api/prefs':   # a phone keeps its own view settings
                    self.send_json({})
                elif p == '/api/ping':
                    self.send_json({'app': APP_ID, 'version': VERSION, 'mode': Ui.mode, 'remote': remote})
                elif p == '/api/lan':
                    self.send_json(Lan.status())
                elif p == '/api/update':
                    self.send_json(Updates.status(state))
                elif p == '/api/prefs':
                    self.send_json(state.cfg.get('ui') or {})
                elif p == '/api/setup':
                    self.send_json(state.setup_status(with_candidates=q.get('scan', ['0'])[0] == '1'))
                elif p in ('/api/slots', '/api/progress') and not state.ready():
                    self.send_json({'error': 'setup required', 'setup': True}, 409)
                elif p == '/api/live':   # Link's position right now (Savage Wind's control port, `pos` only)
                    self.send_json(state.live.status())
                elif p == '/api/slots':
                    self.send_json({'save_dir': None if remote else str(state.save_dir), 'latest': state.latest_slot(),
                                    'slots': state.slots(),
                                    'job': state.job})
                elif p == '/api/progress':
                    slot = q.get('slot', [''])[0]
                    slot = state.latest_slot() if slot in ('', 'latest') else int(slot)
                    if slot is None:
                        self.send_json({'error': 'no readable save yet'}, 404)
                    else:
                        self.send_json(state.progress(slot))
                elif p == '/data/map_data.json':
                    self.send_file(ASSETS / 'map_data.json', cache=True)
                elif p == '/data/quests.json':
                    if state.quests is None:
                        self.send_json({'error': 'quest list not built'}, 404)
                    else:
                        self.send_file(ASSETS / 'quests.json')
                elif p.startswith('/assets/'):
                    self.send_file(ASSETS / p[len('/assets/'):], cache=True)
                elif p in ('/', '/index.html'):
                    self.send_file(WEB / 'index.html')
                else:
                    self.send_file(WEB / p.lstrip('/'))
            except (ValueError, savefile.SaveError, OSError) as e:
                self.send_json({'error': str(e)}, 500)

        def remote_get_allowed(self, p):
            """Phones and other devices on the network only read: the page, the map data and the progress."""
            if p.startswith('/api/'):
                return p in ('/api/ping', '/api/setup', '/api/slots', '/api/progress', '/api/prefs', '/api/i18n',
                          '/api/live')
            return True

        def do_POST(self):
            if remote:   # other devices on the network can't change anything
                self.send_json({'error': 'not available on other devices'}, 403)
                return
            if self.headers.get(CSRF_HEADER) != '1':
                self.send_json({'error': 'forbidden'}, 403)
                return
            try:
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length) or b'{}') if length else {}
            except ValueError:
                self.send_json({'error': 'bad request'}, 400)
                return
            p = urlparse(self.path).path
            try:
                if p == '/api/setup/browse':
                    start = body.get('initial') or (state.install or {}).get('path')
                    chosen = pick_folder(start)
                    self.send_json({'path': chosen, 'info': inspect(chosen) if chosen else None})
                elif p == '/api/setup/check':
                    self.send_json(inspect(body.get('path', '')))
                elif p == '/api/setup/apply':
                    info = state.start_setup(body.get('path', ''))
                    self.send_json({'ok': True, 'info': info})
                elif p == '/api/quit':
                    self.send_json({'ok': True})
                    threading.Thread(target=lambda: (time.sleep(0.3), Ui.quit(server_ref[0])), daemon=True).start()
                elif p == '/api/prefs':
                    self.send_json({'ok': True, 'ui': state.set_prefs(body)})
                elif p == '/api/lan':
                    self.send_json(Lan.set_enabled(state, bool(body.get('enabled'))))
                elif p == '/api/update':
                    self.send_json(Updates.set_enabled(state, bool(body.get('enabled'))))
                elif p == '/api/lang':
                    self.send_json({'ok': True, 'lang': state.set_lang(body.get('lang', 'auto'))})
                elif p == '/api/focus':
                    Ui.focus()
                    self.send_json({'ok': True, 'mode': Ui.mode})
                elif p == '/api/open-browser':
                    webbrowser.open(Ui.url)
                    self.send_json({'ok': True})
                else:
                    self.send_json({'error': 'not found'}, 404)
            except ValueError as e:
                self.send_json({'error': str(e)}, 400)

    return Handler


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR would let a second copy share the port. On Linux it doesn't, and without it a
    # restart right after quitting can find the port still held by closed connections.
    allow_reuse_address = not WINDOWS
    daemon_threads = True


def lan_ip():
    """This PC's address on the local network (the one with the default route), or None."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('10.255.255.255', 1))   # UDP: picks the outgoing interface, sends nothing
        ip = s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()
    return None if ip.startswith('127.') or ip.startswith('169.254.') else ip


class Lan:
    """Optional, read-only copy of the page for phones and tablets on the same network (Settings ->
    Open on your phone). A second listener on the LAN address with a handler that only allows reading."""
    srv = None
    url = None
    port = None
    error = None

    @classmethod
    def start(cls, state):
        if cls.srv:
            return
        ip = lan_ip()
        if not ip:
            cls.error = 'No network connection found. Connect this PC to your Wi-Fi or network first.'
            return
        try:
            ref = [None]
            srv = Server((ip, cls.port), make_handler(state, ref, remote=True))
            ref[0] = srv
        except OSError as e:
            cls.error = f'Could not open port {cls.port} on {ip}: {e}'
            return
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        cls.srv, cls.url, cls.error = srv, f'http://{ip}:{cls.port}/', None
        print(f'Also serving {cls.url} (read-only, local network)')

    @classmethod
    def stop(cls):
        if cls.srv:
            srv, cls.srv, cls.url = cls.srv, None, None
            threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()

    @classmethod
    def set_enabled(cls, state, enabled):
        cls.start(state) if enabled else cls.stop()
        state.cfg['lan'] = bool(enabled and cls.srv)
        save_config(state.cfg)
        return cls.status()

    @classmethod
    def status(cls):
        st = {'enabled': bool(cls.srv), 'url': cls.url, 'error': cls.error}
        if cls.url:
            try:
                import segno
                st['qr'] = segno.make(cls.url, error='m').svg_inline(scale=5, border=2, dark='#000', light='#fff')
            except Exception:
                traceback.print_exc()
        return st


def already_running(host, port):
    try:
        with urllib.request.urlopen(f'http://{host}:{port}/api/ping', timeout=1.5) as r:
            return json.loads(r.read()).get('app') == APP_ID
    except (OSError, ValueError):
        return False


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--save-dir', help='read saves from this folder instead of the configured install')
    ap.add_argument('--port', type=int, default=int(os.environ.get('PORT', 8788)))
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--browser', action='store_true', help='show the map in your browser instead of the app window')
    ap.add_argument('--no-browser', action='store_true', help="don't open a window or browser (server only)")
    args = ap.parse_args()

    if FROZEN and not WINDOWS:
        # PyInstaller points LD_LIBRARY_PATH at its bundle; the programs this app starts (the browser, the
        # folder picker) need the system's own libraries back.
        orig = os.environ.pop('LD_LIBRARY_PATH_ORIG', None)
        if orig is None:
            os.environ.pop('LD_LIBRARY_PATH', None)
        else:
            os.environ['LD_LIBRARY_PATH'] = orig
    # No console window (Windows exe, Linux binary started from the desktop): keep a log next to the data.
    if FROZEN and not (sys.stdout and sys.stdout.isatty()):
        DATA.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(LOG, 'a', encoding='utf-8', buffering=1)
    print(f'--- {APP_NAME} {VERSION} starting {time.ctime()}')

    browse_host = 'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host
    srv = None
    for port in range(args.port, args.port + 10):
        if already_running('127.0.0.1', port):   # second launch: bring the running app to the front
            print(f'Already running on {port}')
            if not args.no_browser:
                focus_running(port, f'http://{browse_host}:{port}/')
            return
        try:
            server_ref = [None]
            state = State(args.save_dir)
            srv = Server((args.host, port), make_handler(state, server_ref))
            server_ref[0] = srv
            break
        except OSError:
            continue
    if srv is None:
        print('No free port found.')
        return
    url = f'http://{browse_host}:{port}/'
    print(f'Data folder: {DATA}')
    print(f'Install: {(state.install or {}).get("path")}  ready={state.ready()}')
    if state.ready() and state.needs_game_build():
        print('Map not built from the game files yet: updating in the background')
        state.start_upgrade()
    elif state.ready() and state.needs_icons():
        threading.Thread(target=state.build_icons, daemon=True).start()
    Ui.url = url
    Lan.port = port
    if state.cfg.get('lan') and args.host == '127.0.0.1':
        Lan.start(state)
    Updates.start(state)
    print(f'Serving {url}', flush=True)
    if args.no_browser:
        serve(srv)
    elif args.browser or not run_window(srv, url):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        serve(srv)
    print('Stopped.')


def serve(srv):
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


def run_window(srv, url):
    """Show the page in the app's own window; closing it stops the app. Returns False, before serving
    anything, when pywebview isn't available; the caller then uses the browser. If the window can't open
    (no WebView2 runtime, e.g. an old or stripped-down Windows; no GTK or Qt web view on Linux), it falls
    back to the browser itself. The packaged Linux build has no pywebview and always uses the browser."""
    if FROZEN and not WINDOWS:
        return False
    try:
        import webview
    except ImportError as e:
        print(f'No pywebview ({e}): using the browser')
        return False
    except Exception:
        traceback.print_exc()
        return False
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = True
        Ui.window = webview.create_window('BotW Progress Map', url + '?app=1', width=1400, height=900,
                                          min_size=(760, 520), background_color='#14181d', text_select=True)
        Ui.mode = 'window'
        webview.start(gui='edgechromium' if WINDOWS else None, private_mode=False, storage_path=str(DATA / 'webview'))
    except Exception:
        traceback.print_exc()
        print('No app window: using the browser')
        Ui.window, Ui.mode = None, 'browser'
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
        try:
            while thread.is_alive():
                thread.join(0.5)
        except KeyboardInterrupt:
            pass
        return True
    srv.shutdown()   # window closed
    return True


def focus_running(port, url):
    try:
        req = urllib.request.Request(f'http://127.0.0.1:{port}/api/focus', data=b'{}', method='POST',
                                     headers={CSRF_HEADER: '1', 'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=3) as r:
            json.loads(r.read())
    except (OSError, ValueError):
        webbrowser.open(url)

if __name__ == '__main__':
    main()
