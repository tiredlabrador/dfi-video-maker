# UI redesign (Oct 2026)

Why: features were added one at a time, so the pages grew into long forms with a
lot of explanatory copy, and the things you use together sat far apart.

## Principles
- **One screen, no scrolling** on a laptop: things you use together sit together.
- **Show, don't explain.** Short labels; guidance appears only in empty states
  and tooltips, at the moment it's needed.
- **Context, not modes.** Controls follow what's selected: a footage clip shows
  framing for footage, a photo clip shows the photo's framing.
- **Nothing is lost** on a reload or restart (Dive In remembers the session).

## Shared shell
One top bar on both pages: the DFI mark, a The Dig / Dive In switch, the
app's status, and that page's main actions on the right. Problems and notes
pop up as messages near the top, not in a box at the bottom of a panel.

## Dive In: an editor
- **Left, Project:** mix, footage folder, photo, artist and episode.
- **Middle, Viewer:** a 4:5 preview of whatever is under the playhead, with
  play/pause and previous/next clip. Above it, the clip under the playhead:
  its times, Footage/Photo, delete.
- **Right, Style:** the footage look, the graphics (colour, rings, twitch),
  advanced tuning.
- **Bottom, Timeline:** the whole mix, a close-up with a playhead.
  Click to move the playhead (the viewer shows that moment: browse the
  footage anywhere, not just inside clips); double-click or "+ Clip" to add a
  clip; drag a clip to move it. Space plays and pauses.
- **Export** opens a panel: what will be made, progress, downloads and each
  footage clip's measured sync.

## The Dig: one track list
"One track" and "A whole batch" become one list (a single track is a list of
one). Each track shows its artwork (click to replace), title, artist and a mini
waveform to pick the 25-second clip and listen to it. Preview or render one
track or all of them; each row shows its own result and download.
