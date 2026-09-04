# DFI Video Maker — Backlog

## Done since this list was written

- **Auto-import track files + match them to sheet rows** — shipped. Files dropped
  in the shared folder are matched by filename (and by ID3 tags for the ones a
  filename can't identify), links written into blank cells only, audio filed away
  into a per-batch subfolder.
- **Organise outputs per batch** — shipped. Videos land in a subfolder named after
  the tab, numbered so they sort in sheet order.
- **Spotify playlist per batch** — shipped, built from the sheet's Spotify links.
- **Artwork QA before rendering** — shipped, and it has already caught junk cover
  art from a bootleg rip.

## Open

### 1. Run it without opening Colab
**Being explored now** as a browser app — see `HANDOFF.md` and
`BROWSER_FINDINGS.md`. That supersedes the original idea here (a Sheet button
calling a hosted service), since a browser page removes the server entirely.
The Colab notebook keeps working regardless.

### 2. Render several videos at once
Each video takes ~5.7s and already uses every core for encoding; the remaining
win is rendering multiple tracks in parallel. Real, but firmly diminishing
returns — only worth it if batches get much bigger.

### 3. Buy Music Club lists
**Parked, probably dead.** No public API, and lists have to be built by pasting
Bandcamp links **one at a time** — so generating a block of links saves nothing
over the sheet. Only worth revisiting if they add bulk import, or if DFI decides
to host its own list pages instead (cheap to generate, but loses BMC's audience).

### 4. Loudness normalisation
Never started. Clips are used at whatever level the source file has, so a quiet
track sounds quiet next to a loud one. `ffmpeg`'s `loudnorm` filter would fix it.

## Deliberately rejected

- **Service-account key file for Google auth** — would remove the per-session
  sign-in, but means a standing credential with access to the whole Drive. Not
  worth it to save two clicks.
- **Scripting Bandcamp purchases/downloads** — buying is a financial decision, and
  automating a login session is brittle and against the spirit of the terms.
- **Faster rotation via a cheaper filter** — already taken (bilinear). There is no
  further quality left to trade without it being visible.
