"""Review videos: one row per video with frames from across it, to see what it's about at a glance.

The page loads the list once (cheap: from the in-memory scan); frames come from the fast frame
grabber in media.py (/thumb/<id>/frame?fast=1&pct=…) and load only when a row is on screen.
"""
from flask import render_template, request

from .core import (
    _inside, app, cached_duration, fmt_duration, fmt_size, folder_name, folder_path, folder_shown,
    folder_trees, get_rec, hidden_folders, hidden_here, load_config, load_playlists, private_folders,
    private_unlocked, scan, visible, allowed,
)
from .core import ratings
from .similar import stored


def _row(r: dict, rated: dict) -> dict:
    fp = stored(r)   # resolution, when Similar videos has already read this file
    d = cached_duration(r["path"], r["mtime"]) or (fp["dur"] if fp else 0.0) or 0.0
    return {"id": r["id"], "title": r["title"], "path": r["host_path"], "size": r["size"], "size_h": fmt_size(r["size"]),
            "dur": d, "dur_h": fmt_duration(d) if d else "", "mtime": r["mtime"], "native": r["native"],
            "folder": folder_name(folder_path(r["folder"], r.get("subdir", ""))),
            "w": fp["w"] if fp and not fp.get("bad") else 0, "h": fp["h"] if fp and not fp.get("bad") else 0,
            "rating": rated.get(r["id"], "")}


def review_list(path: str = "", vids=None) -> list[dict]:
    """The videos to review: picked ids, or a folder (with its subfolders), or the whole library.
    Hidden folders stay hidden, as everywhere else."""
    rated = ratings()   # Liked / Disliked videos
    if vids:
        return [_row(r, rated) for r in (get_rec(v) for v in vids) if allowed(r)]
    hid = hidden_folders()
    return [_row(r, rated) for r in visible(scan()) if not hidden_here(r, hid)
            and (not path or _inside(folder_path(r["folder"], r.get("subdir", "")), path))]


@app.route("/review")
def review_page():
    cfg = load_config()
    priv = private_folders(cfg)
    folders = [f for f in cfg.get("video_folders", []) if folder_shown(f, priv)]
    return render_template("review.html", folders=folders, private_set=priv, hidden_set=hidden_folders(cfg),
                           playlists=load_playlists(), current_folder="", current_sub="", sort="name",
                           folder_trees=folder_trees(scan(), folders), private_on=private_unlocked(),
                           has_pin=bool(cfg.get("pin_hash")), review_page=True)


@app.route("/api/review", methods=["GET", "POST"])
def api_review():
    """GET ?path=<folder[/sub]> (empty: whole library), or POST {vids: [...]} (videos picked on Home)."""
    if request.method == "POST":
        vids = (request.get_json(silent=True) or {}).get("vids") or []
        return {"videos": review_list(vids=vids[:5000])}
    return {"videos": review_list(request.args.get("path", "").rstrip("/"))}
