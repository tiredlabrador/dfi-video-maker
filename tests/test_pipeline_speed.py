"""
Tests for the piped single-pass render.

Frames are built in memory and streamed straight into one ffmpeg process, rather
than written out as PNGs and then encoded twice. That must not change a single
pixel, nor the output's format contract.
"""
import os
import subprocess
import tempfile

from PIL import Image, ImageChops

import generate_video as gv


def _cfg(**kw):
    base = dict(canvas_w=240, canvas_h=300, circle_diameter=180, hole_diameter=8,
                clip_length_seconds=2, spin_period_seconds=1, fps=10,
                motion_blur_samples=1, caption=False)
    base.update(kw)
    return gv.RenderConfig(**base)


def _record(cfg):
    art = Image.new("RGBA", (300, 300), (200, 40, 40, 255))
    return gv.make_record(art, cfg.circle_diameter, cfg.hole_diameter)


def test_iter_frames_yields_one_full_rotation():
    cfg = _cfg()
    frames = list(gv.iter_frames(_record(cfg), 10, cfg))
    assert len(frames) == 10
    assert frames[0].size == (cfg.canvas_w, cfg.canvas_h)


def test_streamed_frames_are_pixel_identical_to_the_png_path():
    """The fast path must produce exactly the same images as before."""
    cfg = _cfg()
    record = _record(cfg)
    streamed = list(gv.iter_frames(record, 6, cfg))
    with tempfile.TemporaryDirectory() as d:
        gv.render_frames(record, d, 6, cfg)
        for i, frame in enumerate(streamed):
            on_disk = Image.open(os.path.join(d, f"{i:05d}.png")).convert("RGB")
            diff = ImageChops.difference(frame.convert("RGB"), on_disk)
            assert diff.getbbox() is None, f"frame {i} differs from the PNG path"


def _probe(path, stream, entries):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream,
         "-show_entries", f"stream={entries}", "-of", "default=nw=1", path],
        capture_output=True, text=True).stdout
    return dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)


def test_rendered_output_keeps_its_format_contract(tmp_path):
    cfg = _cfg()
    audio = str(tmp_path / "tone.mp3")
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=220:duration=6", "-ac", "2", "-q:a", "9", audio],
           "fixture")
    art = str(tmp_path / "art.png")
    Image.new("RGB", (300, 300), (10, 90, 160)).save(art)

    out = str(tmp_path / "out.mp4")
    gv.render_video(audio, art, "0:01", out, cfg)

    video = _probe(out, "v:0", "width,height,codec_name,pix_fmt")
    assert (video["width"], video["height"]) == (str(cfg.canvas_w), str(cfg.canvas_h))
    assert video["codec_name"] == "h264"
    assert video["pix_fmt"] == "yuv420p"

    sound = _probe(out, "a:0", "codec_name,channels")
    assert sound["codec_name"] == "aac"
    assert sound["channels"] == "2"

    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", out],
        capture_output=True, text=True).stdout.strip())
    assert abs(duration - cfg.clip_length_seconds) <= 0.1


def test_clip_longer_than_one_rotation_loops_the_spin(tmp_path):
    """A 2s clip from a 1s rotation must still be exactly 2s."""
    cfg = _cfg(clip_length_seconds=3, spin_period_seconds=1)
    audio = str(tmp_path / "tone.mp3")
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=220:duration=8", "-ac", "2", "-q:a", "9", audio],
           "fixture")
    art = str(tmp_path / "art.png")
    Image.new("RGB", (300, 300), (10, 90, 160)).save(art)
    out = str(tmp_path / "loop.mp4")
    gv.render_video(audio, art, "0:00", out, cfg)
    duration = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", out],
        capture_output=True, text=True).stdout.strip())
    assert abs(duration - 3) <= 0.1
