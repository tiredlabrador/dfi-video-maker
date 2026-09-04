# DFI Video Maker — Team Guide

Turns tracks into spinning-record videos for Instagram. You don't touch any code.

There are two ways to run it.

## The app on your Mac (quicker, nicer)

Set it up once — see **`INSTALL.md`**, or https://tiredlabrador.github.io/dfi-video-maker/

Then: double-click **run**, drag your audio files in, check the details it filled
in for you, press **Render the batch**, download the zip. It numbers everything in
posting order.

It works from files on your computer, so it doesn't read the sheet or put anything
in Drive — you do that bit yourself.

## The Colab notebook (does the whole round trip)

Still works exactly as it always has. Use this when you want the sheet read, the
audio pulled from Drive, the videos uploaded, and the Spotify playlist built.

### Making a batch — the short version

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
