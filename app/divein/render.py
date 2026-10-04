"""
Writing Dive In files: the MP4 and the 1:1 JPG.

The audio in the MP4 is the very samples that were analysed, written out and
muxed back in — not a second, separate cut of the mix — so the picture and the
sound can't disagree by even a frame.
"""
from __future__ import annotations

import os
import tempfile
import wave

import numpy as np

import generate_video as gv


def write_wav(samples: np.ndarray, sample_rate: int, path: str) -> None:
    pcm = np.clip(np.round(samples * 32767.0), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())


def render_video(scene, analysis, samples, sample_rate: int, out_path: str,
                 cfg: dict, seconds: float, width: int | None = None,
                 progress=None) -> str:
    """
    Render `seconds` of the clip to `out_path`.

    `width` makes a smaller preview (same shape, same everything else).
    """
    fps = cfg["canvas"]["fps"]
    total = int(round(seconds * fps))
    ex = cfg["export"]
    preview = width is not None

    def report(fraction, message=""):
        if progress is not None:
            progress(min(1.0, fraction), message)

    folder = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(folder, exist_ok=True)
    handle, wav_path = tempfile.mkstemp(suffix=".wav", dir=folder)
    os.close(handle)
    try:
        need = int(round(seconds * sample_rate))
        clip = samples[:need]
        if clip.shape[0] < need:
            clip = np.concatenate([clip, np.zeros((need - clip.shape[0], 2), np.float32)])
        write_wav(clip, sample_rate, wav_path)

        command = ["ffmpeg", "-y", "-v", "error",
                   "-f", "rawvideo", "-pix_fmt", "rgb24",
                   "-s", f"{scene.W}x{scene.H}", "-r", str(fps), "-i", "pipe:0",
                   "-i", wav_path, "-map", "0:v:0", "-map", "1:a:0"]
        if preview:
            command += ["-vf", f"scale={int(width)}:-2"]
        command += ["-c:v", "libx264",
                    "-preset", "veryfast" if preview else ex["preset"],
                    "-crf", str(23 if preview else ex["crf"]),
                    "-maxrate", f"{ex['max_mbps']}M", "-bufsize", f"{2 * ex['max_mbps']}M",
                    "-pix_fmt", "yuv420p", "-r", str(fps),
                    "-c:a", "aac", "-ac", "2", "-ar", "44100", "-b:a", "192k",
                    "-t", f"{seconds:.3f}", "-movflags", "+faststart", out_path]

        failure = []

        def frames():
            # If drawing fails, stop feeding the encoder and let it finish, so
            # it doesn't linger; the error is raised once it has exited.
            try:
                for f in range(total):
                    if f % 10 == 0:
                        report(0.97 * f / total, "Drawing frames")
                    yield scene.frame_array(f, analysis).tobytes()
            except Exception as exc:                   # noqa: BLE001
                failure.append(exc)

        gv.pipe_frames_to(command, frames())
        if failure:
            if os.path.exists(out_path):
                os.remove(out_path)                    # don't leave half a video
            raise failure[0]
    finally:
        if os.path.exists(wav_path):
            os.remove(wav_path)
    report(1.0, "Finished")
    return out_path


def render_still(scene, out_path: str, quality: int = 92) -> str:
    """The at-rest frame as a JPG: no audio, no twitch, coil at its rest gap."""
    scene.frame(0).save(out_path, "JPEG", quality=int(quality), subsampling=0,
                        optimize=True)
    return out_path
