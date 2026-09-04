"""
The render service: everything between "the user pressed Render" and an MP4.

Deliberately thin. All the pixel and encoding work lives in `generate_video.py`,
which is the same engine the Colab notebook uses — so the two front doors can
never drift apart in what they produce. This layer only handles the things a
web app has to worry about and a notebook does not: untrusted filenames, a
working directory, and reporting progress in words a person can read.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

import generate_video as gv


@dataclass
class RenderRequest:
    """One video to make."""
    audio_path: str
    artwork_path: str | None
    clip_start: str
    track: str
    artist: str
    cfg: gv.RenderConfig
    position: int | None = None      # for numbering a batch: 01, 02, ...
    total: int | None = None


# The stages a render moves through, and how far along each one ends. The
# numbers are rough by nature — the point is honest movement, not accuracy.
_STAGES = [
    (0.05, "Reading the audio file"),
    (0.15, "Preparing the artwork"),
    (0.25, "Cutting the record"),
]


class RenderService:
    """Turns upload bytes plus settings into finished MP4s on disk."""

    def __init__(self, work_dir: str | None = None):
        # Only clean up a directory we made ourselves; a caller-supplied one
        # (the tests, or a future "keep my renders here") is not ours to delete.
        self._owns_work_dir = work_dir is None
        self.work_dir = work_dir or tempfile.mkdtemp(prefix="dfi-video-")
        self.uploads_dir = os.path.join(self.work_dir, "uploads")
        self.output_dir = os.path.join(self.work_dir, "output")
        os.makedirs(self.uploads_dir, exist_ok=True)
        os.makedirs(self.output_dir, exist_ok=True)

    # -- uploads ----------------------------------------------------------
    def save_upload(self, filename: str, content: bytes) -> str:
        """
        Write an uploaded file into the working directory and return its path.

        Two things are going on here.

        The filename comes from the browser, so it is untrusted even though the
        browser is on the same machine: `../../../etc/passwd` is a perfectly
        legal thing to put in a form. Only the extension is kept — the name
        itself is discarded.

        The stored name is a fingerprint of the *contents*, so sending the same
        file twice stores it once. That matters more than it sounds: the page
        re-reads a track's tags every time its artwork is added, changed or
        cleared, and each of those used to leave another full copy on disk
        forever.
        """
        _, extension = os.path.splitext(os.path.basename(filename or ""))
        extension = re.sub(r"[^A-Za-z0-9.]", "", extension)[:12]
        digest = hashlib.sha256(content).hexdigest()[:32]
        path = os.path.join(self.uploads_dir, digest + extension)
        if not os.path.exists(path) or os.path.getsize(path) != len(content):
            with open(path, "wb") as handle:
                handle.write(content)
        return path

    def cleanup(self) -> None:
        """
        Delete everything this run produced.

        Called when the app is stopped. Without it the uploads and the finished
        MP4s sit in a temporary folder that macOS only reclaims after days of
        not being touched — a few batches a week adds up to gigabytes.
        """
        if self._owns_work_dir and os.path.isdir(self.work_dir):
            shutil.rmtree(self.work_dir, ignore_errors=True)

    # -- rendering --------------------------------------------------------
    def render(self, request: RenderRequest, progress=None) -> str:
        """
        Render one video and return the path to the finished MP4.

        `progress(fraction, message)` is called as the work moves along. Raises
        `generate_video.NoArtworkError` if the track has no usable artwork, and
        `generate_video.RenderError` for anything else that goes wrong.
        """
        def report(fraction: float, message: str = "") -> None:
            if progress is not None:
                progress(fraction, message)

        for fraction, message in _STAGES:
            report(fraction, message)

        name = gv.output_filename(request.artist, request.track,
                                  request.position, request.total)
        out_path = os.path.join(self.output_dir, name)

        # The engine reports 0.0 to 1.0 across its own work; map that onto the
        # slice of the bar that is left after the preparation stages above.
        head = _STAGES[-1][0]
        gv.render_video(
            audio_path=request.audio_path,
            artwork_path=request.artwork_path,
            clip_start=request.clip_start,
            output_path=out_path,
            cfg=request.cfg,
            track=request.track,
            artist=request.artist,
            progress=lambda f: report(head + (1.0 - head) * f,
                                      "Rendering the spinning record"),
        )
        report(1.0, "Finished")
        return out_path

    # -- checking ---------------------------------------------------------
    def probe(self, path: str) -> dict:
        """
        Read back what was actually produced, using ffprobe.

        Worth doing on every render: it is the difference between "the render
        did not crash" and "the file is genuinely what we said it would be".
        """
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise gv.RenderError(f"Could not read back the finished file: "
                                 f"{result.stderr.strip()}")
        data = json.loads(result.stdout)
        video = next((s for s in data["streams"]
                      if s["codec_type"] == "video"), {})
        audio = next((s for s in data["streams"]
                      if s["codec_type"] == "audio"), {})

        rate = video.get("avg_frame_rate", "0/1")
        num, _, den = rate.partition("/")
        fps = float(num) / float(den) if float(den or 0) else 0.0

        return {
            "width": video.get("width"),
            "height": video.get("height"),
            "video_codec": video.get("codec_name"),
            "pix_fmt": video.get("pix_fmt"),
            "fps": fps,
            "audio_codec": audio.get("codec_name"),
            "channels": audio.get("channels"),
            "sample_rate": int(audio.get("sample_rate", 0)),
            "duration": float(data.get("format", {}).get("duration", 0.0)),
            "size_bytes": int(data.get("format", {}).get("size", 0)),
        }
