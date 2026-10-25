# oTube

A local YouTube-style player for your own video folders (Flask + ffmpeg).
Thumbnails, durations, search, sort, grid/list views, resume, autoplay, and on-the-fly
remuxing of mkv/avi/etc. so they play in the browser.

Player: prev/next, autoplay with "Up next" countdown, captions (sidecar .srt/.vtt/.ass,
embedded text subs, or **auto-generated speech-to-text** via faster-whisper), speed,
brightness dimmer, loop, theater, miniplayer, fullscreen, keyboard shortcuts (press `?`),
and OS media keys. Set `OTUBE_WHISPER_MODEL` (default large-v3-turbo; tiny/base/small/medium/large-v3) to trade
caption speed for accuracy; the model downloads to `./data/models` on first use.
Captions can be translated (Meta NLLB-200, offline) into the languages in
`OTUBE_TRANSLATE_LANGS` (default `en,ar`).

`docker-compose.yml` mounts whole drives under `/host` (`OTUBE_HOST_ROOT`), so folders can
be added by their Windows path. Note the app listens on your network and can rename/delete
files; use `127.0.0.1:8700:8700` in `ports` to keep it local to the PC.
"Show in folder" / "Open in player" go through `otube-helper.ps1`, a small helper the
launcher starts that listens on 127.0.0.1 only.

## Run with Docker

1. Edit `docker-compose.yml` and mount your video folders, e.g.
   `- "D:/Videos:/videos"`
2. `docker compose up -d --build`
3. Open http://localhost:8700 and add folders by their container path (e.g. `/videos/Kids`).

Config and caches are stored in `./data`.

On Windows, `start-otube.vbs` starts Docker Desktop if needed, brings the app up and opens
the browser. Point a desktop shortcut at it and use `otube.ico` as the icon.

## Phones and other devices

oTube listens on your home network. Start it from the desktop icon and open
**Settings → Devices** for the address and a QR code to scan with a phone.
On a phone, "Add to Home screen" gives you an oTube app icon.

If other devices can't connect, allow the port through Windows Firewall for
private (home) networks. Run once in an **administrator** PowerShell:

    New-NetFirewallRule -DisplayName "oTube (port 8700)" -Direction Inbound -Protocol TCP -LocalPort 8700 -Action Allow -Profile Private

Your Wi-Fi must be set to "Private" in Windows network settings.
