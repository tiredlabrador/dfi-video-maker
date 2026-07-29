# DFI Video Maker — Team Guide

> Working draft — we'll update this as the tool is finished.

## What it is

A tool that turns a track into a short square video for Instagram: the cover art
spinning like a record, with a snippet of the track playing underneath. You don't
touch any code.

## Make a video — 3 steps

1. **Fill in the sheet** (and make sure **Render?** is set to **TRUE** for that row).
2. **Run it** — open the tool in Google Colab
   (https://colab.research.google.com/drive/1ULpQZgM-NUs_Zxrpg0k-w4rFWZACVgto)
   and click **▶ Run all**. Wait a minute or two.
3. **Get your video** — it appears in the output folder in Drive, named
   `Artist - Track.mp4`.

## Check the artwork first (recommended)

Some tracks carry junk or wrong cover art baked into the file — adverts, tracker
banners, the wrong album. To check before you make anything:

1. In the Config cell set **`PREVIEW_ONLY = True`** and **Run all**.
2. It shows every track's artwork in one grid — labelled with where the art came
   from (`embedded` = from the audio file, `override` = from the sheet,
   `fallback` = the DFI label) and its size. **No videos are made.**
3. Spot a bad one? Put an image link in the **Drive artwork file\*** column to
   replace it.
4. Set `PREVIEW_ONLY = False` and Run all for real.

## Good to know

- **Do several at once** — set Render? = TRUE on as many rows as you like, then
  Run all.
- **Stuck on "Connecting…"?** Reload the page (Cmd/Ctrl + R) and click Run all
  again. Nothing is lost.
- **Something didn't work?** Copy the message the tool printed at the bottom and
  send it to Claude or ChatGPT — it'll tell you what went wrong.
