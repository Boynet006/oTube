"""Similar videos: the matching rules with synthetic hashes (no ffmpeg), then real clips."""
import os
import subprocess
import tempfile

import pytest

from otube import core, similar as sv
from otube.similar import LEVELS, best_copy, dhash, dur_close, dur_window, group


def fp(hashes, dur=100.0, blank=0, **kw):
    return {"hashes": list(hashes), "dur": dur, "blank": blank, "w": 640, "h": 360, "size": 1000, **kw}


BASE = [(0x9E3779B97F4A7C15 * (i + 1)) & (2 ** 64 - 1) for i in range(10)]   # 10 unrelated-looking hashes


def flip(h, bits):
    """h with its lowest `bits` bits flipped (Hamming distance = bits)."""
    return h ^ ((1 << bits) - 1)


# ---- dHash ----
def test_dhash_bits_follow_left_brighter_than_right():
    falling = bytes(200 - 10 * x for _ in range(8) for x in range(9))   # every pixel brighter than its right neighbour
    rising = bytes(10 + 10 * x for _ in range(8) for x in range(9))
    assert dhash(falling) == (2 ** 64 - 1, False)
    assert dhash(rising) == (0, False)
    one = bytearray(rising)
    one[0] = 255   # only the very first comparison (row 0, x 0) is "brighter"
    assert dhash(bytes(one))[0] == 1 << 63


def test_dhash_needs_72_pixels():
    with pytest.raises(ValueError):
        dhash(bytes(64))


def test_blank_frame_rule():
    assert dhash(bytes([16] * 72))[1] is True               # black screen
    assert dhash(bytes([100] * 71 + [107]))[1] is True       # range 7: still a fade
    assert dhash(bytes([100] * 71 + [108]))[1] is False      # range 8: a picture


# ---- similar() ----
def test_identical_and_unrelated():
    a = fp(BASE)
    assert sv.similar(a, fp(BASE), LEVELS["strict"])
    assert not sv.similar(a, fp([h ^ (2 ** 64 - 1) for h in BASE]), LEVELS["loose"])


def test_threshold_per_level():
    b = fp([flip(h, 8) for h in BASE])   # every frame 8 bits off
    assert not sv.similar(fp(BASE), b, LEVELS["strict"])
    assert sv.similar(fp(BASE), b, LEVELS["normal"])
    c = fp([flip(h, 12) for h in BASE])
    assert not sv.similar(fp(BASE), c, LEVELS["normal"])
    assert sv.similar(fp(BASE), c, LEVELS["loose"])


def test_eighty_percent_of_frames_must_match():
    eight = fp([h if i < 8 else h ^ (2 ** 64 - 1) for i, h in enumerate(BASE)])
    seven = fp([h if i < 7 else h ^ (2 ** 64 - 1) for i, h in enumerate(BASE)])
    assert sv.similar(fp(BASE), eight, LEVELS["normal"])       # 8 of 10
    assert not sv.similar(fp(BASE), seven, LEVELS["normal"])   # 7 of 10


def test_blank_frames_are_never_compared():
    wrong_tail = [h if i < 6 else h ^ (2 ** 64 - 1) for i, h in enumerate(BASE)]
    blank_tail = 0b1111000000   # frames 6-9 blank in one video
    assert sv.similar(fp(BASE), fp(wrong_tail, blank=blank_tail), LEVELS["normal"])   # 6 of 6 comparable match
    assert not sv.similar(fp(BASE), fp(wrong_tail), LEVELS["normal"])


def test_needs_four_comparable_frames():
    three_left = 0b1111111000   # only frames 0-2 are pictures
    assert not sv.similar(fp(BASE, blank=three_left), fp(BASE), LEVELS["loose"])
    assert sv.similar(fp(BASE, blank=0b1111110000), fp(BASE), LEVELS["loose"])


# ---- duration window ----
def test_duration_window():
    assert dur_window(60, 61) == 2.0                       # short videos: 2 s
    assert dur_window(1000, 990) == pytest.approx(10.0)    # long ones: 1% of the longer
    assert dur_close(60, 62) and not dur_close(60, 62.5)
    assert dur_close(600, 606) and not dur_close(600, 607)


# ---- grouping ----
def test_grouping():
    a = fp(BASE, dur=100, name="a")
    b = fp([flip(h, 5) for h in BASE], dur=101, name="b")
    c = fp([h ^ (((1 << 9) - 1) << 20) for h in BASE], dur=101.5, name="c")   # 9 bits from a, 14 from b
    other = fp([h ^ (2 ** 64 - 1) for h in BASE], dur=100.5, name="other")   # same length, different video
    later = fp(BASE, dur=200, name="later")                          # same pictures, much longer
    lone = fp(BASE, dur=500, name="lone")
    groups = group([lone, c, other, a, later, b], LEVELS["normal"])
    assert [sorted(m["name"] for m in g) for g in groups] == [["a", "b", "c"]]
    strict = group([a, b, c, other], LEVELS["strict"])
    assert [sorted(m["name"] for m in g) for g in strict] == [["a", "b"]]


def test_grouping_chains_through_a_middle_copy():
    a, b, c = fp(BASE, dur=100, name="a"), fp(BASE, dur=101.9, name="b"), fp(BASE, dur=103.8, name="c")
    assert not dur_close(100, 103.8)   # a and c are never compared...
    assert [sorted(m["name"] for m in g) for g in group([a, b, c], LEVELS["normal"])] == [["a", "b", "c"]]   # ...b joins them


def test_best_copy():
    lo = fp(BASE, name="lo", w=640, h=360, size=900, dur=100)
    hi = fp(BASE, name="hi", w=1280, h=720, size=500, dur=100)
    assert best_copy([lo, hi])["name"] == "hi"                       # resolution first
    rich = fp(BASE, name="rich", w=1280, h=720, size=800, dur=100)
    assert best_copy([lo, hi, rich])["name"] == "rich"               # then bitrate
    long_ = fp(BASE, name="long", w=1280, h=720, size=816, dur=102)  # same bitrate, bigger file
    assert best_copy([rich, long_])["name"] == "long"


# ---- real clips (ffmpeg) ----
def _make(path, args):
    subprocess.run(["ffmpeg", "-v", "error", "-y"] + args + [path], check=True)


@pytest.fixture(scope="module")
def clips():
    d = tempfile.mkdtemp(prefix="otube-similar-")
    src = ["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-t", "20"]
    out = {k: os.path.join(d, k) for k in ("orig.mp4", "small.mkv", "other.mp4")}
    _make(out["orig.mp4"], src + ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"])
    # re-encoded, resized, other codec + container
    _make(out["small.mkv"], ["-i", out["orig.mp4"], "-vf", "scale=320:180", "-c:v", "mpeg4", "-q:v", "8"])
    # different footage, same length
    _make(out["other.mp4"], ["-f", "lavfi", "-i", "life=size=640x360:rate=25:seed=7:ratio=0.4:mold=8",
                             "-t", "20", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast"])
    return {k: dict(sv.fingerprint(p), name=k, size=os.path.getsize(p)) for k, p in out.items()}


def test_reencoded_copy_groups_and_other_video_does_not(clips):
    o, s, x = clips["orig.mp4"], clips["small.mkv"], clips["other.mp4"]
    assert not o["bad"] and (o["w"], o["h"]) == (640, 360) and (s["w"], s["h"]) == (320, 180)
    assert dur_close(o["dur"], x["dur"])
    assert sv.similar(o, s, LEVELS["normal"])
    assert not sv.similar(o, x, LEVELS["normal"])
    assert [sorted(m["name"] for m in g) for g in group(list(clips.values()), LEVELS["normal"])] == [["orig.mp4", "small.mkv"]]
    assert best_copy([o, s])["name"] == "orig.mp4"


def test_unreadable_file(tmp_path):
    p = tmp_path / "broken.mp4"
    p.write_bytes(b"not a video" * 100)
    assert sv.fingerprint(str(p))["bad"]


def test_second_analyse_reads_nothing():
    recs = core.scan()
    assert sv.analyse(recs, wait=True)
    assert all(sv.stored(r) is not None for r in recs)
    reads = sv._stats["reads"]
    assert sv.analyse(recs, wait=True)
    assert sv._stats["reads"] == reads
    with core.app.test_request_context():
        assert sv.overview()["pending"] == 0


def test_similar_page_and_api(client):
    sv.analyse(core.scan(), wait=True)
    assert client.get("/similar").status_code == 200
    d = client.post("/api/similar/settings", json={"level": "normal", "include": [], "exclude": [], "min_size": 0}).get_json()
    assert d["level"] == "normal" and d["pending"] == 0
    names = [sorted(m["title"] for m in g["members"]) for g in d["groups"]]
    assert any({"hevc.mkv", "old.avi"} <= set(g) for g in names)   # same 6 s clip, two encodes
    for g in d["groups"]:
        assert sum(m["best"] for m in g["members"]) == 1
    # scope: leaving the whole library out hides every group; the fingerprints stay
    lib = core.load_config()["video_folders"][0]
    d = client.post("/api/similar/settings", json={"exclude": [lib]}).get_json()
    assert d["groups"] == [] and d["videos"] == 0
    d = client.post("/api/similar/settings", json={"exclude": []}).get_json()
    assert d["groups"] and d["pending"] == 0
    assert "similar" in client.get("/api/tasks").get_json()
