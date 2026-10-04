# DFI Video Maker — Team Guide

Makes DFI's Instagram videos on your Mac. You don't touch any code.

**Set it up once** — see `INSTALL.md`, or https://tiredlabrador.github.io/dfi-video-maker/

**Open it** — double-click **run** in the `dfi-video-maker` folder. It opens in
your browser and updates itself every time. Leave the Terminal window open while
you work.

## The Dig — track recommendations

A spinning record of the track's cover art, with the title and artist.

1. Click **A whole batch** and drag in the audio files.
2. Check the track and artist it filled in, set each clip start, and use the
   arrows to put them in posting order.
3. Press **Render the batch**, then **Download all**. Files are numbered `01`,
   `02`… in posting order.

Check the artwork before rendering: bootlegs sometimes have adverts or the wrong
album baked in as cover art. **One track** lets you swap in different artwork.

## Dive In — the mix series

A photo of the DJ, hazard tape with the episode number, and rings that move with
the music. Makes a 4:5 video per clip plus a 1:1 JPG for SoundCloud.

1. **Photo** — drop it in (or paste it, or drop it on the preview). Drag the
   preview to move it; scroll or pinch to zoom.
2. **Details** — the artist's name and the episode, e.g. `02.01`.
3. **The mix** — drop it in. Click on the mix's outline where each clip should
   start; drag a clip to move it; **Listen** plays it.
4. **Style** — the colour, and whether the rings follow the volume (the
   default) or the kick drums.
5. **Preview with sound** to check one, then **Export**, then **Download all**.

If a name uses a letter the brand font doesn't have (Ł, Č, Ж and the like), the
page warns you — it won't appear in the video.

## If something goes wrong

- **The page says ffmpeg or a font is missing** — see `INSTALL.md`.
- **The browser can't connect** — the Terminal window was closed. Double-click
  **run** again.
- **Anything else** — copy the message and send it to Dom.

The old Colab notebook still works as a fallback — see `docs/COLAB_GUIDE.md`.
