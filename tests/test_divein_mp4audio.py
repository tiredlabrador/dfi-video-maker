"""
Tests for reading just the sound out of a camera file, without the picture.

A 17GB camera file holds about 250MB of sound. Reading only those bytes is
what makes the first search through new footage take seconds, not a minute
per video. The answer must be the same sound ffmpeg gets by reading it all.
"""
import subprocess

import numpy as np
import pytest

import generate_video as gv
from app.divein.mp4audio import read_sound

SR = 4000


def full_read(path):
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1",
                          "-ar", str(SR), "-f", "f32le", "pipe:1"], capture_output=True, check=True)
    return np.frombuffer(out.stdout, dtype=np.float32)


def make_video(path, extra=(), audio=("-c:a", "aac", "-ar", "48000", "-ac", "1")):
    gv.run(["ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=s=320x240:r=25:d=20",
            "-f", "lavfi", "-i", "anoisesrc=d=20:c=pink:a=0.3",
            "-c:v", "libx264", "-preset", "ultrafast", *audio, *extra, "-shortest", str(path)],
           "test video")
    return path


@pytest.mark.parametrize("extra", [(), ("-movflags", "+faststart")],
                         ids=["index at the end, as cameras write it", "index at the start"])
def test_the_sound_read_alone_matches_reading_the_whole_file(tmp_path, extra):
    path = make_video(tmp_path / "cam.mp4", extra)
    quick, full = read_sound(str(path), SR), full_read(path)
    assert quick is not None
    assert abs(quick.size - full.size) <= SR // 100            # within 10ms of length
    n = min(quick.size, full.size)
    # Lined up to the sample: the start mustn't shift, or every clip would be off.
    lag = np.argmax(np.correlate(quick[:SR], full[:SR], "full")) - (SR - 1)
    assert lag == 0
    assert np.max(np.abs(quick[:n] - full[:n])) < 1e-3


def test_sound_in_a_format_it_cannot_read_alone_says_so_rather_than_guessing(tmp_path):
    path = make_video(tmp_path / "cam.mov", audio=("-c:a", "pcm_s16le"))
    assert read_sound(str(path), SR) is None


def test_a_file_that_is_not_a_video_says_so(tmp_path):
    path = tmp_path / "junk.mp4"
    path.write_bytes(b"\x00\x00\x00\x08junk" * 100)
    assert read_sound(str(path), SR) is None
