"""oTube — a local YouTube-style video site.

Serves videos from configurable folders (config.json): a YouTube-like home grid
with real ffmpeg thumbnails + durations, a watch page with an "Up next" rail +
autoplay + resume, live search, sort, and add/remove folders. mp4/webm/mov/m4v
stream directly with HTTP range (seeking); mkv/avi are remuxed to fragmented mp4
on the fly so they play in the browser too.
"""
import hashlib
import json
import os
import subprocess
import time

from flask import (Flask, Response, abort, redirect, render_template, request,
                   send_file, url_for)

app = Flask(__name__)

BASE = os.environ.get("OTUBE_DATA") or os.path.dirname(os.path.abspath(__file__))
os.makedirs(BASE, exist_ok=True)
CONFIG_PATH = os.path.join(BASE, "config.json")
DUR_CACHE_PATH = os.path.join(BASE, "duration_cache.json")  # persisted path -> [secs, mtime]
THUMB_DIR = os.path.join(BASE, "thumbs")
TRANS_DIR = os.path.join(BASE, "transcoded")  # cached browser-playable mp4s for mkv/avi/etc.
os.makedirs(THUMB_DIR, exist_ok=True)
os.makedirs(TRANS_DIR, exist_ok=True)

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


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg.get("video_folders"), list):
            cfg = {"video_folders": []}
        return cfg
    except Exception:
        return {"video_folders": []}


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


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


def probe_duration(path: str) -> float:
    global _dur_dirty
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    cached = _dur_cache.get(path)
    if cached and abs(cached[1] - mtime) < 1:  # unchanged file -> trust cache
        return cached[0]
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


def scan() -> list[dict]:
    """Scan configured folders for videos; refresh the id->record cache."""
    cfg = load_config()
    items, cache = [], {}
    for folder in cfg.get("video_folders", []):
        if not os.path.isdir(folder):
            continue
        for root, _dirs, files in os.walk(folder):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in VIDEO_EXTS:
                    continue
                full = os.path.join(root, fn)
                vid = vid_id(full)
                try:
                    size = os.path.getsize(full)
                    mtime = os.path.getmtime(full)
                except OSError:
                    continue
                rec = {"id": vid, "path": full, "title": fn, "folder": folder,
                       "ext": ext, "size": size, "mtime": mtime,
                       "native": ext in NATIVE_EXTS}
                cache[vid] = rec
                items.append(rec)
    _rec_cache.clear()
    _rec_cache.update(cache)
    return items


def enrich(items: list[dict]) -> list[dict]:
    out = []
    for r in items:
        d = probe_duration(r["path"])
        fname = folder_name(r["folder"])
        out.append({**r, "duration": fmt_duration(d), "dur_secs": int(d),
                    "size_h": fmt_size(r["size"]), "folder_name": fname,
                    "added": rel_time(r["mtime"]),
                    "avatar_letter": (fname[:1] or "V").upper(),
                    "avatar_hue": avatar_hue(fname)})
    global _dur_dirty
    if _dur_dirty:
        _save_dur_cache()
        _dur_dirty = False
    return out


def get_rec(vid: str) -> dict | None:
    if vid in _rec_cache:
        return _rec_cache[vid]
    scan()
    return _rec_cache.get(vid)


@app.route("/")
def index():
    cfg = load_config()
    videos = enrich(scan())
    folder = request.args.get("folder", "")
    sort = request.args.get("sort", "name")
    if folder:
        videos = [v for v in videos if v["folder"] == folder]
    keys = {
        "name": (lambda v: v["title"].lower(), False),
        "newest": (lambda v: v["mtime"], True),
        "size": (lambda v: v["size"], True),
        "duration": (lambda v: v["dur_secs"], True),
    }
    keyfn, rev = keys.get(sort, keys["name"])
    videos.sort(key=keyfn, reverse=rev)
    return render_template("index.html", videos=videos,
                           folders=cfg.get("video_folders", []),
                           current_folder=folder, sort=sort)


@app.route("/watch/<vid>")
def watch(vid):
    r = get_rec(vid)
    if not r:
        abort(404)
    everything = enrich(scan())
    same = [v for v in everything if v["folder"] == r["folder"] and v["id"] != vid]
    other = [v for v in everything if v["folder"] != r["folder"]]
    upnext = same + other
    d = probe_duration(r["path"])
    cur = {**r, "duration": fmt_duration(d), "size_h": fmt_size(r["size"])}
    return render_template("watch.html", video=cur, upnext=upnext)


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
    if not r:
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


@app.route("/thumb/<vid>")
def thumb(vid):
    r = get_rec(vid)
    if not r:
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


@app.route("/add_folder", methods=["POST"])
def add_folder():
    path = (request.form.get("folder_path") or "").strip().strip('"').replace("\\", "/")
    if path:
        cfg = load_config()
        if path not in cfg["video_folders"]:
            cfg["video_folders"].append(path)
            save_config(cfg)
    return redirect(url_for("index"))


@app.route("/remove_folder", methods=["POST"])
def remove_folder():
    path = request.form.get("folder_path") or ""
    cfg = load_config()
    if path in cfg["video_folders"]:
        cfg["video_folders"].remove(path)
        save_config(cfg)
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


@app.route("/reveal/<vid>", methods=["POST"])
def reveal(vid):
    """Open the file's folder in the OS file manager, selecting the file."""
    r = get_rec(vid)
    if not r:
        return {"ok": False, "error": "not found"}, 404
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
    except OSError as exc:
        return {"ok": False, "error": str(exc)}, 500
    return {"ok": True}


if __name__ == "__main__":
    _load_dur_cache()  # warm durations from last run -> instant first page load
    app.run(host="0.0.0.0", port=8700, debug=False, threaded=True)
