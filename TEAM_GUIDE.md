# DFI Video Maker — Team Guide

Makes DFI's Instagram videos on your Mac. You don't touch any code.

**Set it up once** — see `INSTALL.md`, or https://tiredlabrador.github.io/dfi-video-maker/

**Open it** — double-click **run** in the `dfi-video-maker` folder. It opens in
your browser and updates itself every time. Leave the Terminal window open while
you work.

## The Dig — track recommendations

A spinning record of the track's cover art, with the title and artist.

1. Drag the audio files onto the page (MP3, FLAC, WAV, AIFF or M4A). One track
   or a whole batch: it's the same list.
2. Check each track's title and artist. Click the waveform where the 25-second
   clip should start (or drag the yellow clip); ▶ plays it.
3. Drag the ⠿ handle to put tracks in posting order: files are numbered `01`,
   `02`… to match.
4. **Preview** makes a quick small version; **Render** makes the real ones,
   then **Download all**.

Check the artwork before rendering: bootlegs sometimes have adverts or the wrong
album baked in as cover art. Click a track's artwork to swap in another image.

## Dive In — the mix series

A 4:5 video per clip, made from the DJ's photo or the camera footage, plus a
1:1 JPG for SoundCloud.

1. **Mix**: drop it anywhere on the page.
2. **Camera footage** (optional): click the box to choose the footage folder, or
   drop the folder anywhere on the page. The white strip on the timeline shows
   what was filmed.
3. **Photo** (optional): for photo clips and the JPG. **Details**: the artist's
   name and the episode, e.g. `02.01`.
4. **Clips**: click the timeline to move the playhead (the viewer shows that
   moment), then **+ Clip** (or double-click) to add a clip there. Drag a clip
   to move it. Each clip uses the footage or the photo: switch it above the
   viewer. Drag the picture to frame it; scroll to zoom.
5. **Style** on the right: the footage look and the graphics.
6. **Preview clip** to check one with sound, then **Export**.

Keys: Space plays and pauses; ← → nudge the clip (Shift: 5 seconds); A adds a
clip; Delete removes it; [ and ] jump between clips.

The page remembers where you were, even after a restart. If a name uses a letter
the brand font doesn't have (Ł, Č, Ж and the like), the page warns you: it won't
appear in the video.

## If something goes wrong

- **The page says ffmpeg or a font is missing** — see `INSTALL.md`.
- **The browser can't connect** — the Terminal window was closed. Double-click
  **run** again.
- **Anything else** — copy the message and send it to Dom.

The old Colab notebook still works as a fallback — see `docs/COLAB_GUIDE.md`.
