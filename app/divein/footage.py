"""
Finding a mix inside camera footage, by listening.

The camera's own mic recorded the room, so its sound track holds the same
music as the clean mix: muddier, quieter, with chatter on top. Short pieces of
the mix are slid along the camera's audio until they line up (a
cross-correlation); where several pieces agree on the same line-up, that's
where the mix sits in the video.

Only the sound is read, at 4,000 samples a second: plenty to line music up to
a fraction of a millisecond, and small enough that a two-hour video's audio
fits in about 110MB. It's saved after the first read, so searching the same
footage again is quick.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import tempfile
import threading

import numpy as np

from app.divein.mp4audio import read_sound

MATCH_SR = 4000
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v")

PIECES = 12               # how many bits of the mix are looked for
PIECE_SECONDS = 15.0
BLOCK = 1 << 21           # camera audio is searched ~9 minutes at a time
MIN_STRENGTH = 20.0       # how far a line-up must stand above a typical one
AGREE_SECONDS = 0.25      # pieces agreeing this closely are the same line-up
MOST = 0.6                # share of the pieces that should be filmed that must match
MAX_DRIFT = 200e-6        # camera clocks are out by tens of parts per million, not more
MIN_MIX_SECONDS = 20.0
CACHE_BYTES = 4 * 1024 ** 3  # saved camera sound kept: about 70 hours of footage


class FootageError(ValueError):
    """Something about the footage a person needs to fix."""


# ── the folder ───────────────────────────────────────────────────────────
def list_videos(folder: str) -> list[str]:
    """The videos in `folder` (not its subfolders), in name order."""
    folder = (folder or "").strip()
    if not folder:
        raise FootageError("Type or paste the footage folder's location first.")
    folder = os.path.expanduser(folder)
    if not os.path.isdir(folder):
        raise FootageError("Couldn't find that folder. Is the drive plugged in?")
    # macOS leaves "._name" shadow files on external drives: never videos.
    names = sorted(n for n in os.listdir(folder)
                   if n.lower().endswith(VIDEO_EXTENSIONS) and not n.startswith(".")
                   and os.path.isfile(os.path.join(folder, n)))
    if not names:
        raise FootageError("That folder has no videos in it (MP4 or MOV).")
    return [os.path.join(folder, n) for n in names]


# ── reading sound ────────────────────────────────────────────────────────
def _probe(video: str) -> dict:
    """
    Whether the video has sound, how long the sound lasts, and how long after
    the picture it starts (the sound's own clock is what gets matched, but
    clips are cut by the picture's).
    """
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                             "stream=codec_type,start_time,duration,pix_fmt,color_transfer", "-of", "json", video],
                            capture_output=True, text=True)
    try:
        streams = json.loads(result.stdout)["streams"]
    except (ValueError, KeyError):
        streams = None
    if result.returncode != 0 or streams is None:
        raise FootageError(f"Couldn't read {os.path.basename(video)}. Is it a damaged video?")

    def first(kind):
        return next((s for s in streams if s.get("codec_type") == kind), None)

    def number(stream, key):
        try:
            return float(stream.get(key))
        except (TypeError, ValueError):
            return None

    audio, picture = first("audio"), first("video")
    # DJI's flat D-Log M mode only records in 10-bit; its normal colour, 8-bit.
    # Its HDR mode is 10-bit too, but labels itself (HLG or PQ) and isn't D-Log.
    log = bool(picture) and any(tag in str(picture.get("pix_fmt", ""))
                                for tag in ("10le", "10be", "p010")) \
        and picture.get("color_transfer") not in ("arib-std-b67", "smpte2084")
    if audio is None:
        return {"sound": False, "lead": 0.0, "duration": None, "log": log}
    lead = (number(audio, "start_time") or 0.0) - \
        ((number(picture, "start_time") or 0.0) if picture else 0.0)
    return {"sound": True, "lead": lead, "duration": number(audio, "duration"), "log": log}


_reading: dict[str, threading.Lock] = {}
_reading_lock = threading.Lock()


def camera_audio(video: str, cache_dir: str, probe: dict | None = None) -> np.ndarray | None:
    """
    The video's sound as mono samples at MATCH_SR, or None if it has none.

    Saved in `cache_dir` under a name made from the file's location, size and
    modified time, so an edited or replaced video is read afresh. Two searches
    of the same video at once share one read.
    """
    probe = probe or _probe(video)
    if not probe["sound"]:
        return None
    info = os.stat(video)
    key = f"{os.path.realpath(video)}|{info.st_size}|{info.st_mtime_ns}|{MATCH_SR}"
    os.makedirs(cache_dir, exist_ok=True)
    saved = os.path.join(cache_dir, hashlib.sha1(key.encode()).hexdigest()[:24] + ".f32")
    with _reading_lock:
        lock = _reading.setdefault(saved, threading.Lock())
    with lock:
        if not _complete(saved, probe["duration"]):
            handle, partial = tempfile.mkstemp(dir=cache_dir, suffix=".part")
            os.close(handle)
            try:
                # Just the sound's bytes, read from the file's own index: seconds,
                # where ffmpeg reading all 17GB takes a minute.
                quick = read_sound(video, MATCH_SR)
                if quick is not None:
                    quick.tofile(partial)
                    result = subprocess.CompletedProcess([], 0)
                else:
                    result = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video,
                                             "-map", "0:a:0", "-ac", "1", "-ar", str(MATCH_SR),
                                             "-f", "f32le", partial], capture_output=True)
                if result.returncode != 0:
                    print("ffmpeg could not read:",
                          result.stderr.decode(errors="replace")[-500:])
                    raise FootageError(f"Couldn't read the sound in {os.path.basename(video)}.")
                os.replace(partial, saved)
            finally:
                if os.path.exists(partial):
                    os.remove(partial)
        os.utime(saved)                   # recently used: kept longest by tidy_cache
        return np.fromfile(saved, dtype=np.float32)


def tidy_cache(cache_dir: str, max_bytes: int = CACHE_BYTES) -> None:
    """
    Clear half-finished reads (the app was quit mid-read), then drop the
    longest-unused saved sound until the folder is within `max_bytes`.
    """
    if not os.path.isdir(cache_dir):
        return
    saved = []
    for name in os.listdir(cache_dir):
        path = os.path.join(cache_dir, name)
        if name.endswith(".part"):
            os.remove(path)
        elif name.endswith(".f32"):
            saved.append((os.path.getmtime(path), os.path.getsize(path), path))
    total = sum(size for _, size, _ in saved)
    for _, size, path in sorted(saved):
        if total <= max_bytes:
            break
        os.remove(path)
        total -= size


def _complete(saved: str, seconds: float | None) -> bool:
    """
    A saved read that exists and is about as long as the sound. Loose (a second,
    or 1% of a long video), since the listed length is only approximate and a
    false "incomplete" means reading a 17GB file again.
    """
    if not os.path.exists(saved):
        return False
    slack = max(MATCH_SR, 0.01 * (seconds or 0) * MATCH_SR)
    return seconds is None or abs(os.path.getsize(saved) / 4 - seconds * MATCH_SR) <= slack


def mix_audio(path: str) -> np.ndarray:
    result = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1",
                             "-ar", str(MATCH_SR), "-f", "f32le", "pipe:1"],
                            capture_output=True)
    if result.returncode != 0 or not result.stdout:
        raise FootageError("The mix's audio file couldn't be read.")
    return np.frombuffer(result.stdout, dtype=np.float32)


# ── lining up ────────────────────────────────────────────────────────────
def _pieces(mix: np.ndarray):
    """Evenly spread bits of the mix: [(start in seconds, samples), ...]."""
    seconds = mix.size / MATCH_SR
    if seconds < MIN_MIX_SECONDS:
        raise FootageError("That mix is too short to look for (under 20 seconds).")
    length = min(PIECE_SECONDS, seconds / 4)
    n = int(length * MATCH_SR)
    # Never overlapping, so two pieces agreeing really is two separate bits.
    starts = np.linspace(0, mix.size - n, min(PIECES, int(seconds // length))).astype(int)
    out = []
    for s in sorted(set(starts.tolist())):
        piece = mix[s:s + n].astype(np.float64)
        out.append((s / MATCH_SR, piece - piece.mean()))
    return out


def _search(cam: np.ndarray, pieces) -> list[tuple[float, float]]:
    """
    For each piece: (where it lines up best in `cam`, in seconds; how strongly).

    Each line-up is scaled by how loud the camera was over that stretch, so a
    loud passage can't stand out just for being loud. Strength is then the best
    line-up compared with a typical one.
    """
    n = pieces[0][1].size
    if cam.size < n:
        return [(0.0, 0.0)] * len(pieces)
    size = max(BLOCK, 1 << int(np.ceil(np.log2(2 * n))))
    step = size - n + 1
    shapes = [np.conj(np.fft.rfft(p, size)) for _, p in pieces]
    best = [(0.0, -1)] * len(pieces)                    # (peak, position)
    typical: list[list[float]] = [[] for _ in pieces]
    quiet = 1e-6 * n * float(np.mean(np.square(cam, dtype=np.float64))) + 1e-20
    for start in range(0, cam.size - n + 1, step):
        block = cam[start:start + size].astype(np.float64)
        valid = min(step, block.size - n + 1)
        spectrum = np.fft.rfft(block, size)
        running = np.concatenate([[0.0], np.cumsum(block * block)])
        loudness = np.sqrt(running[n:n + valid] - running[:valid] + quiet)
        for k, shape in enumerate(shapes):
            c = np.abs(np.fft.irfft(spectrum * shape, size)[:valid]) / loudness
            i = int(np.argmax(c))
            if c[i] > best[k][0]:
                best[k] = (float(c[i]), start + i)
            typical[k].append(float(np.median(c[::16])))
    out = []
    for (peak, pos), medians in zip(best, typical):
        floor = float(np.median(medians)) or 1e-12
        out.append((pos / MATCH_SR, peak / floor))
    return out


def _agree(pieces, hits, cam_seconds: float) -> tuple[float, float] | None:
    """
    The line-up the pieces agree on: (video time of the mix's 0:00, how fast
    the camera's clock runs against the mix's), or None.

    Agreeing isn't enough on its own: of the pieces that line-up says were
    filmed, most must actually be there. Otherwise one record that two DJs
    both played would pass for a match.
    """
    strong = [(at, pos) for (at, _), (pos, s) in zip(pieces, hits) if s >= MIN_STRENGTH]
    groups = []
    for _, o in [(at, pos - at) for at, pos in strong]:
        group = [(at, pos) for at, pos in strong if abs(pos - at - o) <= AGREE_SECONDS]
        if not any(set(group) == set(g) for g in groups):
            groups.append(group)
    groups.sort(key=len, reverse=True)
    if not groups or len(groups[0]) < 2:
        return None
    if len(groups) > 1 and len(groups[1]) == len(groups[0]) \
            and not set(groups[0]) & set(groups[1]):
        return None                                     # two equally good answers
    best = groups[0]
    at = np.array([a for a, _ in best])
    pos = np.array([p for _, p in best])
    rate, offset = 1.0, float(np.median(pos - at))
    if len(best) >= 3 and at.max() - at.min() >= 60:
        fit_rate, fit_offset = np.polyfit(at, pos, 1)
        if abs(fit_rate - 1) <= MAX_DRIFT:
            rate, offset = float(fit_rate), float(fit_offset)

    length = pieces[0][1].size / MATCH_SR
    filmed = sum(1 for a, _ in pieces
                 if offset + rate * a >= 0 and offset + rate * (a + length) <= cam_seconds)
    if len(best) < max(2, math.ceil(MOST * filmed)):
        return None
    return offset, rate


def _locate_one(pieces, cam: np.ndarray, mix_seconds: float) -> dict | None:
    cam_seconds = cam.size / MATCH_SR
    found = _agree(pieces, _search(cam, pieces), cam_seconds)
    if found is None:
        return None
    offset, rate = found
    mix_from = max(0.0, -offset / rate)
    mix_to = min(mix_seconds, (cam_seconds - offset) / rate)
    if mix_to <= mix_from:
        return None
    return {"offset": offset, "rate": rate, "mix_from": mix_from, "mix_to": mix_to}


def video_time(match: dict, mix_time: float) -> float:
    """Where in the video a moment of the mix is."""
    return match["offset"] + match["rate"] * mix_time


def locate(mix: np.ndarray, cams: list[np.ndarray]) -> list[dict]:
    """
    Where `mix` is in each camera recording that holds some of it.

    `offset` is the video time at which the mix's 0:00 plays (negative when the
    camera started late), and `rate` how fast the camera's clock runs against
    the mix's (see video_time); `mix_from`..`mix_to` is the part of the mix filmed.
    """
    pieces = _pieces(mix)
    out = []
    for i, cam in enumerate(cams):
        match = _locate_one(pieces, cam, mix.size / MATCH_SR)
        if match:
            out.append({"video": i, **match})
    return out


# ── the whole job ────────────────────────────────────────────────────────
def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 \
        else f"{s // 60}:{s % 60:02d}"


def _videos(n: int) -> str:
    return "the 1 video" if n == 1 else f"any of the {n} videos"


def describe(matches: list[dict], names: list[str], silent: int = 0) -> str:
    """One or two plain sentences saying what was found."""
    if not matches:
        text = f"Couldn't find this mix in {_videos(len(names))}. Is it the right folder?"
    else:
        parts = [f"video {m['video'] + 1} ({names[m['video']]}) has "
                 f"{_clock(m['mix_from'])} – {_clock(m['mix_to'])}" for m in matches]
        text = "Found it: " + " and ".join(parts) + " of this mix."
    if silent:
        text += f" ({silent} of the videos had no sound, so couldn't be checked.)"
    return text


def find_mix(mix_path: str, folder: str, cache_dir: str, progress=None,
             videos: list[str] | None = None) -> dict:
    """
    Look for the mix in every video in `folder` (or in `videos`, if the
    caller has already listed them). Reports as it goes.
    """
    report = progress or (lambda fraction, message="": None)
    videos = videos if videos is not None else list_videos(folder)
    names = [os.path.basename(v) for v in videos]
    report(0.0, "Reading the mix")
    mix = mix_audio(mix_path)
    pieces = _pieces(mix)
    matches, silent = [], 0
    for i, video in enumerate(videos):
        report(i / len(videos), f"Listening to video {i + 1} of {len(videos)}")
        probe = _probe(video)
        cam = camera_audio(video, cache_dir, probe)
        if cam is None:
            silent += 1
            continue
        match = _locate_one(pieces, cam, mix.size / MATCH_SR)
        if match:
            match["offset"] += probe["lead"]
            matches.append({"video": i, "name": names[i], "log": probe["log"], **match})
    report(1.0, "Finished")
    return {"videos": names, "matches": matches,
            "message": describe(matches, names, silent)}
