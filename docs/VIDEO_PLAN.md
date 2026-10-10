# Dive In with video — plan

Status: **Steps 1 and 2 done; Step 3 parts 1–2 done (10 Oct 2026).** Next: move the crop.

Step 3 is split into four parts, built in this order:
1. **See the footage while choosing clips** — done. Selecting a clip shows the camera
   footage at that moment (4:5) in the preview; Listen plays it in step with the mix.
   Saved camera sound now lives in `~/Library/Caches/DFI Video Maker/` (kept under 4GB),
   so the slow first read happens once per video, not once per app start.
   Also done: dotted lines on the waveform where one record likely gives way to the next
   (`app/divein/transitions.py`; rough, near the middle of each blend), easier clip
   grabbing, and live D-Log M correction in the preview. D-Log is spotted as 10-bit, non-HDR
   footage, and fixed with DJI's Osmo Action 4 LUT, which lives in `assets/luts/` and is
   kept off GitHub (the repo is public; the file is DJI's). Other Macs need their own copy.
2. **Export video clips** — done. Each filmed clip has a Footage/Photo switch (footage by
   default). Footage clips: 1080×1350 at the camera's frame rate, D-Log corrected, mix
   underneath, no overlays yet. Each is fine-tuned by listening before cutting, then
   measured against the camera's own sound cut in the same pass; the result shows by the
   downloads. Real clips from Oct and May all measured in sync. A footage-only export
   needs no photo (and then makes no square JPG). The first read of a video's sound now
   takes seconds (`app/divein/mp4audio.py` reads only the sound's bytes).
3. **Move the crop** per clip.
4. **Suggested moments**, e.g. big drops in the filmed part of the mix.

## Pick up here next time (Dom's notes, 10 Oct 2026)
- **Rethink the Dive In layout:** the mix should come first (it drives everything now),
  then footage, clips, photo/details, style. Free to rejig where things live and how
  they look now that footage exists.
- **"Video preview isn't working"** (Dom, in Safari). Not yet diagnosed: first ask which
  part (the footage in the preview box, Listen, or "Preview with sound") and on which
  footage. Likely suspect: Safari not letting WebGL read HEVC frames, which blanks the
  D-Log colour-corrected preview. A fallback now shows the plain footage if the corrected
  picture comes out blank. Check in real Safari.
- Then: move the crop per clip (Step 3 part 3), overlays on footage (Step 4).

## Step 1 results (Tom)
- Footage is in `Edits/10.02 - Pirate October/` on the SSD: two 1080p, 25fps files,
  normal colour (not D-Log), mono camera mic.
- Matching the mix against the camera audio worked first time, with strong, clear matches.
  Tom's mix 7:31.18 = video 1 at 0:00 (the camera started late, so his first 7½ minutes
  have no footage).
- **The first test clips were 57.6s out of sync** (a sign error when reading negative
  times) and still looked fine by eye. So by-eye checks can't be trusted: every clip's
  sync must be measured, by matching the clip's audio against the camera's audio.
- Drift is small but not zero: 0.04s (one frame) at mix 12:00, under 0.001s at 22:00 and
  32:00. So the app should fine-tune the match near each clip, not use one number.
- A plain centre 4:5 crop looks fine to Dom, but the app needs a way to move the crop
  for when it isn't (e.g. the DJ drifts out of frame).
- Corrected test clips are in `output/sync-test/`, measured within one frame.

## Step 2 results (finding the mix inside Dive In)
- Under "3 The mix" there's a "Camera footage" box: paste the folder, press Find the mix.
  It says which video holds which part of the mix, and a white strip under the waveform
  shows the filmed parts. Code: `app/divein/footage.py`.
- The first look at a video takes a minute or two (it reads the sound from 17GB); after
  that its sound is saved and a search takes about 10 seconds.
- All six mixes from 2 Oct were found:
  - Tom: video 1, 7:31–35:40. Dom: video 1, all of it. Matt: video 1, all of it.
  - Luke & Ralph: video 1 has 0:00–10:03, video 2 has 10:03–1:12:54.
  - **"Unknown 5-min Piece"** sits in video 2 straight after Luke & Ralph and just before
    Fred, so it's the end of Luke & Ralph or the start of Fred.
  - **Fred: only his first 5:20 was filmed.** Video 2 ends there.
- Camera clock drift measured at 1–38 parts per million (up to ~0.1s over an hour); the
  app follows it.
- Known limit: if a mix's opening record also ends a *different* video, a short wrong
  match at that video's edge is possible. Sound alone can't tell these apart; the
  covered times in the message make it obvious.
- For Step 3: fine-tune the match near each clip before cutting it, and measure each
  finished clip's sync (see Step 1 lesson).

## The idea
Dive In makes 4:5 clips from a DJ photo and their mix. We want to use the real camera
footage from a session instead of the photo, in sync with the music.

The tool works out which second of video goes with which second of the mix by
listening. The camera's own mic recorded the room, so its audio can be matched
against the clean mix.

## Decisions so far
- **Prove the syncing first, with no overlays at all.** No tape, logo, name or rings,
  just footage plus mix audio.
- **The first proof is a one-off test, done outside the app.** We only change the app
  once we know syncing works.
- **Each clip can be a still or video.** For example, a carousel of 2 photo clips and
  2 video clips.
- **The look (black-and-white like now, or colour) is decided later**, after seeing
  ungraded footage.
- **The footage lives on Dom's external SSD**, not Google Drive. That's faster, and it
  doesn't fill the Mac. The SSD has to be plugged in when making clips. The mixes can
  live there too.

## The material
- **Session:** Pirate Studios, 2 Oct 2026. The order was Tom, Dom, Matt, Luke & Ralph
  (back to back), Fred.
- **Footage:** DJI Osmo, about 27GB across 2 files, on the external SSD.
- **Mixes:** already cut out of the room recording, in Google Drive under
  `Mix Recordings/26.10.03 Oct 2nd Pirate Session/`.
  - **"Unknown 5-min Piece"** still needs Dom to say who it belongs to.
  - **Fred's mix** is cut off at the end, because the recording stopped.

## Step 1: one-off sync test (no app changes)
1. Dom gives the SSD path to the footage and names one DJ to test (Fred or Tom are
   good picks).
2. Claude checks the footage: length, resolution, frame rate, and whether it was shot
   in DJI's flat "D-Log" colour mode, which would make it look washed out until graded.
3. Claude matches that DJ's mix against the camera audio to find where it starts in
   the footage. It then double-checks a few points later in the set, because the
   camera and recorder clocks can drift slightly over an hour.
4. Claude makes 2–3 plain 30-second clips at moments Dom picks: footage cropped to 4:5,
   with the clean mix audio underneath.
5. **Dom watches them.** Do the DJ's hands match the music? Drops and scratches are the
   easiest moments to judge.

If the clips are in sync, go to Step 2. If not, we find out why (noisy camera mic,
drift) before building anything.

## Step 2 onwards: into the app (only after Step 1 works)
2. **Syncing inside Dive In:** you point it at the footage, and it says something like
   "Found this mix at 38:59 in video 1".
3. **A photo/video switch per clip**, with the footage simply behind nothing at first,
   plus a way to move the crop.
4. **Bring back the overlays** (tape, name, logo, rings) on video clips.
5. **Choose and add a look:** black-and-white Dive In style, or a colour grade.

Each step is built and checked fully before the next one starts.

## Side note: evening out mix volumes
This can be done reliably: measure each mix's overall loudness, then turn each up or
down to the same target. It's not done yet. Tom's mix is the quietest of this session.
