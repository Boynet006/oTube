"""Playing videos: direct or converted live piece by piece, thumbnails, seek-bar frames, the background warmer."""
import json
import os
import subprocess
import threading
from collections import OrderedDict

from flask import Response, abort, g, request, send_file

from .core import (
    FFMPEG, FFPROBE, THUMB_DIR, _dur_cache, _dur_state, _save_dur_cache, _scan_state, allowed,
    app, cached_duration, get_rec, load_config, probe_duration, save_config, scan_hooks,
)
from .captions import LANG_NAMES, _file_subs


# ---- MKV/AVI/TS…: converted live, a short piece at a time (nothing is stored) ----
# The player asks for ~30 s pieces (/stream/<vid>?start=&len=) and feeds them to a Media
# Source buffer (static/stream.js). Pieces keep the file's own timestamps, so they line up
# exactly: duration, seeking and captions behave like a normal file. H.264/VP9/AV1 video
# is copied untouched (no quality loss, almost no CPU); only codecs no browser can play
# (MPEG-4 Part 2, MPEG-2, WMV, HEVC…) are re-encoded to H.264.
_media: dict[str, dict] = {}   # vid -> {mtime, v, audio: [...], start}: codecs + first timestamp


def media_info(r: dict) -> dict:
    m = _media.get(r["id"])
    if m and m["mtime"] == r["mtime"]:
        return m
    c = _dur_cache.get(r["path"])
    if c and len(c) > 2 and abs(c[1] - r["mtime"]) < 1 and "audio" in c[2]:   # probed in an earlier run
        m = dict(c[2], mtime=r["mtime"])
        _media[r["id"]] = m
        return m
    v, audio, start = "", [], 0.0
    try:
        out = json.loads(subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries",
             "stream=codec_type,codec_name,channels:stream_tags=language,title:stream_disposition=attached_pic:format=start_time",
             "-of", "json", r["path"]], capture_output=True, text=True, timeout=20).stdout or "{}")
        for st in out.get("streams", []):
            if st.get("codec_type") == "video" and not v and not st.get("disposition", {}).get("attached_pic"):
                v = st.get("codec_name", "")
            elif st.get("codec_type") == "audio":
                tags = st.get("tags", {})
                audio.append({"codec": st.get("codec_name", "").lower(), "lang": tags.get("language", ""),
                              "title": tags.get("title", ""), "ch": st.get("channels") or 0})
        start = float(out.get("format", {}).get("start_time") or 0)
    except Exception:
        pass
    m = {"mtime": r["mtime"], "v": v.lower(), "audio": audio, "start": start}
    _media[r["id"]] = m
    if v:
        _dur_cache[r["path"]] = (probe_duration(r["path"], r["mtime"]), r["mtime"],
                                 {"v": m["v"], "audio": audio, "start": start})
        _dur_state["dirty"] = True
    return m


# ISO 639-2 codes (what MKV/MP4 tracks use) -> the 2-letter codes of LANG_NAMES
LANG3 = {"eng": "en", "ara": "ar", "fre": "fr", "fra": "fr", "spa": "es", "ger": "de", "deu": "de",
         "ita": "it", "por": "pt", "tur": "tr", "rus": "ru", "per": "fa", "fas": "fa", "urd": "ur",
         "hin": "hi", "ben": "bn", "ind": "id", "may": "ms", "msa": "ms", "chi": "zh", "zho": "zh",
         "jpn": "ja", "kor": "ko", "dut": "nl", "nld": "nl", "swe": "sv", "pol": "pl", "ukr": "uk",
         "heb": "he", "gre": "el", "ell": "el", "swa": "sw"}


def audio_label(a: dict, i: int) -> str:
    lang = (a.get("lang") or "").lower()
    name = a.get("title") or LANG_NAMES.get(LANG3.get(lang, lang), "") or \
        (lang.upper() if lang and lang != "und" else "") or f"Track {i + 1}"
    ch = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}.get(a.get("ch") or 0)
    return f"{name} ({ch})" if ch else name


COPY_VIDEO = {"h264": "avc1.640029", "vp9": "vp09.00.40.08", "av1": "av01.0.08M.08"}  # codec -> MSE codec string
HEVC_CODEC = "hvc1.1.6.L150.90"
X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]


def live_opts(r: dict) -> dict:
    """Options from the query string: hevc=1 (browser decodes H.265), a=<audio track>,
    burn=<subtitle stream index> (picture subtitles drawn into the video)."""
    burn = request.args.get("burn", type=int)
    if burn is not None and not any(t.get("image") and t.get("stream") == burn for t in _file_subs(r)):
        burn = None
    return {"hevc": request.args.get("hevc") == "1", "a": max(0, request.args.get("a", 0, type=int)), "burn": burn}


def live_plan(m: dict, hevc: bool = False, a: int = 0, burn: int | None = None) -> tuple[list, str, bool]:
    """(ffmpeg stream maps + codec args, MIME type for Media Source, video copied untouched?)"""
    if burn is not None:   # PGS/DVD subtitles: overlaid on the picture (bottom centre), re-encoded
        args = ["-filter_complex", f"[0:V:0][0:{int(burn)}]overlay=(W-w)/2:H-h[v]", "-map", "[v]"] + X264
        codecs, copy = ["avc1.640029"], False
    elif hevc and m["v"] == "hevc":
        args, codecs, copy = ["-map", "0:V:0", "-c:v", "copy", "-tag:v", "hvc1"], [HEVC_CODEC], True
    else:
        copy = m["v"] in COPY_VIDEO
        args = ["-map", "0:V:0"] + (["-c:v", "copy"] if copy else X264)
        codecs = [COPY_VIDEO.get(m["v"], "avc1.640029")]
    tracks = m.get("audio") or []
    if tracks:
        a = a if a < len(tracks) else 0
        codec = tracks[a]["codec"]
        args += ["-map", f"0:a:{a}"]
        if codec == "aac":
            args += ["-c:a", "copy", "-bsf:a", "aac_adtstoasc"]; codecs.append("mp4a.40.2")
        elif codec == "opus":
            args += ["-c:a", "copy"]; codecs.append("opus")
        else:
            args += ["-c:a", "aac", "-b:a", "192k"]; codecs.append("mp4a.40.2")
    else:
        args += ["-an"]
    return args, 'video/mp4; codecs="' + ",".join(codecs) + '"', copy


def keyframe_before(r: dict, m: dict, t: float) -> float:
    """A copied piece can only start on a keyframe, and seeking in some containers
    (MPEG-TS) lands on the keyframe *after* the target, which would leave a gap the
    player stalls on. So: find the keyframe at or before t (seconds from the start)."""
    if t < 0.05:
        return 0.0
    st = m.get("start") or 0.0
    for back in (12, 90):
        try:
            out = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0", "-read_intervals", f"{max(0.0, t - back) + st:.3f}%{t + st + 0.001:.3f}",
                 "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", r["path"]],
                capture_output=True, text=True, timeout=20).stdout
        except Exception:
            break
        kfs = []
        for line in out.splitlines():
            p = line.split(",")
            try:
                if len(p) > 1 and "K" in p[1] and float(p[0]) - st <= t + 0.001:
                    kfs.append(float(p[0]) - st)
            except ValueError:
                pass
        if kfs:
            return max(0.0, max(kfs))
        if t - back <= 0:
            break
    return max(0.0, t - 12)


def _live_stream(r: dict, opts: dict) -> Response:
    """Fallback for browsers without Media Source: one continuous stream from the
    start (plays, but can't seek past what has arrived)."""
    args = live_plan(media_info(r), **opts)[0]

    def generate():
        cmd = ([FFMPEG, "-v", "error", "-i", r["path"]] + args +
               ["-sn", "-dn", "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-f", "mp4", "pipe:1"])
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


@app.route("/api/media/<vid>")
def api_media(vid):
    """How the player should load a video: directly, or piece by piece (see above).
    ?force=1 loads even an MP4 piece by piece (to switch audio track / burn subtitles)."""
    r = get_rec(vid)
    if not allowed(r):
        abort(404)
    m = media_info(r)
    tracks = [{"label": audio_label(a, i), "lang": a.get("lang", "")} for i, a in enumerate(m.get("audio") or [])]
    if r["native"] and request.args.get("force") != "1":
        return {"native": True, "audio": tracks}
    _args, mime, copy = live_plan(m, **live_opts(r))
    return {"native": False, "dur": probe_duration(r["path"], r["mtime"]), "mime": mime, "audio": tracks,
            "offset": m["start"], "first": 8 if copy else 4, "chunk": 30 if copy else 12}


@app.route("/stream/<vid>")
def stream(vid):
    r = get_rec(vid)
    if not allowed(r):
        abort(404)
    start = request.args.get("start", type=float)
    if r["native"] and start is None:
        # Direct stream with HTTP Range support (seeking works).
        return send_file(r["path"], conditional=True)
    opts = live_opts(r)
    if start is None:
        return _live_stream(r, opts)
    length = min(60.0, max(1.0, request.args.get("len", 30.0, type=float)))
    m = media_info(r)
    args, _mime, copy = live_plan(m, **opts)
    start = max(0.0, start)
    if copy:   # start exactly on the keyframe at/before `start` (re-encoding starts anywhere)
        kf = keyframe_before(r, m, start)
        length += start - kf
        start = kf
    # -copyts + frag_discont (needs delay_moov): the piece keeps the file's own timestamps
    cmd = ([FFMPEG, "-v", "error", "-copyts", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", r["path"]] + args +
           ["-sn", "-dn", "-movflags", "frag_keyframe+delay_moov+default_base_moof+frag_discont", "-f", "mp4", "pipe:1"])
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=180).stdout
    except subprocess.TimeoutExpired:
        abort(504)
    return Response(out, mimetype="video/mp4", headers={"Cache-Control": "no-store"})


def thumb_overrides() -> dict:
    """vid -> second of the video chosen as its thumbnail (Change thumbnail…)."""
    return load_config().get("thumbs", {})


def thumb_url(vid: str) -> str:
    if "thumbs" not in g:  # read once per page, not once per card
        g.thumbs = thumb_overrides()
    t = g.thumbs.get(vid)
    return f"/thumb/{vid}" + (f"?at={t:g}" if t is not None else "")  # new URL -> browsers drop the old image


def make_thumb(r: dict) -> str | None:
    """Create (once) and return the thumbnail path for a video record."""
    tp = os.path.join(THUMB_DIR, r["id"] + ".jpg")
    if os.path.exists(tp) and os.path.getsize(tp) > 0:
        return tp
    at = thumb_overrides().get(r["id"])
    for ss in ((str(at),) if at is not None else ()) + ("00:00:05", "00:00:01"):
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


@app.route("/thumb/<vid>/frame")
def thumb_frame(vid):
    """A small frame at ?t=seconds: the thumbnail picker, and the seek-bar preview
    (?fast=1: the nearest keyframe, ~0.1 s). Kept in memory, never stored."""
    r = get_rec(vid)
    if not allowed(r):
        abort(404)
    t = max(0.0, request.args.get("t", 0, type=float))
    fast = request.args.get("fast") == "1"
    key = (vid, round(t, 1), fast, r["mtime"])
    out = _frames.get(key)
    if out is None:
        try:
            out = subprocess.run([FFMPEG, "-v", "error"] + (["-noaccurate_seek"] if fast else []) +
                                 ["-ss", f"{t:.2f}", "-i", r["path"], "-map", "0:V:0", "-frames:v", "1",
                                  "-vf", "scale=320:-2", "-q:v", "5" if fast else "4", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
                                 capture_output=True, timeout=30).stdout
        except Exception:
            out = b""
        if not out:
            abort(404)
        _frames[key] = out
        while len(_frames) > 800:   # ~10 MB
            _frames.popitem(last=False)
    else:
        _frames.move_to_end(key)
    return Response(out, mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


_frames: "OrderedDict[tuple, bytes]" = OrderedDict()


@app.route("/api/thumb/<vid>", methods=["GET", "POST"])
def api_thumb(vid):
    """GET: duration + chosen time. POST {t: seconds | null}: use that frame (null = default)."""
    r = get_rec(vid)
    if not allowed(r):
        abort(404)
    dur = probe_duration(r["path"], r["mtime"])
    if request.method == "POST":
        t = (request.get_json(silent=True) or {}).get("t")
        cfg = load_config()
        thumbs = cfg.setdefault("thumbs", {})
        if t is None:
            thumbs.pop(vid, None)
        else:
            try:
                t = round(min(max(0.0, float(t)), max(0.0, dur - 0.5) if dur else float(t)), 2)
            except (TypeError, ValueError):
                return {"ok": False, "error": "bad time"}, 400
            thumbs[vid] = t
        save_config(cfg)
        tp = os.path.join(THUMB_DIR, vid + ".jpg")
        try:
            if os.path.exists(tp):
                os.remove(tp)
        except OSError:
            pass
        make_thumb(r)
    return {"ok": True, "dur": dur, "t": thumb_overrides().get(vid), "src": thumb_url(vid)}


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


# ---- Background warmer: fills in durations + thumbnails so pages never wait ----
_warm_event = threading.Event()
_warmer = None


def _wake_warmer() -> None:
    global _warmer
    _warm_event.set()
    if _warmer is None or not _warmer.is_alive():
        _warmer = threading.Thread(target=_warm_loop, daemon=True)
        _warmer.start()


_warm_state = {"busy": False}


def _warm_loop() -> None:
    while True:
        _warm_event.wait()
        _warm_event.clear()
        _warm_state["busy"] = True
        try:
            _warm_once()
        finally:
            _warm_state["busy"] = False


def _warm_once() -> None:
    items = list(_scan_state["items"] or [])
    # durations first (they show on every card), then thumbnails
    n = 0
    for r in items:
        if cached_duration(r["path"], r["mtime"]) is None:
            probe_duration(r["path"], r["mtime"])
            n += 1
            if n % 25 == 0:
                _save_dur_cache(); _dur_state["dirty"] = False
    for r in items:   # codecs of MKV/AVI/TS…, so even their first play starts without a probe
        if not r["native"] and not _warm_event.is_set():
            media_info(r)
            n += 1
            if n % 50 == 0 and _dur_state["dirty"]:
                _save_dur_cache(); _dur_state["dirty"] = False
    if _dur_state["dirty"]:
        _save_dur_cache(); _dur_state["dirty"] = False
    for r in items:
        if _warm_event.is_set():
            break  # library changed: _warm_loop runs again with the new list
        if not os.path.exists(os.path.join(THUMB_DIR, r["id"] + ".jpg")):
            make_thumb(r)



scan_hooks.append(_wake_warmer)
