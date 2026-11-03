"""oTube — a local YouTube-style video site.

Serves videos from configurable folders (config.json): a YouTube-like home grid
with real ffmpeg thumbnails + durations, a watch page with an "Up next" rail +
autoplay + resume, live search, sort, and add/remove folders. mp4/webm/mov/m4v
stream directly with HTTP range (seeking); mkv/avi/ts are repackaged (or, for old
codecs, re-encoded) live, a piece at a time, so they play and seek too. Nothing
converted is stored.

Run: python app.py (port 8700). The code lives in the otube/ package:
core (config, modes, scan) -> captions -> media (playback, thumbnails) ->
progress -> library -> pages. Each module registers its own routes.
"""
from otube import captions, core, library, media, pages, progress
from otube.core import _load_dur_cache, app, rescan_async

MODULES = (core, captions, media, progress, library, pages)   # importing them registers the routes

if __name__ == "__main__":
    _load_dur_cache()  # warm durations from last run -> instant first page load
    rescan_async()     # first scan starts now, before anyone opens a page
    from waitress import serve
    serve(app, host="0.0.0.0", port=8700, threads=16, channel_timeout=300)
