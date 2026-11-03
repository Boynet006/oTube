"""Watch progress shared by every device."""
import json
import os
import threading

from flask import request

from .core import BASE, app, scan, visible


# ---- Watch progress, shared by every device (static/sync.js keeps localStorage in step) ----
# vid -> {t, d, ts, w} or a deletion marker {del: 1, ts}; the newest ts wins.
PROGRESS_PATH = os.path.join(BASE, "progress.json")
_progress: dict[str, dict] = {}
_progress_lock = threading.Lock()
try:
    with open(PROGRESS_PATH, encoding="utf-8") as _f:
        _progress.update(json.load(_f))
except (OSError, ValueError):
    pass


def page_progress() -> dict:
    """Progress of the videos this mode can see (embedded in each page)."""
    ids = {r["id"] for r in visible(scan())}
    with _progress_lock:
        return {k: v for k, v in _progress.items() if k in ids}


@app.route("/api/progress", methods=["GET", "POST"])
def api_progress():
    if request.method == "POST":
        entries = (request.get_json(silent=True) or {}).get("entries") or {}
        changed = False
        with _progress_lock:
            for vid, e in list(entries.items())[:5000]:
                if not isinstance(e, dict) or not isinstance(vid, str) or len(vid) > 64:
                    continue
                try:
                    clean = {k: float(e[k]) for k in ("t", "d", "ts") if e.get(k) is not None}
                except (TypeError, ValueError):
                    continue
                clean["ts"] = clean.get("ts") or 1.0
                if e.get("w"):
                    clean["w"] = 1
                if e.get("del"):
                    clean = {"del": 1, "ts": clean["ts"]}
                cur = _progress.get(vid)
                if cur and cur.get("ts", 0) >= clean["ts"]:
                    continue
                _progress[vid] = clean
                changed = True
            if changed:
                tmp = PROGRESS_PATH + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(_progress, f)
                os.replace(tmp, PROGRESS_PATH)
        return {"ok": True}
    return {"progress": page_progress()}
