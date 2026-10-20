# oTube

A local YouTube-style player for your own video folders (Flask + ffmpeg).
Thumbnails, durations, search, sort, grid/list views, resume, autoplay, and on-the-fly
remuxing of mkv/avi/etc. so they play in the browser.

Player: prev/next, autoplay with "Up next" countdown, captions (sidecar .srt/.vtt/.ass,
embedded text subs, or **auto-generated speech-to-text** via faster-whisper), speed,
brightness dimmer, loop, theater, miniplayer, fullscreen, keyboard shortcuts (press `?`),
and OS media keys. Set `OTUBE_WHISPER_MODEL` (tiny/base/small/medium/large-v3) to trade
caption speed for accuracy; the model downloads to `./data/models` on first use.

## Run with Docker

1. Edit `docker-compose.yml` and mount your video folders, e.g.
   `- "D:/Videos:/videos"`
2. `docker compose up -d --build`
3. Open http://localhost:8700 and add folders by their container path (e.g. `/videos/Kids`).

Config and caches are stored in `./data`.

On Windows, `start-otube.vbs` starts Docker Desktop if needed, brings the app up and opens
the browser. Point a desktop shortcut at it and use `otube.ico` as the icon.
