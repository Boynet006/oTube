"""Flask app, data paths, config + settings, Family/Private mode, durations, the library scan."""
import hashlib
import json
import os
import re
import subprocess
import threading
import time

from flask import Flask, session


app = Flask(__name__, root_path=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 31536000  # static css/js: cached, but URLs are versioned


@app.url_defaults
def static_version(endpoint, values):
    """Add ?v=<file mtime> to static URLs, so browsers cache them for a long time
    yet pick up a new version immediately after it changes."""
    if endpoint == "static" and "filename" in values:
        try:
            values["v"] = int(os.path.getmtime(os.path.join(app.static_folder, values["filename"])))
        except OSError:
            pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the project folder
BASE = os.environ.get("OTUBE_DATA") or ROOT
os.makedirs(BASE, exist_ok=True)
CONFIG_PATH = os.path.join(BASE, "config.json")
_KEY_PATH = os.path.join(BASE, "secret.key")
if not os.path.exists(_KEY_PATH):
    with open(_KEY_PATH, "wb") as _f:
        _f.write(os.urandom(32))
with open(_KEY_PATH, "rb") as _f:
    app.secret_key = _f.read()
PLAYLISTS_PATH = os.path.join(BASE, "playlists.json")  # [{id, name, items: [vid, ...]}]
DUR_CACHE_PATH = os.path.join(BASE, "duration_cache.json")  # persisted path -> [secs, mtime, media info?]
THUMB_DIR = os.path.join(BASE, "thumbs")
AUTO_SUBS_DIR = os.path.join(BASE, "subs")  # speech-to-text captions: <vid>.vtt + <vid>.json
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(AUTO_SUBS_DIR, exist_ok=True)

# All the video containers we recognise. Browser-native ones stream directly
# (seekable); everything else is converted live as it plays (see /stream).
NATIVE_EXTS = {".mp4", ".m4v", ".webm", ".mov", ".ogv", ".ogg"}
NONNATIVE_EXTS = {
    ".mkv", ".avi", ".ts", ".mts", ".m2ts", ".m2t", ".flv", ".f4v", ".wmv",
    ".asf", ".mpg", ".mpeg", ".mpe", ".m2v", ".mp2", ".vob", ".dat", ".3gp",
    ".3g2", ".ogm", ".divx", ".rm", ".rmvb", ".mxf", ".qt", ".dv", ".y4m",
}
VIDEO_EXTS = NATIVE_EXTS | NONNATIVE_EXTS
FFMPEG, FFPROBE = "ffmpeg", "ffprobe"

_rec_cache: dict[str, dict] = {}            # id -> record
_dur_cache: dict[str, tuple] = {}           # path -> (seconds, mtime[, media_info dict])
_dur_state = {"dirty": False}               # unsaved duration-cache entries?


def _load_dur_cache() -> None:
    try:
        with open(DUR_CACHE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            _dur_cache[k] = (float(v[0]), float(v[1])) + ((v[2],) if len(v) > 2 and isinstance(v[2], dict) else ())
    except Exception:
        pass


def _save_dur_cache() -> None:
    try:
        with open(DUR_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({k: list(v) for k, v in _dur_cache.items()}, f)
    except Exception:
        pass


# In Docker, host drives are mounted under OTUBE_HOST_ROOT (/host/c, /host/q, ...), so
# folders can be added with their normal Windows paths (C:/Users/...).
HOST_ROOT = os.environ.get("OTUBE_HOST_ROOT", "").rstrip("/")


def to_local(path: str) -> str:
    """'C:/Users/x' -> '/host/c/Users/x' when running with mounted host drives."""
    m = re.match(r"^([A-Za-z]):[\\/]*(.*)$", path)
    if HOST_ROOT and m:
        return os.path.join(HOST_ROOT, m.group(1).lower(), m.group(2).replace("\\", "/")).rstrip("/")
    return path


PATH_MAP = [tuple(x.split("=", 1)) for x in os.environ.get("OTUBE_PATH_MAP", "").split(";") if "=" in x]


def to_host(path: str) -> str:
    """Container path -> the real path on the host: '/host/c/Users/x' -> 'C:/Users/x',
    plus any OTUBE_PATH_MAP mounts. Unchanged when not running in Docker."""
    for prefix, host in PATH_MAP:
        prefix = prefix.rstrip("/")
        if path == prefix or path.startswith(prefix + "/"):
            return host.rstrip("/") + path[len(prefix):]
    if HOST_ROOT and (path + "/").startswith(HOST_ROOT + "/"):
        rest = path[len(HOST_ROOT):].strip("/")
        letter, _, tail = rest.partition("/")
        if letter:
            return f"{letter.upper()}:/{tail}"
    return path


_cfg_cache: list = [None, None]  # [mtime, cfg]


def load_config() -> dict:
    """Parsed config.json, re-read only when the file changes. Returns a copy
    so callers can modify it before save_config()."""
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
        if _cfg_cache[0] != mtime:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg.get("video_folders"), list):
                cfg = {"video_folders": []}
            _cfg_cache[:] = [mtime, cfg]
        return json.loads(json.dumps(_cfg_cache[1]))
    except Exception:
        return {"video_folders": []}


# ---- App-wide settings (defaults for every device; stored in config.json) ----
SETTINGS_DEFAULTS = {
    "sort": "name",            # name | newest | size | duration | random
    "view": "grid",            # grid | list
    "thumbw": 300,             # card width in px (120-640)
    "continueShelf": True,
    "hoverPreview": True,
    "newDays": 7,              # "New" badge for files added within N days
    "autoplay": True,
    "upnextSeconds": 5,        # countdown before the next video (0 = straight away)
    "resume": True,
    "volume": 100,             # %
    "speed": 1,
    "captionsOn": False,
    "theater": False,
    "seekStep": 5,             # seconds for ← / →
    "miniMode": False,
    "display": {"b": 1, "c": 1, "n": False, "w": 50},
    "whisperModel": os.environ.get("OTUBE_WHISPER_MODEL", "large-v3-turbo"),
    "topTasks": "auto",        # background-tasks indicator in the top bar: auto (while working) | always | never
    "topBrightness": True,     # top-bar buttons
    "topMini": True,
    "topMode": True,
}
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3-turbo", "large-v3"]
SORT_ICONS = {'name': '<svg viewBox="0 0 24 24" width="22" height="22"><text x="2" y="11" font-size="9" font-weight="700" fill="currentColor">A</text><text x="2" y="21" font-size="9" font-weight="700" fill="currentColor">Z</text><path fill="currentColor" d="M16 4h2v12h3l-4 4-4-4h3z"/></svg>', 'newest': '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm.5-13H11v6l5.2 3.2.8-1.3-4.5-2.7V7z"/></svg>', 'size': '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M2 20h20v-4H2v4zm2-3h2v2H4v-2zM2 4v4h20V4H2zm4 3H4V5h2v2zm-4 7h20v-4H2v4zm2-3h2v2H4v-2z"/></svg>', 'duration': '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M15 1H9v2h6V1zm-4 13h2V8h-2v6zm8.03-6.61 1.42-1.42c-.43-.51-.9-.99-1.41-1.41l-1.42 1.42A8.96 8.96 0 0 0 12 4a9 9 0 1 0 9 9c0-2.12-.74-4.07-1.97-5.61zM12 20a7 7 0 1 1 0-14 7 7 0 0 1 0 14z"/></svg>', 'random': '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path fill="currentColor" d="M10.59 9.17 5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z"/></svg>'}


def get_settings() -> dict:
    s = dict(SETTINGS_DEFAULTS)
    s.update(load_config().get("settings", {}))
    return s


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


# ---- Family / Private mode ----
PIN_RE = re.compile(r"^\d{4,8}$")
PIN_MAX_TRIES, PIN_LOCKOUT_SECS = 5, 30
_pin_fails: dict[str, list] = {}  # client ip -> [failures, locked_until]
LIKED_ID = "liked"  # built-in "Liked videos" playlist, filled by the Like button
# Folders listed in config "private_folders" are hidden everywhere (lists, search,
# up next, playlists, direct URLs) unless this browser session unlocked Private
# mode -- with the PIN, if one is set. The session cookie ends with the browser.
def private_unlocked() -> bool:
    return bool(session.get("private"))


def private_folders(cfg: dict | None = None) -> set:
    return set((cfg or load_config()).get("private_folders", []))


# The two modes are separate libraries: Family shows only public folders,
# Private shows only private ones.
def folder_shown(folder: str, priv: set | None = None) -> bool:
    priv = private_folders() if priv is None else priv
    return (folder in priv) == private_unlocked()


def visible(items: list[dict]) -> list[dict]:
    priv = private_folders()
    return [v for v in items if folder_shown(v["folder"], priv)]


def allowed(r: dict | None) -> bool:
    return bool(r) and folder_shown(r["folder"])


def load_playlists() -> list[dict]:
    try:
        with open(PLAYLISTS_PATH, "r", encoding="utf-8") as f:
            pls = json.load(f)
        return pls if isinstance(pls, list) else []
    except Exception:
        return []


def save_playlists(pls: list[dict]) -> None:
    with open(PLAYLISTS_PATH, "w", encoding="utf-8") as f:
        json.dump(pls, f, indent=2)


def find_playlist(pls: list[dict], pid: str) -> dict | None:
    return next((p for p in pls if p.get("id") == pid), None)


def vid_id(path: str) -> str:
    return hashlib.md5(path.encode("utf-8", "replace")).hexdigest()


def fmt_duration(secs: float) -> str:
    s = int(secs or 0)
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def rel_time(mtime: float) -> str:
    diff = max(0, time.time() - mtime)
    for unit, secs in (("year", 31536000), ("month", 2592000), ("week", 604800),
                       ("day", 86400), ("hour", 3600), ("minute", 60)):
        n = int(diff // secs)
        if n >= 1:
            return f"{n} {unit}{'s' if n > 1 else ''} ago"
    return "just now"


def folder_name(folder: str) -> str:
    return folder.rstrip("/\\").replace("\\", "/").split("/")[-1] or folder


def avatar_hue(s: str) -> int:
    return int(hashlib.md5(s.encode("utf-8", "replace")).hexdigest(), 16) % 360


def fmt_size(b: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{int(b)} {unit}" if unit == "B" else f"{b:.0f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def cached_duration(path: str, mtime: float) -> float | None:
    """Duration from the cache if the file is unchanged, else None (no I/O)."""
    c = _dur_cache.get(path)
    return c[0] if c and abs(c[1] - mtime) < 1 else None


def probe_duration(path: str, mtime: float | None = None) -> float:
    if mtime is None:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
    cached = cached_duration(path, mtime)
    if cached is not None:  # unchanged file -> trust cache
        return cached
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nokey=1:noprint_wrappers=1", path],
            capture_output=True, text=True, timeout=30).stdout.strip()
        val = float(out)
    except Exception:
        val = 0.0
    _dur_cache[path] = (val, mtime)
    _dur_state["dirty"] = True
    return val


# Walking the folders is the slow part (thousands of files over Docker's
# Windows file sharing), so it never happens on a page load: the result is kept
# in memory, refreshed in the background every SCAN_TTL seconds, and right away
# after anything that changes the library (add/remove folder, rename, delete).
SCAN_TTL = 60
_scan_state = {"items": None, "at": 0.0, "running": False}
scan_hooks: list = []   # called after every scan
_scan_lock = threading.Lock()


def scan() -> list[dict]:
    """All video records, from memory. Only the very first call waits for a scan."""
    if _scan_state["items"] is None:
        with _scan_lock:
            if _scan_state["items"] is None:
                _do_scan()
    elif time.time() - _scan_state["at"] > SCAN_TTL:
        rescan_async()
    return _scan_state["items"]


def rescan_async() -> None:
    if _scan_state["running"]:
        return
    _scan_state["running"] = True

    def run():
        try:
            with _scan_lock:
                _do_scan()
        finally:
            _scan_state["running"] = False
    threading.Thread(target=run, daemon=True).start()


def rescan_now() -> None:
    with _scan_lock:
        _do_scan()


def _do_scan() -> None:
    items = _scan_disk()
    _scan_state["items"], _scan_state["at"] = items, time.time()
    for hook in scan_hooks:   # e.g. the background warmer (media.py), disk numbers (pages.py)
        hook()


def _scan_disk() -> list[dict]:
    """Walk the configured folders for videos; refresh the id->record cache."""
    cfg = load_config()
    items, cache = [], {}
    for folder in cfg.get("video_folders", []):
        local = to_local(folder)
        if not os.path.isdir(local):
            continue
        for root, _dirs, files in os.walk(local):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in VIDEO_EXTS:
                    continue
                full = os.path.join(root, fn)
                vid = vid_id(full)
                try:
                    st = os.stat(full)  # one stat call per file
                    size, mtime = st.st_size, st.st_mtime
                except OSError:
                    continue
                sub = os.path.relpath(root, local).replace("\\", "/")
                rec = {"id": vid, "path": full, "host_path": to_host(full), "title": fn, "folder": folder,
                       "subdir": "" if sub == "." else sub,
                       "ext": ext, "size": size, "mtime": mtime,
                       "native": ext in NATIVE_EXTS}
                cache[vid] = rec
                items.append(rec)
    _rec_cache.clear()
    _rec_cache.update(cache)
    return items


def enrich(items: list[dict]) -> list[dict]:
    out = []
    new_secs = float(get_settings()["newDays"]) * 86400
    for r in items:
        d = cached_duration(r["path"], r["mtime"]) or 0.0
        fname = folder_name(r["folder"])
        out.append({**r, "duration": fmt_duration(d), "dur_secs": int(d),
                    "size_h": fmt_size(r["size"]), "folder_name": fname,
                    "added": rel_time(r["mtime"]),
                    "avatar_letter": (fname[:1] or "V").upper(),
                    "avatar_hue": avatar_hue(fname),
                    "is_new": time.time() - r["mtime"] < new_secs})
    return out


def folder_trees(items: list[dict], folders: list[str]) -> dict:
    """{folder: {"count": n, "children": [node...]}} where a node is
    {"name", "rel", "count", "children"} -- only subfolders that hold videos."""
    trees = {f: {"count": 0, "children": {}} for f in folders}
    for r in items:
        t = trees.get(r["folder"])
        if t is None:
            continue
        t["count"] += 1
        node, rel = t, ""
        for part in [p for p in r["subdir"].split("/") if p]:
            rel = f"{rel}/{part}" if rel else part
            node = node["children"].setdefault(part, {"name": part, "rel": rel, "count": 0, "children": {}})
            node["count"] += 1

    def finish(n):
        n["children"] = sorted((finish(c) for c in n["children"].values()), key=lambda c: c["name"].lower())
        return n
    return {f: finish(t) for f, t in trees.items()}


def unique(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for v in items:
        if v["id"] not in seen:
            seen.add(v["id"])
            out.append(v)
    return out


def sub_level(trees: dict, folder: str, sub: str) -> dict | None:
    """For the chip row: the node being viewed and its direct subfolders."""
    node = trees.get(folder) if folder else None
    if not node:
        return None
    for part in [p for p in sub.split("/") if p]:
        node = next((c for c in node["children"] if c["name"] == part), None)
        if node is None:
            return None
    parent = sub.rsplit("/", 1)[0] if "/" in sub else ""
    return {"count": node["count"], "children": node["children"], "parent": parent,
            "name": sub.rsplit("/", 1)[-1] if sub else folder_name(folder)}


def get_rec(vid: str) -> dict | None:
    if not _rec_cache:
        scan()
    return _rec_cache.get(vid)


# One directory listing instead of a file check per video: checking hundreds of files
# one by one on the host-mounted data folder takes seconds.
_listings: dict[str, tuple] = {}


def dir_names(path: str, ttl: float = 3) -> set:
    hit = _listings.get(path)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        names = set(os.listdir(path))
    except OSError:
        names = set()
    _listings[path] = (time.time(), names)
    return names
