# DFI Video Maker — Backlog / future ideas

Things we've deliberately **parked** to keep focus. Not lost — just not now.

## 1. Trigger a render without opening Colab (a "button")
- **Goal:** a button (e.g. in the Google Sheet) or a schedule that kicks off
  rendering, instead of opening Colab and clicking Run all.
- **Why it's not trivial:** Colab can't be triggered from the outside, and Apps
  Script can't render video itself (no video muscle). The real fix is to host the
  render engine on a small always-on cloud service that a button/schedule can
  call.
- **When:** after the tool's look-and-feel (styling) is finished.

## 2. Auto-import track files + match them to sheet rows
- **Goal:** the team types Artist/Track into the sheet; Tom drops the downloaded
  audio files into a Drive folder; a tool then matches each file to the right row
  and fills in its Drive link automatically — no manual copying.
- **Why it's not trivial:** the filenames are messy and inconsistent, so matching
  is fuzzy and won't be 100%. Correct design is "auto-link the confident matches,
  flag the uncertain ones for a human to confirm" — never silently link the wrong
  track.
- **When:** parked until styling is done; revisit if manual linking becomes a
  real daily pain.

## 3. (Nice-to-have) Organise outputs per batch
- Drop finished videos into a per-batch output folder rather than one shared
  folder. Small config change when we want it.
