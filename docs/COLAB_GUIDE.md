# The Colab notebook — team guide

The original way of making The Dig videos, kept as a fallback. It reads the
sheet, pulls audio from Drive, uploads the videos and builds the Spotify
playlist. It still works, but it's no longer being developed: the app on your
Mac is quicker and easier (see `TEAM_GUIDE.md`).

Open it here: https://colab.research.google.com/drive/1ULpQZgM-NUs_Zxrpg0k-w4rFWZACVgto

## Making a batch — the short version

1. **Fill in the sheet** — Track, Artist, Spotify link, Clip start (`mm:ss`), and
   tick **Render?**. Use one tab per batch.
2. **Drop the audio files** into the shared Drive drop folder. No renaming, no
   uploading anywhere else, no copying links.
3. **Open the tool** and click **▶ Run all**:
   https://colab.research.google.com/drive/1ULpQZgM-NUs_Zxrpg0k-w4rFWZACVgto
4. **Answer two questions** as it goes (below).
5. **Collect the videos** from the output folder in Drive — in a subfolder named
   after your tab, numbered `01`, `02`, … in sheet order, ready to post.

A Spotify playlist for the batch is created automatically at the end.

## The two questions it asks

**"Link these files into the sheet?"** — it has matched the audio you dropped to
your rows and wants to fill in the links. Check the list looks right, then `y`.
It only ever fills **blank** cells; it will never overwrite a link you already
put in.

**"Render these videos?"** — it shows you every track's artwork in one grid
first. This is your chance to catch bad cover art — some files, especially
bootlegs, have adverts or the wrong album baked in. Answer `n` if something's
wrong, fix it, and run again.

Each question is in its own cell, so the answer box appears right below it. Type
`y` and press Enter.

## Before it renders, it checks your rows

If something would break the run it stops and tells you **before** anything is
downloaded — a clip start it can't read, or a broken audio link. Warnings (like a
blank clip start, which just means the clip begins at 0:00) don't stop it.

## Fixing bad artwork

Put an image link in the **Drive artwork file\*** column for that row. That
overrides whatever is in the audio file. Tracks with no artwork at all fall back
to the DFI record label design.

## If something goes wrong

- **A row failed** — usually a dead Drive link, or the file isn't shared. The
  summary at the bottom says which row and why. Other rows are unaffected.
- **A file wasn't matched** — its filename probably doesn't say who the artist
  is. It'll be listed rather than guessed at; just paste that one link in by hand.
- **Stuck on "Connecting…"** — reload the page (Cmd/Ctrl + R) and Run all again.
  Nothing is lost.
- **It looks frozen** — it's probably waiting for a `y`. Check the bottom of the
  cell that's running.
- **Anything else** — copy the message it printed and send it to Claude or
  ChatGPT.

## Only want to check the artwork?

Set **`PREVIEW_ONLY = True`** in the Config cell and Run all. It shows every
cover and makes no videos. Set it back to `False` when you're ready.

---

# How the notebook works

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

## The sheet it reads

Columns the tool reads — **all configurable in the notebook** if they get renamed:

`Track` · `Artist` · `Drive audio file` · `Drive artwork file*` · `Clip start` ·
`Render?` · `Spotify link`

`Clip start` is `mm:ss`. Only `Render? = TRUE` rows render. Blank clip start
warns and begins at 0:00; an unreadable one blocks the run.

Artwork is resolved in this order: **override** from the sheet → **embedded** in
the audio file → the **DFI fallback label** → otherwise the row is skipped.
