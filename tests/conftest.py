"""Test library: a few short clips made with ffmpeg, in a throwaway data folder.

Run inside the Docker image (it has ffmpeg):  ./run-tests.ps1
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = tempfile.mkdtemp(prefix="otube-data-")
LIB = tempfile.mkdtemp(prefix="otube-lib-")
os.environ["OTUBE_DATA"] = DATA
os.environ.pop("OTUBE_HOST_ROOT", None)
with open(os.path.join(DATA, "config.json"), "w") as f:
    json.dump({"video_folders": [LIB]}, f)

V = ["-f", "lavfi", "-i", "testsrc=size=320x180:rate=25"]
SINE = lambda hz: ["-f", "lavfi", "-i", f"sine=frequency={hz}"]   # noqa: E731
H264 = ["-c:v", "libx264", "-preset", "ultrafast", "-g", "50"]   # a keyframe every 2 s

CLIPS = {
    # H.264 + AAC in MPEG-TS (most of the real library), with a sidecar .srt
    "clip.ts": V + SINE(440) + ["-t", "12"] + H264 + ["-c:a", "aac"],
    # two audio tracks (stereo English AAC, mono French AC-3) + a text subtitle stream
    "multi.mkv": V + SINE(440) + SINE(880) + ["-f", "srt", "-i", "{srt}", "-t", "10",
                 "-map", "0", "-map", "1", "-map", "2", "-map", "3"] + H264 +
                 ["-c:a:0", "aac", "-ac:a:0", "2", "-c:a:1", "ac3", "-ac:a:1", "1", "-c:s", "srt",
                  "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre", "-metadata:s:s:0", "language=eng"],
    # MP4 (plays directly) with two audio tracks
    "native.mp4": V + SINE(440) + SINE(660) + ["-t", "8", "-map", "0", "-map", "1", "-map", "2"] + H264 +
                  ["-c:a", "aac", "-ac:a:1", "1", "-movflags", "+faststart"],
    "hevc.mkv": V + SINE(440) + ["-t", "6", "-c:v", "libx265", "-x265-params", "log-level=none", "-c:a", "aac"],
    "old.avi": V + SINE(440) + ["-t", "6", "-c:v", "mpeg4", "-c:a", "libmp3lame"],
}
SRT = "1\n00:00:01,000 --> 00:00:03,000\nHello there\n\n2\n00:00:04,000 --> 00:00:06,000\nSecond line\n"


def _make_library():
    srt = os.path.join(LIB, "clip.en.srt")
    with open(srt, "w") as f:
        f.write(SRT)
    for name, args in CLIPS.items():
        args = [a.replace("{srt}", srt) for a in args]
        subprocess.run(["ffmpeg", "-v", "error", "-y"] + args + [os.path.join(LIB, name)], check=True)


_make_library()
sys.path.insert(0, ROOT)
import app  # noqa: E402  (registers every route)
from otube import core, progress  # noqa: E402

core.rescan_now()


@pytest.fixture(scope="session")
def progress_module():
    return progress


@pytest.fixture()
def client():
    app.app.config["TESTING"] = True
    with app.app.test_client() as c:
        yield c


@pytest.fixture(scope="session")
def ids():
    """file name -> video id"""
    return {os.path.basename(r["path"]): r["id"] for r in core.scan()}
