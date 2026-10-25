"""oTube — a local YouTube-style video site.

Serves videos from configurable folders (config.json): a YouTube-like home grid
with real ffmpeg thumbnails + durations, a watch page with an "Up next" rail +
autoplay + resume, live search, sort, and add/remove folders. mp4/webm/mov/m4v
stream directly with HTTP range (seeking); mkv/avi are remuxed to fragmented mp4
on the fly so they play in the browser too.
"""
import glob
import gzip
import hashlib
import json
import os
import queue
import random
import re
import subprocess
import threading
import time

from flask import (Flask, Response, abort, redirect, render_template, request,
                   send_file, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
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

BASE = os.environ.get("OTUBE_DATA") or os.path.dirname(os.path.abspath(__file__))
os.makedirs(BASE, exist_ok=True)
CONFIG_PATH = os.path.join(BASE, "config.json")
_KEY_PATH = os.path.join(BASE, "secret.key")
if not os.path.exists(_KEY_PATH):
    with open(_KEY_PATH, "wb") as _f:
        _f.write(os.urandom(32))
with open(_KEY_PATH, "rb") as _f:
    app.secret_key = _f.read()
PLAYLISTS_PATH = os.path.join(BASE, "playlists.json")  # [{id, name, items: [vid, ...]}]
DUR_CACHE_PATH = os.path.join(BASE, "duration_cache.json")  # persisted path -> [secs, mtime]
THUMB_DIR = os.path.join(BASE, "thumbs")
TRANS_DIR = os.path.join(BASE, "transcoded")  # cached browser-playable mp4s for mkv/avi/etc.
AUTO_SUBS_DIR = os.path.join(BASE, "subs")  # speech-to-text captions: <vid>.vtt + <vid>.json
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(TRANS_DIR, exist_ok=True)
os.makedirs(AUTO_SUBS_DIR, exist_ok=True)

# All the video containers we recognise. Browser-native ones stream directly
# (seekable); everything else is remuxed/transcoded to mp4 on the fly.
NATIVE_EXTS = {".mp4", ".m4v", ".webm", ".mov", ".ogv", ".ogg"}
NONNATIVE_EXTS = {
    ".mkv", ".avi", ".ts", ".mts", ".m2ts", ".m2t", ".flv", ".f4v", ".wmv",
    ".asf", ".mpg", ".mpeg", ".mpe", ".m2v", ".mp2", ".vob", ".dat", ".3gp",
    ".3g2", ".ogm", ".divx", ".rm", ".rmvb", ".mxf", ".qt", ".dv", ".y4m",
}
VIDEO_EXTS = NATIVE_EXTS | NONNATIVE_EXTS
FFMPEG, FFPROBE = "ffmpeg", "ffprobe"

_rec_cache: dict[str, dict] = {}            # id -> record
_dur_cache: dict[str, tuple] = {}           # path -> (seconds, mtime)
_dur_dirty = False                          # unsaved duration-cache entries?


def _load_dur_cache() -> None:
    try:
        with open(DUR_CACHE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            _dur_cache[k] = (float(v[0]), float(v[1]))
    except Exception:
        pass


def _save_dur_cache() -> None:
    try:
        with open(DUR_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump({k: [v[0], v[1]] for k, v in _dur_cache.items()}, f)
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


def probe_vcodec(path: str) -> str:
    """The video stream's codec (e.g. 'h264', 'hevc', 'mpeg2video'), or '' on error."""
    try:
        return subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name",
             "-of", "default=nokey=1:noprint_wrappers=1", path],
            capture_output=True, text=True, timeout=20).stdout.strip().lower()
    except Exception:
        return ""


def probe_codecs(path: str) -> tuple[str, str]:
    """(video_codec, audio_codec) lowercased, e.g. ('vp9','opus'); '' on error."""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,codec_name",
             "-of", "csv=p=0", path], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ("", "")
    v = a = ""
    for line in out.splitlines():
        toks = [t.strip().lower() for t in line.split(",") if t.strip()]
        if "video" in toks:
            v = v or next((t for t in toks if t != "video"), "")
        elif "audio" in toks:
            a = a or next((t for t in toks if t != "audio"), "")
    return (v, a)


def cached_duration(path: str, mtime: float) -> float | None:
    """Duration from the cache if the file is unchanged, else None (no I/O)."""
    c = _dur_cache.get(path)
    return c[0] if c and abs(c[1] - mtime) < 1 else None


def probe_duration(path: str, mtime: float | None = None) -> float:
    global _dur_dirty
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
    _dur_dirty = True
    return val


# Walking the folders is the slow part (thousands of files over Docker's
# Windows file sharing), so it never happens on a page load: the result is kept
# in memory, refreshed in the background every SCAN_TTL seconds, and right away
# after anything that changes the library (add/remove folder, rename, delete).
SCAN_TTL = 60
_scan_state = {"items": None, "at": 0.0, "running": False}
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
    _wake_warmer()


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


# ---- Background warmer: fills in durations + thumbnails so pages never wait ----
_warm_event = threading.Event()
_warmer = None


def _wake_warmer() -> None:
    global _warmer
    _warm_event.set()
    if _warmer is None or not _warmer.is_alive():
        _warmer = threading.Thread(target=_warm_loop, daemon=True)
        _warmer.start()


def _warm_loop() -> None:
    global _dur_dirty
    while True:
        _warm_event.wait()
        _warm_event.clear()
        items = list(_scan_state["items"] or [])
        # durations first (they show on every card), then thumbnails
        n = 0
        for r in items:
            if cached_duration(r["path"], r["mtime"]) is None:
                probe_duration(r["path"], r["mtime"])
                n += 1
                if n % 25 == 0:
                    _save_dur_cache(); _dur_dirty = False
        if _dur_dirty:
            _save_dur_cache(); _dur_dirty = False
        for r in items:
            if _warm_event.is_set():
                break  # library changed: start over with the new list
            if not os.path.exists(os.path.join(THUMB_DIR, r["id"] + ".jpg")):
                make_thumb(r)


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


@app.route("/")
def index():
    cfg = load_config()
    pls = load_playlists()
    all_items = scan()
    videos = visible(enrich(all_items))
    folder = request.args.get("folder", "")
    sub = request.args.get("sub", "").strip("/")
    sort = request.args.get("sort") or get_settings()["sort"]
    playlist = find_playlist(pls, request.args.get("playlist", ""))
    if playlist:
        # Playlist order is the user's order; skip items whose file is gone.
        by_id = {v["id"]: v for v in videos}
        videos = [by_id[i] for i in playlist["items"] if i in by_id]
        folder = ""
    else:
        if folder:
            videos = [v for v in videos if v["folder"] == folder]
        else:
            videos = unique(videos)  # overlapping folders (a folder + its subfolder) list a file twice
        if folder and sub:
            videos = [v for v in videos if v["subdir"] == sub or v["subdir"].startswith(sub + "/")]
        keys = {
            "name": (lambda v: v["title"].lower(), False),
            "newest": (lambda v: v["mtime"], True),
            "size": (lambda v: v["size"], True),
            "duration": (lambda v: v["dur_secs"], True),
        }
        if sort == "random":
            # Seeded so the order is stable across reloads/back; a new seed reshuffles.
            seed = request.args.get("seed") or str(random.randrange(1, 10**6))
            videos.sort(key=lambda v: v["id"])
            random.Random(seed).shuffle(videos)
        else:
            keyfn, rev = keys.get(sort, keys["name"])
            videos.sort(key=keyfn, reverse=rev)
    hidden = private_folders(cfg)
    unlocked = private_unlocked()
    folders = [f for f in cfg.get("video_folders", []) if folder_shown(f, hidden)]
    return render_template("index.html", videos=videos, folders=folders,
                           current_folder=folder, current_sub=sub, sort=sort,
                           folder_trees=(trees := folder_trees(all_items, folders)),
                           total_count=len({v["id"] for v in all_items if v["folder"] in trees}),
                           sub_level=sub_level(trees, folder, sub),
                           playlists=pls, playlist=playlist,
                           private_on=unlocked, private_set=hidden, has_pin=bool(cfg.get("pin_hash")))


@app.route("/watch/<vid>")
def watch(vid):
    r = get_rec(vid)
    if not allowed(r):
        abort(404)
    everything = unique(visible(enrich(scan())))
    playlist = find_playlist(load_playlists(), request.args.get("list", ""))
    shuffle_seed = request.args.get("shuffle", "") if playlist else ""
    if playlist and vid in playlist["items"]:
        # Playing a playlist: the rail shows the whole list, "next" is the following item.
        by_id = {v["id"]: v for v in everything}
        items = [by_id[i] for i in playlist["items"] if i in by_id]
        if shuffle_seed:
            random.Random(shuffle_seed).shuffle(items)
        pos = next((n for n, v in enumerate(items) if v["id"] == vid), 0)
        upnext = items
        next_vid = items[pos + 1]["id"] if pos + 1 < len(items) else None
        prev_vid = items[pos - 1]["id"] if pos > 0 else None
    else:
        playlist, pos, prev_vid = None, 0, None
        same = [v for v in everything if v["folder"] == r["folder"] and v["id"] != vid]
        other = [v for v in everything if v["folder"] != r["folder"]]
        upnext = same + other
        next_vid = upnext[0]["id"] if upnext else None
    probe_duration(r["path"], r["mtime"])  # just this one file, if not cached yet
    cur = next((v for v in enrich([r])), None)
    next_item = next((v for v in upnext if v["id"] == next_vid), None)
    liked = find_playlist(load_playlists(), LIKED_ID)
    cfg = load_config()
    if request.args.get("embed"):
        # Player-only page used inside the mini player (keeps list/shuffle + embed on next/prev)
        lq = ("?list=" + playlist["id"] + ("&shuffle=" + shuffle_seed if shuffle_seed else "")) if playlist else ""
        return render_template("embed.html", video=cur, next_vid=next_vid, next_item=next_item,
                               prev_vid=prev_vid, eq=(lq + "&embed=1") if lq else "?embed=1")
    return render_template("watch.html", video=cur, upnext=upnext, next_vid=next_vid,
                           next_item=next_item, prev_vid=prev_vid,
                           playlist=playlist, pos=pos, shuffle_seed=shuffle_seed,
                           lq=("?list=" + playlist["id"] + ("&shuffle=" + shuffle_seed if shuffle_seed else "")) if playlist else "",
                           folder_count=sum(1 for v in everything if v["folder"] == r["folder"]),
                           liked=bool(liked and vid in liked["items"]),
                           private_on=private_unlocked(), has_pin=bool(cfg.get("pin_hash")),
                           # sidebar drawer
                           folders=[f for f in cfg.get("video_folders", [])
                                    if folder_shown(f, private_folders(cfg))],
                           playlists=load_playlists(), private_set=private_folders(cfg),
                           current_folder="", current_sub="", sort="name",
                           folder_trees=folder_trees(scan(), cfg.get("video_folders", [])))


def _live_stream(r: dict) -> Response:
    """Fallback: pipe a live fragmented mp4 (plays immediately, but the browser
    can't know the total duration / seek well). Used only when we can't quickly
    produce a cached file."""
    vcodec = probe_vcodec(r["path"])
    vargs = ["-c:v", "copy"] if vcodec == "h264" else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]

    def generate():
        cmd = ([FFMPEG, "-i", r["path"]] + vargs +
               ["-c:a", "aac", "-b:a", "160k",
                "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                "-f", "mp4", "pipe:1"])
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                proc.kill()
            except Exception:
                pass
    return Response(generate(), mimetype="video/mp4")


@app.route("/stream/<vid>")
def stream(vid):
    r = get_rec(vid)
    if not allowed(r):
        abort(404)
    if r["native"]:
        # Direct stream with HTTP Range support (seeking works).
        return send_file(r["path"], conditional=True)

    # Non-native (mkv/ts/avi/…): serve a CACHED real mp4 so the player gets the
    # correct total duration AND can seek. First play builds the cache; later plays
    # are instant. For H.264 we copy the video (fast) + re-encode audio to AAC; for
    # other codecs we transcode once. The '+faststart' moov carries the duration.
    v, a = probe_codecs(r["path"])
    if v == "h264":
        # Already H.264 → remux to mp4 (copy video, AAC audio). Fast.
        ext, fmt = "mp4", "mp4"
        args = ["-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]
    elif v in ("vp9", "vp8", "av1"):
        # WebM-native codecs → remux to WebM (copy, instant, no quality loss).
        aargs = ["-c:a", "copy"] if a in ("opus", "vorbis") else ["-c:a", "libopus"]
        ext, fmt = "webm", "webm"
        args = ["-c:v", "copy"] + aargs
    else:
        # Anything else (mpeg2/vc1/wmv/…) → transcode to H.264/mp4 once.
        ext, fmt = "mp4", "mp4"
        args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"]

    cache = os.path.join(TRANS_DIR, vid + "." + ext)
    if not (os.path.exists(cache) and os.path.getsize(cache) > 0):
        tmp = os.path.join(TRANS_DIR, vid + ".part." + ext)
        cmd = [FFMPEG, "-y", "-i", r["path"]] + args + ["-f", fmt, tmp]
        try:
            subprocess.run(cmd, capture_output=True, timeout=3600)
            if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, cache)
            else:
                return _live_stream(r)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            return _live_stream(r)
    return send_file(cache, conditional=True)


# ---- Subtitles / captions ----
# Sidecar files next to the video (movie.srt, movie.en.vtt, ...) and text subtitle
# streams embedded in the container (mkv etc.) are offered as WebVTT tracks.
SUB_EXTS = (".vtt", ".srt", ".ass", ".ssa")
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
_subs_cache: dict[str, tuple] = {}  # vid -> (mtime, [tracks])


def list_subs(r: dict) -> list[dict]:
    """[{key, label, lang}] for every subtitle we can serve for this video."""
    return _file_subs(r) + _auto_sub(r["id"])


def _auto_sub(vid: str) -> list[dict]:
    vtt = os.path.join(AUTO_SUBS_DIR, vid + ".vtt")
    if not os.path.exists(vtt):
        return []
    try:
        with open(os.path.join(AUTO_SUBS_DIR, vid + ".json"), encoding="utf-8") as f:
            lang = json.load(f).get("lang", "")
    except Exception:
        lang = ""
    name = LANG_NAMES.get(lang, lang.upper())
    tracks = [{"key": "auto", "label": f"{name} (auto-generated)" if name else "Auto-generated",
               "lang": lang, "src": vtt, "auto": True}]
    for f in sorted(glob.glob(os.path.join(AUTO_SUBS_DIR, glob.escape(vid) + ".tr-*.vtt"))):
        tl = os.path.basename(f)[len(vid) + 4:-4]
        tracks.append({"key": f"tr-{tl}", "label": f"{LANG_NAMES.get(tl, tl)} (auto-translated)",
                       "lang": tl, "src": f, "auto": True})
    return tracks


def _file_subs(r: dict) -> list[dict]:
    cached = _subs_cache.get(r["id"])
    if cached and cached[0] == r["mtime"]:
        return cached[1]
    tracks = []
    stem = os.path.splitext(r["path"])[0]
    sidecars = sorted(f for f in glob.glob(glob.escape(stem) + ".*")
                      if f.lower().endswith(SUB_EXTS))
    for n, f in enumerate(sidecars):
        mid = os.path.basename(f)[len(os.path.basename(stem)):].strip(".")
        mid = os.path.splitext(mid)[0]  # "en.srt" -> "en"
        tracks.append({"key": f"f{n}", "label": mid or "Subtitles", "lang": mid[:3] if mid else "",
                       "src": f})
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "s",
             "-show_entries", "stream=index,codec_name:stream_tags=language,title",
             "-of", "json", r["path"]], capture_output=True, text=True, timeout=20).stdout
        for st in json.loads(out or "{}").get("streams", []):
            if st.get("codec_name") not in TEXT_SUB_CODECS:
                continue  # image-based subs (PGS/DVD) can't become WebVTT
            tags = st.get("tags", {})
            lang = tags.get("language", "")
            label = tags.get("title") or (lang.upper() if lang and lang != "und" else "") or f"Track {len(tracks) + 1}"
            tracks.append({"key": f"s{st['index']}", "label": label, "lang": lang, "stream": st["index"]})
    except Exception:
        pass
    _subs_cache[r["id"]] = (r["mtime"], tracks)
    return tracks


@app.route("/subs/<vid>")
def subs_list(vid):
    r = get_rec(vid)
    if not allowed(r):
        abort(404)
    tracks = list_subs(r)
    return {"tracks": [{"key": t["key"], "label": t["label"], "lang": t["lang"],
                        "src": url_for("subs_file", vid=vid, key=t["key"])}
                       for t in tracks],
            "auto": _job_status(vid, has_auto=any(t["key"] == "auto" for t in tracks)),
            "source_lang": next((t["lang"] for t in tracks if t["key"] == "auto"), None),
            "translate": [{"lang": l, "name": LANG_NAMES.get(l, l),
                           **_job_status(f"{vid}:{l}", any(t["key"] == f"tr-{l}" for t in tracks))}
                          for l in TRANSLATE_LANGS]}


@app.route("/subs/<vid>/<key>.vtt")
def subs_file(vid, key):
    r = get_rec(vid)
    if not r:
        abort(404)
    t = next((t for t in list_subs(r) if t["key"] == key), None)
    if not t:
        abort(404)
    if t.get("auto"):
        return send_file(t["src"], mimetype="text/vtt", max_age=0)
    out = os.path.join(TRANS_DIR, f"{vid}.{key}.vtt")
    if not (os.path.exists(out) and os.path.getsize(out) > 0):
        if "src" in t and t["src"].lower().endswith(".vtt"):
            return send_file(t["src"], mimetype="text/vtt")
        cmd = [FFMPEG, "-y", "-i", t.get("src") or r["path"]]
        if "stream" in t:
            cmd += ["-map", f"0:{t['stream']}"]
        cmd += ["-f", "webvtt", out]
        try:
            subprocess.run(cmd, capture_output=True, timeout=120)
        except Exception:
            pass
        if not os.path.exists(out):
            abort(404)
    return send_file(out, mimetype="text/vtt")


# ---- Auto-generated captions (speech-to-text with faster-whisper) ----
# One background worker, one video at a time, so the CPU isn't swamped.
WHISPER_MODEL = os.environ.get("OTUBE_WHISPER_MODEL", "large-v3-turbo")
CUE_MAX_SECS, CUE_MAX_CHARS = 5.0, 42  # keep each caption short and readable
_jobs: dict[str, dict] = {}       # job key ("<vid>" or "<vid>:<lang>") -> {state, progress, error}
_job_q: "queue.Queue[str]" = queue.Queue()
_job_lock = threading.Lock()
_worker = None
_model = None
_model_name = [None]


def _job_status(vid: str, has_auto: bool) -> dict:
    j = _jobs.get(vid)
    if j:
        return dict(j)
    return {"state": "done" if has_auto else "none", "progress": 1 if has_auto else 0}


def _vtt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"


def _split_cues(seg) -> list[tuple]:
    """Break a Whisper segment into cues of at most CUE_MAX_SECS / CUE_MAX_CHARS,
    using word timings so each cue is on screen only while it's being sung/said."""
    words = [w for w in (seg.words or []) if w.word.strip()]
    if not words:
        text = seg.text.strip()
        return [(seg.start, seg.end, text)] if text else []
    cues, cur = [], []
    for w in words:
        if cur:
            text = "".join(x.word for x in cur + [w]).strip()
            if w.end - cur[0].start > CUE_MAX_SECS or len(text) > CUE_MAX_CHARS:
                cues.append(cur)
                cur = []
        cur.append(w)
    if cur:
        cues.append(cur)
    return [(c[0].start, c[-1].end, "".join(x.word for x in c).strip()) for c in cues]


def _transcribe(vid: str) -> None:
    global _model
    r = get_rec(vid)
    if not r:
        raise RuntimeError("video not found")
    want = get_settings()["whisperModel"]
    if _model is None or _model_name[0] != want:
        from faster_whisper import WhisperModel  # heavy import; only when first needed
        _model = WhisperModel(want, device="cpu", compute_type="int8",
                              download_root=os.path.join(BASE, "models"))
        _model_name[0] = want
    # Decode with our own ffmpeg (16 kHz mono PCM) instead of faster-whisper's PyAV
    # path: handles every container we play and avoids PyAV version breakage.
    import numpy as np
    pcm = subprocess.run([FFMPEG, "-nostdin", "-v", "error", "-i", r["path"], "-vn",
                          "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"],
                         capture_output=True, timeout=1800).stdout
    if not pcm:
        raise RuntimeError("no audio track")
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    segments, info = _model.transcribe(
        audio, beam_size=5,
        # No VAD: Silero VAD treats singing over music as "not speech" and drops
        # most of a song. hallucination_silence_threshold handles real gaps instead.
        vad_filter=False,
        # Each window decoded on its own: stops one bad line repeating through a song.
        condition_on_previous_text=False,
        word_timestamps=True,                 # lets us split long segments into short cues
        hallucination_silence_threshold=2.0,  # drop text "heard" in long music-only gaps
        no_speech_threshold=0.6)
    total = info.duration or 0
    lines = ["WEBVTT", ""]
    last_text = None
    for seg in segments:
        for start, end, text in _split_cues(seg):
            if text == last_text:  # collapse back-to-back duplicates
                continue
            last_text = text
            lines += [f"{_vtt_time(start)} --> {_vtt_time(end)}", text, ""]
        if total:
            _jobs[vid]["progress"] = min(0.99, seg.end / total)
    tmp = os.path.join(AUTO_SUBS_DIR, vid + ".part.vtt")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(AUTO_SUBS_DIR, vid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"lang": info.language, "model": _model_name[0], "path": r["path"]}, f)
    os.replace(tmp, os.path.join(AUTO_SUBS_DIR, vid + ".vtt"))


def _caption_worker() -> None:
    while True:
        key = _job_q.get()
        vid, _, lang = key.partition(":")
        _jobs[key] = {"state": "running", "progress": 0}
        try:
            if lang:
                if not os.path.exists(os.path.join(AUTO_SUBS_DIR, vid + ".vtt")):
                    _jobs[vid] = _jobs[key]  # transcribe first, sharing progress
                    _transcribe(vid)
                    _jobs.pop(vid, None)
                _translate(vid, lang, key)
            else:
                _transcribe(vid)
            _jobs.pop(key, None)  # file on disk now reports "done"
        except Exception as exc:  # noqa: BLE001
            _jobs.pop(vid, None) if lang else None
            _jobs[key] = {"state": "error", "progress": 0, "error": str(exc)[:300]}


# ---- Caption translation (Meta NLLB-200 via CTranslate2, fully offline after download) ----
NLLB_REPO = os.environ.get("OTUBE_TRANSLATE_MODEL", "JustFrederik/nllb-200-distilled-600M-ct2-int8")
TRANSLATE_LANGS = [c.strip() for c in os.environ.get("OTUBE_TRANSLATE_LANGS", "en,ar").split(",") if c.strip()]
NLLB_CODES = {  # Whisper language code -> NLLB code
    "en": "eng_Latn", "ar": "arb_Arab", "fr": "fra_Latn", "es": "spa_Latn", "de": "deu_Latn",
    "it": "ita_Latn", "pt": "por_Latn", "tr": "tur_Latn", "ru": "rus_Cyrl", "fa": "pes_Arab",
    "ur": "urd_Arab", "hi": "hin_Deva", "bn": "ben_Beng", "id": "ind_Latn", "ms": "zsm_Latn",
    "zh": "zho_Hans", "ja": "jpn_Jpan", "ko": "kor_Hang", "nl": "nld_Latn", "sv": "swe_Latn",
    "pl": "pol_Latn", "uk": "ukr_Cyrl", "he": "heb_Hebr", "el": "ell_Grek", "sw": "swh_Latn",
}
LANG_NAMES = {
    "en": "English", "ar": "Arabic", "fr": "French", "es": "Spanish", "de": "German",
    "it": "Italian", "pt": "Portuguese", "tr": "Turkish", "ru": "Russian", "fa": "Persian",
    "ur": "Urdu", "hi": "Hindi", "bn": "Bengali", "id": "Indonesian", "ms": "Malay",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "nl": "Dutch", "sv": "Swedish",
    "pl": "Polish", "uk": "Ukrainian", "he": "Hebrew", "el": "Greek", "sw": "Swahili",
}
_mt = None  # (translator, sentencepiece)


def _read_vtt(path: str) -> list[tuple]:
    cues, lines = [], open(path, encoding="utf-8").read().split("\n")
    i = 0
    while i < len(lines):
        if "-->" in lines[i]:
            timing, text = lines[i].strip(), []
            i += 1
            while i < len(lines) and lines[i].strip():
                text.append(lines[i].strip())
                i += 1
            cues.append((timing, " ".join(text)))
        i += 1
    return cues


def _translate(vid: str, lang: str, key: str) -> None:
    global _mt
    with open(os.path.join(AUTO_SUBS_DIR, vid + ".json"), encoding="utf-8") as f:
        src = json.load(f).get("lang", "")
    if src not in NLLB_CODES or lang not in NLLB_CODES:
        raise RuntimeError(f"can't translate {src or '?'} -> {lang}")
    if _mt is None:
        import ctranslate2
        import sentencepiece
        from huggingface_hub import snapshot_download
        d = snapshot_download(NLLB_REPO, cache_dir=os.path.join(BASE, "models"))
        _mt = (ctranslate2.Translator(d, device="cpu", compute_type="int8"),
               sentencepiece.SentencePieceProcessor(model_file=os.path.join(d, "sentencepiece.bpe.model")))
    translator, sp = _mt
    cues = _read_vtt(os.path.join(AUTO_SUBS_DIR, vid + ".vtt"))
    out, batch = ["WEBVTT", ""], 16
    for b in range(0, len(cues), batch):
        chunk = cues[b:b + batch]
        if src == lang:
            texts = [t for _, t in chunk]
        else:
            sources = [[NLLB_CODES[src]] + sp.encode(t, out_type=str) + ["</s>"] for _, t in chunk]
            res = translator.translate_batch(sources, target_prefix=[[NLLB_CODES[lang]]] * len(sources),
                                             beam_size=4, max_decoding_length=128)
            texts = [sp.decode(r.hypotheses[0][1:]) for r in res]
        for (timing, _), text in zip(chunk, texts):
            # NLLB leaves spaces before punctuation/contractions ("Hi , I 'll go .")
            text = re.sub(r"\s+([.,!?;:%)\]])", r"\1", text.strip())
            text = re.sub(r"(\w)\s+'(\w)", r"\1'\2", text)
            out += [timing, text, ""]
        _jobs[key]["progress"] = min(0.99, (b + batch) / max(1, len(cues)))
    tmp = os.path.join(AUTO_SUBS_DIR, f"{vid}.tr-{lang}.part")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    os.replace(tmp, os.path.join(AUTO_SUBS_DIR, f"{vid}.tr-{lang}.vtt"))


def _enqueue(key: str) -> dict:
    global _worker
    with _job_lock:
        if _jobs.get(key, {}).get("state") not in ("queued", "running"):
            _jobs[key] = {"state": "queued", "progress": 0}
            _job_q.put(key)
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_caption_worker, daemon=True)
            _worker.start()
    return dict(_jobs[key])


@app.route("/subs/<vid>/translate/<lang>", methods=["POST"])
def subs_translate(vid, lang):
    if not get_rec(vid):
        return {"ok": False, "error": "not found"}, 404
    if lang not in NLLB_CODES:
        return {"ok": False, "error": "unsupported language"}, 400
    return {"ok": True, **_enqueue(f"{vid}:{lang}")}


@app.route("/subs/<vid>/generate", methods=["POST"])
def subs_generate(vid):
    if not get_rec(vid):
        return {"ok": False, "error": "not found"}, 404
    return {"ok": True, **_enqueue(vid)}


@app.route("/subs/<vid>/status")
def subs_status(vid):
    return _job_status(vid, os.path.exists(os.path.join(AUTO_SUBS_DIR, vid + ".vtt")))


def make_thumb(r: dict) -> str | None:
    """Create (once) and return the thumbnail path for a video record."""
    tp = os.path.join(THUMB_DIR, r["id"] + ".jpg")
    if os.path.exists(tp) and os.path.getsize(tp) > 0:
        return tp
    for ss in ("00:00:05", "00:00:01"):
        try:
            subprocess.run(
                [FFMPEG, "-ss", ss, "-i", r["path"], "-frames:v", "1",
                 "-vf", "scale=480:-1", "-y", tp],
                capture_output=True, timeout=45)
            if os.path.exists(tp) and os.path.getsize(tp) > 0:
                return tp
        except Exception:
            pass
    return None


@app.route("/thumb/<vid>")
def thumb(vid):
    r = get_rec(vid)
    if not allowed(r):
        abort(404)
    tp = make_thumb(r)
    if tp:
        # thumbnails never change for a given id -> let the browser keep them
        return send_file(tp, mimetype="image/jpeg", max_age=7 * 86400)
    abort(404)
    tp = os.path.join(THUMB_DIR, vid + ".jpg")
    if not os.path.exists(tp):
        for ss in ("00:00:05", "00:00:01"):
            try:
                subprocess.run(
                    [FFMPEG, "-ss", ss, "-i", r["path"], "-frames:v", "1",
                     "-vf", "scale=480:-1", "-y", tp],
                    capture_output=True, timeout=45)
                if os.path.exists(tp) and os.path.getsize(tp) > 0:
                    break
            except Exception:
                pass
    if os.path.exists(tp) and os.path.getsize(tp) > 0:
        return send_file(tp, mimetype="image/jpeg")
    abort(404)


@app.route("/browse")
def browse():
    """Server-side folder browser — lists drives (at the root) or the subfolders of
    a path, so the UI can navigate the real filesystem (the browser can't expose
    paths itself). Also reports how many videos sit directly in a folder."""
    import string
    path = request.args.get("path", "")
    if HOST_ROOT:
        return _browse_host(path)
    if not path:
        if os.name == "nt":
            drives = [f"{d}:/" for d in string.ascii_uppercase if os.path.exists(f"{d}:/")]
            return {"path": "", "parent": None, "is_root": True,
                    "dirs": [{"name": d, "path": d} for d in drives], "video_count": 0}
        path = "/"
    path = os.path.abspath(path)
    dirs = []
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                        dirs.append({"name": e.name, "path": os.path.join(path, e.name).replace("\\", "/")})
                except OSError:
                    continue
    except (OSError, PermissionError) as exc:
        return {"path": path.replace("\\", "/"), "parent": "", "dirs": [], "error": str(exc), "video_count": 0}
    dirs.sort(key=lambda x: x["name"].lower())
    vids = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.is_file() and os.path.splitext(e.name)[1].lower() in VIDEO_EXTS:
                    vids += 1
    except OSError:
        pass
    stripped = path.rstrip("/\\")
    parent = os.path.dirname(stripped)
    if parent == stripped or (os.name == "nt" and len(stripped) <= 2):
        parent = ""  # back to the drive list
    return {"path": path.replace("\\", "/"), "parent": parent.replace("\\", "/"),
            "is_root": False, "dirs": dirs, "video_count": vids}


def _browse_host(path: str):
    """Folder browser over the mounted host drives, speaking Windows paths."""
    if not path:
        drives = sorted(d for d in os.listdir(HOST_ROOT) if os.path.isdir(os.path.join(HOST_ROOT, d)))
        return {"path": "", "parent": None, "is_root": True, "video_count": 0,
                "dirs": [{"name": f"{d.upper()}:/", "path": f"{d.upper()}:/"} for d in drives]}
    local = to_local(path)
    dirs, vids = [], 0
    try:
        with os.scandir(local) as it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if not e.name.startswith(".") and not e.name.startswith("$"):
                            dirs.append({"name": e.name, "path": to_host(os.path.join(local, e.name))})
                    elif os.path.splitext(e.name)[1].lower() in VIDEO_EXTS:
                        vids += 1
                except OSError:
                    continue
    except OSError as exc:
        return {"path": to_host(local), "parent": "", "dirs": [], "error": str(exc), "video_count": 0}
    dirs.sort(key=lambda x: x["name"].lower())
    parent_local = os.path.dirname(local)
    parent = "" if parent_local in (HOST_ROOT, os.path.dirname(HOST_ROOT)) else to_host(parent_local)
    return {"path": to_host(local), "parent": parent, "is_root": False, "dirs": dirs, "video_count": vids}


PIN_RE = re.compile(r"^\d{4,8}$")
PIN_MAX_TRIES, PIN_LOCKOUT_SECS = 5, 30
_pin_fails: dict[str, list] = {}  # client ip -> [failures, locked_until]


@app.after_request
def compress(resp):
    """gzip text responses (the home page HTML is large with big libraries)."""
    if (resp.direct_passthrough or resp.status_code != 200 or "gzip" not in request.headers.get("Accept-Encoding", "")
            or resp.mimetype not in ("text/html", "application/json", "text/css", "application/javascript", "text/vtt")
            or resp.headers.get("Content-Encoding")):
        return resp
    data = resp.get_data()
    if len(data) < 1024:
        return resp
    resp.set_data(gzip.compress(data, compresslevel=5))
    resp.headers["Content-Encoding"] = "gzip"
    resp.headers["Content-Length"] = str(len(resp.get_data()))
    resp.headers["Vary"] = "Accept-Encoding"
    return resp


@app.context_processor
def inject_settings():
    return {"app_settings": get_settings()}


@app.route("/settings")
def settings_page():
    cfg = load_config()
    model_opts = [(m, m + (" (recommended)" if m == "large-v3-turbo" else "")) for m in WHISPER_MODELS]
    return render_template("settings.html", s=get_settings(), defaults=SETTINGS_DEFAULTS, model_opts=model_opts,
                           SORT_IC=SORT_ICONS,
                           whisper_models=WHISPER_MODELS, stats=library_stats(),
                           private_on=private_unlocked(), has_pin=bool(cfg.get("pin_hash")),
                           lan_url=lan_url())


def _dir_size(path: str) -> int:
    total = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def library_stats() -> dict:
    items = visible(scan())
    cfg = load_config()
    shown = [f for f in cfg.get("video_folders", []) if folder_shown(f, private_folders(cfg))]
    return {
        "videos": len({v["id"] for v in items}),
        "folders": len(shown),
        "size": fmt_size(sum(v["size"] for v in {v["id"]: v for v in items}.values())),
        "thumbs": len(os.listdir(THUMB_DIR)),
        "captions": len(glob.glob(os.path.join(AUTO_SUBS_DIR, "*.vtt"))),
        "cache": fmt_size(_dir_size(TRANS_DIR) + _dir_size(THUMB_DIR)),
        "models": fmt_size(_dir_size(os.path.join(BASE, "models"))),
        "scanned": rel_time(_scan_state["at"]) if _scan_state["at"] else "never",
    }


def lan_url() -> str:
    """Address other devices on the network can use. The launcher passes the PC's
    Wi-Fi/LAN IP in OTUBE_LAN_URL; if you opened oTube by IP we use that."""
    host = request.host.split(":")[0]
    if host not in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return request.host_url.rstrip("/")
    return os.environ.get("OTUBE_LAN_URL", "").rstrip("/")


@app.route("/qr.svg")
def qr_svg():
    """QR code (SVG) for a URL, used by Settings → Devices."""
    import io
    import qrcode
    import qrcode.image.svg
    text = request.args.get("text", "")[:300]
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return Response(buf.getvalue(), mimetype="image/svg+xml", headers={"Cache-Control": "max-age=3600"})


@app.route("/manifest.webmanifest")
def manifest():
    """Lets phones 'Add to Home screen' and open oTube full-screen like an app."""
    return Response(json.dumps({
        "name": "oTube", "short_name": "oTube", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#0f0f0f", "theme_color": "#0f0f0f",
        "icons": [{"src": url_for("static", filename="favicon.png"), "sizes": "256x256", "type": "image/png", "purpose": "any maskable"}],
    }), mimetype="application/manifest+json")


@app.route("/api/rescan", methods=["POST"])
def api_rescan():
    rescan_now()
    return {"ok": True, "stats": library_stats()}


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "GET":
        return get_settings()
    data = request.get_json(silent=True) or {}
    cfg = load_config()
    cur = cfg.setdefault("settings", {})
    if data.get("reset"):
        cfg["settings"] = {}
    else:
        for k, v in data.items():
            if k not in SETTINGS_DEFAULTS:
                continue
            d = SETTINGS_DEFAULTS[k]
            try:  # coerce to the default's type; ignore junk
                if isinstance(d, bool):
                    v = bool(v)
                elif isinstance(d, (int, float)) and not isinstance(d, bool):
                    v = type(d)(v) if not isinstance(d, int) or float(v).is_integer() else float(v)
                elif isinstance(d, dict):
                    v = {kk: v.get(kk, dv) for kk, dv in d.items()} if isinstance(v, dict) else d
                else:
                    v = str(v)
            except (TypeError, ValueError):
                continue
            if k == "sort" and v not in ("name", "newest", "size", "duration", "random"):
                continue
            if k == "view" and v not in ("grid", "list"):
                continue
            if k == "whisperModel" and v not in WHISPER_MODELS:
                continue
            cur[k] = v
    save_config(cfg)
    return {"ok": True, "settings": get_settings()}


@app.template_filter("host_path")
def host_path_filter(folder: str) -> str:
    """Configured folder -> its real path on the PC (for Copy path / Show in Explorer)."""
    return to_host(to_local(folder))


@app.route("/folder_play")
def folder_play():
    """Play all / Shuffle play for a folder (or subfolder): jump to its first
    (or a random) video; Up next continues through the same folder."""
    folder = request.args.get("folder", "")
    sub = request.args.get("sub", "").strip("/")
    vids = [v for v in visible(scan()) if v["folder"] == folder and
            (not sub or v["subdir"] == sub or v["subdir"].startswith(sub + "/"))]
    if not vids:
        return redirect(url_for("index", folder=folder))
    if request.args.get("shuffle"):
        pick = random.choice(vids)
    else:
        pick = min(vids, key=lambda v: (v["subdir"].lower(), v["title"].lower()))
    return redirect(url_for("watch", vid=pick["id"]))


@app.route("/playlist_play")
def playlist_play():
    """Play all / Shuffle play for a playlist."""
    pl = find_playlist(load_playlists(), request.args.get("pid", ""))
    if not pl:
        return redirect(url_for("index"))
    ok = {v["id"] for v in visible(scan())}
    items = [i for i in pl["items"] if i in ok]
    if not items:
        return redirect(url_for("index", playlist=pl["id"]))
    if request.args.get("shuffle"):
        seed = str(random.randrange(1, 10**6))
        random.Random(seed).shuffle(items)  # same seeded order the watch page uses
        return redirect(url_for("watch", vid=items[0], list=pl["id"], shuffle=seed))
    return redirect(url_for("watch", vid=items[0], list=pl["id"]))


@app.route("/folder_move", methods=["POST"])
def folder_move():
    """Reorder the folder list (sidebar + chips)."""
    folder, d = request.form.get("folder_path", ""), request.form.get("dir", "")
    cfg = load_config()
    fl = cfg["video_folders"]
    priv = set(cfg.get("private_folders", []))
    shown = [f for f in fl if folder_shown(f, priv)]  # swap with the neighbour you can see
    if folder in shown:
        k = shown.index(folder)
        k2 = k - 1 if d == "up" else k + 1
        if 0 <= k2 < len(shown):
            i, j = fl.index(folder), fl.index(shown[k2])
            fl[i], fl[j] = fl[j], fl[i]
            save_config(cfg)
    return {"ok": True}


@app.route("/mode", methods=["POST"])
def set_mode():
    """Switch Family/Private mode for this browser session."""
    want = request.form.get("private") == "1"
    if want:
        cfg = load_config()
        pin_hash = cfg.get("pin_hash")
        if pin_hash:
            ip = request.remote_addr or "?"
            fails, until = _pin_fails.get(ip, [0, 0])
            wait = int(until - time.time())
            if wait > 0:
                return {"ok": False, "error": f"Too many tries. Wait {wait}s.", "wait": wait}, 429
            if not check_password_hash(pin_hash, request.form.get("pin", "")):
                fails += 1
                if fails >= PIN_MAX_TRIES:
                    _pin_fails[ip] = [0, time.time() + PIN_LOCKOUT_SECS]
                    return {"ok": False, "error": f"Too many tries. Wait {PIN_LOCKOUT_SECS}s.",
                            "wait": PIN_LOCKOUT_SECS}, 429
                _pin_fails[ip] = [fails, 0]
                left = PIN_MAX_TRIES - fails
                return {"ok": False, "error": f"Wrong PIN. {left} tr{'y' if left == 1 else 'ies'} left."}, 403
            _pin_fails.pop(ip, None)
        session["private"] = True
    else:
        session.pop("private", None)
    return {"ok": True, "private": want}


@app.route("/folder_private", methods=["POST"])
def folder_private():
    """Mark a folder private (moves it to Private mode) or public again.
    Hiding is allowed from Family mode; making public needs Private mode."""
    make_private = request.form.get("private") == "1"
    if not make_private and not private_unlocked():
        return {"ok": False, "error": "Switch to Private mode first"}, 403
    folder = request.form.get("folder_path", "")
    cfg = load_config()
    priv = cfg.setdefault("private_folders", [])
    if request.form.get("private") == "1":
        if folder in cfg["video_folders"] and folder not in priv:
            priv.append(folder)
    elif folder in priv:
        priv.remove(folder)
    save_config(cfg)
    return {"ok": True}


@app.route("/pin", methods=["POST"])
def set_pin():
    """Set or clear the PIN needed to enter Private mode (only from Private mode)."""
    if not private_unlocked():
        return {"ok": False, "error": "Switch to Private mode first"}, 403
    pin = (request.form.get("pin") or "").strip()
    if pin and not PIN_RE.match(pin):
        return {"ok": False, "error": "PIN must be 4 to 8 digits"}, 400
    cfg = load_config()
    if pin:
        cfg["pin_hash"] = generate_password_hash(pin)
    else:
        cfg.pop("pin_hash", None)
    save_config(cfg)
    return {"ok": True, "has_pin": bool(pin)}


@app.route("/add_folder", methods=["POST"])
def add_folder():
    path = (request.form.get("folder_path") or "").strip().strip('"').replace("\\", "/")
    if path:
        cfg = load_config()
        if path not in cfg["video_folders"]:
            cfg["video_folders"].append(path)
            if private_unlocked():  # added in Private mode -> belongs to Private mode
                cfg.setdefault("private_folders", []).append(path)
            save_config(cfg)
            rescan_now()
    return redirect(url_for("index"))


@app.route("/remove_folder", methods=["POST"])
def remove_folder():
    path = request.form.get("folder_path") or ""
    cfg = load_config()
    if path in cfg["video_folders"]:
        cfg["video_folders"].remove(path)
        if path in cfg.get("private_folders", []):
            cfg["private_folders"].remove(path)
        save_config(cfg)
        rescan_now()
    return redirect(url_for("index"))


def _clear_caches(vid: str) -> None:
    for p in (os.path.join(THUMB_DIR, vid + ".jpg"),
              os.path.join(TRANS_DIR, vid + ".mp4"),
              os.path.join(TRANS_DIR, vid + ".webm")):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    for p in (glob.glob(os.path.join(TRANS_DIR, glob.escape(vid) + ".*.vtt")) +
              glob.glob(os.path.join(AUTO_SUBS_DIR, glob.escape(vid) + ".*"))):
        try:
            os.remove(p)
        except OSError:
            pass
    _subs_cache.pop(vid, None)


@app.route("/reveal/<vid>", methods=["POST"])
def reveal(vid):
    """Open the file's folder in the OS file manager, selecting the file."""
    r = get_rec(vid)
    if not r:
        return {"ok": False, "error": "not found"}, 404
    if os.path.exists("/.dockerenv"):
        # A container can't open the host's file manager; the browser forwards
        # this to the on-PC helper (otube-helper.ps1) instead.
        return {"ok": True, "delegate": r["host_path"]}
    path = r["path"]
    try:
        import sys
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}, 500
    return {"ok": True}


@app.route("/open/<vid>", methods=["POST"])
def open_external(vid):
    """Open the video in the OS default media player."""
    r = get_rec(vid)
    if not r:
        return {"ok": False, "error": "not found"}, 404
    if os.path.exists("/.dockerenv"):
        return {"ok": True, "delegate": r["host_path"]}
    try:
        import sys
        if sys.platform.startswith("win"):
            os.startfile(r["path"])  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", r["path"]])
        else:
            subprocess.Popen(["xdg-open", r["path"]])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}, 500
    return {"ok": True}


@app.route("/rename/<vid>", methods=["POST"])
def rename(vid):
    """Rename the file on disk (same folder, extension preserved)."""
    r = get_rec(vid)
    if not r:
        return {"ok": False, "error": "not found"}, 404
    new = (request.form.get("name") or "").strip()
    if not new:
        return {"ok": False, "error": "empty name"}, 400
    # Strip path separators + keep the original extension.
    new = os.path.basename(new).replace("/", "_").replace("\\", "_")
    if not new.lower().endswith(r["ext"]):
        new += r["ext"]
    dest = os.path.join(os.path.dirname(r["path"]), new)
    if os.path.exists(dest):
        return {"ok": False, "error": "a file with that name already exists"}, 409
    try:
        os.rename(r["path"], dest)
        _clear_caches(vid)
        rescan_now()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}, 500
    return {"ok": True}


@app.route("/delete/<vid>", methods=["POST"])
def delete_video(vid):
    """Delete the video file from disk (and its cached thumb/transcode)."""
    r = get_rec(vid)
    if not r:
        return {"ok": False, "error": "not found"}, 404
    try:
        os.remove(r["path"])
        _clear_caches(vid)
        rescan_now()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}, 500
    return {"ok": True}


# ---- Playlists (JSON API used by the "Save to playlist" dialog + playlist page) ----
LIKED_ID = "liked"  # built-in "Liked videos" playlist, filled by the Like button


@app.route("/api/like/<vid>", methods=["POST"])
def api_like(vid):
    if not allowed(get_rec(vid)):
        return {"ok": False, "error": "not found"}, 404
    pls = load_playlists()
    pl = find_playlist(pls, LIKED_ID)
    if not pl:
        pl = {"id": LIKED_ID, "name": "Liked videos", "items": []}
        pls.insert(0, pl)
    if vid in pl["items"]:
        pl["items"].remove(vid)
        liked = False
    else:
        pl["items"].insert(0, vid)  # newest like first, like YouTube
        liked = True
    save_playlists(pls)
    return {"ok": True, "liked": liked}


@app.route("/download/<vid>")
def download(vid):
    r = get_rec(vid)
    if not allowed(r):
        abort(404)
    return send_file(r["path"], as_attachment=True, download_name=r["title"])


@app.route("/api/info/<vid>")
def api_info(vid):
    """Technical details for the description box's "...more"."""
    r = get_rec(vid)
    if not allowed(r):
        abort(404)
    info = {}
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries",
             "format=format_long_name,bit_rate:stream=codec_type,codec_long_name,width,height,"
             "avg_frame_rate,channels,sample_rate", "-of", "json", r["path"]],
            capture_output=True, text=True, timeout=20).stdout
        info = json.loads(out or "{}")
    except Exception:
        pass
    fmt = info.get("format", {})
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), {})
    fps = ""
    try:
        n, d = (v.get("avg_frame_rate") or "0/1").split("/")
        fps = f"{float(n) / float(d):.3g}" if float(d) else ""
    except Exception:
        pass
    return {
        "path": r["host_path"], "container": fmt.get("format_long_name", ""),
        "resolution": f'{v.get("width")}×{v.get("height")}' if v.get("width") else "",
        "fps": fps, "video": v.get("codec_long_name", ""), "audio": a.get("codec_long_name", ""),
        "audio_ch": a.get("channels"), "bitrate": int(fmt.get("bit_rate") or 0),
        "modified": time.strftime("%b %d, %Y", time.localtime(r["mtime"])),
    }

@app.route("/api/playlists")
def api_playlists():
    vids = set(request.args.getlist("vid"))
    return {"playlists": [{"id": p["id"], "name": p["name"], "count": len(p["items"]),
                           "has": len(vids & set(p["items"]))} for p in load_playlists()]}


@app.route("/api/playlists", methods=["POST"])
def api_playlist_create():
    name = (request.form.get("name") or "").strip()[:100]
    if not name:
        return {"ok": False, "error": "empty name"}, 400
    pls = load_playlists()
    pl = {"id": os.urandom(5).hex(), "name": name, "items": []}
    for vid in request.form.getlist("vid"):
        if vid and vid not in pl["items"]:
            pl["items"].append(vid)
    pls.append(pl)
    save_playlists(pls)
    return {"ok": True, "id": pl["id"]}


@app.route("/api/playlists/<pid>/<action>", methods=["POST"])
def api_playlist_action(pid, action):
    pls = load_playlists()
    pl = find_playlist(pls, pid)
    if not pl:
        return {"ok": False, "error": "no such playlist"}, 404
    vids = [v for v in request.form.getlist("vid") if v]  # one or many
    vid = vids[0] if vids else ""
    items = pl["items"]
    if action == "add":
        for v in vids:
            if v not in items:
                items.append(v)
    elif action == "remove":
        pl["items"] = items = [i for i in items if i not in vids]
    elif action in ("up", "down"):
        if vid in items:
            i = items.index(vid)
            j = i - 1 if action == "up" else i + 1
            if 0 <= j < len(items):
                items[i], items[j] = items[j], items[i]
    elif action in ("listup", "listdown"):
        i = pls.index(pl)
        j = i - 1 if action == "listup" else i + 1
        if 0 <= j < len(pls):
            pls[i], pls[j] = pls[j], pls[i]
    elif action == "rename":
        name = (request.form.get("name") or "").strip()[:100]
        if not name:
            return {"ok": False, "error": "empty name"}, 400
        pl["name"] = name
    elif action == "delete":
        pls.remove(pl)
    else:
        return {"ok": False, "error": "unknown action"}, 400
    save_playlists(pls)
    return {"ok": True}


if __name__ == "__main__":
    _load_dur_cache()  # warm durations from last run -> instant first page load
    rescan_async()     # first scan starts now, before anyone opens a page
    from waitress import serve
    serve(app, host="0.0.0.0", port=8700, threads=16, channel_timeout=300)
