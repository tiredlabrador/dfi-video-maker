# DFI Video Maker — Backlog

## Where things stand

Both video types are made in the app on your Mac (`./run`), straight from
files — no Google Sheet, Drive or logins involved. The Colab notebook still
works as a fallback for The Dig but is no longer developed
(`docs/COLAB_GUIDE.md`). Nothing below is blocked on anyone.

**Done:** The Dig (one track list, waveform clip picking, ~5s a video, three at
once), self-updating installs, and Dive In (an editor: the mix, camera footage
synced by sound or the DJ's photo, a timeline with a playhead, the Dive In look
on footage, a 4:5 video per clip and a 1:1 JPG). Both pages redesigned in Oct
2026: see `docs/UI_REDESIGN.md`.

## Open, in the order worth doing

1. **Loudness normalisation for The Dig.** Clips play at whatever level the
   source has, so a quiet track sounds quiet next to a loud one. ffmpeg's
   `loudnorm` fixes it; wants an on/off switch and a check it doesn't squash
   already-loud masters.
2. **The Dig remembers its track list between visits**, as Dive In now does
   (needs the server to hand back a track's audio for its waveform).
3. **Dive In: tape, name and rings on footage clips** (Step 4 of
   `docs/VIDEO_PLAN.md`).
4. **Save straight to a folder** instead of downloading a zip — once the
   unzipping step has proved annoying in real use.
5. **Spotify playlists from the app — parked** until DFI starts paying. The
   links live in the sheet; the simplest route is dropping the sheet's exported
   CSV into the app (no Google login, and the matching code already exists).
   Spotify's own login needs no secret, so the one in Colab can be retired.
   Don't auto-search Spotify by name: dubs, edits and bootlegs either aren't
   there or match the wrong version.

## Deliberately rejected

- **Rendering in the browser** (Sept 2026) — slower uploads over home
  broadband, Google app-review risk, Chrome-only, and less tolerant of odd
  audio files. Details in `docs/history/`.
- **A Google service-account key** — a standing credential with access to the
  whole Drive, to save two clicks.
- **Scripting Bandcamp purchases** — a financial decision, and brittle.
- **Buy Music Club lists** — no API; links go in one at a time.
