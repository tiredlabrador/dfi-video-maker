# DFI Video Maker — Backlog

## Where this is going (decided 4 September 2026)

**The Google Sheet is no longer part of making videos.** Videos are generated
straight from track files in the local app. The sheet stays as a place to keep
links and notes, but the tool doesn't read it and doesn't need to.

(It becomes relevant again for Spotify playlists, since that is where the
Spotify links live — but there are ways to get those across without the Google
API. See item 5 under *Open*.)

That removes a lot at once:

- No Google Sheets API, no Drive API, no OAuth, no app-verification review.
- No filename-to-row matching, no ingest step, no pre-flight validation of rows.
- No per-batch Drive subfolders — you download a zip.

The Colab notebook still does the full sheet round trip and still works. It is
the fallback, not the direction.

**Nothing outstanding is blocked on anything from Dom.** The only credentials
still on the list are for Spotify, and Spotify is parked (below).

---

## Done

- **The local app** — `./run` on your own Mac, opens in the browser. One track or
  a whole batch, tags read automatically, artwork checked before rendering,
  numbered outputs, zip download. Same engine as the notebook, so the two cannot
  drift apart in what they produce. ~5.6s per video against Colab's 8.4s.
- **Self-updating** — pulls from GitHub on every start, so there is never a
  version for the team to download by hand.
- **Auto-import + sheet matching, per-batch output folders, Spotify playlists,
  artwork QA** — all shipped in the notebook. The first two are now moot for the
  app; artwork QA carried over and earns its keep.

---

## Open, in the order worth doing

### 1. Loudness normalisation
The clearest remaining quality win, and it affects every video. Clips currently
play at whatever level the source file happens to have, so a quiet track sounds
quiet next to a loud one in the feed. `ffmpeg`'s `loudnorm` filter fixes it.
Wants a visible on/off control and a check that it doesn't squash dynamics on
already-loud masters.

### 2. Remember settings between runs
Clip start, and any per-batch choices, are re-entered every time. Small, but it
is friction on every single use.

### 3. Save straight to a folder
Right now everything comes out as one zip through the browser. Letting the app
write finished videos into a folder you pick would remove the unzip step. Worth
doing once the app is in real weekly use and the annoyance is proven.

### 4. Render several videos at once
Each video takes ~5.6s and already uses every core for encoding; the remaining
win is rendering multiple tracks in parallel. Real, but diminishing returns —
a batch of ten is about a minute. Only worth it if batches get much bigger.

### 5. Spotify playlist from the app — **parked**
Waiting on DFI starting to pay, so not scheduled.

**The sheet comes back into the picture here**, because it is where the Spotify
links live. But it does not have to bring Google authentication with it — that
is the part worth not assuming. Three ways to get the links across:

1. **Paste the column.** A Spotify link box on each batch row; copy the column
   out of the sheet, paste once. No API, no auth, no setup.
2. **Drop the exported CSV in.** Sheets publishes a tab as CSV; the app matches
   its rows to the loaded tracks. This reuses the matching logic already written
   and tested in `generate_video.py`. Still no auth.
3. **Read the sheet through the Google API.** The nicest experience, and the
   only one needing OAuth. Before committing to it, check whether the
   `drive.file` scope plus the Google Picker avoids the app-verification review
   — it grants access only to the one file the user picks and is not classed as
   sensitive. Verify this rather than assuming it.

**Recommendation: start at 2, keep 3 as an upgrade.** The CSV route is about an
hour's work, needs nothing from Google, and reuses tested code. Move to 3 only
if exporting a CSV each time proves annoying in real use — by which point we
will also know whether the verification question is real.

**Rejected: auto-searching Spotify by track and artist.** It would need no sheet
at all, but DFI's material is full of dubs, edits, bootlegs and white labels.
Plenty are not on Spotify, and the ones that are will match the wrong version. A
human putting the link in is doing real work, which is why the sheet has that
column.

On credentials: this needs **Spotify** credentials, not Google ones. The browser
login flow (PKCE) needs no client secret, so the secret currently in Colab
Secrets can be retired rather than moved. The one open question is where a
returning login is stored now there is no Drive `_config` folder — most likely a
small file next to the app.

Google authentication is only needed if we ever want the app to **upload to
Drive** by itself, or if we take option 3 above.

---

## Deliberately rejected

- **A browser-based renderer** — investigated properly in September 2026 and
  rejected. Bandwidth would move from Google's datacentre onto a home
  connection, Google OAuth verification was an open-ended risk, WebCodecs is
  realistically Chrome-only, and losing ffmpeg means losing tolerance for odd
  audio files — which matters when much of the source is bootlegs and Soulseek
  rips. Measurements kept in `BROWSER_FINDINGS.md` in case it is revisited.
- **Service-account key file for Google auth** — a standing credential with
  access to the whole Drive, to save two clicks. Doubly moot now.
- **Scripting Bandcamp purchases/downloads** — buying is a financial decision,
  and automating a login session is brittle and against the spirit of the terms.
- **Buy Music Club lists** — no public API, and lists are built by pasting
  Bandcamp links one at a time, so generating a block of links saves nothing.
  Revisit only if they add bulk import.
- **Faster rotation via a cheaper filter** — already taken (bilinear). No
  quality left to trade without it being visible.
