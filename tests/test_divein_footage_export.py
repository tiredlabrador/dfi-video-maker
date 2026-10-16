"""
Tests for exporting clips made from camera footage instead of the photo.

The one that matters most: a made-up camera video with a single white flash,
and a click in the mix at the very same moment. In the exported clip the flash
and the click must land in the same frame. That's sync, checked by picture
and sound, not by trusting the arithmetic.
"""
import json
import subprocess
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest

import generate_video as gv
from app.server import create_server
from test_divein_footage import SR, chatter, music, room, write_wav

FPS = 25
OFFSET = 20.0             # the mix's 0:00 is 20s into the camera video
FLASH_FRAME = 1250        # camera frame 1250 = 50.0s into the video = 30.0s into the mix
CLICK_AT = FLASH_FRAME / FPS - OFFSET


@pytest.fixture(scope="module")
def shoot(tmp_path_factory):
    """A mix with a click, and a camera that filmed it with a flash at that instant."""
    tmp = tmp_path_factory.mktemp("shoot")
    mix = music(90)
    burst = np.random.default_rng(5).normal(size=int(0.01 * SR)) * 8
    at = int(CLICK_AT * SR)
    mix[at:at + burst.size] += burst.astype(np.float32)
    cam = chatter(120, seed=7)
    cam[int(OFFSET * SR):int(OFFSET * SR) + mix.size] += room(mix)
    write_wav(tmp / "mix.wav", mix)
    write_wav(tmp / "cam.wav", cam)
    folder = tmp / "Camera"
    folder.mkdir()
    picture = f"color=c=black:s=160x90:r={FPS}:d=120,drawbox=c=white:t=fill:" \
              f"enable='eq(n,{FLASH_FRAME})'"
    for name, fmt in [("normal.mp4", "yuv420p"), ("flat.mp4", "yuv420p10le")]:
        sub = folder if name == "normal.mp4" else tmp / "Flat"
        sub.mkdir(exist_ok=True)
        gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", picture,
                "-i", str(tmp / "cam.wav"), "-c:v", "libx264", "-pix_fmt", fmt, "-g", "50",
                "-c:a", "aac", "-ar", "48000", "-shortest", str(sub / "DJI_0001.MP4")],
               "camera video")
    # Some cameras start the picture a moment after the sound (or before it). The
    # flash and the click still happen at the same real instant: picture frame
    # 1250 is now 0.4s later in the file, and so is the sound that goes with it.
    for name, shift in [("Late picture", 0.4), ("Late sound", -0.4)]:
        sub = tmp / name
        sub.mkdir()
        late_pic, late_snd = (shift, 0.0) if shift > 0 else (0.0, -shift)
        # The sound's first sample is whatever was heard at the moment it started.
        cam_shift = np.concatenate([np.zeros(int(late_pic * SR), np.float32), cam]) if shift > 0 \
            else cam[int(late_snd * SR):]
        write_wav(tmp / f"cam_{name}.wav", cam_shift)
        gv.run(["ffmpeg", "-y", "-v", "error", "-itsoffset", str(late_pic), "-f", "lavfi",
                "-i", picture, "-itsoffset", str(late_snd), "-i", str(tmp / f"cam_{name}.wav"),
                "-c:v", "libx264", "-g", "50", "-c:a", "aac", "-ar", "48000",
                str(sub / "DJI_0001.MP4")], "camera video")
    return tmp


@pytest.fixture
def server(tmp_path):
    srv = create_server(host="127.0.0.1", port=0, work_dir=str(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


def call(server, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(server.base_url + path, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        response = urllib.request.urlopen(req, timeout=120)
        return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def finished(server, job_id, timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, job = call(server, f"/api/divein/jobs/{job_id}")
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.3)
    raise AssertionError("job never finished")


def found(server, shoot, folder="Camera"):
    """Upload the mix and find it in the footage, as the page does."""
    req = urllib.request.Request(f"{server.base_url}/api/divein/upload?kind=audio",
                                 data=(shoot / "mix.wav").read_bytes(), method="POST",
                                 headers={"Content-Type": "application/octet-stream",
                                          "X-Filename": "mix.wav"})
    token = json.load(urllib.request.urlopen(req, timeout=60))["token"]
    _, job = call(server, "/api/divein/footage", {"audio": token, "folder": str(shoot / folder)})
    job = finished(server, job["id"])
    assert job["status"] == "done" and job["footage"]["matches"], job
    return token


PLAIN = {"bw": False, "contrast": False, "grain": False, "vignette": False, "ghost": False}


def design(token, **kw):
    d = {"audio": token, "artist": "Artist", "episode": "02.01", "starts": [27.0],
         "clip_seconds": 6, "sources": ["video"], "overrides": {}}
    d.update(kw)
    return d


def download(server, job, name_end):
    [f] = [f for f in job["files"] if f["name"].endswith(name_end)]
    return urllib.request.urlopen(server.base_url + f["url"], timeout=60).read()


def flash_and_click(path, tmp_path):
    """(time of the brightest frame, time of the loudest moment) in a video file."""
    frames = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", "scale=8:10,format=gray",
                             "-f", "rawvideo", "pipe:1"], capture_output=True, check=True).stdout
    brightness = np.frombuffer(frames, np.uint8).reshape(-1, 80).mean(axis=1)
    sound = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "8000",
                            "-f", "f32le", "pipe:1"], capture_output=True, check=True).stdout
    sound = np.abs(np.frombuffer(sound, np.float32))
    return int(np.argmax(brightness)) / FPS, int(np.argmax(sound)) / 8000


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                          "stream=codec_type,width,height,r_frame_rate:format=duration",
                          "-of", "json", str(path)], capture_output=True, check=True, text=True)
    return json.loads(out.stdout)


# ── the export ───────────────────────────────────────────────────────────
def test_a_footage_clip_shows_the_flash_and_plays_the_click_in_the_same_frame(server, shoot,
                                                                                tmp_path):
    token = found(server, shoot)
    status, job = call(server, "/api/divein/export", design(token))
    assert status == 202
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    flash, click = flash_and_click(clip, tmp_path)
    assert flash == pytest.approx(CLICK_AT - 27.0, abs=0.05)
    assert abs(flash - click) <= 1 / FPS


def test_a_footage_clip_is_full_size_4_by_5_at_the_cameras_frame_rate(server, shoot, tmp_path):
    token = found(server, shoot)
    _, job = call(server, "/api/divein/export", design(token))
    job = finished(server, job["id"])
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    info = probe(clip)
    video = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert (video["width"], video["height"]) == (1080, 1350)
    assert video["r_frame_rate"] == f"{FPS}/1"
    assert float(info["format"]["duration"]) == pytest.approx(6, abs=0.1)
    assert any(s["codec_type"] == "audio" for s in info["streams"])


def test_each_clips_sync_is_measured_and_reported(server, shoot):
    token = found(server, shoot)
    _, job = call(server, "/api/divein/export", design(token, starts=[27.0, 40.0],
                                                       sources=["video", "video"]))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    assert len(job["sync"]) == 2
    assert all(s["checked"] and abs(s["off_by"]) < 1 / FPS for s in job["sync"]), job["sync"]


def test_a_clip_that_was_not_filmed_is_refused_before_anything_renders(server, shoot):
    token = found(server, shoot)
    # 86s + 6s runs past the 90s the camera filmed.
    status, body = call(server, "/api/divein/export", design(token, starts=[86.0]))
    assert status == 400
    assert "clip 1" in body["error"].lower() and "filmed" in body["error"].lower()


def test_footage_clips_need_no_photo_and_then_there_is_no_square_jpg(server, shoot):
    token = found(server, shoot)
    _, job = call(server, "/api/divein/export", design(token))
    job = finished(server, job["id"])
    names = [f["name"] for f in job["files"]]
    assert len(names) == 1 and names[0].endswith(".mp4")


def test_a_photo_clip_still_needs_the_photo(server, shoot):
    token = found(server, shoot)
    status, body = call(server, "/api/divein/export", design(token, sources=["photo"]))
    assert status == 400 and "photo" in body["error"].lower()


def test_the_preview_of_a_footage_clip_is_short_and_small(server, shoot, tmp_path):
    token = found(server, shoot)
    status, job = call(server, "/api/divein/preview", design(token, clip_seconds=20))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    clip = tmp_path / "preview.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    info = probe(clip)
    video = [s for s in info["streams"] if s["codec_type"] == "video"][0]
    assert video["width"] == 540
    assert float(info["format"]["duration"]) == pytest.approx(8, abs=0.2)


# ── colour ───────────────────────────────────────────────────────────────
def red_lut(path):
    """A LUT that turns everything pure red: unmistakable when it's applied."""
    lines = ["LUT_3D_SIZE 2"] + ["1 0 0"] * 8
    path.write_text("\n".join(lines) + "\n")
    return path


def middle_colour(clip):
    frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(clip), "-frames:v", "1",
                            "-vf", "scale=4:5", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                           capture_output=True, check=True).stdout
    return np.frombuffer(frame, np.uint8).reshape(-1, 3).mean(axis=0)


def test_flat_footage_gets_the_colour_correction(server, shoot, tmp_path):
    server.divein.lut_path = str(red_lut(tmp_path / "red.cube"))
    token = found(server, shoot, folder="Flat")
    _, job = call(server, "/api/divein/export", design(token, footage_look=PLAIN))
    job = finished(server, job["id"])
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    r, g, b = middle_colour(clip)
    assert r > 200 and g < 60 and b < 60


def test_colour_correction_can_be_turned_off(server, shoot, tmp_path):
    server.divein.lut_path = str(red_lut(tmp_path / "red.cube"))
    token = found(server, shoot, folder="Flat")
    _, job = call(server, "/api/divein/export", design(token, lut=False, footage_look=PLAIN))
    job = finished(server, job["id"])
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    assert middle_colour(clip)[0] < 60


def test_normal_footage_is_left_as_filmed(server, shoot, tmp_path):
    server.divein.lut_path = str(red_lut(tmp_path / "red.cube"))
    token = found(server, shoot)
    _, job = call(server, "/api/divein/export", design(token, footage_look=PLAIN))
    job = finished(server, job["id"])
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    assert middle_colour(clip)[0] < 60


@pytest.mark.parametrize("reported, fps", [("25/1\n", 25.0), ("25/1,\n", 25.0),
                                           ("30000/1001\n", 30000 / 1001)])
def test_frame_rates_are_read_however_the_camera_writes_them(reported, fps):
    """DJI files report theirs with a stray comma: "25/1,"."""
    from app.divein.footage_clip import parse_rate
    assert parse_rate(reported) == pytest.approx(fps)


def test_a_slightly_wrong_line_up_is_corrected_by_listening_before_cutting(server, shoot,
                                                                           tmp_path):
    """Camera clocks wander: the whole-mix match can be a few frames out at any one clip."""
    token = found(server, shoot)
    server.divein._footage[token][1][0]["offset"] += 0.12     # three frames out
    _, job = call(server, "/api/divein/export", design(token))
    job = finished(server, job["id"])
    assert job["sync"][0]["confirmed"]
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    flash, click = flash_and_click(clip, tmp_path)
    assert abs(flash - click) <= 1 / FPS



@pytest.mark.parametrize("folder", ["Late picture", "Late sound"])
def test_a_camera_whose_picture_and_sound_start_apart_is_still_cut_in_sync(server, shoot,
                                                                           tmp_path, folder):
    token = found(server, shoot, folder=folder)
    _, job = call(server, "/api/divein/export", design(token))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    flash, click = flash_and_click(clip, tmp_path)
    assert abs(flash - click) <= 1 / FPS
    assert job["sync"][0]["in_sync"]


def test_a_clip_cut_out_of_sync_is_reported_as_such(server, shoot, monkeypatch):
    """The check must look at what was made, not repeat the sums that made it."""
    from app.divein import footage_clip
    real = footage_clip.render
    monkeypatch.setattr(footage_clip, "render",
                        lambda video, video_t, *a, **k: real(video, video_t + 0.2, *a, **k))
    token = found(server, shoot)
    _, job = call(server, "/api/divein/export", design(token))
    job = finished(server, job["id"])
    [measured] = job["sync"]
    assert measured["checked"] and not measured["in_sync"]
    assert abs(abs(measured["off_by"]) - 0.2) < 0.03


def test_a_forgotten_photo_does_not_spoil_an_all_footage_export(server, shoot):
    """Uploads are cleared when the app restarts: an old photo mustn't fail the export at the end."""
    token = found(server, shoot)
    _, job = call(server, "/api/divein/export", design(token, photo="long-gone.jpg"))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    assert [f["name"][-4:] for f in job["files"]] == [".mp4"]


def test_footage_that_does_not_hold_the_mix_says_so(server, shoot, tmp_path):
    other = tmp_path / "Other"
    other.mkdir()
    write_wav(tmp_path / "other.wav", chatter(60, seed=11))
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x80:r=5:d=60",
            "-i", str(tmp_path / "other.wav"), "-c:v", "libx264", "-c:a", "aac", "-shortest",
            str(other / "x.mp4")], "unrelated video")
    req = urllib.request.Request(f"{server.base_url}/api/divein/upload?kind=audio",
                                 data=(shoot / "mix.wav").read_bytes(), method="POST",
                                 headers={"Content-Type": "application/octet-stream",
                                          "X-Filename": "mix.wav"})
    token = json.load(urllib.request.urlopen(req, timeout=60))["token"]
    _, job = call(server, "/api/divein/footage", {"audio": token, "folder": str(other)})
    finished(server, job["id"])
    status, body = call(server, "/api/divein/export", design(token))
    assert status == 400 and "wasn't found" in body["error"]


def test_the_preview_shows_the_clip_picked_even_when_two_start_together(server, shoot, tmp_path):
    from PIL import Image
    token = found(server, shoot)
    Image.new("RGB", (400, 500), "gray").save(tmp_path / "dj.jpg")
    req = urllib.request.Request(f"{server.base_url}/api/divein/upload?kind=photo",
                                 data=(tmp_path / "dj.jpg").read_bytes(), method="POST",
                                 headers={"Content-Type": "application/octet-stream",
                                          "X-Filename": "dj.jpg"})
    photo = json.load(urllib.request.urlopen(req, timeout=60))["token"]
    _, job = call(server, "/api/divein/preview",
                  design(token, photo=photo, starts=[27.0, 27.0], sources=["photo", "video"],
                         preview_index=1))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    assert job["sync"], "the footage clip should have been previewed, not the photo one"


# ── framing ──────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def stripes(shoot, tmp_path_factory):
    """The same camera, but its picture is white on the left third, grey middle, black right."""
    tmp = tmp_path_factory.mktemp("stripes")
    folder = tmp / "Camera"
    folder.mkdir()
    picture = (f"color=c=black:s=1280x720:r={FPS}:d=120,"
               "drawbox=x=0:y=0:w=427:h=720:c=white:t=fill,"
               "drawbox=x=427:y=0:w=426:h=360:c=gray:t=fill,"
               "drawbox=x=427:y=360:w=426:h=360:c=0x202020:t=fill")
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", picture,
            "-i", str(shoot / "cam.wav"), "-c:v", "libx264", "-preset", "ultrafast", "-g", "50",
            "-c:a", "aac", "-ar", "48000", "-shortest", str(folder / "DJI_0001.MP4")], "stripes")
    (tmp / "mix.wav").write_bytes((shoot / "mix.wav").read_bytes())
    return tmp


def brightness(server, job, tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    return middle_colour(clip).mean()


@pytest.mark.parametrize("cx, light", [(0.0, True), (1.0, False)])
def test_footage_can_be_framed_further_left_or_right(server, stripes, tmp_path, cx, light):
    token = found(server, stripes)
    _, job = call(server, "/api/divein/export",
                  design(token, footage_look=PLAIN, footage_crops=[{"cx": cx, "cy": 0.5, "zoom": 1}]))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    level = brightness(server, job, tmp_path)
    assert (level > 200) if light else (level < 60)


@pytest.mark.parametrize("cy, level", [(0.0, (110, 150)), (1.0, (20, 50))])
def test_zoomed_in_footage_can_be_framed_higher_or_lower(server, stripes, tmp_path, cy, level):
    """Zoomed in 2x on the middle third: its top half is grey, its bottom half nearly black."""
    token = found(server, stripes)
    _, job = call(server, "/api/divein/export",
                  design(token, footage_look=PLAIN, footage_crops=[{"cx": 0.5, "cy": cy, "zoom": 2}]))
    job = finished(server, job["id"])
    low, high = level
    assert low < brightness(server, job, tmp_path) < high


def test_a_strange_framing_is_kept_within_bounds_not_refused(server, stripes, tmp_path):
    token = found(server, stripes)
    _, job = call(server, "/api/divein/export",
                  design(token, footage_crops=[{"cx": -3, "cy": "x", "zoom": 50}]))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")



# ── the look ─────────────────────────────────────────────────────────────
def test_footage_gets_the_dive_in_look_by_default(server, stripes, tmp_path):
    """Black and white with grain: grey everywhere, and no two frames alike."""
    token = found(server, stripes)
    _, job = call(server, "/api/divein/export", design(token))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    # Full size (shrinking would average the grain away), the middle 200x200.
    frames = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-frames:v", "3",
                             "-vf", "crop=200:200", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                            capture_output=True, check=True).stdout
    rgb = np.frombuffer(frames, np.uint8).reshape(3, 200, 200, 3).astype(int)
    assert np.abs(rgb[..., 0] - rgb[..., 1]).mean() < 3        # grey
    assert np.abs(rgb[0] - rgb[2]).mean() > 1                   # grain changes


def test_the_look_keeps_the_sync(server, shoot, tmp_path):
    token = found(server, shoot)
    _, job = call(server, "/api/divein/export", design(token))
    job = finished(server, job["id"])
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    flash, click = flash_and_click(clip, tmp_path)
    assert abs(flash - click) <= 1 / FPS
    assert job["sync"][0]["in_sync"]
    assert float(probe(clip)["format"]["duration"]) == pytest.approx(6, abs=0.1)


# ── brightness ───────────────────────────────────────────────────────────
def squaring_lut(path, size=33):
    """A LUT that squares every value: so brightening before it differs from after."""
    steps = np.linspace(0, 1, size)
    lines = [f"LUT_3D_SIZE {size}"]
    for b in steps:
        for g in steps:
            for r in steps:
                lines.append(f"{r * r:.6f} {g * g:.6f} {b * b:.6f}")
    path.write_text("\n".join(lines) + "\n")
    return path


@pytest.fixture(scope="module")
def grey_flat(shoot, tmp_path_factory):
    """D-Log-style (10-bit) footage of a plain 40% grey wall, with the session's sound."""
    tmp = tmp_path_factory.mktemp("greyflat")
    folder = tmp / "Camera"
    folder.mkdir()
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", f"color=c=0x666666:s=320x180:r={FPS}:d=120", "-i", str(shoot / "cam.wav"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p10le", "-preset", "ultrafast",
            "-c:a", "aac", "-ar", "48000", "-shortest", str(folder / "DJI_0001.MP4")], "grey")
    (tmp / "mix.wav").write_bytes((shoot / "mix.wav").read_bytes())
    return tmp


def grey_level(server, token, tmp_path, **look):
    _, job = call(server, "/api/divein/export",
                  design(token, footage_look={**PLAIN, **look}))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    return middle_colour(clip).mean() / 255


def test_brightness_lifts_the_footage(server, grey_flat, tmp_path):
    server.divein.lut_path = str(tmp_path / "none.cube")              # no colour file
    token = found(server, grey_flat)
    plain = grey_level(server, token, tmp_path)
    brighter = grey_level(server, token, tmp_path, brightness=50)
    darker = grey_level(server, token, tmp_path, brightness=-50)
    assert brighter == pytest.approx(plain * 1.5, abs=0.03)
    assert darker == pytest.approx(plain * 0.5, abs=0.03)


def test_on_log_footage_brightness_comes_before_the_colour_file(server, grey_flat, tmp_path):
    """Graded the usual way: exposure set on the flat footage, then the LUT."""
    server.divein.lut_path = str(squaring_lut(tmp_path / "square.cube"))
    token = found(server, grey_flat)
    x = 0x66 / 255
    level = grey_level(server, token, tmp_path, brightness=50)
    assert level == pytest.approx((1.5 * x) ** 2, abs=0.03)              # before: 0.36
    assert abs(level - 1.5 * x * x) > 0.08                               # after would be 0.24


# ── 9:16 reels and zooming out ───────────────────────────────────────────
def frame_of(path, w, h):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(path), "-frames:v", "1",
                          "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "pipe:1"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w).astype(float)


def test_a_footage_clip_can_be_a_9_by_16_reel(server, shoot, tmp_path):
    token = found(server, shoot)
    _, job = call(server, "/api/divein/export", design(token, aspect="9:16"))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    video = [s for s in probe(clip)["streams"] if s["codec_type"] == "video"][0]
    assert (video["width"], video["height"]) == (1080, 1920)


@pytest.mark.parametrize("look", [PLAIN, {**PLAIN, "contrast": True}])
def test_zoomed_out_footage_sits_over_a_blurred_copy_of_itself(server, stripes, tmp_path, look):
    """
    White, grey, black stripes: all three across the middle. Above, the
    frame-filling cut (framed a quarter of the way along, so it spans the
    white and grey stripes), dimmed and blurred.
    """
    token = found(server, stripes)
    _, job = call(server, "/api/divein/export",
                  design(token, aspect="9:16", footage_look=look,
                         footage_crops=[{"cx": 0.25, "cy": 0.5, "zoom": 0.1}]))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(download(server, job, ".mp4"))
    frame = frame_of(clip, 108, 192)
    middle, top = frame[96], frame[20]
    assert middle[5] > 200 and middle[-5] < 50                     # the whole picture, edge to edge
    assert top[5] > top[-5] + 40                                   # a copy above it...
    assert top[5] < 200 and 15 < top[-5]                           # ...dimmed, not black...
    assert np.abs(np.diff(top)).max() < np.abs(np.diff(middle)).max() / 2   # ...and blurred


def test_zoomed_out_footage_keeps_the_sync(server, shoot, tmp_path):
    token = found(server, shoot)
    _, job = call(server, "/api/divein/export",
                  design(token, aspect="9:16", footage_crops=[{"cx": 0.5, "cy": 0.5, "zoom": 0.4}]))
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    assert job["sync"][0]["in_sync"], job["sync"]
