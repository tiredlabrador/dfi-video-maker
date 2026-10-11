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
import subprocess

import numpy as np

from app.divein.footage import MATCH_SR, camera_audio, video_time

SEARCH = 0.25             # seconds either side of where the clip should be: under a beat
LISTEN = 15.0             # seconds of the clip used to fine-tune and to measure
APART = 0.1               # a rival line-up at least this far from the best...
CLEAR = 1.2               # ...must be this much weaker for the best to count
MAX_ZOOM = 2.0            # further in, 1080p footage looks soft on a phone
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
           progress=None) -> str:
    """
    Cut `seconds` of footage from `video_t` (seconds after the picture starts)
    with the mix from `mix_t` under it. Alongside, saves the camera's own sound
    for the same stretch, for `measure`; returns where.
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
    # The tallest 4:5 window the picture allows, made smaller by `zoom`, placed
    # by cx/cy: 0 = hard left/top, 1 = hard right/bottom of the room to move.
    aspect = f"{cfg['canvas']['width']}/{cfg['canvas']['height']}"
    zoom = min(max(zoom, 1.0), MAX_ZOOM)
    cw, ch = f"(ih*{aspect}/{zoom:.4f})", f"(ih/{zoom:.4f})"
    shape = f"crop={cw}:{ch}:(iw-{cw})*{min(max(cx, 0), 1):.4f}:(ih-{ch})*{min(max(cy, 0), 1):.4f}"
    filters = [shape]
    if lut:
        filters.append(f"lut3d=file={_quoted(lut)}")
    filters += [f"scale={w}:{h}:flags=lanczos", "format=yuv420p"]
    ex = cfg["export"]
    preview = width is not None
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
