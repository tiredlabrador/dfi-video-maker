# DFI spinning-record video generator — Phase 1 prototype

Takes **one** local audio file (MP3 or FLAC) with embedded cover art and produces
**one** looping MP4: the artwork spinning like a record over a coloured canvas,
with a short trimmed audio clip underneath.

This is a bare-bones prototype to prove the video engine works. Batch processing,
Google Sheets/Drive, branding/text, loudness normalisation, etc. are intentionally
**out of scope**.

## Requirements

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/) (the `ffmpeg` **and** `ffprobe` binaries on your PATH)

Install ffmpeg:

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python generate_video.py
```

That's the whole thing — one command. All configuration lives in the clearly
labelled `CONFIG` block at the top of `generate_video.py`:

| Setting | Default | Meaning |
|---|---|---|
| `INPUT_DIR` | `.` | Folder to read the first audio file from |
| `CLIP_START` | `"1:13"` | Where the audio clip starts (`"mm:ss"` or seconds) |
| `CLIP_LENGTH_SECONDS` | `25` | Length of the output clip |
| `SPIN_PERIOD_SECONDS` | `6` | Time for one full 360° rotation |
| `FPS` | `30` | Frames per second |
| `CANVAS_W` / `CANVAS_H` | `1080` / `1080` | Output size (set `1350` height for 4:5) |
| `CIRCLE_DIAMETER` | `960` | Diameter of the spinning record, in pixels |
| `BG_COLOUR` | `black` | Canvas background (any Pillow colour name/hex) |
| `OUTPUT_DIR` | `output` | Where the finished MP4 is written |

The script picks the **first** MP3/FLAC in `INPUT_DIR` (alphabetically). Drop a
single track in there and run.

## What it does

1. Extracts the embedded cover art with **mutagen** (fails clearly if the file
   has none).
2. With **Pillow**: centre-crops the art to a square, resizes to
   `CIRCLE_DIAMETER`, and applies an anti-aliased circular mask.
3. Renders exactly **one full rotation** as frames (`SPIN_PERIOD_SECONDS × FPS`),
   each rotated `i × 360/num_frames` degrees — angles span `0` up to *but not
   including* `360`, so the spin loops with no seam.
4. Encodes those frames into a short seamless-looping spin video.
5. Uses **ffmpeg** to loop that spin video up to `CLIP_LENGTH_SECONDS` and mux in
   the audio, trimmed from `CLIP_START`.
6. Outputs **H.264 / yuv420p** MP4, **AAC stereo** audio, at the configured
   canvas size and FPS, then prints an `ffprobe` summary with acceptance checks.

## No audio file? Self-test mode

If `INPUT_DIR` contains no MP3/FLAC, the script generates a synthetic test asset
(a colourful PNG cover + a short tone, embedded via mutagen) and runs the full
pipeline on it — so you can verify everything end to end with zero setup.

## Output

The finished video lands in `OUTPUT_DIR/` named after the source track, e.g.
`output/Midland - In The Mood For Love.mp4`.
```
Dimensions : 1080x1080   Video: h264 (yuv420p) @ 30 fps
Duration   : 25.000 s    Audio: aac (2 ch @ 44100 Hz)
```

---

# Phase 2 — Google Sheet + Drive batch (Colab)

`DFI_batch_render.ipynb` is a Google Colab notebook that batch-produces videos
for the whole team. It reads the DFI track sheet, and for every row where
**Render? = TRUE** it downloads the audio (and optional override artwork) from
Google Drive, renders it with the **exact same Phase 1 engine** (`render_video`
in `generate_video.py`), and writes `Artist - Track.mp4` to a Drive output
folder.

The notebook embeds a copy of `generate_video.py` (written to disk via
`%%writefile` in step 4) so it's fully self-contained — no file uploads needed.
`generate_video.py` remains the single source of truth for the engine.

## One-time setup (per team)

1. **Share the sheet** with everyone who'll run the notebook (Viewer is enough).
2. **Share the Drive files** — the audio files (and any override artwork) — with
   the team, or keep them in a shared folder everyone can read.
3. **Create a Drive output folder**, share it with **Editor** access, and copy
   its folder ID (the part of the URL after `/folders/`).

## Running it

1. Open `DFI_batch_render.ipynb` in [Google Colab](https://colab.research.google.com/)
   (File → Upload notebook, or open from Drive).
2. In the **Config** cell (step 1), set:
   - `SHEET_ID` — already filled in with the DFI sheet.
   - `DRIVE_OUTPUT_FOLDER_ID` — paste your output folder ID.
   - `WORKSHEET_NAME` — leave `None` for the first tab, or set a tab name/index.
   - Render settings (`CLIP_LENGTH_SECONDS`, canvas size, etc.) if you want to
     change the defaults.
3. **Runtime → Run all**. When prompted, sign in with your Google account
   (Colab's built-in auth — you run as yourself, no key files).
4. Watch the log. Each finished MP4 is uploaded to the output folder, and a
   summary prints at the end: **rendered OK / skipped (with reasons) / failed**.

## The sheet

Columns (header row): `Track | Artist | Spotify link | Bandcamp link | Other link
| Submitted by | Genre/Vibe | Notes | Audio file | Artwork file* | Clip start |
Render?`

Per-row behaviour:

| Situation | What happens |
|---|---|
| `Render?` not `TRUE` | Row ignored |
| `Audio file` blank | Skipped, logged |
| `Artwork file*` set | That image (Drive link or URL) overrides the embedded art |
| No override + no embedded art | Skipped, logged as **no artwork** (branded fallback card is a later phase) |
| Dead link / unreadable file | That row **fails** and is logged — the batch keeps going |

`Audio file` and `Artwork file*` accept standard Drive share links
(`.../file/d/<id>/view`, `...open?id=<id>`), a bare file id, or — for artwork
only — a plain image URL. `Clip start` is `mm:ss` (e.g. `1:13`). `Clip length`
is global, set once in the Config cell.

## Robustness

One bad row never halts the batch: every row is wrapped in its own try/except,
the reason is logged, and the run continues to the next row.
