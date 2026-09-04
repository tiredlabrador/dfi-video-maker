> **Outcome (4 September 2026).** The browser rewrite this document was written
> to explore was **investigated and rejected**. What shipped instead is a local
> web app (`app/`) wrapping the existing Python engine unchanged — same quality,
> same speed, no OAuth verification problem, no browser-support limit, and about
> a fifth of the work. The Colab notebook still works and is the fallback.
>
> The browser research was not wasted: `BROWSER_FINDINGS.md` records what the
> browser *can* actually do, measured rather than assumed, if this is ever
> revisited. Section 9 below is the part that is now out of date.

# DFI Video Maker — full context handoff

Everything a fresh session needs to know about this tool: what it does, how it's
built, the mistakes made getting here, and the non-obvious things that will bite
you. Written for a chat exploring **replacing the Colab notebook with a browser
app**.

---

## 1. What it does

Turns a Google Sheet of track recommendations into Instagram videos for a London
DJ collective (**DFI / "Don't Fall In"**, sometimes "The Dig").

Each video: the track's cover art spinning like a vinyl record (with a centre
spindle hole and motion blur), the DFI logo top-left, the track title and artist
burnt in bottom-left, over a 25-second clip of the audio.

**Current flow — one "Run all" in Colab:**

1. **Ingest** — match audio files dropped in a shared Drive folder to sheet rows
   still waiting for audio; write the Drive links into the sheet; file the audio
   into a per-batch subfolder. *Asks y/n first.*
2. **Pre-flight** — validate every flagged row before downloading anything.
   Blocks on errors.
3. **Fetch + artwork check** — download audio in parallel, work out which artwork
   each track will use, show them all in one grid.
4. **Confirm** — y/n.
5. **Render + upload** — numbered MP4s into a Drive subfolder named after the tab.
6. **Spotify** — build/update a playlist from the sheet's Spotify links.

---

## 2. Repo and files

**Public repo:** https://github.com/tiredlabrador/dfi-video-maker (GitHub user
`tiredlabrador`, `gh` CLI authenticated locally).

| File | What it is |
|---|---|
| `generate_video.py` | The engine. Pure, importable, ~1000 lines. All the logic worth reusing. |
| `DFI_batch_render.ipynb` | The Colab launcher. Downloads the engine from GitHub raw at run time. |
| `tests/` | Full suite, all passing. `pytest` at the repo root. |
| `assets/` | `overlay-portrait.png` (1080×1350), `overlay-square.png` (1080×1080), `fallback.png` (830×830). Fonts are **git-ignored**. |
| `TEAM_GUIDE.md` | Plain-English guide for the 3-person team. |
| `BACKLOG.md` | Parked ideas. |

**Local project also contains** (git-ignored): `venv/`, `0 - Test/` (8 real MP3s
used as a test corpus), `output/`, `backups/`, `assets/fonts/SquidBoy.otf`.

---

## 3. The visual spec (must be reproduced exactly)

Canvas **1080×1350** (4:5). Background **black**.

- **Disc**: 830px diameter, centred (lands at x=125, y=260). Artwork is
  centre-cropped to a square, resized, then given an anti-aliased circular mask
  (rendered at 4× and downsampled).
- **Spindle hole**: 24px, punched *transparent* through the disc centre so the
  background shows through — not a painted dot.
- **Spin**: clockwise, one full rotation per 6 seconds, 30fps → 180 unique frames,
  angles spanning 0 up to *but not including* 360 so the loop is seamless.
- **Motion blur**: 180°-shutter style. 10 samples across `shutter_fraction` 0.7
  of one frame's rotation.
- **Overlay**: static PNG the exact size of the canvas, composited on top of every
  frame. Logo currently top-left. Does **not** spin.
- **Caption**: track title then artist, bottom-left, margin 60px, white, in the
  brand font (**Squid Boy**). Title wraps to max 2 lines and shrinks to fit.
  Title size = 4.5% of canvas height, artist = 2.8%. Also static.
- **Output**: H.264 / yuv420p, AAC stereo 44.1kHz, exactly 25.000s, 30fps.
- **Rotation filter**: bilinear per frame (speed); bicubic for the one-off
  pre-blur pass, where it costs nothing.

Full defaults are in `RenderConfig` in `generate_video.py`.

---

## 4. Artwork resolution order

1. Override image from the sheet's artwork column
2. Cover art embedded in the audio file (mutagen)
3. `fallback.png` — the DFI record-label design
4. A generated text card (title + artist) — built but not currently used
5. Otherwise `NoArtworkError`, and that row is skipped

---

## 5. The sheet

**ID** `1S_MFhIt0V8OJWZ8IMYAFf9_bQJ2WVMcJpYLe5uCqlRY`. One tab per batch
("Batch 5" etc.). Columns the tool reads (**all configurable — see mistakes**):

`Track` · `Artist` · `Drive audio file` · `Drive artwork file*` · `Clip start` ·
`Render?` · `Spotify link`

`Clip start` is `mm:ss`. Only `Render? = TRUE` rows render (but ingest links
regardless of that flag).

**Drive IDs:** output `1iW4LFcTWxxA2qze4jga0O7s6EGG5c8cw` · drop folder
`186bEzUNfPb75BGjjfOcEMcyRUZOHbfJY` · overlay/fallback/font live in Drive and are
fetched at run time by link. Google account: **dontfallinldn@gmail.com** (all
three team members share it).

---

## 6. Mistakes made, and what they cost

Read this section. Most of these were found the hard way.

**Sheet columns get renamed, repeatedly.** Twice a run broke because someone
renamed a column (`Audio file` → `Drive audio file`). Fix: every column name is a
config value. **A browser version must do the same.** Also: near-duplicate headers
(`Submitted By` *and* `Submitted by`) silently shifted data one column across —
identical headers would break the read entirely.

**Writing PNG frames to disk was 76% of render time.** 149MB written per video
purely so ffmpeg could read it back, then the video was encoded *twice*. Fix:
build frames in memory, stream raw into one ffmpeg pass.

**Naive motion blur was 4× slower than necessary.** Blurring every frame is
redundant: rotational blur *commutes* with rotation, so blur the disc **once** and
then just rotate the pre-blurred image per frame. Same pixels, a fraction of the
cost. Don't regress this.

**Measure, don't assume.** An "obvious" optimisation (RGB compositing instead of
RGBA) measured at **1.03×** and was discarded. Another (bilinear instead of
bicubic rotation) looked risky because the *maximum* pixel difference was 255 —
but that turned out to be entirely in **fully transparent** pixels that are never
drawn. Only ~25 visible pixels of an 830px disc actually differ, so it shipped:
render went 8.4s → **5.7s**. Check *where* a difference is before trusting it.

**Colab's `input()` box can be unreachable.** Long cell output goes in a
fixed-height scroll pane and the input widget after a big image simply isn't
there — the run looks hung. First fix (print a loud "scroll down" banner) **did
not work**. Real fix: put the question in its **own cell** with nothing above it.

**Overlays get silently stretched.** An overlay whose aspect ratio doesn't match
the canvas is scaled to fit, distorting the logo. Now warns loudly.

**`googleapiclient` clients are not thread-safe.** Parallel downloads sharing one
client corrupt each other's responses in ways that look like random failures.
Each worker thread builds its own.

**Test fixtures had bugs twice**, and both times looked like product bugs: a
hidden `.DS_Store` shifted track indices, and a padding expectation was simply
written wrong. Check whether the *test* is wrong before "fixing" the code.

**Never commit:** the copyrighted MP3s, or the licensed **Squid Boy** font — the
repo is public, so committing the font would be redistributing it. `.gitignore`
enforces both.

---

## 7. Nuances that will bite you

- **Moving a file in Drive keeps its ID**, so links already written into the sheet
  stay valid after the audio is filed away. Looks like it should break; doesn't.
- **Filename-only matching is surprisingly good** — 8/8 on the real test corpus,
  even with track numbers, catalogue codes, BPM/key suffixes and underscores. But
  it fails when the filename has no artist (`03. Hallucinations.mp3`). Fallback:
  download *only* those files and read their ID3 tags. Took Batch 5 from 5/9 to
  9/9 auto-linked, downloading 3 files instead of 10.
- **"Row fully contained in the filename" is a strong signal.** Files carry extra
  credits the sheet doesn't (`aka …`, `feat. …`). Guarded to rows of 3+ words.
- **Never overwrite a populated cell.** Ingest only ever writes into *blank* audio
  cells. A row with a link is refused even on a perfect match.
- **Artwork QA earns its keep** — it caught a "TORRENT DAY" advertising banner
  embedded as cover art in a Soulseek rip that would have gone into a video. Tags
  are clean ~80–90% of the time; bootlegs are the risk.
- **Colab auth is per-runtime-session** and can't be avoided without a service
  account (deliberately rejected: standing credential with Drive access).
- **Google Drive for Desktop is mounted locally**, so the live Colab notebook can
  be edited directly at
  `~/Library/CloudStorage/GoogleDrive-dontfallinldn@gmail.com/My Drive/Colab Notebooks/DFI_batch_render.ipynb`
  — no re-upload. **Must not be open in Colab while editing**, or Colab overwrites.
- **Spotify's refresh token lives in a `_config` folder in Drive**, so the login
  happens once. The client secret is in **Colab Secrets**, never the repo.
- Outputs are numbered `01 …`, `02 …` so they sort in sheet order = posting order.

---

## 8. Testing approach that worked

Two layers, both worth keeping:

1. **Unit tests** on the engine (82, pure functions, fast).
2. **Simulations that execute the notebook's real cells** with Google Drive,
   Sheets, Spotify and `input()` stubbed out. These caught genuine bugs that unit
   tests couldn't — including a crash when pre-flight blocked a run. Scripts live
   in the session scratchpad, not the repo; worth rebuilding in whatever form the
   browser version takes.

TDD throughout: failing test first, then the code. Never weaken a test to pass.

---

## 9. What a browser version has to solve

**The big architectural finding first:**

> **It cannot be a published Claude Artifact, and it cannot be a local `file://`
> page.** Published Artifacts block network calls to non-allowlisted hosts, so
> Google and Spotify APIs are out. Google OAuth refuses `file://` origins. It has
> to be a **hosted static page over HTTPS** — GitHub Pages off the existing repo,
> or Vercel. Settle this before designing anything.

The genuinely hard part is **rendering video in the browser**. Options:

- **Canvas + WebCodecs + an MP4 muxer** — modern, fast, no huge download. Best bet.
- **`ffmpeg.wasm`** — closest to current behaviour, but a 10–25MB download and
  noticeably slower.
- Canvas + `MediaRecorder` — easiest, but gives WebM and imprecise duration.

Things that get *easier* in the browser: Canvas does rotation, masking and
compositing natively; **Spotify PKCE needs no client secret** (so the secret can
be retired); Google APIs support CORS with proper OAuth.

Things that stay hard: reading ID3 tags (needs a JS library), exact 25.000s
duration, and matching the current render quality/speed.

**Suggested approach:** prove the render alone first — one local audio file plus
one image, in the browser, producing a correct MP4. If that works, the rest
(Sheets, Drive, matching, Spotify) is mostly porting logic that's already written
and tested in `generate_video.py`.

---

## 10. How Dom likes to work

In `~/.claude/CLAUDE.md`, but the important ones: vertical slices, TDD (red →
green, no green-hacking), plain English with jargon explained, concise answers,
and give options with a recommendation rather than a menu. He is not a developer —
explain the *why*, not just the *what*.
