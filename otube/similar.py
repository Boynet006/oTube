"""Similar videos: the same footage in different files (re-encodes, other sizes or bitrates,
renamed copies, other containers).

Each video gets a fingerprint: 10 tiny grey frames (at 5%, 15% … 95%) turned into 64-bit
difference hashes, kept in data/fingerprints.db by path + size + mtime, so a file is read
once until it changes. Grouping works on the stored fingerprints only, so switching
Strict / Normal / Loose is instant. Ported from oSyncBack's finder.
"""
import json
import os
import sqlite3
import struct
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import abort, render_template, request

from .core import (
    BASE, FFMPEG, FFPROBE, _inside, app, folder_name, folder_path, folder_shown, folder_trees, fmt_duration,
    fmt_size, hidden_folders, load_config, load_playlists, private_folders, private_unlocked, save_config,
    scan, scan_hooks, visible,
)

# ---- The matching rules ----
FRAMES = 10
LEVELS = {"strict": 6, "normal": 10, "loose": 14}   # max differing bits for two frames to match
MATCH_SHARE = 0.8      # share of comparable frames that must match
MIN_COMPARABLE = 4     # fewer comparable (non-blank) frames than this: never similar
BLANK_RANGE = 8        # max(px) - min(px) below this: a fade or black screen
TIMEOUT = 30           # seconds per ffmpeg / ffprobe call
SIZES = [0, 100 * 1024, 1024 ** 2, 10 * 1024 ** 2, 100 * 1024 ** 2]   # "Skip files smaller than"


def dhash(px: bytes) -> tuple[int, bool]:
    """72 grey bytes (9 wide x 8 high) -> (64-bit difference hash, blank?).
    A bit is set when a pixel is brighter than its right-hand neighbour."""
    if len(px) != 72:
        raise ValueError("need 9x8 = 72 pixels")
    h = 0
    for y in range(8):
        row = px[y * 9:y * 9 + 9]
        for x in range(8):
            h = (h << 1) | (row[x] > row[x + 1])
    return h, max(px) - min(px) < BLANK_RANGE


def dur_window(a: float, b: float) -> float:
    """How far apart two durations may be: max(2 s, 1% of the longer one)."""
    return max(2.0, 0.01 * max(a, b))


def dur_close(a: float, b: float) -> bool:
    return abs(a - b) <= dur_window(a, b)


def similar(a: dict, b: dict, max_bits: int) -> bool:
    """a, b: {"hashes": [10 ints], "blank": bitmask}. Same footage when at least 80% of the
    frames that are blank in neither video match within max_bits (and there are 4+ of them)."""
    comparable = matched = 0
    for i, (ha, hb) in enumerate(zip(a["hashes"], b["hashes"])):
        if (a["blank"] >> i) & 1 or (b["blank"] >> i) & 1:
            continue
        comparable += 1
        matched += (ha ^ hb).bit_count() <= max_bits
    return comparable >= MIN_COMPARABLE and matched >= MATCH_SHARE * comparable


def group(fps: list[dict], max_bits: int) -> list[list[dict]]:
    """Fingerprints ({"dur", "hashes", "blank", ...}) -> groups of 2+ similar videos.
    Sorted by duration, each one is compared forward only while durations still qualify."""
    fps = sorted(fps, key=lambda f: f["dur"])
    parent = list(range(len(fps)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(fps):
        for j in range(i + 1, len(fps)):
            b = fps[j]
            if not dur_close(a["dur"], b["dur"]):
                break
            if similar(a, b, max_bits):
                parent[root(j)] = root(i)
    out: dict[int, list] = {}
    for i, f in enumerate(fps):
        out.setdefault(root(i), []).append(f)
    return [g for g in out.values() if len(g) > 1]


def best_copy(members: list[dict]) -> dict:
    """Highest resolution, then highest bitrate (size / duration), then the largest file."""
    return max(members, key=lambda m: (m["w"] * m["h"], m["size"] / m["dur"] if m["dur"] else 0, m["size"]))


# ---- Reading a video ----
def probe(path: str) -> tuple[float, int, int]:
    """(duration, width, height); duration 0 when there's no video stream."""
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height,duration:stream_disposition=attached_pic",
         "-of", "json", path], capture_output=True, text=True, timeout=TIMEOUT).stdout
    info = json.loads(out or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    if not v:
        return 0.0, 0, 0
    try:
        dur = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    except ValueError:
        dur = 0.0
    return dur, int(v.get("width") or 0), int(v.get("height") or 0)


def grab(path: str, t: float) -> bytes | None:
    """One 9x8 grey frame at t seconds (fast seek), or None."""
    try:
        out = subprocess.run(
            [FFMPEG, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-map", "0:V:0", "-an", "-sn",
             "-frames:v", "1", "-vf", "scale=9:8:flags=area,format=gray", "-f", "rawvideo", "-threads", "1", "pipe:1"],
            capture_output=True, timeout=TIMEOUT).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    return out[:72] if len(out) >= 72 else None


def fingerprint(path: str) -> dict:
    """{"dur", "w", "h", "hashes", "blank", "bad"}. bad: no picture or no length (unreadable)."""
    _stats["reads"] += 1
    try:
        dur, w, h = probe(path)
    except Exception:
        dur, w, h = 0.0, 0, 0
    fp = {"dur": dur, "w": w, "h": h, "hashes": [0] * FRAMES, "blank": (1 << FRAMES) - 1, "bad": dur <= 0}
    if fp["bad"]:
        return fp
    blank, got = 0, 0
    for i in range(FRAMES):
        t = dur * (0.05 + 0.1 * i)
        px = grab(path, t) or (grab(path, t - 1) if t >= 1 else None)
        if px is None:
            blank |= 1 << i
            continue
        got += 1
        fp["hashes"][i], is_blank = dhash(px)
        blank |= is_blank << i
    fp["blank"] = blank
    fp["bad"] = got == 0
    return fp


# ---- The fingerprint store (SQLite, one row per file, committed as each is made) ----
DB_PATH = os.path.join(BASE, "fingerprints.db")
_db_lock = threading.Lock()
_db: list = [None]
_fps: dict[str, dict] = {}   # path -> row, mirrors the table
_stats = {"reads": 0}        # fingerprint() calls since start (tests: a second run reads nothing)


def db() -> sqlite3.Connection:
    if _db[0] is None:
        con = sqlite3.connect(DB_PATH, check_same_thread=False)
        con.execute("CREATE TABLE IF NOT EXISTS fp (path TEXT PRIMARY KEY, size INTEGER, mtime REAL, dur REAL,"
                    " w INTEGER, h INTEGER, hashes BLOB, blank INTEGER, bad INTEGER)")
        con.commit()
        for path, size, mtime, dur, w, h, hashes, blank, bad in con.execute("SELECT * FROM fp"):
            _fps[path] = {"path": path, "size": size, "mtime": mtime, "dur": dur, "w": w, "h": h,
                          "hashes": list(struct.unpack("<10Q", hashes)), "blank": blank, "bad": bool(bad)}
        _db[0] = con
    return _db[0]


def store(path: str, size: int, mtime: float, fp: dict) -> None:
    row = {"path": path, "size": size, "mtime": mtime, **fp}
    with _db_lock:
        con = db()
        con.execute("INSERT OR REPLACE INTO fp VALUES (?,?,?,?,?,?,?,?,?)",
                    (path, size, mtime, fp["dur"], fp["w"], fp["h"], struct.pack("<10Q", *fp["hashes"]),
                     fp["blank"], int(fp["bad"])))
        con.commit()
        _fps[path] = row


def stored(r: dict) -> dict | None:
    """The fingerprint of library record r, if made from the file as it is now."""
    with _db_lock:
        db()
        f = _fps.get(r["path"])
    return f if f and f["size"] == r["size"] and abs(f["mtime"] - r["mtime"]) < 1 else None


# ---- What to search (remembered in config.json) ----
def get_cfg() -> dict:
    c = load_config().get("similar") or {}
    return {"include": [p for p in c.get("include", []) if isinstance(p, str)],
            "exclude": [p for p in c.get("exclude", []) if isinstance(p, str)],
            "min_size": int(c.get("min_size") or 0), "level": c.get("level") if c.get("level") in LEVELS else "normal",
            "resume": bool(c.get("resume"))}


def set_cfg(**kw) -> dict:
    cfg = load_config()
    cur = cfg.setdefault("similar", {})
    cur.update(kw)
    save_config(cfg)
    return get_cfg()


def in_scope(r: dict, sc: dict) -> bool:
    loc = folder_path(r["folder"], r.get("subdir", ""))
    return (r["size"] >= sc["min_size"]
            and (not sc["include"] or any(_inside(loc, p) for p in sc["include"]))
            and not any(_inside(loc, p) for p in sc["exclude"]))


def scoped(recs: list[dict], sc: dict) -> list[dict]:
    """Records in scope, one per file (overlapping library folders list a file twice)."""
    seen, out = set(), []
    for r in recs:
        if r["path"] not in seen and in_scope(r, sc):
            seen.add(r["path"])
            out.append(r)
    return out


def needs_reading(r: dict) -> bool:
    """No fingerprint yet, or the file changed. Unreadable files wait until they change."""
    return stored(r) is None


# ---- The background task: "Analyse new videos" ----
_task = {"running": False, "done": 0, "total": 0, "cancel": threading.Event(), "finished": 0.0}


def workers() -> int:
    return max(1, (os.cpu_count() or 2) // 2)


def analyse(recs: list[dict], wait: bool = False) -> bool:
    """Fingerprint the records that need it, a few at a time. False if already running."""
    if _task["running"]:
        return False
    todo = [r for r in recs if needs_reading(r)]
    _task.update(running=True, done=0, total=len(todo))
    _task["cancel"].clear()
    set_cfg(resume=True)

    def run():
        try:
            with ThreadPoolExecutor(max_workers=workers()) as ex:
                futs = {ex.submit(fingerprint, r["path"]): r for r in todo}
                for fut in as_completed(futs):
                    if _task["cancel"].is_set():
                        ex.shutdown(wait=False, cancel_futures=True)
                        break
                    r = futs[fut]
                    try:
                        store(r["path"], r["size"], r["mtime"], fut.result())
                    except Exception:
                        pass
                    _task["done"] += 1
            if not _task["cancel"].is_set():
                set_cfg(resume=False)
        finally:
            _task.update(running=False, finished=time.time())
    if wait:
        run()
    else:
        threading.Thread(target=run, daemon=True).start()
    return True


def task_status() -> dict:
    return {"running": _task["running"], "done": _task["done"], "total": _task["total"]}


def _resume_after_restart() -> None:
    """A run stopped by a restart (not by Cancel) carries on with the saved choice."""
    if _task["running"] or _resume_state["tried"]:
        return
    _resume_state["tried"] = True
    sc = get_cfg()
    if sc["resume"]:
        analyse(scoped(scan(), sc))


_resume_state = {"tried": False}
scan_hooks.append(_resume_after_restart)


# ---- Groups for the screen ----
_quick: dict[tuple, str] = {}   # (path, size, mtime) -> sample hash


def quick_hash(r: dict) -> str:
    """Size + 64 KB from the start, middle and end: tells byte-identical copies apart cheaply."""
    import hashlib
    key = (r["path"], r["size"], r["mtime"])
    if key not in _quick:
        h = hashlib.md5(str(r["size"]).encode())
        try:
            with open(r["path"], "rb") as f:
                for off in (0, max(0, r["size"] // 2 - 32768), max(0, r["size"] - 65536)):
                    f.seek(off)
                    h.update(f.read(65536))
        except OSError:
            return key[0]   # unreadable now: treat as different
        _quick[key] = h.hexdigest()
    return _quick[key]


def build_groups(recs: list[dict], sc: dict, level: str) -> dict:
    """recs: the library this mode can see. Groups are made from every stored fingerprint of
    those files, then kept when 2+ members are in scope (only those members are shown)."""
    by_path, fps = {}, []
    for r in recs:
        if r["path"] in by_path:
            continue
        by_path[r["path"]] = r
        f = stored(r)
        if f and not f["bad"]:
            fps.append(dict(f, rec=r))
    groups = []
    for g in group(fps, LEVELS[level]):
        shown = [m for m in g if in_scope(m["rec"], sc)]
        if len(shown) < 2:
            continue
        if len({m["size"] for m in shown}) == 1 and len({quick_hash(m["rec"]) for m in shown}) == 1:
            continue   # byte-identical copies: an exact-duplicate problem, not this one
        best = best_copy(shown)
        shown.sort(key=lambda m: (m is not best, -m["w"] * m["h"], -m["size"]))
        groups.append({"size": sum(m["size"] for m in shown), "members": [card(m, m is best) for m in shown]})
    groups.sort(key=lambda g: -g["size"])
    return {"groups": groups}


def card(m: dict, best: bool) -> dict:
    r = m["rec"]
    return {"id": r["id"], "title": r["title"], "path": r["host_path"], "size": r["size"], "size_h": fmt_size(r["size"]),
            "w": m["w"], "h": m["h"], "dur": m["dur"], "dur_h": fmt_duration(m["dur"]), "best": best,
            "folder": folder_name(folder_path(r["folder"], r.get("subdir", ""))), "native": r["native"]}


def overview(level: str | None = None) -> dict:
    sc = get_cfg()
    level = level if level in LEVELS else sc["level"]
    recs = visible(scan())
    inside = scoped(recs, sc)
    pending = bad = 0
    for r in inside:
        f = stored(r)
        if f is None:
            pending += 1
        elif f["bad"]:
            bad += 1
    out = build_groups(recs, sc, level)
    out.update(level=level, videos=len(inside), pending=pending, unreadable=bad, task=task_status(),
               scope={"include": sc["include"], "exclude": sc["exclude"], "min_size": sc["min_size"]})
    return out


# ---- Routes ----
@app.route("/similar")
def similar_page():
    cfg = load_config()
    priv = private_folders(cfg)
    folders = [f for f in cfg.get("video_folders", []) if folder_shown(f, priv)]
    return render_template("similar.html", folders=folders, private_set=priv, hidden_set=hidden_folders(cfg),
                           playlists=load_playlists(), current_folder="", current_sub="", sort="name",
                           folder_trees=folder_trees(scan(), folders), private_on=private_unlocked(),
                           has_pin=bool(cfg.get("pin_hash")), sizes=SIZES, similar_page=True)


@app.route("/api/similar")
def api_similar():
    return overview(request.args.get("level"))


@app.route("/api/similar/folders")
def api_similar_folders():
    """Library folders and subfolders this mode can see, for Look in / Leave out."""
    cfg = load_config()
    priv = private_folders(cfg)
    folders = [f for f in cfg.get("video_folders", []) if folder_shown(f, priv)]
    out = []

    def walk(top, node, depth):
        for c in node["children"]:
            out.append({"path": folder_path(top, c["rel"]), "name": c["name"], "depth": depth, "count": c["count"]})
            walk(top, c, depth + 1)
    for f, t in folder_trees(scan(), folders).items():
        out.append({"path": f, "name": folder_name(f), "depth": 0, "count": t["count"]})
        walk(f, t, 1)
    return {"folders": out}


@app.route("/api/similar/settings", methods=["POST"])
def api_similar_settings():
    d = request.get_json(silent=True) or {}
    kw = {}
    for k in ("include", "exclude"):
        if k in d:
            if not isinstance(d[k], list) or not all(isinstance(p, str) for p in d[k]):
                abort(400)
            kw[k] = list(dict.fromkeys(p.rstrip("/") or p for p in d[k]))[:200]
    if "min_size" in d:
        try:
            kw["min_size"] = max(0, int(d["min_size"]))
        except (TypeError, ValueError):
            abort(400)
    if d.get("level") in LEVELS:
        kw["level"] = d["level"]
    set_cfg(**kw)
    return overview()


@app.route("/api/similar/analyse", methods=["POST"])
def api_similar_analyse():
    started = analyse(scoped(visible(scan()), get_cfg()))
    return {"ok": True, "started": started, "task": task_status()}


@app.route("/api/similar/status")
def api_similar_status():
    return task_status()


@app.route("/api/similar/cancel", methods=["POST"])
def api_similar_cancel():
    _task["cancel"].set()
    set_cfg(resume=False)
    return {"ok": True, "task": task_status()}
