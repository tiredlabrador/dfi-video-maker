#!/usr/bin/env python3
"""
DFI spinning-record video generator — render engine.

Phase 1 gave us a working engine: take ONE local audio file (MP3/FLAC) with
embedded cover art and produce ONE looping MP4 of the artwork spinning like a
record, with a trimmed audio clip underneath.

Phase 2 turns that engine into a reusable callable — `render_video(...)` — so a
Colab notebook can batch-render rows from a Google Sheet. The proven pixel/ffmpeg
behaviour is unchanged; the module-level CONFIG + `main()` still run Phase 1
standalone exactly as before:

    python generate_video.py

Out of scope (both phases): branding/text, fallback artwork card, loudness
normalisation, Spotify fallback.
"""

import io
import math
import os
import re
import shutil
import subprocess
import threading
import sys
import tempfile
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# CONFIG  — used only when running this file standalone (Phase 1). The Colab
# notebook builds its own RenderConfig and ignores these.
# ---------------------------------------------------------------------------
INPUT_DIR           = "0 - Test"   # folder to read the first audio file from
CLIP_START          = "1:13"       # "mm:ss" (or plain seconds) where the clip begins
CLIP_LENGTH_SECONDS = 25           # length of the output clip, in seconds
SPIN_PERIOD_SECONDS = 6            # time for one full 360-degree rotation
FPS                 = 30           # frames per second
CANVAS_W            = 1080         # output width
CANVAS_H            = 1350         # output height (1350 = 4:5; set 1080 for square)
CIRCLE_DIAMETER     = 830          # diameter of the spinning record, in pixels
HOLE_DIAMETER       = 24           # centre spindle hole (0 = no hole)
DISC_OFFSET_Y       = 0            # nudge the record up (-) / down (+) from centre
BG_COLOUR           = "black"      # canvas background (any Pillow colour name/hex)
FALLBACK_PATH       = None         # static PNG used when a track has no artwork
FALLBACK_TEXT       = False        # or True to generate a title/artist card instead
FONT_PATH           = None         # brand font for the card (None = a default)
OVERLAY_PATH        = "assets/overlay-portrait.png"  # must match the canvas shape
MOTION_BLUR_SAMPLES = 10           # frames blended per output frame (1 = no blur)
SHUTTER_FRACTION    = 0.7          # blur amount; 0.5 = 180-degree shutter, higher = more
OUTPUT_DIR          = "output"     # folder the finished MP4 is written to
# ---------------------------------------------------------------------------

AUDIO_EXTENSIONS = (".mp3", ".flac")


# ---------------------------------------------------------------------------
# Config object + exceptions (the importable contract)
# ---------------------------------------------------------------------------
@dataclass
class RenderConfig:
    """All the render settings the engine needs, threaded through explicitly."""
    clip_length_seconds: int = 25
    spin_period_seconds: int = 6
    fps: int = 30
    canvas_w: int = 1080
    canvas_h: int = 1350          # 1080x1350 = 4:5, the Instagram grid format
    circle_diameter: int = 830
    hole_diameter: int = 24       # centre spindle hole, so it reads as a real record
    disc_offset_y: int = 0        # nudge the record up (-) or down (+) from centre
    bg_colour: str = "black"
    overlay_path: str = None      # optional static branding overlay (PNG w/ alpha)
    # Burnt-in caption: the track title + artist, drawn bottom-left. Static — it
    # does not spin with the record.
    caption: bool = True
    caption_colour: str = "white"
    caption_margin: int = 60      # gap from the left and bottom edges
    caption_max_lines: int = 2    # title wraps up to this many lines, then shrinks
    caption_title_size: int = None    # None = scale from the canvas height
    caption_artist_size: int = None   # None = scale from the canvas height
    # Fallback artwork, used only when a track has no embedded art and no override.
    fallback_path: str = None     # a static PNG to use as the disc
    fallback_text: bool = False   # or generate a card with the track title + artist
    fallback_bg: str = "white"    # background of the generated text card
    fallback_fg: str = "black"    # text colour of the generated text card
    font_path: str = None         # brand font (.ttf/.otf); None = pick a default
    encode_preset: str = "veryfast"   # x264 speed/size trade-off (faster = bigger file)
    encode_crf: int = 21              # lower = better quality, bigger file
    motion_blur_samples: int = 1  # 1 = off; >1 enables 180-degree-style motion blur
    shutter_fraction: float = 0.5 # 0.5 = a 180-degree shutter (blur spans half a frame)


class RenderError(Exception):
    """Any failure while rendering (bad input, ffmpeg error, etc.)."""


class NoArtworkError(RenderError):
    """No override artwork was given AND the audio has no embedded cover art."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def require_ffmpeg() -> None:
    """Make sure the ffmpeg/ffprobe binaries are on PATH."""
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            raise RenderError(
                f"'{binary}' not found on PATH. Install it first, e.g.:\n"
                f"    macOS:  brew install ffmpeg\n"
                f"    Ubuntu: sudo apt install ffmpeg"
            )


def parse_timecode(value) -> float:
    """Parse 'mm:ss', 'hh:mm:ss', or plain seconds into a float of seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        raise RenderError(f"Could not parse clip start {value!r}. Use 'mm:ss' or seconds.")
    seconds = 0.0
    for part in parts:              # left-to-right: h, m, s (or m, s / just s)
        seconds = seconds * 60 + part
    return seconds


def run(cmd: list, desc: str) -> None:
    """Run a subprocess, raising with stderr on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RenderError(f"{desc} failed:\n{result.stderr.strip()}")


def sanitise_filename(name: str) -> str:
    """Strip filesystem-illegal characters so `name` is safe as a filename."""
    illegal = '<>:"/\\|?*'
    cleaned = "".join(("_" if c in illegal or ord(c) < 32 else c) for c in name)
    return cleaned.strip(" .") or "untitled"


# ---------------------------------------------------------------------------
# Artwork sourcing (mutagen for embedded art; direct file for overrides)
# ---------------------------------------------------------------------------
def extract_embedded_artwork(audio_path: str):
    """Return embedded cover art as an RGBA Image, or None if there is none."""
    ext = os.path.splitext(audio_path)[1].lower()
    data = None

    if ext == ".mp3":
        from mutagen.id3 import ID3
        try:
            tags = ID3(audio_path)
        except Exception:
            tags = None
        if tags is not None:
            apics = tags.getall("APIC")
            if apics:
                front = next((p for p in apics if p.type == 3), apics[0])
                data = front.data

    elif ext == ".flac":
        from mutagen.flac import FLAC
        flac = FLAC(audio_path)
        if flac.pictures:
            front = next((p for p in flac.pictures if p.type == 3), flac.pictures[0])
            data = front.data

    else:
        raise RenderError(f"Unsupported audio type: {ext}")

    if not data:
        return None
    try:
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as exc:
        raise RenderError(f"Embedded artwork could not be decoded as an image: {exc}")


# Fonts we'll try, in order, when no brand font is configured. First one that
# exists wins. Colab/Ubuntu ships DejaVu; macOS ships Arial/Helvetica.
DEFAULT_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def _load_font(cfg: RenderConfig, size: int):
    """Load the configured brand font, or the first available default."""
    candidates = ([cfg.font_path] if cfg.font_path else []) + list(DEFAULT_FONT_CANDIDATES)
    for path in candidates:
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()      # last resort: tiny bitmap font


def _fit_font(draw, text: str, max_width: int, cfg: RenderConfig, start_size: int):
    """Shrink the font until `text` fits inside `max_width`."""
    size = start_size
    while size > 10:
        font = _load_font(cfg, size)
        box = draw.textbbox((0, 0), text, font=font)
        if (box[2] - box[0]) <= max_width:
            return font
        size -= 2
    return _load_font(cfg, 10)


def _wrap_text(draw, text: str, font, max_width: int):
    """Greedily break `text` into lines that each fit inside `max_width`."""
    lines, current = [], ""
    for word in text.split():
        trial = (current + " " + word).strip()
        if not current or draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_caption(draw, text: str, cfg: RenderConfig, max_width: int,
                 max_lines: int, start_size: int):
    """Shrink the font until `text` wraps into at most `max_lines` lines."""
    size = start_size
    font = _load_font(cfg, size)
    lines = _wrap_text(draw, text, font, max_width)
    while len(lines) > max_lines and size > 12:
        size -= 2
        font = _load_font(cfg, size)
        lines = _wrap_text(draw, text, font, max_width)
    return font, lines[:max_lines]


def _baseline_draw(draw, x: int, baseline_y: int, text: str, font, fill):
    """Draw `text` so its baseline sits on `baseline_y` (PIL draws from the top)."""
    try:
        ascent, _ = font.getmetrics()
    except AttributeError:            # bitmap fallback font
        ascent = getattr(font, "size", 12)
    draw.text((x, baseline_y - ascent), text, font=font, fill=fill)


def draw_caption(layer: Image.Image, track: str, artist: str,
                 cfg: RenderConfig) -> None:
    """
    Draw the track title and artist bottom-left on `layer`, stacked upwards from
    the bottom margin: artist on the last line, title (1..max_lines) above it.
    """
    if not cfg.caption:
        return
    track = str(track or "").strip().upper()
    artist = str(artist or "").strip().upper()
    if not track and not artist:
        return

    draw = ImageDraw.Draw(layer)
    margin = cfg.caption_margin
    max_width = cfg.canvas_w - 2 * margin
    title_size = cfg.caption_title_size or max(12, int(cfg.canvas_h * 0.045))
    artist_size = cfg.caption_artist_size or max(10, int(cfg.canvas_h * 0.028))

    title_font, title_lines = (None, [])
    if track:
        title_font, title_lines = _fit_caption(
            draw, track, cfg, max_width, cfg.caption_max_lines, title_size
        )

    baseline = cfg.canvas_h - margin
    if artist:
        artist_font = _load_font(cfg, artist_size)
        _baseline_draw(draw, margin, baseline, artist, artist_font, cfg.caption_colour)
        baseline -= int(artist_size * 1.45)

    for line in reversed(title_lines):
        _baseline_draw(draw, margin, baseline, line, title_font, cfg.caption_colour)
        baseline -= int(title_size * 1.15)


def build_static_layer(cfg: RenderConfig, track: str = None,
                       artist: str = None) -> Image.Image:
    """
    The everything-that-doesn't-spin layer: branding overlay + burnt-in caption,
    composed once and stamped onto every frame.
    """
    layer = Image.new("RGBA", (cfg.canvas_w, cfg.canvas_h), (0, 0, 0, 0))
    overlay = load_overlay(cfg)
    if overlay is not None:
        layer.alpha_composite(overlay)
    draw_caption(layer, track, artist, cfg)
    return layer


def make_fallback_card(track: str, artist: str, size: int,
                       cfg: RenderConfig) -> Image.Image:
    """
    Generate a plain card with the track title above centre and the artist below,
    leaving the middle clear for the spindle hole. Used when a track has no
    artwork. Text is auto-shrunk so long titles stay inside the disc.
    """
    card = Image.new("RGBA", (size, size), cfg.fallback_bg)
    draw = ImageDraw.Draw(card)

    # Keep text well inside the circle — the card gets a circular mask later.
    max_width = int(size * 0.62)
    centre = size / 2

    for text, y_frac in ((str(track or "").upper(), 0.38),
                         (str(artist or "").upper(), 0.62)):
        if not text:
            continue
        font = _fit_font(draw, text, max_width, cfg, start_size=int(size * 0.075))
        box = draw.textbbox((0, 0), text, font=font)
        x = centre - (box[2] - box[0]) / 2 - box[0]
        y = size * y_frac - (box[3] - box[1]) / 2 - box[1]
        draw.text((x, y), text, font=font, fill=cfg.fallback_fg)

    return card


# ---------------------------------------------------------------------------
# Pre-flight checks — catch bad rows before any downloading or rendering
# ---------------------------------------------------------------------------
def extract_drive_id(link):
    """Pull a Drive file id out of the common share-link shapes (or a bare id)."""
    link = str(link or "").strip()
    for pattern in (r"/d/([A-Za-z0-9_-]{20,})", r"[?&]id=([A-Za-z0-9_-]{20,})"):
        found = re.search(pattern, link)
        if found:
            return found.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", link):
        return link
    return None


def preflight(entries):
    """
    Check rows before the batch runs.

    `entries` are dicts of {row, track, artist, audio_link, clip_start}. Returns
    {ok, errors, warnings, rows: {row: {problems: [{level, message}], ...}}}.
    An "error" would break the run, so it blocks; a "warning" is something worth
    knowing (a blank clip start still works — it just starts at 0:00).
    """
    report = {"rows": {}, "errors": 0, "warnings": 0}

    names = {}
    for entry in entries:
        name = sanitise_filename(
            f"{str(entry.get('artist') or '').strip()} - "
            f"{str(entry.get('track') or '').strip()}"
        )
        names.setdefault(name, []).append(entry.get("row"))

    for entry in entries:
        row = entry.get("row")
        problems = []
        track = str(entry.get("track") or "").strip()
        artist = str(entry.get("artist") or "").strip()
        audio = str(entry.get("audio_link") or "").strip()
        clip = str(entry.get("clip_start") or "").strip()

        if not audio:
            problems.append({"level": "warning",
                             "message": "No audio link — this row will be skipped."})
        elif not (extract_drive_id(audio) or audio.lower().startswith("http")):
            problems.append({"level": "error",
                             "message": f"Audio link isn't a Drive link or URL: {audio[:40]!r}"})

        if not clip:
            problems.append({"level": "warning",
                             "message": "No clip start — the clip will begin at 0:00."})
        else:
            try:
                parse_timecode(clip)
            except RenderError:
                problems.append({"level": "error",
                                 "message": f"Clip start {clip!r} isn't a time like '1:13'."})

        if not track and not artist:
            problems.append({"level": "warning",
                             "message": "No track or artist — the file will be named 'untitled' "
                                        "and the caption will be blank."})

        name = sanitise_filename(f"{artist} - {track}")
        clash = [r for r in names.get(name, []) if r != row]
        if clash:
            problems.append({"level": "warning",
                             "message": f"Duplicate output name — also row(s) {clash}."})

        report["rows"][row] = {"track": track, "artist": artist, "problems": problems}
        report["errors"] += sum(1 for p in problems if p["level"] == "error")
        report["warnings"] += sum(1 for p in problems if p["level"] == "warning")

    report["ok"] = report["errors"] == 0
    return report


def format_preflight(report) -> str:
    """Human-readable pre-flight summary for the notebook output."""
    lines = ["=" * 64, "  PRE-FLIGHT CHECK", "=" * 64]
    for row, info in sorted(report["rows"].items()):
        if not info["problems"]:
            continue
        label = f"{info['artist']} - {info['track']}".strip(" -") or "(blank row)"
        lines.append(f"  row {row}: {label}")
        for problem in info["problems"]:
            mark = "ERROR  " if problem["level"] == "error" else "warning"
            lines.append(f"      [{mark}] {problem['message']}")
    if report["errors"]:
        lines += ["", f"  {report['errors']} error(s) must be fixed before rendering.",
                  "  Nothing has been downloaded or rendered."]
    elif report["warnings"]:
        lines += ["", f"  {report['warnings']} warning(s) — safe to continue."]
    else:
        lines.append("  All rows look good.")
    lines.append("=" * 64)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Matching dropped audio files to sheet rows
# ---------------------------------------------------------------------------
_BRACKETED = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_LEADING_NUM = re.compile(r"^\s*\d{1,3}\s*[-._)\s]")
_SIDE_MARKER = re.compile(r"\b[ab][1-9]\d?\b")          # vinyl sides: A1, B2
_KEY_OR_BPM = re.compile(r"\b(\d{1,2}[ab]|\d{2,3}(\.\d+)?)\b")   # 6A, 126.0, 133
_NOISE_WORDS = {"copy", "mix", "original", "edit", "master", "mp3", "flac", "wav"}


def normalise_for_match(text) -> str:
    """
    Reduce a filename or tag to comparable words: drop extensions, bracketed
    extras, track numbers, vinyl side markers, key/BPM, and filler words.
    """
    s = str(text or "").lower()
    s = re.sub(r"\.(mp3|flac|wav|aiff?|m4a)$", " ", s)
    s = _BRACKETED.sub(" ", s)
    s = s.replace("_", " ").replace("-", " ").replace("&", " ")
    s = _LEADING_NUM.sub(" ", s)
    s = _SIDE_MARKER.sub(" ", s)
    s = _KEY_OR_BPM.sub(" ", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    words = [w for w in s.split() if w and w not in _NOISE_WORDS]
    return " ".join(words)


def _token_key(text) -> str:
    """Order-insensitive, duplicate-insensitive form, so 'artist track' matches
    'track artist' and repeated artist names don't skew the score."""
    return " ".join(sorted(set(normalise_for_match(text).split())))


def _similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    a, b = _token_key(a), _token_key(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def score_file_against_row(file_info: dict, row: dict) -> float:
    """
    Best score from either the file's tags or its filename against the row's
    artist+track. Using both matters: bootlegs often have junk or missing tags,
    while clean rips often have messy filenames.
    """
    target = f"{row.get('artist', '')} {row.get('track', '')}"
    from_tags = f"{file_info.get('artist') or ''} {file_info.get('title') or ''}".strip()
    sources = [file_info.get("filename", "")] + ([from_tags] if from_tags else [])
    scores = [_similarity(source, target) for source in sources]

    # Files often carry extra credits the sheet doesn't — "aka ...", "feat. ...",
    # remixer names. If every word of the row appears in the file, that's a strong
    # match even though the extra words drag the plain similarity down. Needs at
    # least 3 words, so a generic row can't be "contained" in just anything.
    row_words = set(normalise_for_match(target).split())
    if len(row_words) >= 3:
        for source in sources:
            if row_words <= set(normalise_for_match(source).split()):
                scores.append(0.88)
                break
    return max(scores)


def match_files_to_rows(files, rows, auto_threshold: float = 0.82,
                        review_threshold: float = 0.55):
    """
    Match dropped audio files to sheet rows awaiting audio.

    Returns one result per file: {file, row, score, decision, candidates} where
    decision is "auto" (confident — safe to link), "review" (plausible, needs a
    human) or "none". Rows that already have audio are never matched, and no row
    is claimed by two files.
    """
    open_rows = [r for r in rows if not r.get("has_audio")]

    scored = []
    for f in files:
        ranked = sorted(
            ((score_file_against_row(f, r), r) for r in open_rows),
            key=lambda pair: pair[0], reverse=True,
        )
        scored.append((f, ranked))

    # Strongest matches get first claim on a row.
    order = sorted(range(len(scored)),
                   key=lambda i: scored[i][1][0][0] if scored[i][1] else 0.0,
                   reverse=True)

    taken, results = set(), [None] * len(scored)
    for i in order:
        f, ranked = scored[i]
        best_row, best_score = None, 0.0
        for score, row in ranked:
            if row["row"] in taken:
                continue
            best_row, best_score = row, score
            break
        if best_row is not None and best_score >= auto_threshold:
            decision, chosen = "auto", best_row["row"]
            taken.add(chosen)
        elif best_row is not None and best_score >= review_threshold:
            decision, chosen = "review", best_row["row"]
        else:
            decision, chosen = "none", None
        results[i] = {
            "file": f, "row": chosen, "score": round(best_score, 3),
            "decision": decision,
            "candidates": [{"row": r["row"], "track": r["track"],
                            "artist": r["artist"], "score": round(s, 3)}
                           for s, r in ranked[:3]],
        }
    return results


# ---------------------------------------------------------------------------
# Spotify links from the sheet
# ---------------------------------------------------------------------------
_SPOTIFY_ID = r"([A-Za-z0-9]{22})"
_SPOTIFY_PATTERNS = (
    r"open\.spotify\.com/(?:intl-[a-z-]+/)?track/" + _SPOTIFY_ID,   # web link
    r"spotify:track:" + _SPOTIFY_ID,                                 # URI
)


def extract_spotify_track_id(link):
    """
    Pull a Spotify TRACK id out of a link, URI or bare id. Returns None for
    anything else — an album or playlist link can't be added to a playlist.
    """
    text = str(link or "").strip()
    if not text:
        return None
    for pattern in _SPOTIFY_PATTERNS:
        found = re.search(pattern, text)
        if found:
            return found.group(1)
    if re.fullmatch(_SPOTIFY_ID, text):
        return text
    return None


def collect_spotify_tracks(rows):
    """
    Turn sheet rows into an ordered, de-duplicated list of Spotify track ids.

    `rows` are dicts of {row, track, artist, spotify}. Returns
    {tracks, missing, duplicates}: `missing` covers blank links and links that
    aren't tracks, so nothing disappears silently.
    """
    tracks, missing, duplicates, seen = [], [], [], set()
    for row in rows:
        track_id = extract_spotify_track_id(row.get("spotify"))
        label = f"{row.get('artist', '')} - {row.get('track', '')}".strip(" -")
        if not track_id:
            missing.append({"row": row.get("row"), "label": label,
                            "link": str(row.get("spotify") or "").strip()})
            continue
        if track_id in seen:
            duplicates.append(row.get("row"))
            continue
        seen.add(track_id)
        tracks.append({"id": track_id, "row": row.get("row"), "label": label})
    return {"tracks": tracks, "missing": missing, "duplicates": duplicates}


def output_filename(artist, track, position: int = None, total: int = None) -> str:
    """
    Name for a finished video. With `position`, it's prefixed with a zero-padded
    number so the files sort in sheet order rather than alphabetically — which is
    the order they get posted in.
    """
    base = sanitise_filename(f"{str(artist or '').strip()} - {str(track or '').strip()}")
    if position is None:
        return f"{base}.mp4"
    width = max(2, len(str(int(total or position))))
    return f"{str(position).zfill(width)} {base}.mp4"


def plan_playlist_additions(track_ids, existing_ids):
    """Which tracks still need adding, in sheet order. Re-running adds nothing new."""
    already = set(existing_ids or [])
    plan = []
    for track_id in track_ids:
        if track_id not in already:
            plan.append(track_id)
            already.add(track_id)
    return plan


def chunked(items, size: int):
    """Yield `items` in batches — Spotify takes at most 100 tracks per request."""
    items = list(items)
    for start in range(0, len(items), size):
        yield items[start:start + size]


def read_audio_tags(audio_path: str):
    """Read title/artist from an audio file's tags. Missing tags are not an error."""
    result = {"title": None, "artist": None}
    ext = os.path.splitext(audio_path)[1].lower()
    try:
        if ext == ".mp3":
            from mutagen.id3 import ID3
            tags = ID3(audio_path)
            title, artist = tags.get("TIT2"), tags.get("TPE1")
            result["title"] = str(title) if title else None
            result["artist"] = str(artist) if artist else None
        else:
            import mutagen
            tags = mutagen.File(audio_path)
            if tags:
                for key, field in (("title", "title"), ("artist", "artist")):
                    value = tags.get(key) or tags.get(field.upper())
                    if value:
                        result[field] = str(value[0] if isinstance(value, list) else value)
    except Exception:
        pass                      # no tags, or unreadable — the caller copes
    return result


def refine_with_tags(results, rows, read_tags, auto_threshold: float = 0.82,
                     review_threshold: float = 0.55):
    """
    Second pass for files that didn't match confidently on filename alone.

    `read_tags(file_info)` is called ONLY for those files — so the caller only has
    to download the few that need it — and should return {"title", "artist"} or
    None. Rows already claimed by a confident filename match are never taken.
    """
    refined = [dict(r) for r in results]
    taken = {r["row"] for r in refined if r["decision"] == "auto" and r["row"]}
    open_rows = [r for r in rows if not r.get("has_audio")]

    for result in refined:
        if result["decision"] == "auto":
            continue

        tags = read_tags(result["file"])
        if not tags or not (tags.get("title") or tags.get("artist")):
            continue

        enriched = dict(result["file"],
                        title=tags.get("title"), artist=tags.get("artist"))
        ranked = sorted(((score_file_against_row(enriched, row), row)
                         for row in open_rows if row["row"] not in taken),
                        key=lambda pair: pair[0], reverse=True)
        if not ranked:
            continue

        best_score, best_row = ranked[0]
        if best_score <= result["score"]:
            continue                            # tags didn't help

        result["score"] = round(best_score, 3)
        result["candidates"] = [{"row": r["row"], "track": r["track"],
                                 "artist": r["artist"], "score": round(s, 3)}
                                for s, r in ranked[:3]]
        result["matched_on"] = "tags"
        if best_score >= auto_threshold:
            result["decision"], result["row"] = "auto", best_row["row"]
            taken.add(best_row["row"])
        elif best_score >= review_threshold:
            result["decision"], result["row"] = "review", best_row["row"]
    return refined


def format_ingest(results, rows) -> str:
    """Readable summary of what the drop-folder ingest is proposing to do."""
    if not results:
        return ("=" * 68 + "\n  DROP FOLDER\n" + "=" * 68 +
                "\n  No new audio files to bring in — nothing to do.\n" + "=" * 68)

    by_row = {r["row"]: r for r in rows}
    auto = [r for r in results if r["decision"] == "auto"]
    review = [r for r in results if r["decision"] == "review"]
    none = [r for r in results if r["decision"] == "none"]

    lines = ["=" * 68, "  DROP FOLDER", "=" * 68]

    lines.append(f"  WILL LINK ({len(auto)}):")
    if not auto:
        lines.append("      (none)")
    for r in auto:
        row = by_row.get(r["row"], {})
        lines.append(f"      {r['file']['filename'][:44]}")
        lines.append(f"          -> row {r['row']}: "
                     f"{row.get('artist', '')} - {row.get('track', '')}  "
                     f"({r['score']:.2f})")

    if review:
        lines += ["", f"  NEEDS A HUMAN ({len(review)}) — not confident, left alone:"]
        for r in review:
            lines.append(f"      {r['file']['filename'][:44]}")
            for candidate in r["candidates"][:2]:
                lines.append(f"          maybe row {candidate['row']}: "
                             f"{candidate['artist']} - {candidate['track']}  "
                             f"({candidate['score']:.2f})")

    if none:
        lines += ["", f"  NO MATCH ({len(none)}) — no row is waiting for these:"]
        for r in none:
            lines.append(f"      {r['file']['filename'][:44]}")

    lines.append("=" * 68)
    return "\n".join(lines)


def resolve_artwork(audio_path: str, artwork_path, cfg: "RenderConfig" = None,
                    track: str = None, artist: str = None):
    """
    Same resolution as load_artwork, but also reports WHERE the artwork came from:
    "override" | "embedded" | "fallback" | "card". That source is the useful QA
    signal — "embedded" art from an unknown rip is the risky one to eyeball.
    """
    if artwork_path:
        return load_artwork(audio_path, artwork_path, cfg, track, artist), "override"
    if extract_embedded_artwork(audio_path) is not None:
        return load_artwork(audio_path, None, cfg, track, artist), "embedded"
    if cfg is not None and cfg.fallback_path:
        return load_artwork(audio_path, None, cfg, track, artist), "fallback"
    return load_artwork(audio_path, None, cfg, track, artist), "card"


def make_contact_sheet(items, cols: int = 3, thumb: int = 300, pad: int = 14,
                       cfg: "RenderConfig" = None) -> Image.Image:
    """
    Build one labelled grid image from `items` — a list of (image_or_None, label).
    Used for the artwork QA pass: see every cover at once before rendering.
    """
    cfg = cfg or RenderConfig()
    cols = max(1, cols)
    rows = max(1, (len(items) + cols - 1) // cols)
    label_h = max(18, int(thumb * 0.17))
    cell_w, cell_h = thumb + pad, thumb + label_h + pad
    sheet = Image.new("RGB", (pad + cols * cell_w, pad + rows * cell_h), (18, 18, 18))
    draw = ImageDraw.Draw(sheet)
    font = _load_font(cfg, max(11, int(thumb * 0.058)))

    for i, (image, label) in enumerate(items):
        x = pad + (i % cols) * cell_w
        y = pad + (i // cols) * cell_h
        if image is None:                       # a row we couldn't load
            draw.rectangle([x, y, x + thumb, y + thumb], fill=(60, 30, 30))
            draw.text((x + 8, y + thumb // 2), "no image", font=font, fill=(255, 140, 140))
        else:
            t = image.convert("RGB").copy()
            t.thumbnail((thumb, thumb), Image.LANCZOS)
            sheet.paste(t, (x + (thumb - t.width) // 2, y + (thumb - t.height) // 2))
        for j, line in enumerate(str(label).split("\n")[:2]):
            draw.text((x + 2, y + thumb + 4 + j * (label_h // 2)),
                      line[:46], font=font, fill=(235, 235, 235))
    return sheet


def load_artwork(audio_path: str, artwork_path, cfg: "RenderConfig" = None,
                 track: str = None, artist: str = None):
    """
    Resolve the artwork to use, in priority order:
      1. `artwork_path` override, if given.
      2. The audio's embedded cover art.
      3. cfg.fallback_path — a static PNG stand-in.
      4. cfg.fallback_text — a generated card with the track title + artist.
    Raise NoArtworkError if none of those are available.
    """
    if artwork_path:
        try:
            return Image.open(artwork_path).convert("RGBA")
        except Exception as exc:
            raise RenderError(f"Override artwork could not be read ({artwork_path}): {exc}")

    art = extract_embedded_artwork(audio_path)
    if art is not None:
        return art

    if cfg is not None and cfg.fallback_path:
        if not os.path.exists(cfg.fallback_path):
            raise RenderError(f"Fallback artwork not found: {cfg.fallback_path}")
        try:
            return Image.open(cfg.fallback_path).convert("RGBA")
        except Exception as exc:
            raise RenderError(f"Fallback artwork could not be read: {exc}")

    if cfg is not None and cfg.fallback_text:
        return make_fallback_card(track, artist, cfg.circle_diameter, cfg)

    raise NoArtworkError(
        f"No embedded artwork in {os.path.basename(audio_path)}, no override, "
        f"and no fallback configured."
    )


# ---------------------------------------------------------------------------
# Image prep + frame rendering
# ---------------------------------------------------------------------------
def make_record(art: Image.Image, diameter: int, hole_diameter: int = 0) -> Image.Image:
    """
    Centre-crop to square, resize to `diameter`, apply a circular alpha mask.

    If `hole_diameter` > 0, punch a transparent spindle hole through the centre so
    it reads as a real record (the canvas behind shows through).
    """
    w, h = art.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    square = art.crop((left, top, left + side, top + side)).resize(
        (diameter, diameter), Image.LANCZOS
    )

    # Anti-aliased mask: render at 4x then downsample.
    scale = 4
    big = Image.new("L", (diameter * scale, diameter * scale), 0)
    draw = ImageDraw.Draw(big)
    draw.ellipse([0, 0, diameter * scale - 1, diameter * scale - 1], fill=255)
    if hole_diameter and hole_diameter > 0:
        r = (hole_diameter * scale) / 2.0
        c = (diameter * scale) / 2.0
        draw.ellipse([c - r, c - r, c + r, c + r], fill=0)   # punch the hole
    mask = big.resize((diameter, diameter), Image.LANCZOS)

    square.putalpha(mask)
    return square


def _rotate_sharp(record: Image.Image, angle: float,
                  resample=Image.BILINEAR) -> Image.Image:
    """
    One rotation. Negative angle => clockwise (records spin clockwise).

    Bilinear, not bicubic: this runs once per frame (180 per video) and is the
    single biggest cost in a render, and bilinear is ~2x faster. Measured on real
    artwork, only about 25 visible pixels of an 830px disc differ — the large raw
    differences are all in fully transparent areas that never get drawn.
    """
    return record.rotate(-angle, resample=resample, expand=False, center=None)


def _rotate_motion_blurred(record: Image.Image, angle: float, width_deg: float,
                           n_samples: int) -> Image.Image:
    """
    Motion-blurred rotation: average `n_samples` crisp rotations spread across the
    `width_deg` slice the shutter is open for (centred on `angle`). This mimics a
    real camera shutter — the 180-degree rule means width = half of one frame's
    rotation. Alpha is premultiplied before averaging so the circle's soft edge
    blends cleanly instead of darkening.
    """
    offsets = np.linspace(-width_deg / 2.0, width_deg / 2.0, n_samples)
    acc = np.zeros((record.height, record.width, 4), dtype=np.float32)
    for off in offsets:
        # Bicubic here: this runs once per video, not once per frame, so the
        # better filter is effectively free.
        arr = np.asarray(_rotate_sharp(record, angle + off, Image.BICUBIC),
                         dtype=np.float32)
        alpha = arr[:, :, 3:4] / 255.0
        arr[:, :, :3] *= alpha                       # premultiply
        acc += arr
    acc /= n_samples
    mean_alpha = acc[:, :, 3:4] / 255.0
    rgb = np.divide(acc[:, :, :3], mean_alpha,       # un-premultiply
                    out=np.zeros_like(acc[:, :, :3]), where=mean_alpha > 0)
    out = np.concatenate([rgb, acc[:, :, 3:4]], axis=2)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


def iter_frames(record: Image.Image, num_frames: int, cfg: RenderConfig,
                overlay: Image.Image = None, progress=None):
    """
    Yield one full rotation as RGB images, without touching the disk.

    This is the single source of truth for what a frame looks like; render_frames
    writes these out as PNGs, while render_video streams them straight into
    ffmpeg (much faster — no PNG compression and no disk round-trip).

    `progress`, if given, is called with a fraction from 0.0 to 1.0 as frames
    are produced. It observes the render and must never alter it.
    """
    step = 360.0 / num_frames
    n_samples = cfg.motion_blur_samples or 1
    if n_samples > 1:
        spin_source = _rotate_motion_blurred(
            record, 0.0, step * cfg.shutter_fraction, n_samples)
    else:
        spin_source = record

    ox = (cfg.canvas_w - record.width) // 2
    oy = (cfg.canvas_h - record.height) // 2 + cfg.disc_offset_y
    for i in range(num_frames):
        canvas = Image.new("RGBA", (cfg.canvas_w, cfg.canvas_h), cfg.bg_colour)
        canvas.alpha_composite(_rotate_sharp(spin_source, i * step), (ox, oy))
        if overlay is not None:
            canvas.alpha_composite(overlay)
        if progress is not None:
            progress((i + 1) / num_frames)
        yield canvas.convert("RGB")


def render_frames(record: Image.Image, frames_dir: str, num_frames: int,
                  cfg: RenderConfig, overlay: Image.Image = None) -> None:
    """
    Render exactly one full rotation: angles span 0 up to (not incl.) 360.

    If `cfg.motion_blur_samples > 1`, each frame is motion-blurred across half a
    frame of rotation (the 180-degree shutter look). If `overlay` is given (an
    RGBA image the size of the canvas), it is composited on top of every frame —
    static branding that doesn't spin with the record.
    """
    for i, frame in enumerate(iter_frames(record, num_frames, cfg, overlay)):
        frame.save(os.path.join(frames_dir, f"{i:05d}.png"))


def load_overlay(cfg: RenderConfig):
    """Load the branding overlay to canvas size, or None if none is configured."""
    if not cfg.overlay_path:
        return None
    if not os.path.exists(cfg.overlay_path):
        raise RenderError(f"Overlay image not found: {cfg.overlay_path}")
    overlay = Image.open(cfg.overlay_path).convert("RGBA")
    if overlay.size != (cfg.canvas_w, cfg.canvas_h):
        # Same shape, different size => a clean scale. Different shape => the
        # branding would be stretched, so say so loudly rather than ship it squashed.
        if abs(overlay.width / overlay.height - cfg.canvas_w / cfg.canvas_h) > 0.01:
            print(
                f"WARNING: overlay is {overlay.width}x{overlay.height} but the canvas "
                f"is {cfg.canvas_w}x{cfg.canvas_h}. The branding will be STRETCHED. "
                f"Use an overlay matching the canvas shape."
            )
        overlay = overlay.resize((cfg.canvas_w, cfg.canvas_h), Image.LANCZOS)
    return overlay


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------
def encode_spin(frames_dir: str, num_frames: int, spin_path: str,
                cfg: RenderConfig) -> None:
    """Encode the one-rotation frame sequence into a seamless-looping video."""
    run(
        ["ffmpeg", "-y",
         "-framerate", str(cfg.fps),
         "-i", os.path.join(frames_dir, "%05d.png"),
         "-frames:v", str(num_frames),
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-r", str(cfg.fps),
         spin_path],
        "Spin-video encode",
    )


def build_output(spin_path: str, audio_path: str, start: float, out_path: str,
                 cfg: RenderConfig) -> None:
    """Loop the spin video to clip length and mux in the trimmed audio."""
    run(
        ["ffmpeg", "-y",
         "-stream_loop", "-1", "-i", spin_path,      # input 0: looping video
         "-ss", f"{start:.3f}", "-i", audio_path,    # input 1: audio, seeked
         "-map", "0:v:0", "-map", "1:a:0",
         "-t", str(cfg.clip_length_seconds),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(cfg.fps),
         "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k",
         "-movflags", "+faststart",
         out_path],
        "Final mux/encode",
    )


# ---------------------------------------------------------------------------
# The reusable entry point
# ---------------------------------------------------------------------------
def pipe_frames_to(command: list, chunks) -> None:
    """
    Run `command`, feed it `chunks` on stdin, and raise RenderError if it fails.

    The care here is about deadlock. We write hundreds of megabytes of raw
    frames into ffmpeg's input while ffmpeg writes to its error pipe. A pipe
    holds only about 64KB, so if nobody empties the error pipe, a talkative
    ffmpeg stops reading its input and the two processes wait for each other
    for ever — a render that never finishes, with nothing to cancel it.

    So stderr is drained on its own thread for the whole run, and stdout is
    discarded outright since nothing ever reads it.
    """
    process = subprocess.Popen(command, stdin=subprocess.PIPE,
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
    collected = []

    def drain():
        try:
            collected.append(process.stderr.read())
        except Exception:                          # noqa: BLE001
            collected.append(b"")

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    try:
        for chunk in chunks:
            process.stdin.write(chunk)
    except (BrokenPipeError, OSError):
        pass                     # the tool died; its own message says why
    finally:
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    process.wait()
    reader.join(timeout=10)
    if process.returncode != 0:
        message = (collected[0] if collected else b"").decode(errors="replace").strip()
        raise RenderError(f"Render failed:\n{message}")


def stream_render(record, overlay, audio_path, start, output_path,
                  num_frames: int, cfg: RenderConfig, progress=None) -> None:
    """
    Encode the whole clip in ONE ffmpeg pass, streaming frames in over a pipe.

    The spin is only `num_frames` long, so frames are cycled to fill the clip.
    This avoids writing ~150MB of PNGs to disk and re-encoding the video a second
    time — roughly twice as fast as the frames-on-disk route, same output.
    """
    total = int(round(cfg.clip_length_seconds * cfg.fps))
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{cfg.canvas_w}x{cfg.canvas_h}", "-r", str(cfg.fps), "-i", "pipe:0",
        "-ss", f"{start:.3f}", "-i", audio_path,
        "-map", "0:v:0", "-map", "1:a:0",
        "-t", str(cfg.clip_length_seconds),
        "-c:v", "libx264", "-preset", cfg.encode_preset, "-crf", str(cfg.encode_crf),
        "-pix_fmt", "yuv420p", "-r", str(cfg.fps),
        "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k",
        "-movflags", "+faststart", output_path,
    ]
    # Only `num_frames` frames are unique (one rotation); the rest of the clip
    # reuses them. Building the cycle is the slow half, so it gets the bulk of
    # the progress bar and writing to ffmpeg gets the tail.
    cycle = [frame.tobytes()
             for frame in iter_frames(
                 record, num_frames, cfg, overlay,
                 progress=(lambda f: progress(0.7 * f)) if progress else None)]

    def frames():
        for i in range(total):
            if progress is not None and i % 25 == 0:
                progress(0.7 + 0.25 * (i / total))
            yield cycle[i % num_frames]

    pipe_frames_to(command, frames())


def render_video(audio_path: str, artwork_path, clip_start, output_path: str,
                 cfg: RenderConfig, track: str = None, artist: str = None,
                 progress=None) -> str:
    """
    Render one spinning-record MP4.

    audio_path   : local path to the source MP3/FLAC.
    artwork_path : local path to override artwork, or None to use embedded art.
    clip_start   : "mm:ss", "hh:mm:ss", or seconds — where the audio clip begins.
    output_path  : where to write the finished MP4.
    cfg          : RenderConfig with the render settings.

    Returns output_path. Raises NoArtworkError if there is no artwork to use,
    or RenderError for any other failure. Behaviour matches the Phase 1 engine.
    """
    require_ffmpeg()

    num_frames = int(round(cfg.spin_period_seconds * cfg.fps))
    if num_frames < 1:
        raise RenderError("spin_period_seconds * fps must be >= 1 frame.")
    start = parse_timecode(clip_start)

    art = load_artwork(audio_path, artwork_path, cfg, track, artist)  # may raise NoArtworkError
    record = make_record(art, cfg.circle_diameter, cfg.hole_diameter)
    overlay = build_static_layer(cfg, track, artist)   # branding + burnt-in caption

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    stream_render(record, overlay, audio_path, start, output_path, num_frames,
                  cfg, progress=progress)
    if progress is not None:
        progress(1.0)
    return output_path


# ===========================================================================
# Phase 1 standalone CLI (unchanged output behaviour)
# ===========================================================================
def die(msg: str) -> None:
    """Print a clear error and exit non-zero (CLI only)."""
    print(f"\nERROR: {msg}\n", file=sys.stderr)
    sys.exit(1)


def find_audio_file(folder: str) -> str:
    """Return the first MP3/FLAC in `folder`, or synthesise one if none exist."""
    if not os.path.isdir(folder):
        die(f"INPUT_DIR does not exist: {folder}")

    candidates = sorted(
        f for f in os.listdir(folder)
        if f.lower().endswith(AUDIO_EXTENSIONS)
    )
    if candidates:
        chosen = os.path.join(folder, candidates[0])
        print(f"Using audio file: {chosen}")
        return chosen

    print("No audio file found — generating a synthetic test asset.")
    return make_synthetic_asset(folder)


def make_synthetic_asset(folder: str) -> str:
    """Create a colourful PNG + a short tone, embed the art, return the MP3 path."""
    from mutagen.id3 import ID3, APIC, error as id3_error

    png_path = os.path.join(folder, "_synthetic_cover.png")
    mp3_path = os.path.join(folder, "_synthetic_test.mp3")

    size = 700
    img = Image.new("RGB", (size, size))
    px = img.load()
    cx = cy = size / 2
    for y in range(size):
        for x in range(size):
            ang = (math.degrees(math.atan2(y - cy, x - cx)) + 180) / 360
            r = int(255 * ang)
            g = int(255 * (1 - ang))
            b = int(128 + 127 * math.sin(ang * math.pi * 4))
            px[x, y] = (r, g, b)
    ImageDraw.Draw(img).ellipse(
        [size * 0.42, size * 0.42, size * 0.58, size * 0.58], fill=(20, 20, 20)
    )
    img.save(png_path)

    run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i",
         "sine=frequency=220:sample_rate=44100:duration=40",
         "-ac", "2", "-q:a", "4", mp3_path],
        "Synthetic tone generation",
    )

    with open(png_path, "rb") as fh:
        art = fh.read()
    try:
        tags = ID3(mp3_path)
    except id3_error:
        tags = ID3()
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="Cover", data=art))
    tags.save(mp3_path)

    print(f"Synthetic asset created: {mp3_path}")
    return mp3_path


def probe_report(out_path: str, cfg: RenderConfig) -> None:
    """Print an ffprobe summary and check it against the acceptance criteria."""
    def probe(entries, stream=None):
        cmd = ["ffprobe", "-v", "error", "-of", "default=nw=1"]
        if stream:
            cmd += ["-select_streams", stream]
        cmd += ["-show_entries", entries, out_path]
        return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()

    v = dict(
        line.split("=", 1) for line in probe(
            "stream=codec_name,width,height,pix_fmt,avg_frame_rate", "v:0"
        ).splitlines() if "=" in line
    )
    a = dict(
        line.split("=", 1) for line in probe(
            "stream=codec_name,channels,sample_rate", "a:0"
        ).splitlines() if "=" in line
    )
    duration = float(probe("format=duration").split("=", 1)[-1] or 0)

    num, den = (v.get("avg_frame_rate", "0/1").split("/") + ["1"])[:2]
    fps = float(num) / float(den) if float(den) else 0.0

    print("\n" + "=" * 52)
    print("  FFPROBE SUMMARY")
    print("=" * 52)
    print(f"  File          : {out_path}")
    print(f"  Dimensions    : {v.get('width')}x{v.get('height')}")
    print(f"  Video codec   : {v.get('codec_name')} ({v.get('pix_fmt')})")
    print(f"  Frame rate    : {fps:.3f} fps")
    print(f"  Duration      : {duration:.3f} s")
    print(f"  Audio codec   : {a.get('codec_name')} "
          f"({a.get('channels')} ch @ {a.get('sample_rate')} Hz)")
    print("=" * 52)

    checks = [
        (f"{v.get('width')}x{v.get('height')} == {cfg.canvas_w}x{cfg.canvas_h}",
         v.get("width") == str(cfg.canvas_w) and v.get("height") == str(cfg.canvas_h)),
        ("video codec is h264", v.get("codec_name") == "h264"),
        ("pixel format is yuv420p", v.get("pix_fmt") == "yuv420p"),
        (f"frame rate ~= {cfg.fps}", abs(fps - cfg.fps) < 0.5),
        (f"duration ~= {cfg.clip_length_seconds}s (<=0.1s)",
         abs(duration - cfg.clip_length_seconds) <= 0.1),
        ("audio codec is aac", a.get("codec_name") == "aac"),
        ("audio is stereo", a.get("channels") == "2"),
    ]
    print("  ACCEPTANCE CHECKS")
    all_ok = True
    for label, ok in checks:
        print(f"    [{'PASS' if ok else 'FAIL'}] {label}")
        all_ok = all_ok and ok
    print("=" * 52)
    print("  RESULT: " + ("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"))
    print("=" * 52 + "\n")


def main() -> None:
    cfg = RenderConfig(
        clip_length_seconds=CLIP_LENGTH_SECONDS,
        spin_period_seconds=SPIN_PERIOD_SECONDS,
        fps=FPS,
        canvas_w=CANVAS_W,
        canvas_h=CANVAS_H,
        circle_diameter=CIRCLE_DIAMETER,
        hole_diameter=HOLE_DIAMETER,
        disc_offset_y=DISC_OFFSET_Y,
        bg_colour=BG_COLOUR,
        fallback_path=FALLBACK_PATH,
        fallback_text=FALLBACK_TEXT,
        font_path=FONT_PATH,
        overlay_path=OVERLAY_PATH if OVERLAY_PATH and os.path.exists(OVERLAY_PATH) else None,
        motion_blur_samples=MOTION_BLUR_SAMPLES,
        shutter_fraction=SHUTTER_FRACTION,
    )
    try:
        audio_path = find_audio_file(INPUT_DIR)
        base = os.path.splitext(os.path.basename(audio_path))[0]
        out_path = os.path.join(OUTPUT_DIR, f"{base}.mp4")
        render_video(audio_path, None, CLIP_START, out_path, cfg)
    except RenderError as exc:
        die(str(exc))

    print(f"\nDone -> {out_path}")
    probe_report(out_path, cfg)


if __name__ == "__main__":
    main()
