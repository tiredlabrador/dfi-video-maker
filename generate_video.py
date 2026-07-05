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
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# CONFIG  — used only when running this file standalone (Phase 1). The Colab
# notebook builds its own RenderConfig and ignores these.
# ---------------------------------------------------------------------------
INPUT_DIR           = "0 - Test"   # folder to read the first audio file from
CLIP_START          = "1:13"       # "mm:ss" (or plain seconds) where the clip begins
CLIP_LENGTH_SECONDS = 25           # length of the output clip, in seconds
SPIN_PERIOD_SECONDS = 6            # time for one full 360-degree rotation
FPS                 = 30           # frames per second
CANVAS_W            = 1080         # output width  (1080 = 1:1 square)
CANVAS_H            = 1080         # output height (set 1350 for 4:5, etc.)
CIRCLE_DIAMETER     = 790          # diameter of the spinning record, in pixels
BG_COLOUR           = "black"      # canvas background (any Pillow colour name/hex)
OVERLAY_PATH        = "assets/overlay.png"  # static branding overlay (None to skip)
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
    canvas_h: int = 1080
    circle_diameter: int = 790
    bg_colour: str = "black"
    overlay_path: str = None      # optional static branding overlay (PNG w/ alpha)
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


def load_artwork(audio_path: str, artwork_path):
    """
    Resolve the artwork to use, in priority order:
      1. `artwork_path` override, if given.
      2. Otherwise the audio's embedded cover art.
    Raise NoArtworkError if neither is available.
    """
    if artwork_path:
        try:
            return Image.open(artwork_path).convert("RGBA")
        except Exception as exc:
            raise RenderError(f"Override artwork could not be read ({artwork_path}): {exc}")

    art = extract_embedded_artwork(audio_path)
    if art is None:
        raise NoArtworkError(
            f"No embedded artwork in {os.path.basename(audio_path)} and no override supplied."
        )
    return art


# ---------------------------------------------------------------------------
# Image prep + frame rendering
# ---------------------------------------------------------------------------
def make_record(art: Image.Image, diameter: int) -> Image.Image:
    """Centre-crop to square, resize to `diameter`, apply a circular alpha mask."""
    w, h = art.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    square = art.crop((left, top, left + side, top + side)).resize(
        (diameter, diameter), Image.LANCZOS
    )

    # Anti-aliased circular mask: render at 4x then downsample.
    scale = 4
    big = Image.new("L", (diameter * scale, diameter * scale), 0)
    ImageDraw.Draw(big).ellipse(
        [0, 0, diameter * scale - 1, diameter * scale - 1], fill=255
    )
    mask = big.resize((diameter, diameter), Image.LANCZOS)

    square.putalpha(mask)
    return square


def _rotate_sharp(record: Image.Image, angle: float) -> Image.Image:
    """One crisp rotation. Negative angle => clockwise (records spin clockwise)."""
    return record.rotate(-angle, resample=Image.BICUBIC, expand=False, center=None)


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
        arr = np.asarray(_rotate_sharp(record, angle + off), dtype=np.float32)
        alpha = arr[:, :, 3:4] / 255.0
        arr[:, :, :3] *= alpha                       # premultiply
        acc += arr
    acc /= n_samples
    mean_alpha = acc[:, :, 3:4] / 255.0
    rgb = np.divide(acc[:, :, :3], mean_alpha,       # un-premultiply
                    out=np.zeros_like(acc[:, :, :3]), where=mean_alpha > 0)
    out = np.concatenate([rgb, acc[:, :, 3:4]], axis=2)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


def render_frames(record: Image.Image, frames_dir: str, num_frames: int,
                  cfg: RenderConfig, overlay: Image.Image = None) -> None:
    """
    Render exactly one full rotation: angles span 0 up to (not incl.) 360.

    If `cfg.motion_blur_samples > 1`, each frame is motion-blurred across half a
    frame of rotation (the 180-degree shutter look). If `overlay` is given (an
    RGBA image the size of the canvas), it is composited on top of every frame —
    static branding that doesn't spin with the record.
    """
    step = 360.0 / num_frames
    n_samples = cfg.motion_blur_samples or 1

    # Motion blur commutes with rotation about the same centre: blurring the record
    # then spinning it gives the same frame as blurring every spun frame. So we blur
    # the record ONCE here, then just do a single cheap rotation per frame — same
    # look as per-frame blur, but ~n_samples times faster.
    if n_samples > 1:
        blur_width = step * cfg.shutter_fraction     # degrees the shutter is open
        spin_source = _rotate_motion_blurred(record, 0.0, blur_width, n_samples)
    else:
        spin_source = record

    for i in range(num_frames):
        rotated = _rotate_sharp(spin_source, i * step)
        canvas = Image.new("RGBA", (cfg.canvas_w, cfg.canvas_h), cfg.bg_colour)
        ox = (cfg.canvas_w - record.width) // 2
        oy = (cfg.canvas_h - record.height) // 2
        canvas.alpha_composite(rotated, (ox, oy))
        if overlay is not None:
            canvas.alpha_composite(overlay)      # static branding, on top
        canvas.convert("RGB").save(os.path.join(frames_dir, f"{i:05d}.png"))


def load_overlay(cfg: RenderConfig):
    """Load the branding overlay to canvas size, or None if none is configured."""
    if not cfg.overlay_path:
        return None
    if not os.path.exists(cfg.overlay_path):
        raise RenderError(f"Overlay image not found: {cfg.overlay_path}")
    overlay = Image.open(cfg.overlay_path).convert("RGBA")
    if overlay.size != (cfg.canvas_w, cfg.canvas_h):
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
def render_video(audio_path: str, artwork_path, clip_start, output_path: str,
                 cfg: RenderConfig) -> str:
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

    art = load_artwork(audio_path, artwork_path)      # may raise NoArtworkError
    record = make_record(art, cfg.circle_diameter)
    overlay = load_overlay(cfg)                        # static branding, or None

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dfi_frames_") as frames_dir:
        render_frames(record, frames_dir, num_frames, cfg, overlay=overlay)
        spin_path = os.path.join(frames_dir, "spin.mp4")
        encode_spin(frames_dir, num_frames, spin_path, cfg)
        build_output(spin_path, audio_path, start, output_path, cfg)

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
        bg_colour=BG_COLOUR,
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
