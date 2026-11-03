"""Pages (home, watch, settings, folder browser), settings API, library stats, app manifest, background tasks."""
import glob
import gzip
import json
import os
import random
import threading
import time

from flask import Response, abort, render_template, request, send_file, url_for

from .core import (
    AUTO_SUBS_DIR, BASE, HOST_ROOT, LIKED_ID, SETTINGS_DEFAULTS, SORT_ICONS, THUMB_DIR,
    VIDEO_EXTS, WHISPER_MODELS, _scan_state, allowed, app, dir_names, enrich, find_playlist,
    fmt_size, folder_shown, folder_trees, get_rec, get_settings, load_config, load_playlists,
    private_folders, private_unlocked, probe_duration, rel_time, rescan_now, save_config, scan,
    scan_hooks, sub_level, to_host, to_local, unique, visible,
)
from .captions import LANG_NAMES, _jobs
from .media import _wake_warmer, _warm_state, thumb_url
from .progress import page_progress


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


@app.route("/api/tasks")
def api_tasks():
    """Background work, for the top-bar indicator."""
    ids = {r["id"] for r in visible(scan())}
    tn = dir_names(THUMB_DIR)
    thumbs = sum(1 for v in ids if v + ".jpg" in tn)
    caps, seen = [], set()
    for key, j in list(_jobs.items()):
        vid, _, lang = key.partition(":")
        if j.get("state") not in ("queued", "running") or vid not in ids or id(j) in seen:
            continue
        seen.add(id(j))   # translating first transcribes: both keys share one job
        r = get_rec(vid)
        caps.append({"title": r["title"] if r else vid, "state": j["state"],
                     "progress": round(j.get("progress") or 0, 3), "lang": LANG_NAMES.get(lang, lang)})
    caps.sort(key=lambda c: c["state"] != "running")
    return {"thumbs": {"done": thumbs, "total": len(ids), "running": _warm_state["busy"] and thumbs < len(ids)},
            "captions": caps}


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
    return {"app_settings": get_settings(), "thumb_url": thumb_url, "page_progress": page_progress}


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


@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    """Delete cached files on demand: thumbnails."""
    what = (request.get_json(silent=True) or {}).get("what")
    freed, n = 0, 0
    if what == "thumbs":
        for e in os.scandir(THUMB_DIR):
            try:
                size = e.stat().st_size
                os.remove(e.path); freed += size; n += 1
            except OSError:
                pass
        _wake_warmer()  # rebuild them in the background
    else:
        return {"ok": False, "error": "unknown cache"}, 400
    disk_stats(fresh=True)
    return {"ok": True, "freed": fmt_size(freed), "files": n, "stats": library_stats()}


def library_stats() -> dict:
    items = visible(scan())
    cfg = load_config()
    shown = [f for f in cfg.get("video_folders", []) if folder_shown(f, private_folders(cfg))]
    return {
        "videos": len({v["id"] for v in items}),
        "folders": len(shown),
        "size": fmt_size(sum(v["size"] for v in {v["id"]: v for v in items}.values())),
        "scanned": rel_time(_scan_state["at"]) if _scan_state["at"] else "never",
        **disk_stats(),
    }


# Summing thousands of files on the host-mounted data folder takes seconds, so the
# numbers are kept in memory and refreshed in the background (Settings opens at once).
_disk = {"at": 0.0, "v": None, "busy": False}
_DISK_KEYS = ("thumbs", "captions", "thumbsSize", "models")


def _scan_dir(path: str) -> tuple[int, int]:
    n = total = 0
    try:
        for e in os.scandir(path):
            try:
                if e.is_file():
                    n += 1; total += e.stat().st_size
                elif e.is_dir():
                    sn, st = _scan_dir(e.path); n += sn; total += st
            except OSError:
                pass
    except OSError:
        pass
    return n, total


def _refresh_disk() -> None:
    try:
        tn, ts = _scan_dir(THUMB_DIR)
        _disk["v"] = {
            "thumbs": tn, "thumbsSize": fmt_size(ts),
            "captions": len(glob.glob(os.path.join(AUTO_SUBS_DIR, "*.vtt"))),
            "models": fmt_size(_scan_dir(os.path.join(BASE, "models"))[1]),
        }
        _disk["at"] = time.time()
    finally:
        _disk["busy"] = False


def disk_stats(fresh: bool = False, max_age: float = 60) -> dict:
    """Cached disk numbers; "…" until the first count finishes. fresh=True recounts now."""
    if fresh:
        _disk["busy"] = True
        _refresh_disk()
    elif time.time() - _disk["at"] > max_age and not _disk["busy"]:
        _disk["busy"] = True
        threading.Thread(target=_refresh_disk, daemon=True).start()
    return dict(_disk["v"] or {k: "…" for k in _DISK_KEYS})


@app.route("/api/stats")
def api_stats():
    if _disk["v"] is None:  # first count still running: wait for it
        for _ in range(300):
            if _disk["v"] is not None:
                break
            disk_stats()
            time.sleep(0.1)
    return library_stats()


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
        "description": "Your videos, YouTube-style", "orientation": "any",
        "icons": [{"src": url_for("static", filename="icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
                  {"src": url_for("static", filename="icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
                  {"src": url_for("static", filename="icon-maskable.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    }), mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    """At the site root so it controls every page (installable app, offline page)."""
    return send_file(os.path.join(app.static_folder, "sw.js"), mimetype="text/javascript", max_age=0)


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
            if k == "topTasks" and v not in ("auto", "always", "never"):
                continue
            cur[k] = v
    save_config(cfg)
    return {"ok": True, "settings": get_settings()}


@app.template_filter("host_path")
def host_path_filter(folder: str) -> str:
    """Configured folder -> its real path on the PC (for Copy path / Show in Explorer)."""
    return to_host(to_local(folder))




def _count_disk_once() -> None:
    if _disk["v"] is None:
        disk_stats()   # count cache folders in the background so Settings opens instantly


scan_hooks.append(_count_disk_once)
