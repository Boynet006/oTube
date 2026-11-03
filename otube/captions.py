"""Subtitles: sidecar/embedded tracks as WebVTT, auto-captions (faster-whisper), translation (NLLB)."""
import glob
import json
import os
import queue
import re
import subprocess
import threading

from flask import Response, abort, send_file, url_for

from .core import AUTO_SUBS_DIR, BASE, FFMPEG, FFPROBE, allowed, app, get_rec, get_settings


# ---- Subtitles / captions ----
# Sidecar files next to the video (movie.srt, movie.en.vtt, ...) and text subtitle
# streams embedded in the container (mkv etc.) are offered as WebVTT tracks. Picture
# subtitles (Blu-ray PGS, DVD, DVB) can't become text: they're drawn into the video
# while playing instead (/stream?burn=<stream index>).
SUB_EXTS = (".vtt", ".srt", ".ass", ".ssa")
TEXT_SUB_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text"}
IMAGE_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
_subs_cache: dict[str, tuple] = {}  # vid -> (mtime, [tracks])
_vtt_cache: dict[tuple, bytes] = {}  # (vid, key, mtime) -> converted WebVTT


def list_subs(r: dict) -> list[dict]:
    """[{key, label, lang}] for every subtitle we can serve as text (WebVTT)."""
    return [t for t in _file_subs(r) if not t.get("image")] + _auto_sub(r["id"])


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
            image = st.get("codec_name") in IMAGE_SUB_CODECS
            if not image and st.get("codec_name") not in TEXT_SUB_CODECS:
                continue
            tags = st.get("tags", {})
            lang = tags.get("language", "")
            label = tags.get("title") or (lang.upper() if lang and lang != "und" else "") or f"Track {len(tracks) + 1}"
            tracks.append({"key": f"s{st['index']}", "label": label, "lang": lang, "stream": st["index"], "image": image})
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
            "burn": [{"stream": t["stream"], "label": t["label"]} for t in _file_subs(r) if t.get("image")],
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
    if "src" in t and t["src"].lower().endswith(".vtt"):
        return send_file(t["src"], mimetype="text/vtt")
    out = _vtt_cache.get((vid, key, r["mtime"]))
    if out is None:   # .srt/.ass sidecar or embedded track -> WebVTT, kept in memory
        cmd = [FFMPEG, "-v", "error", "-i", t.get("src") or r["path"]]
        if "stream" in t:
            cmd += ["-map", f"0:{t['stream']}"]
        cmd += ["-f", "webvtt", "pipe:1"]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=120).stdout
        except Exception:
            out = b""
        if not out:
            abort(404)
        if len(_vtt_cache) > 200:
            _vtt_cache.clear()
        _vtt_cache[(vid, key, r["mtime"])] = out
    return Response(out, mimetype="text/vtt")


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
