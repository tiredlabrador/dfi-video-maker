# DFI Video Maker

Makes Instagram videos for DFI (Don't Fall In), on your own Mac:

- **The Dig** — a track's cover art spinning like a vinyl record, with the DFI
  logo, title and artist, over a 25-second clip. One track or a whole batch.
- **Dive In** — the mix series: a treated photo of the DJ, hazard tape with the
  episode number, and a ring coil that moves with the music. A 4:5 video per
  clip plus a 1:1 JPG for SoundCloud.

Both are 1080×1350, H.264, AAC stereo. Start the app with `./run`; it opens in
your browser and pulls the latest version from GitHub each time.

## What's where

- **`generate_video.py`** — The Dig's render engine. Also used by the old Colab
  notebook (`DFI_batch_render.ipynb`), which downloads it from this repo at run
  time — so both always produce the same videos.
- **`app/`** — the local web app: the server and The Dig's page.
- **`app/divein/`** — Dive In. Every number lives in `app/divein/config.py` and
  can be overridden from the Tuning box on the page.
- **`assets/`** — the logo overlays, fallback artwork and the hole motif. The
  licensed fonts go in `assets/fonts` and are never committed.

## Using it

Setup: `INSTALL.md`. How to make each kind of video: `TEAM_GUIDE.md`. The
Colab notebook and the sheet it reads: `docs/COLAB_GUIDE.md`. Plans and
decisions: `BACKLOG.md`.

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
