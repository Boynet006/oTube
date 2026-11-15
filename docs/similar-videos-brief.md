# Brief: Similar videos finder

This brief is ported from oSyncBack, where the feature is proven on a library of about 3,000 videos. Build the same thing for oTube, adapting it to oTube's stack (Flask, ffmpeg, the existing library and thumbnails). The behaviour below is what matters; the code shape is up to you.

## What it does
It finds videos that are the same footage even when the files differ: re-encodes, other resolutions or bitrates, renamed copies, other containers. It shows them in groups, suggests the best copy in each group, and lets the user mark the others to remove.

## How the matching works

1. **Probe** each video with ffmpeg for its duration, width and height.
   - If there's no video stream or the duration is 0, mark it "unreadable".
   - Skip unreadable videos until the file changes (size or mtime).
2. **Sample 10 frames** at 5%, 15%, … 95% of the duration.
   - Use a fast seek: `-ss <t>` before `-i`.
   - Grab one frame with `-frames:v 1 -vf scale=9:8:flags=area,format=gray -f rawvideo -threads 1 pipe:1`.
   - Each grab returns 72 grey bytes.
   - If a grab at `t` fails, retry at `t - 1s`.
   - Give each ffmpeg call a timeout (30 s).
   - This reads only a few MB per video, so it's fast even on big files.
3. **Hash each frame** with a difference hash (dHash):
   - For each of 8 rows and 8 column pairs, set the bit if `px[y][x] > px[y][x+1]`. That gives a 64-bit integer.
   - Treat the frame as **blank** (a fade or black screen) when `max(px) - min(px) < 8`. Blank frames are never compared.
4. **Store the fingerprint**: 10 × uint64 plus a 10-bit blank mask, with the duration, width and height.
   - Key it by path + size + mtime, so it's computed once and reused until the file changes.
   - Commit each result as it's made, so a stopped run resumes where it left off.
5. **Compare two videos** only when their durations are within **max(2 s, 1% of the longer one)**:
   - The comparable frames are those not blank in either video. There must be at least 4.
   - A frame matches when the Hamming distance between the two hashes is ≤ N bits. N is 6 for Strict, 10 for Normal and 14 for Loose.
   - The videos are similar when at least **80%** of comparable frames match.
6. **Group the videos:**
   - Sort by duration.
   - For each video, compare it forward only while the durations still qualify, then break. That keeps it near-linear.
   - Union-find similar pairs into groups, and keep groups with 2 or more members.
   - Skip groups whose members are all byte-identical: that's an exact-duplicate problem, not a similar-video one.
   - Order groups by total size, largest first.
7. **Pick the best copy** in each group:
   - highest resolution (width × height);
   - then the highest bitrate (size ÷ duration);
   - then the largest file.

## Running it
- **It's a background task** with progress ("N of M videos"), cancel, and resume after a restart.
- **It's gentle on the machine:** a few workers (e.g. half the cores), each with ffmpeg `-threads 1`.
- **It's incremental:**
  - "Analyse new videos" reads only videos with no fingerprint, or whose file changed.
  - Show a "still to analyse: N" count.
  - Show how many videos couldn't be read.
- **It's scoped:** the user picks folders to look in, folders to leave out, and a minimum size.
  - Remember the choice.
  - Analysing reads only what's inside the choice.
  - A group is shown only when at least 2 of its members are inside it.
  - Fingerprints made earlier outside the choice are kept.

## The screen
- **A "What to search" box** at the top with: Look in (folder chips + "Add folder"), Leave out, and Skip files smaller than (Any / 100 KB / 1 MB / 10 MB / 100 MB). It folds to a one-line summary once set.
- **The header row:**
  - the title;
  - the Strict / Normal / Loose switch, which re-groups instantly with no re-analysis;
  - an "Analyse new videos" button.
- **A summary line:** "**14** groups among 2,963 videos", plus "Mark all except the best of each".
- **Each group is a bordered row of cards.** Each card has:
  - a thumbnail at 30% into the video, with the duration in the corner;
  - the file name, with a ★ on the best copy, whose card has a green ring;
  - "W×H · size · folder".
- **Interaction:**
  - **Click** a card to mark it for removal (red ring, dimmed).
  - **Double-click** to play it in oTube's player.
  - **Right-click, the "…" button on hover or focus, or Shift+F10** opens oTube's existing file menu: Play, Show in folder, Copy path (the full path), Rename, Delete…
  - The "…" button and the menu never mark the card.
- **A removal bar** with "Move N to … (size)" or "Delete N…", with a confirmation. At least one video per group is always kept.
- **While removing:** the affected cards dim and show "Deleting…". When it ends, they disappear, any that failed are labelled, and a toast gives the totals.

## Done when
- Re-encoded or resized copies of the same video group together at Normal, and different videos with similar durations don't.
- A second "Analyse" with no changes reads nothing.
- There are tests for dHash, the blank-frame rule, `similar()`, the duration window and grouping, using synthetic hashes, without ffmpeg.
