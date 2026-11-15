"""Library actions: folders, modes + PIN, rename/delete/reveal, likes, playlists, downloads."""
import glob
import json
import os
import random
import subprocess
import time

from flask import abort, redirect, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .core import (folder_path, 
    AUTO_SUBS_DIR, BUILTIN_PLAYLISTS, DISLIKED_ID, FFPROBE, LIKED_ID, PIN_LOCKOUT_SECS, PIN_MAX_TRIES, PIN_RE, THUMB_DIR,
    _pin_fails, allowed, app, find_playlist, folder_shown, get_rec, load_config, load_playlists,
    private_unlocked, rescan_now, save_config, save_playlists, scan, to_local, visible,
)
from .captions import _subs_cache


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


@app.route("/folder_hidden", methods=["POST"])
def folder_hidden():
    """Hide a folder from Home (or show it again). Unlike Private mode, nothing is locked:
    the folder stays in the sidebar and opens as usual."""
    top = request.form.get("folder_path", "")
    cfg = load_config()
    hid = cfg.setdefault("hidden_folders", [])
    if top not in cfg["video_folders"]:
        return {"ok": False, "error": "Unknown folder"}, 404
    sub_ = request.form.get("sub", "").strip("/")
    if ".." in sub_.split("/"):
        return {"ok": False, "error": "Bad subfolder"}, 400
    folder = folder_path(top, sub_) if sub_ else top
    if request.form.get("hidden") == "1":
        if folder not in hid:
            hid.append(folder)
    elif folder in hid:
        hid.remove(folder)
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
        cfg["hidden_folders"] = [h for h in cfg.get("hidden_folders", [])
                                 if h != path and not h.startswith(path.rstrip("/") + "/")]
        save_config(cfg)
        rescan_now()
        prune_orphans()
    return redirect(url_for("index"))


def prune_orphans() -> int:
    """Delete thumbnails, auto-captions and playlist entries of videos that are no longer in the library
    (folder removed, file deleted outside oTube). Skipped while any library folder is
    unreachable (unplugged drive), so its thumbnails survive until it comes back."""
    folders = load_config().get("video_folders", [])
    if not all(os.path.isdir(to_local(f)) for f in folders):
        return 0
    keep, n = {r["id"] for r in scan()}, 0
    for d in (THUMB_DIR, AUTO_SUBS_DIR):
        for e in os.scandir(d):
            vid = e.name.split(".", 1)[0]
            if e.is_file() and len(vid) == 32 and vid not in keep:
                _clear_caches(vid); n += 1
    cfg = load_config()   # chosen thumbnail times of videos that are gone
    picked = cfg.get("thumbs", {})
    if any(v not in keep for v in picked):
        cfg["thumbs"] = {v: t for v, t in picked.items() if v in keep}
        save_config(cfg)
    pls = load_playlists()   # Liked / Disliked / your playlists: drop videos that are gone
    if any(v not in keep for pl in pls for v in pl.get("items", [])):
        for pl in pls:
            pl["items"] = [v for v in pl.get("items", []) if v in keep]
        save_playlists(pls)
    return n


def _clear_caches(vid: str) -> None:
    try:
        os.remove(os.path.join(THUMB_DIR, vid + ".jpg"))
    except OSError:
        pass
    for p in glob.glob(os.path.join(AUTO_SUBS_DIR, glob.escape(vid) + ".*")):
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


@app.route("/gom/<vid>", methods=["POST"])
def open_gom(vid):
    """Play the video in GOM Player (GOM mode). From Docker the on-PC helper launches it."""
    r = get_rec(vid)
    if not allowed(r):
        return {"ok": False, "error": "not found"}, 404
    if os.path.exists("/.dockerenv"):
        return {"ok": True, "delegate": r["host_path"]}
    import shutil
    exe = next((p for p in (r"C:\Program Files\GRETECH\GOMPlayerPlus\GOM64.EXE",
                            r"C:\Program Files (x86)\GOM\GOMPlayer\GOM.exe") if os.path.exists(p)),
               shutil.which("GOM64") or shutil.which("GOM"))
    if not exe:
        return {"ok": False, "error": "GOM Player isn't installed"}, 404
    subprocess.Popen([exe, r["path"]])
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
    """Delete the video file from disk (and its cached thumb)."""
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


@app.route("/delete_many", methods=["POST"])
def delete_many():
    """Bulk delete to the Recycle Bin. The on-PC helper moves the files (Docker can't reach the
    Recycle Bin), so step 1 returns their PC paths; step 2 (done=1) forgets the ones that are gone."""
    recs = [r for r in (get_rec(v) for v in request.form.getlist("vid")) if allowed(r)]
    if not request.form.get("done"):
        return {"ok": True, "delegate": [{"id": r["id"], "path": r["host_path"]} for r in recs]}
    gone = {r["id"] for r in recs if not os.path.exists(r["path"])}
    for vid in gone:
        _clear_caches(vid)
    cfg = load_config()
    if any(v in gone for v in cfg.get("thumbs", {})):
        cfg["thumbs"] = {v: t for v, t in cfg["thumbs"].items() if v not in gone}
        save_config(cfg)
    pls = load_playlists()
    if any(v in gone for pl in pls for v in pl.get("items", [])):
        for pl in pls:
            pl["items"] = [v for v in pl.get("items", []) if v not in gone]
        save_playlists(pls)
    rescan_now()
    return {"ok": True, "deleted": sorted(gone)}


# ---- Playlists (JSON API used by the "Save to playlist" dialog + playlist page) ----


@app.route("/api/like/<vid>", methods=["POST"])
def api_like(vid):
    """Toggle Like (older pages); see /api/rate."""
    if not allowed(get_rec(vid)):
        return {"ok": False, "error": "not found"}, 404
    liked = vid in find_playlist(load_playlists(), LIKED_ID)["items"]
    return {"ok": True, "liked": set_rating(vid, "none" if liked else "like") == "like"}


def set_rating(vid: str, rating: str) -> str:
    """Put the video in Liked or Disliked videos (newest first, like YouTube), or neither."""
    pls = load_playlists()
    for pid, r in ((LIKED_ID, "like"), (DISLIKED_ID, "dislike")):
        items = find_playlist(pls, pid)["items"]
        if vid in items:
            items.remove(vid)
        if r == rating:
            items.insert(0, vid)
    save_playlists(pls)
    return rating if rating in ("like", "dislike") else "none"


@app.route("/api/rate/<vid>", methods=["POST"])
def api_rate(vid):
    """{rating: like | dislike | none} -> the video's rating + both list sizes."""
    if not allowed(get_rec(vid)):
        return {"ok": False, "error": "not found"}, 404
    rating = (request.get_json(silent=True) or {}).get("rating")
    if rating not in ("like", "dislike", "none"):
        return {"ok": False, "error": "bad rating"}, 400
    rating = set_rating(vid, rating)
    pls = load_playlists()
    return {"ok": True, "rating": rating, "counts": {pid: len(find_playlist(pls, pid)["items"]) for pid in BUILTIN_PLAYLISTS}}


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
                           "has": len(vids & set(p["items"]))} for p in load_playlists()
                          if p["id"] != DISLIKED_ID]}   # disliking is done with its button


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
    if pid in BUILTIN_PLAYLISTS and action in ("rename", "delete"):
        return {"ok": False, "error": "built-in playlist"}, 400
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
