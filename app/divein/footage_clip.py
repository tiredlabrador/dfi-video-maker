"""
Cutting a clip out of camera footage, in sync with the mix.

Three steps per clip:

1. Fine-tune. The whole-mix match is accurate to a few hundredths of a second,
   but camera clocks wander a little. So the clip's own stretch of the mix is
   matched against the camera's sound just around where it should be.
2. Cut. The picture starts exactly on a video frame, and the mix is started at
   the matching instant (not the nearest whole frame), so the sound is placed
   to well under a millisecond.
3. Measure. The finished file's sound is matched against the camera's own
   sound for the same frames. If they disagree by more than a frame, the clip
   is reported as out of sync, rather than trusted.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import numpy as np

from app.divein.footage import MATCH_SR, camera_audio, video_time

SEARCH = 0.25             # seconds either side of where the clip should be: under a beat
LISTEN = 15.0             # seconds of the clip used to fine-tune and to measure
APART = 0.1               # a rival line-up at least this far from the best...
CLEAR = 1.2               # ...must be this much weaker for the best to count
MAX_ZOOM = 2.0            # further in, 1080p footage looks soft on a phone
MIN_ZOOM = 0.1            # asked-for zooms below the whole picture stop at the whole picture
MIN_LIKENESS = 0.15       # and the best must be at least this alike (0 to 1)


def _sound(path: str, start: float = 0.0, seconds: float | None = None) -> np.ndarray:
    command = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.4f}"]
    if seconds is not None:
        command += ["-t", f"{seconds:.4f}"]
    command += ["-i", path, "-vn", "-ac", "1", "-ar", str(MATCH_SR), "-f", "f32le", "pipe:1"]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        raise ValueError("The sound couldn't be read.")
    return np.frombuffer(result.stdout, dtype=np.float32).astype(np.float64)


def frame_rate(video: str) -> float:
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=r_frame_rate", "-of", "csv=p=0", video],
                            capture_output=True, text=True)
    fps = parse_rate(result.stdout)
    if not 1 <= fps <= 240:
        raise ValueError("That video's frame rate couldn't be read.")
    return fps


def parse_rate(text: str) -> float:
    """ffprobe's "25/1" as 25.0 (DJI files add a stray comma: "25/1,"). 0 if unreadable."""
    first = text.strip().split(",")[0].split()[0] if text.strip() else ""
    top, _, bottom = first.partition("/")
    try:
        return float(top) / float(bottom or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _line_up(piece: np.ndarray, cam: np.ndarray, expected: float) -> tuple[float, bool]:
    """
    Where `piece` best lines up in `cam` (seconds, audio clock), searching
    SEARCH seconds either side of `expected`, and whether that's clear.

    Club music repeats every beat, so the test isn't "far above average": it's
    "clearly better than any other line-up a tenth of a second or more away".
    """
    piece = piece - piece.mean()
    first = max(0, int(round((expected - SEARCH) * MATCH_SR)))
    last = int(round((expected + SEARCH) * MATCH_SR)) + piece.size
    window = cam[first:min(cam.size, last)].astype(np.float64)
    if window.size < piece.size or not piece.any():
        return expected, False
    size = 1 << int(np.ceil(np.log2(window.size + piece.size)))
    c = np.abs(np.fft.irfft(np.fft.rfft(window, size) * np.conj(np.fft.rfft(piece, size)),
                            size)[:window.size - piece.size + 1])
    running = np.concatenate([[0.0], np.cumsum(window * window)])
    c /= np.sqrt(running[piece.size:] - running[:-piece.size] + 1e-12) * np.linalg.norm(piece)
    best = int(np.argmax(c))
    rivals = c[np.abs(np.arange(c.size) - best) > APART * MATCH_SR]
    clear = c[best] >= MIN_LIKENESS and (not rivals.size or c[best] >= CLEAR * rivals.max())
    return (first + best) / MATCH_SR, bool(clear)


def fine_tune(match: dict, video: str, cache_dir: str, mix_path: str, mix_time: float,
              seconds: float) -> tuple[float, bool]:
    """(video time where `mix_time` is, whether that was confirmed by listening)."""
    lead = match.get("lead", 0.0)
    expected = video_time(match, mix_time)
    cam = camera_audio(video, cache_dir)
    if cam is None:
        return expected, False
    piece = _sound(mix_path, mix_time, min(seconds, LISTEN))
    found, clear = _line_up(piece, cam, expected - lead)
    return (found + lead, True) if clear else (expected, False)


EARLY = 0.001             # the cut starts this long before its first frame


def measure(clip: str, camera_sound: str, seconds: float) -> float | None:
    """
    How far the finished clip's sound is from the camera's own sound, cut in
    the same pass as its picture, in seconds (positive: the sound is late), or
    None if the camera's sound is too unclear to tell.

    This looks at what was actually made: if the picture were cut from the
    wrong place, the camera's sound would have come from there too, and the
    mix wouldn't line up with it.
    """
    try:
        cam = np.fromfile(camera_sound, dtype=np.float32)
    except OSError:
        return None
    heard = _sound(clip, 0.0, min(seconds, LISTEN))
    # The middle of the clip, so there's real camera sound either side to search.
    margin = int(SEARCH * MATCH_SR)
    piece = heard[margin:min(heard.size, cam.size - margin)]
    if piece.size < MATCH_SR:
        return None
    expected = EARLY + SEARCH
    found, clear = _line_up(piece, cam, expected)
    return expected - found if clear else None


# ── framing ──────────────────────────────────────────────────────────────
def _even(value: float) -> int:
    return int(round(value / 2)) * 2


def min_zoom(iw: int, ih: int, W: int, H: int) -> float:
    """How far out the zoom goes: the whole picture just fits across (or down) the frame."""
    return min(W / iw, H / ih) / max(W / iw, H / ih)


def layout(iw: int, ih: int, W: int, H: int, cx: float, cy: float, zoom: float) -> dict:
    """
    Where an iw x ih picture goes in a W x H frame. Zoom 1 fills the frame
    (cut to its shape); in, a smaller part fills it; out, the picture gets
    smaller than the frame and a blurred copy fills the rest ("backdrop").
    cx/cy place it: 0 = hard left/top, 1 = hard right/bottom of the room to move.

    source: (x, y, w, h) of the picture used; dest: (x, y, w, h) where it goes.
    """
    cover = max(W / iw, H / ih)
    zoom = min(max(zoom, min_zoom(iw, ih, W, H)), MAX_ZOOM)
    s = cover * zoom
    dw, dh = min(W, _even(iw * s)), min(H, _even(ih * s))
    sw, sh = min(iw, dw / s), min(ih, dh / s)
    cx, cy = min(max(cx, 0.0), 1.0), min(max(cy, 0.0), 1.0)
    return {"source": ((iw - sw) * cx, (ih - sh) * cy, sw, sh),
            "dest": (_even((W - dw) / 2), _even((H - dh) / 2), dw, dh),
            "size": (dw, dh), "backdrop": dw < W or dh < H}


def _picture_size(video: str) -> tuple[int, int]:
    """The picture's size the right way up: phones store it sideways with a note to turn it."""
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=width,height:stream_side_data=rotation:stream_tags=rotate",
                             "-of", "json", video], capture_output=True, text=True)
    try:
        stream = json.loads(result.stdout)["streams"][0]
        w, h = int(stream["width"]), int(stream["height"])
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError("The footage couldn't be read. Is the drive still plugged in?")
    turns = [s.get("rotation") for s in stream.get("side_data_list") or []]
    turns.append((stream.get("tags") or {}).get("rotate"))
    for turn in turns:
        try:
            if round(float(turn)) % 180 == 90:
                return h, w
        except (TypeError, ValueError):
            continue
    return w, h


def _picture_filters(iw, ih, w, h, cx, cy, zoom, adjust, backdrop_cfg) -> str:
    """
    The ffmpeg filters that frame the footage in a w x h frame. `adjust` are
    the colour filters (brightness, the colour file), applied to the footage
    as filmed. Zoomed out, a blurred, dimmed copy fills the space around it.
    """
    box = layout(iw, ih, w, h, cx, cy, zoom)
    sx, sy, sw, sh = box["source"]
    dx, dy, dw, dh = box["dest"]
    crop = f"crop={sw:.3f}:{sh:.3f}:{sx:.3f}:{sy:.3f}"
    if not box["backdrop"]:
        return ",".join([crop, *adjust, f"scale={w}:{h}:flags=lanczos"])
    # The backdrop: the frame-filling cut of the same picture, made small,
    # blurred and dimmed, then stretched back up (blurring it full size is slow).
    bx, by, bw, bh = layout(iw, ih, w, h, cx, cy, 1.0)["source"]
    small_w, small_h = max(2, _even(w / 4)), max(2, _even(h / 4))
    sigma = max(0.5, backdrop_cfg["blur"] * w / 1080 / 4)
    dim = backdrop_cfg["brightness"]
    back = ",".join([f"crop={bw:.3f}:{bh:.3f}:{bx:.3f}:{by:.3f}",
                     f"scale={small_w}:{small_h}:flags=area", *adjust,
                     f"gblur=sigma={sigma:.2f}:steps=3", f"scale={w}:{h}:flags=bicubic",
                     f"colorchannelmixer=rr={dim:.3f}:gg={dim:.3f}:bb={dim:.3f}"])
    front = ",".join([crop, f"scale={dw}:{dh}:flags=lanczos", *adjust])
    return f"split[front][back];[back]{back}[backdrop];[front]{front}[picture];" \
           f"[backdrop][picture]overlay={dx}:{dy}"


def _start_times(video: str) -> tuple[float, float]:
    """(when the picture starts, when the file starts) on the file's own clock."""
    result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                             "stream=start_time:format=start_time", "-of", "json", video],
                            capture_output=True, text=True)

    def number(value, fallback):
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback
    try:
        info = json.loads(result.stdout)
    except ValueError:
        info = {}
    streams = info.get("streams") or [{}]
    picture = number(streams[0].get("start_time"), 0.0)
    return picture, min(picture, number((info.get("format") or {}).get("start_time"), picture))


def _quoted(path: str) -> str:
    return "'" + path.replace("\\", "\\\\").replace("'", r"'\''") + "'"


def render(video: str, video_t: float, mix_path: str, mix_t: float, rate: float,
           seconds: float, out_path: str, cfg: dict, fps: float, lut: str | None = None,
           width: int | None = None, cx: float = 0.5, cy: float = 0.5, zoom: float = 1.0,
           progress=None, look=None, gain: float = 1.0) -> str:
    """
    Cut `seconds` of footage from `video_t` (seconds after the picture starts)
    with the mix from `mix_t` under it. Alongside, saves the camera's own sound
    for the same stretch, for `measure`; returns where.

    `look(width, height)`, if given, makes a FootageLook to draw each frame
    through (black and white, grain and so on).
    """
    first_frame = round(video_t * fps) / fps
    if first_frame < 0:
        raise ValueError("That clip starts before the camera's picture does.")
    # ffmpeg counts from the start of the file, which can be before the picture.
    picture_start, file_start = _start_times(video)
    seek = first_frame + picture_start - file_start - EARLY
    camera_sound = out_path + ".camera.f32"
    # The picture can only start on a frame; start the sound at that same instant.
    mix_start = mix_t + (first_frame - video_t) / rate
    w, h = cfg["canvas"]["width"], cfg["canvas"]["height"]
    if width is not None:
        w, h = int(width), int(round(int(width) * h / w / 2)) * 2
    adjust = []
    if abs(gain - 1.0) > 1e-3:
        # Brightness, as a gain on the footage as filmed: before the colour
        # file, which is where exposure is set on flat (log) footage.
        adjust.append(f"colorchannelmixer=rr={gain:.4f}:gg={gain:.4f}:bb={gain:.4f}")
    if lut:
        adjust.append(f"lut3d=file={_quoted(lut)}")
    iw, ih = _picture_size(video)
    filters = [_picture_filters(iw, ih, w, h, cx, cy, zoom, adjust, cfg["backdrop"]),
               "format=yuv420p"]
    ex = cfg["export"]
    preview = width is not None
    encode = ["-c:v", "libx264", "-preset", "veryfast" if preview else ex["preset"],
              "-crf", str(23 if preview else ex["crf"]),
              "-maxrate", f"{ex['max_mbps']}M", "-bufsize", f"{2 * ex['max_mbps']}M",
              "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k",
              "-t", f"{seconds:.4f}", "-movflags", "+faststart", out_path]
    check = ["-map", "0:a:0?", "-ac", "1", "-ar", str(MATCH_SR), "-t", f"{seconds:.4f}",
             "-f", "f32le", camera_sound]
    if look is not None:
        _render_looked(video, seek, filters[:-1], w, h, fps, mix_path, mix_start, seconds,
                       encode, check, look(w, h), progress)
        return camera_sound
    command = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
               # A hair early, so the frame at first_frame is the first one kept.
               "-ss", f"{max(0.0, seek):.4f}", "-i", video,
               "-ss", f"{max(0.0, mix_start):.4f}", "-t", f"{seconds:.4f}", "-i", mix_path,
               "-map", "0:v:0", "-map", "1:a:0", "-vf", ",".join(filters),
               "-c:v", "libx264", "-preset", "veryfast" if preview else ex["preset"],
               "-crf", str(23 if preview else ex["crf"]),
               "-maxrate", f"{ex['max_mbps']}M", "-bufsize", f"{2 * ex['max_mbps']}M",
               "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k",
               "-t", f"{seconds:.4f}", "-movflags", "+faststart", out_path,
               # The camera's own sound for the same stretch, for checking.
               "-map", "0:a:0?", "-ac", "1", "-ar", str(MATCH_SR), "-t", f"{seconds:.4f}",
               "-f", "f32le", camera_sound]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in process.stdout:
        if progress and line.startswith("out_time_us="):
            try:
                done = int(line.split("=", 1)[1]) / 1e6
            except ValueError:
                continue
            progress(min(1.0, done / seconds), "Cutting the footage")
    error = process.stderr.read()
    if process.wait() != 0:
        print("ffmpeg could not cut the footage:", error[-800:])
        raise ValueError("The footage couldn't be cut. Is the drive still plugged in?")
    return camera_sound


def _render_looked(video, seek, filters, w, h, fps, mix_path, mix_start, seconds,
                   encode, check, look, progress):
    """
    The same cut, with every frame drawn through the look on its way: one
    ffmpeg reads and crops the footage, Python draws each frame, a second
    ffmpeg encodes it with the mix underneath.
    """
    rate = str(Fraction(fps).limit_denominator(1001))
    frames = int(round(seconds * fps))
    reader = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, seek):.4f}", "-i", video,
         "-map", "0:v:0", "-vf", ",".join(filters), "-r", rate, "-frames:v", str(frames),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1", *check],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    writer = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{w}x{h}", "-r", rate, "-i", "pipe:0",
         "-ss", f"{max(0.0, mix_start):.4f}", "-t", f"{seconds:.4f}", "-i", mix_path,
         "-map", "0:v:0", "-map", "1:a:0", *encode],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    size = w * h * 3
    # Several frames are drawn at once (numpy and Pillow do the heavy lifting
    # outside Python's one-at-a-time lock): about three times quicker. They
    # still go to the encoder strictly in order.
    workers = max(1, min(4, (os.cpu_count() or 2) - 1))
    pending: deque = deque()
    try:
        with ThreadPoolExecutor(workers) as pool:
            for f in range(frames):
                raw = reader.stdout.read(size)
                if len(raw) < size:
                    break                                # the footage ran out early
                picture = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
                pending.append(pool.submit(look.frame, f, picture))
                if len(pending) > 2 * workers:
                    writer.stdin.write(pending.popleft().result().tobytes())
                if progress and f % 10 == 0:
                    progress(f / frames, "Drawing the footage")
            while pending:
                writer.stdin.write(pending.popleft().result().tobytes())
    except BrokenPipeError:
        pass
    finally:
        writer.stdin.close()
        reader.stdout.close()
    read_error, write_error = reader.stderr.read(), writer.stderr.read()
    if reader.wait() != 0 or writer.wait() != 0:
        print("ffmpeg could not cut the footage:", (read_error + write_error)[-800:])
        raise ValueError("The footage couldn't be cut. Is the drive still plugged in?")
