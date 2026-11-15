# Plan: Review videos (storyboard view)

## Why
Thumbnails show one frame, so it's hard to tell what a video is about without playing it.
A row of frames from across the video shows it at a glance, so you can decide quickly
what to keep, delete, rename or move.

## What changes
- **A new "Review videos" page** (sidebar, under Similar videos), shaped like Similar videos.
  - **What to show:** a folder (with its subfolders), or the whole library. Filter: All / Unwatched / Watched.
    Sort: name, newest, size, longest.
  - **One row per video:** title, length, size, resolution and folder, then a strip of frames
    taken evenly across it (the middle of each slice, so it skips the very start and end).
  - **Frames per video: adjustable, 3-12** with quick buttons (4, 6, 8) plus a slider and number box.
    **Frame size: adjustable** (small / medium / large). Both are remembered.
  - **Click a frame:** plays the video in the mini player from that moment.
  - **Hover a frame:** shows its time. A small "Use as thumbnail" button sets it as the video's thumbnail.
- **Selecting and acting:** the same tick circle and selection bar as Home
  (Select all, Watched / Unwatched, Thumbnails, Save to playlist, Delete to the Recycle Bin).
  Delete follows the in-place progress pattern: rows dim with "Deleting…", then leave.
- **The same per-video menu** as everywhere else (… button, right-click, Shift+F10):
  Play, Save to playlist, Show in Explorer, Open in player, Copy path, Rename, Change thumbnail,
  Mark as watched, Delete.
- **Other ways in:** the folder menu gets "Review videos", and the Home selection bar gets "Review".
- **Fast on big folders:** rows render 30 at a time as you scroll, and frames load only when visible.
  Frames come from the existing fast frame grabber; its memory cache grows to fit a few pages.

## Out of scope
- Detecting what's in a video automatically (scenes, faces, text). Possible later.
- Moving videos to another folder (the app has no Move yet).

## Tasks
- [x] Server: `/review` page + `/api/review` (filtered, sorted list in pages), bigger frame cache.
- [x] Page: options bar, rows, frame strip, lazy loading, mini player on click, Use as thumbnail.
- [x] Selection bar and shared menu wired in; delete with in-place progress.
- [x] Entry points: sidebar, folder menu, Home selection bar.
- [x] Tests (API, filters, paging) + browser check at wide and narrow widths.
