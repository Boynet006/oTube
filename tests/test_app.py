"""Streaming (pieces, audio tracks, H.265), subtitles, frames, watch-progress sync, pages."""
import json
import struct
import subprocess
import tempfile


def probe(data: bytes) -> list[dict]:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(data); f.flush()
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,channels",
                              "-of", "json", f.name], capture_output=True, text=True).stdout
    return json.loads(out or "{}").get("streams", [])


def first_tfdt_seconds(data: bytes) -> list[float]:
    """Base decode time of the first fragment of each track, in seconds."""
    scales, i = [], -1
    while (i := data.find(b"mdhd", i + 1)) >= 0:
        off = i + 4 + 4 + (16 if data[i + 4] == 1 else 8)
        scales.append(struct.unpack(">I", data[off:off + 4])[0])
    out, i = [], -1
    while len(out) < len(scales) and (i := data.find(b"tfdt", i + 1)) >= 0:
        v = data[i + 4]
        out.append(struct.unpack(">Q", data[i + 8:i + 16])[0] if v == 1 else struct.unpack(">I", data[i + 8:i + 12])[0])
    return [t / s for t, s in zip(out, scales)]


# ---- pages ----
def test_pages_render(client, ids):
    for url in ("/", "/settings", f"/watch/{ids['clip.ts']}", f"/watch/{ids['clip.ts']}?embed=1"):
        r = client.get(url)
        assert r.status_code == 200, url
        assert b"OTUBE_PROGRESS" in r.data and b"sync.js" in r.data, url


def test_manifest_and_service_worker(client):
    m = client.get("/manifest.webmanifest").get_json(force=True)
    assert {i["sizes"] for i in m["icons"]} >= {"192x192", "512x512"}
    assert any(i["purpose"] == "maskable" for i in m["icons"])
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and b"respondWith" in sw.data


# ---- live streaming ----
def test_ts_is_copied(client, ids):
    m = client.get(f"/api/media/{ids['clip.ts']}").get_json()
    assert m["native"] is False
    assert m["mime"] == 'video/mp4; codecs="avc1.640029,mp4a.40.2"'
    assert m["first"] == 8 and 11.5 < m["dur"] < 12.5


def test_piece_keeps_real_timestamps(client, ids):
    vid = ids["clip.ts"]
    m = client.get(f"/api/media/{vid}").get_json()
    r = client.get(f"/stream/{vid}?start=6.5&len=3")
    assert r.status_code == 200 and r.headers["Content-Type"] == "video/mp4"
    video_start = first_tfdt_seconds(r.data)[0] - m["offset"]
    assert 6.0 < video_start <= 6.5   # starts on the keyframe before 6.5 s (one every 2 s)
    assert {s["codec_name"] for s in probe(r.data)} == {"h264", "aac"}


def test_pieces_never_leave_a_gap(client, ids):
    vid = ids["clip.ts"]
    off = client.get(f"/api/media/{vid}").get_json()["offset"]
    for start in (0.5, 2.9, 5.0, 7.99, 10.3):   # every piece starts at or before where it was asked for
        assert first_tfdt_seconds(client.get(f"/stream/{vid}?start={start}&len=2").data)[0] - off <= start + 0.001, start


def test_old_codec_is_reencoded(client, ids):
    vid = ids["old.avi"]
    m = client.get(f"/api/media/{vid}").get_json()
    assert m["mime"].startswith('video/mp4; codecs="avc1') and m["first"] == 4
    streams = probe(client.get(f"/stream/{vid}?start=2&len=2").data)
    assert {s["codec_name"] for s in streams} == {"h264", "aac"}


def test_hevc_copied_only_when_browser_can_decode(client, ids):
    vid = ids["hevc.mkv"]
    assert "avc1" in client.get(f"/api/media/{vid}").get_json()["mime"]
    assert "hvc1" in client.get(f"/api/media/{vid}?hevc=1").get_json()["mime"]
    streams = probe(client.get(f"/stream/{vid}?start=0&len=2&hevc=1").data)
    assert streams[0]["codec_name"] == "hevc"


# ---- audio tracks ----
def test_audio_tracks_listed_and_switchable(client, ids):
    vid = ids["multi.mkv"]
    m = client.get(f"/api/media/{vid}").get_json()
    assert [a["lang"] for a in m["audio"]] == ["eng", "fre"]
    assert m["audio"][0]["label"] == "English (stereo)" and "(mono)" in m["audio"][1]["label"]
    first = [s for s in probe(client.get(f"/stream/{vid}?start=0&len=2").data) if s["codec_type"] == "audio"]
    second = [s for s in probe(client.get(f"/stream/{vid}?start=0&len=2&a=1").data) if s["codec_type"] == "audio"]
    assert len(first) == len(second) == 1
    assert first[0]["channels"] == 2 and second[0]["channels"] == 1   # AC-3 mono -> AAC mono


def test_mp4_with_audio_tracks(client, ids):
    vid = ids["native.mp4"]
    m = client.get(f"/api/media/{vid}").get_json()
    assert m["native"] is True and len(m["audio"]) == 2
    forced = client.get(f"/api/media/{vid}?force=1&a=1").get_json()
    assert forced["native"] is False and "mp4a" in forced["mime"]
    assert client.get(f"/stream/{vid}").status_code == 200          # the file itself
    assert len(client.get(f"/stream/{vid}?start=0&len=2&a=1").data) > 1000


def test_unknown_burn_stream_is_ignored(client, ids):
    vid = ids["clip.ts"]
    assert client.get(f"/api/media/{vid}?burn=7").get_json()["first"] == 8   # still copied


# ---- subtitles ----
def test_subtitles(client, ids):
    d = client.get(f"/subs/{ids['clip.ts']}").get_json()
    assert d["burn"] == [] and len(d["tracks"]) == 1
    vtt = client.get(d["tracks"][0]["src"])
    assert vtt.data.startswith(b"WEBVTT") and b"Hello there" in vtt.data
    d = client.get(f"/subs/{ids['multi.mkv']}").get_json()
    assert any(t["lang"] == "eng" for t in d["tracks"])
    assert b"Second line" in client.get(d["tracks"][0]["src"]).data


# ---- seek-bar frames ----
def test_frames(client, ids):
    for fast in ("1", "0"):
        r = client.get(f"/thumb/{ids['multi.mkv']}/frame?t=3&fast={fast}")
        assert r.status_code == 200 and r.data[:2] == b"\xff\xd8"


# ---- watch progress shared between devices ----
def test_progress_newest_wins(client, ids, progress_module):
    vid = ids["clip.ts"]
    post = lambda e: client.post("/api/progress", json={"entries": {vid: e}})  # noqa: E731
    post({"t": 5, "d": 12, "ts": 2000})
    post({"t": 1, "d": 12, "ts": 1000})                      # older: ignored
    assert client.get("/api/progress").get_json()["progress"][vid]["t"] == 5
    post({"t": 12, "d": 12, "ts": 3000, "w": 1})
    assert client.get("/api/progress").get_json()["progress"][vid]["w"] == 1
    post({"del": 1, "ts": 4000})                             # "mark as unwatched"
    assert client.get("/api/progress").get_json()["progress"][vid] == {"del": 1, "ts": 4000.0}
    with open(progress_module.PROGRESS_PATH) as f:                # saved to disk
        assert json.load(f)[vid]["del"] == 1


def test_progress_rejects_junk(client):
    assert client.post("/api/progress", json={"entries": {"x" * 100: {"t": 1}, "y": "nope", "z": {"t": "bad"}}}).get_json()["ok"]
    assert not set(client.get("/api/progress").get_json()["progress"]) & {"y", "z"}
