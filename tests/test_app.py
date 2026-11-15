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


# ---- "Up next" rail ----
def test_up_next_starts_with_next_file_and_changes_each_time(ids):
    import random
    from otube import core, pages
    with pages.app.test_request_context():
        everything = core.unique(core.visible(core.enrich(core.scan())))
        r = core.get_rec(ids["clip.ts"])
        lists = [pages.recommend(r, everything, random.Random(seed)) for seed in range(8)]
    folder = sorted((v for v in everything if v["folder"] == r["folder"]), key=lambda v: v["title"].lower())
    nxt = folder[folder.index(next(v for v in folder if v["id"] == r["id"])) + 1]
    for up in lists:
        assert up[0]["id"] == nxt["id"]                      # autoplay: the next file in the folder
        assert r["id"] not in {v["id"] for v in up}          # never the video being watched
        assert len(up) == len({v["id"] for v in up}) <= pages.UPNEXT_SIZE
    assert len({tuple(v["id"] for v in up[1:]) for up in lists}) > 1   # the rest is reshuffled


# ---- settings ----
def test_select_reach_setting_is_clamped(client):
    post = lambda v: client.post("/api/settings", json={"selectReach": v}).get_json()["settings"]["selectReach"]  # noqa: E731
    assert post(20) == 20 and post(999) == 40 and post(-5) == 0
    assert "--sel-reach: 0px" in client.get("/").get_data(as_text=True)
    assert b'class="sr-num"' in client.get("/settings").data
    client.post("/api/settings", json={"selectReach": 14})


# ---- Like / Dislike ----
def test_like_dislike(client, ids):
    vid = ids["clip.ts"]
    rate = lambda r: client.post(f"/api/rate/{vid}", json={"rating": r}).get_json()  # noqa: E731
    d = rate("like")
    assert d["rating"] == "like" and d["counts"]["liked"] == 1 and d["counts"]["disliked"] == 0
    d = rate("dislike")                                       # one or the other, never both
    assert d["counts"] == {"liked": 0, "disliked": 1}
    assert 'data-rate="dislike"' in client.get("/").get_data(as_text=True)
    assert client.get("/?playlist=disliked").status_code == 200
    names = [p["name"] for p in client.get("/api/playlists").get_json()["playlists"]]
    assert "Liked videos" in names and "Disliked videos" not in names   # Save dialog
    assert client.post("/api/playlists/disliked/delete").status_code == 400   # built-in
    assert rate("none")["counts"] == {"liked": 0, "disliked": 0}
    assert client.post(f"/api/rate/{vid}", json={"rating": "love"}).status_code == 400


# ---- removing a folder cleans up after it ----
def test_removing_a_folder_deletes_its_thumbnails(client, ids):
    import os, shutil, tempfile
    from otube import core, library
    extra = tempfile.mkdtemp(prefix="otube-extra-")
    lib = os.path.dirname(core.get_rec(ids["clip.ts"])["path"])
    shutil.copy(os.path.join(lib, "old.avi"), os.path.join(extra, "gone.avi"))
    cfg = core.load_config(); cfg["video_folders"].append(extra); core.save_config(cfg); core.rescan_now()
    gone = core.vid_id(os.path.join(extra, "gone.avi"))
    thumb = lambda v: os.path.join(core.THUMB_DIR, v + ".jpg")  # noqa: E731
    for v in (gone, ids["clip.ts"]):
        open(thumb(v), "wb").close()
    # a library folder on an unplugged drive: nothing is pruned
    cfg = core.load_config(); cfg["video_folders"].append("/nope/unplugged"); core.save_config(cfg)
    assert library.prune_orphans() == 0
    cfg["video_folders"].remove("/nope/unplugged"); core.save_config(cfg)
    client.post(f"/api/rate/{gone}", json={"rating": "like"})
    client.post("/remove_folder", data={"folder_path": extra})
    assert gone not in core.find_playlist(core.load_playlists(), core.LIKED_ID)["items"]   # likes too
    assert not os.path.exists(thumb(gone))            # removed folder's thumbnail: gone
    assert os.path.exists(thumb(ids["clip.ts"]))       # the rest: kept


# ---- Hide from Home ----
def test_hide_folder_from_home(client, ids):
    import os, random, shutil, tempfile
    from otube import core, pages
    extra = tempfile.mkdtemp(prefix="otube-hide-")
    lib = os.path.dirname(core.get_rec(ids["clip.ts"])["path"])
    shutil.copy(os.path.join(lib, "old.avi"), os.path.join(extra, "secret.avi"))
    cfg = core.load_config(); cfg["video_folders"].append(extra); core.save_config(cfg); core.rescan_now()
    sid = core.vid_id(os.path.join(extra, "secret.avi"))
    assert sid in client.get("/").get_data(as_text=True)
    assert client.post("/folder_hidden", data={"folder_path": extra, "hidden": "1"}).get_json()["ok"]
    assert sid not in client.get("/").get_data(as_text=True)                                   # off Home
    opened = client.get("/", query_string={"folder": extra}).get_data(as_text=True)
    assert sid not in opened and 'id="unhide-btn"' in opened                                  # hidden there too, with "Show on Home"
    assert client.get(f"/watch/{sid}").status_code == 200                                     # and plays
    with pages.app.test_request_context():
        everything = core.unique(core.visible(core.enrich(core.scan())))
        for seed in range(5):
            assert sid not in {v["id"] for v in pages.recommend(core.get_rec(ids["clip.ts"]), everything, random.Random(seed))}
    client.post("/folder_hidden", data={"folder_path": extra, "hidden": "0"})
    assert sid in client.get("/").get_data(as_text=True)
    client.post("/remove_folder", data={"folder_path": extra})
    assert extra not in core.load_config().get("hidden_folders", [])


def test_hide_subfolder_from_home(client, ids):
    import os, shutil, tempfile
    from otube import core
    extra = tempfile.mkdtemp(prefix="otube-hidesub-")
    lib = os.path.dirname(core.get_rec(ids["clip.ts"])["path"])
    os.makedirs(os.path.join(extra, "Season 1", "Extras"))
    shutil.copy(os.path.join(lib, "old.avi"), os.path.join(extra, "Season 1", "ep1.avi"))
    shutil.copy(os.path.join(lib, "old.avi"), os.path.join(extra, "Season 1", "Extras", "bts.avi"))
    shutil.copy(os.path.join(lib, "old.avi"), os.path.join(extra, "top.avi"))
    cfg = core.load_config(); cfg["video_folders"].append(extra); core.save_config(cfg); core.rescan_now()
    ep1, bts, top = (core.vid_id(os.path.join(extra, *p)) for p in (("Season 1", "ep1.avi"), ("Season 1", "Extras", "bts.avi"), ("top.avi",)))
    page = lambda **q: client.get("/", query_string=q).get_data(as_text=True)  # noqa: E731
    assert client.post("/folder_hidden", data={"folder_path": extra, "sub": "Season 1/Extras", "hidden": "1"}).get_json()["ok"]
    home, parent, inside = page(), page(folder=extra), page(folder=extra, sub="Season 1/Extras")
    assert top in home and ep1 in home and bts not in home          # off Home
    assert ep1 in parent and bts not in parent                       # off its parent folder too
    assert bts not in inside and 'data-sub="Season 1/Extras"' in inside   # opening it: hidden, offers to show it
    assert 'data-hidden="1" data-hidden-by="" data-folder' in home                     # marked in the sidebar
    assert client.post("/folder_hidden", data={"folder_path": extra, "sub": "../x", "hidden": "1"}).status_code == 400
    client.post("/folder_hidden", data={"folder_path": extra, "hidden": "1"})      # hide the whole folder
    home = page()
    assert top not in home and ep1 not in home and bts not in home                # everything inside goes
    assert 'data-hidden="2" data-hidden-by=' in home                              # subfolders show they follow it
    assert ep1 not in page(folder=extra, sub="Season 1")                          # hidden until shown again
    client.post("/remove_folder", data={"folder_path": extra})
    assert not core.load_config().get("hidden_folders")              # forgotten with the folder


# ---- GOM mode ----
def test_gom_hands_file_to_helper(client, ids):
    from otube import core
    d = client.post(f"/gom/{ids['clip.ts']}").get_json()
    assert d["ok"] and d["delegate"] == core.get_rec(ids["clip.ts"])["host_path"]
    assert client.post("/gom/" + "0" * 32).status_code == 404
    assert b'id="gom-toggle"' in client.get("/").data


def test_bulk_delete_to_recycle_bin(client, ids):
    import os
    from otube import core
    vid = ids["clip.ts"]
    rec = core.get_rec(vid)
    client.post(f"/api/rate/{vid}", json={"rating": "like"})
    d = client.post("/delete_many", data={"vid": [vid, "0" * 32]}).get_json()
    assert d["delegate"] == [{"id": vid, "path": rec["host_path"]}]        # helper does the recycling
    assert client.post("/delete_many", data={"vid": vid, "done": "1"}).get_json()["deleted"] == []  # still there
    data = open(rec["path"], "rb").read()
    os.remove(rec["path"])                                                   # what the helper does
    assert client.post("/delete_many", data={"vid": vid, "done": "1"}).get_json()["deleted"] == [vid]
    assert vid not in core.find_playlist(core.load_playlists(), core.LIKED_ID)["items"]
    assert b'id="sel-delete"' in client.get("/").data
    open(rec["path"], "wb").write(data); core.rescan_now()                   # put it back for other tests


def test_bulk_thumbnails(client, ids):
    from otube import core
    vids = [ids["clip.ts"]]
    rec = core.get_rec(vids[0])
    dur = core.probe_duration(rec["path"], rec["mtime"])
    d = client.post("/api/thumbs", json={"vids": vids + ["0" * 32], "sample": True}).get_json()
    assert d["count"] == 1 and d["sample"] == vids
    d = client.post("/api/thumbs", json={"vids": vids, "pct": 50}).get_json()
    t = core.load_config()["thumbs"][vids[0]]
    assert d["count"] == 1 and abs(t - min(dur / 2, dur - .5)) < .02 and d["srcs"][vids[0]].endswith(f"?at={t:g}")
    assert client.get(f"/thumb/{vids[0]}/frame?pct=50&fast=1").status_code == 200
    client.post("/api/thumbs", json={"vids": vids, "random": True, "seed": 7})
    t1 = core.load_config()["thumbs"][vids[0]]
    assert .1 * dur - .01 <= t1 <= .9 * dur + .01
    client.post("/api/thumbs", json={"vids": vids, "random": True, "seed": 7})
    assert core.load_config()["thumbs"][vids[0]] == t1          # same seed -> the spot that was previewed
    assert client.get(f"/thumb/{vids[0]}/frame?seed=7&fast=1").status_code == 200
    n = client.post("/api/thumbs", json={"folder": rec["folder"], "sample": True}).get_json()["count"]
    assert n >= 1
    assert client.post("/api/thumbs", json={"folder": rec["folder"], "reset": True}).get_json()["count"] == n
    assert vids[0] not in core.load_config().get("thumbs", {})
    assert b'id="sel-thumbs"' in client.get("/").data


def test_review_videos(client, ids):
    from otube import core
    vid = ids["clip.ts"]
    rec = core.get_rec(vid)
    assert b"Review videos" in client.get("/review").data
    every = client.get("/api/review").get_json()["videos"]
    assert vid in {v["id"] for v in every}
    row = next(v for v in every if v["id"] == vid)
    assert row["path"] == rec["host_path"] and row["size"] == rec["size"] and "folder" in row and "dur" in row
    inside = client.get("/api/review", query_string={"path": rec["folder"]}).get_json()["videos"]
    assert vid in {v["id"] for v in inside} and len(inside) <= len(every)
    assert client.get("/api/review", query_string={"path": "/nowhere"}).get_json()["videos"] == []
    picked = client.post("/api/review", json={"vids": [vid, "0" * 32]}).get_json()["videos"]
    assert [v["id"] for v in picked] == [vid]
    # a folder hidden from Home is left out here too
    client.post("/folder_hidden", data={"folder_path": rec["folder"], "hidden": "1"})
    assert vid not in {v["id"] for v in client.get("/api/review").get_json()["videos"]}
    client.post("/folder_hidden", data={"folder_path": rec["folder"], "hidden": "0"})
    # "Use as thumbnail" from a frame: that far into the video
    dur = core.probe_duration(rec["path"], rec["mtime"])
    d = client.post(f"/api/thumb/{vid}", json={"pct": 50}).get_json()
    assert d["ok"] and abs(d["t"] - min(dur / 2, dur - .5)) < .02
    client.post(f"/api/thumb/{vid}", json={"t": None})
    assert b'id="sel-review"' in client.get("/").data
    client.post(f"/api/rate/{vid}", json={"rating": "dislike"})
    assert client.post("/api/review", json={"vids": [vid]}).get_json()["videos"][0]["rating"] == "dislike"
    client.post(f"/api/rate/{vid}", json={"rating": "none"})
    assert client.post("/api/review", json={"vids": [vid]}).get_json()["videos"][0]["rating"] == ""
