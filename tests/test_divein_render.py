"""
Tests for writing Dive In files: the MP4 and the 1:1 JPG.

These run the real encoder, because the promise is about the file that comes
out — size, codec, length, audio — not about the code that made it.
"""
import json
import subprocess

import numpy as np
import pytest
from PIL import Image

from app.divein.audio import analyse
from app.divein.compose import Scene, load_photo
from app.divein.config import merge_config
from app.divein.render import render_still, render_video
from tests.test_divein_audio import beat

SR, FPS = 44100, 30


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                          "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    a = next(s for s in data["streams"] if s["codec_type"] == "audio")
    return v, a, float(data["format"]["duration"])


@pytest.fixture
def setup(tmp_path):
    cfg = merge_config({})
    photo = tmp_path / "p.jpg"
    y, x = np.mgrid[0:900, 0:700]
    Image.fromarray(((x + y) % 256).astype(np.uint8)).convert("RGB").save(photo)
    samples, _ = beat(2.0)
    analysis = analyse(samples, SR, FPS, cfg, episode="02.01")
    scene = Scene(cfg, load_photo(str(photo)), "Artist\nName", "02.01")
    return cfg, scene, samples, analysis, tmp_path


def test_the_video_meets_the_spec(setup):
    cfg, scene, samples, analysis, tmp = setup
    out = tmp / "clip.mp4"
    render_video(scene, analysis, samples, SR, str(out), cfg, seconds=2.0)
    v, a, duration = probe(out)
    assert (v["width"], v["height"]) == (1080, 1350)
    assert v["codec_name"] == "h264" and v["pix_fmt"] == "yuv420p"
    assert v["avg_frame_rate"] == "30/1"
    assert a["codec_name"] == "aac" and a["channels"] == 2
    assert int(a["sample_rate"]) == 44100
    assert duration == pytest.approx(2.0, abs=0.05)


def test_a_preview_is_smaller_but_the_same_shape(setup):
    cfg, scene, samples, analysis, tmp = setup
    out = tmp / "preview.mp4"
    render_video(scene, analysis, samples, SR, str(out), cfg, seconds=1.0, width=540)
    v, _, duration = probe(out)
    # 1350 x 540/1080 = 675, but H.264 needs even sizes, so it rounds to 676.
    assert (v["width"], v["height"]) == (540, 676)
    assert duration == pytest.approx(1.0, abs=0.05)


def test_progress_is_reported_to_the_end(setup):
    cfg, scene, samples, analysis, tmp = setup
    seen = []
    render_video(scene, analysis, samples, SR, str(tmp / "c.mp4"), cfg, seconds=1.0,
                 progress=lambda f, m="": seen.append(f))
    assert seen == sorted(seen) and seen[-1] == pytest.approx(1.0)


def test_the_debug_overlay_can_be_burnt_in(setup):
    cfg, scene, samples, analysis, tmp = setup
    plain, debug = tmp / "p.mp4", tmp / "d.mp4"
    render_video(scene, analysis, samples, SR, str(plain), cfg, seconds=0.5)
    render_video(scene, analysis, samples, SR, str(debug), cfg, seconds=0.5, debug=True)
    def frame0(path):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
                              "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
                             capture_output=True, check=True).stdout
        return np.frombuffer(raw, np.uint8).reshape(1350, 1080)
    assert np.abs(frame0(plain)[170:360, 50:390].astype(int)
                  - frame0(debug)[170:360, 50:390].astype(int)).mean() > 5


def test_the_square_still_is_a_1080_jpg(setup, tmp_path):
    cfg = merge_config({})
    photo = tmp_path / "s.jpg"
    Image.new("RGB", (800, 1200), (120, 80, 40)).save(photo)
    scene = Scene(cfg, load_photo(str(photo)), "Artist", "02.01", fmt="square")
    out = tmp_path / "square.jpg"
    render_still(scene, str(out), quality=92)
    img = Image.open(out)
    assert img.format == "JPEG" and img.size == (1080, 1080)


def test_grain_does_not_balloon_the_file_size(setup):
    """
    Grain that changes every two frames is nearly incompressible: uncapped, a
    25s clip came out at ~126MB. The bitrate is capped (16 Mbit/s keeps ~95% of
    the grain) so files stay a sensible size.
    """
    cfg, scene, samples, analysis, tmp = setup
    out = tmp / "capped.mp4"
    render_video(scene, analysis, samples, SR, str(out), cfg, seconds=2.0)
    v, _, _ = probe(out)
    assert int(v["bit_rate"]) <= cfg["export"]["max_mbps"] * 1_000_000 * 1.15
