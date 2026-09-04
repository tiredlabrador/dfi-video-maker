# DFI Video Maker

Turns a Google Sheet of track recommendations into Instagram videos for the DFI
(Don't Fall In) crate-digging series.

Each video: the track's cover art spinning like a vinyl record — centre spindle
hole, motion blur — with the DFI logo top-left, the track title and artist burnt
in bottom-left, over a 25-second clip of the audio. **1080×1350 (4:5)**, H.264,
AAC stereo.

Two pieces:

- **`generate_video.py`** — the render engine. Pure and importable; `render_video()`
  is the entry point and every setting lives on a `RenderConfig`.
- **`DFI_batch_render.ipynb`** — a Google Colab notebook that drives it: reads the
  sheet, pulls files from Drive, renders, uploads, builds a Spotify playlist.

The notebook **downloads the engine from this repo at run time**, so improvements
ship by pushing here — no re-uploading the notebook.

> A browser-based replacement for the notebook is being explored in parallel.
> See `HANDOFF.md` and `BROWSER_FINDINGS.md`; nothing below is affected by it.

---

## Using it (the team)

See **`TEAM_GUIDE.md`**. Short version: fill in the sheet, drop the audio in the
shared Drive folder, open the notebook, Run all, answer two yes/no questions.

## What a run does

1. **Drop folder** — matches audio dropped in the shared folder to rows still
   waiting for audio, writes the Drive links into the sheet, files the audio into
   a per-batch subfolder. *Asks first.*
2. **Pre-flight** — validates every flagged row **before** downloading anything.
   Blocks on unusable clip starts or malformed links.
3. **Artwork check** — downloads audio in parallel, works out which artwork each
   track will use, shows them all in one grid.
4. **Confirm** — yes/no, in its own cell.
5. **Render + upload** — numbered MP4s (`01 …`, `02 …`) into a Drive subfolder
   named after the tab, so they sort in sheet order.
6. **Spotify** — creates or updates a playlist from the sheet's Spotify links.

## The sheet

Columns the tool reads — **all configurable in the notebook** if they get renamed:

`Track` · `Artist` · `Drive audio file` · `Drive artwork file*` · `Clip start` ·
`Render?` · `Spotify link`

`Clip start` is `mm:ss`. Only `Render? = TRUE` rows render. Blank clip start
warns and begins at 0:00; an unreadable one blocks the run.

Artwork is resolved in this order: **override** from the sheet → **embedded** in
the audio file → the **DFI fallback label** → otherwise the row is skipped.

---

## Running the engine locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Needs **ffmpeg** on your PATH (`brew install ffmpeg`).

```bash
python generate_video.py     # renders the first audio file in INPUT_DIR
pytest                       # the test suite
```

With no audio file present it synthesises a test tone and cover so it can verify
itself end to end, then prints an `ffprobe` report with pass/fail checks.

All standalone settings sit in the `CONFIG` block at the top of
`generate_video.py`; the notebook ignores those and builds its own `RenderConfig`.

### Changing the shape

Set `CANVAS_W` / `CANVAS_H` (1080×1350 for 4:5, 1080×1080 for square). Two
things to keep in sync: use an **overlay of the same shape** — a mismatched one
gets stretched, and the tool will warn you — and keep `CIRCLE_DIAMETER` below the
narrower side.

## Performance notes

A 25-second video renders in **~5.7s**. Three things got it there, and all three
are easy to undo by accident:

- Frames are built in memory and **streamed into a single ffmpeg pass** — not
  written out as PNGs and encoded twice.
- Motion blur is applied to the disc **once**, then that pre-blurred image is
  rotated per frame. Rotational blur commutes with rotation, so blurring every
  frame is redundant work for identical output.
- The per-frame spin uses **bilinear** interpolation. Bicubic is ~2× slower for a
  visible difference of roughly 25 pixels on an 830px disc.

Tests pin all three.

## Never commit

The audio (copyright) or `assets/fonts/` (licensed font, and this repo is
public). `.gitignore` covers both.
