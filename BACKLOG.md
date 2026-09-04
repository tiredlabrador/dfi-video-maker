# DFI Video Maker — Backlog

## Where this is going (decided 4 September 2026)

**The Google Sheet is no longer part of making videos.** Videos are generated
straight from track files in the local app. The sheet stays as a place to keep
links and notes, but the tool doesn't read it and doesn't need to.

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

Worth being precise about what it actually needs, because it is easy to
over-estimate: this needs **Spotify credentials, not Google ones**. The browser
version of Spotify's login (PKCE) needs no client secret at all, so the secret
currently sitting in Colab can be retired rather than moved. The one real
question is where a returning login gets stored now that there is no Drive
`_config` folder — most likely a small file next to the app.

Google authentication is only needed if we ever want the app to **upload to
Drive** by itself. With the sheet gone and a zip download working, that may
never be wanted.

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
