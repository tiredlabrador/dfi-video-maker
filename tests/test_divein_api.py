"""
Tests for the Dive In part of the local server, over real HTTP.

Covers the test area's own uploads (which must cope with whole mixes, far
bigger than a single track), the live still, the moving preview and the export.
"""
import io
import json
import threading
import time
import urllib.error
import urllib.request

import numpy as np
import pytest
from PIL import Image

import generate_video as gv
from app.server import create_server


@pytest.fixture
def server(tmp_path):
    srv = create_server(host="127.0.0.1", port=0, work_dir=str(tmp_path))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    folder = tmp_path_factory.mktemp("divein-media")
    mix = folder / "mix.mp3"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=55:duration=30", "-ac", "2", "-ar", "44100",
            str(mix)], "test mix")
    photo = folder / "dj.jpg"
    y, x = np.mgrid[0:1200, 0:1600]
    Image.fromarray(np.stack([x % 256, y % 256, (x * y) % 256], 2).astype(np.uint8)) \
        .save(photo, quality=90)
    return mix.read_bytes(), photo.read_bytes()


def raw_upload(server, kind, name, body, headers=None):
    merged = {"Content-Type": "application/octet-stream", "X-Filename": name}
    merged.update(headers or {})
    req = urllib.request.Request(f"{server.base_url}/api/divein/upload?kind={kind}",
                                 data=body, headers=merged, method="POST")
    return json.load(urllib.request.urlopen(req, timeout=60))


def post_json(server, path, payload, raw=False):
    req = urllib.request.Request(server.base_url + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    response = urllib.request.urlopen(req, timeout=120)
    return response if raw else json.load(response)


def get(server, path):
    return urllib.request.urlopen(server.base_url + path, timeout=60)


def design(photo, audio=None, **kw):
    d = {"photo": photo, "artist": "Artist\nName", "episode": "02.01",
         "crop": {"zoom": 1, "cx": 0.5, "cy": 0.5}, "glyph": "rings",
         "twitch": True, "debug": False, "overrides": {}}
    if audio:
        d.update(audio=audio, starts="0:05", clip_seconds=1)
    d.update(kw)
    return d


def wait(server, job_id, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = json.load(get(server, f"/api/divein/jobs/{job_id}"))
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.2)
    raise AssertionError("job never finished")


# ── uploads ─────────────────────────────────────────────────────────────
def test_a_photo_upload_reports_its_size(server, media):
    _, photo = media
    info = raw_upload(server, "photo", "dj.jpg", photo)
    assert info["token"] and (info["width"], info["height"]) == (1600, 1200)


def test_an_audio_upload_reports_its_length(server, media):
    mix, _ = media
    info = raw_upload(server, "audio", "mix.mp3", mix)
    assert info["duration"] == pytest.approx(30, abs=0.5)


def test_a_whole_mix_is_not_refused_for_being_big(server):
    """
    A two-hour mix can be 300MB; the rest of the app refuses anything over
    100MB. The Dive In upload streams to disk instead, so size isn't the issue.
    Junk bytes, so this fails for being unreadable — the point is it isn't 413.
    """
    body = b"\0" * (110 * 1024 * 1024)
    with pytest.raises(urllib.error.HTTPError) as caught:
        raw_upload(server, "audio", "huge.mp3", body)
    assert caught.value.code == 400
    assert "couldn't be read" in caught.value.read().decode()


def test_a_file_that_is_not_a_photo_says_so(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        raw_upload(server, "photo", "x.jpg", b"definitely not a picture")
    assert caught.value.code == 400
    assert "photo" in caught.value.read().decode().lower()


def test_an_unknown_upload_kind_is_refused(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        raw_upload(server, "spreadsheet", "x.csv", b"a,b")
    assert caught.value.code == 400


# ── the live still ──────────────────────────────────────────────────────
def test_the_still_comes_back_as_a_4_by_5_jpg(server, media):
    _, photo = media
    token = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    response = post_json(server, "/api/divein/still", design(token), raw=True)
    assert response.headers["Content-Type"] == "image/jpeg"
    assert Image.open(io.BytesIO(response.read())).size == (1080, 1350)


def test_the_still_can_show_the_square_version(server, media):
    _, photo = media
    token = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    response = post_json(server, "/api/divein/still", design(token, format="square"), raw=True)
    assert Image.open(io.BytesIO(response.read())).size == (1080, 1080)


def test_the_still_can_show_a_moment_in_the_audio(server, media):
    """Scrubbing to a time shows the coil as it is at that moment."""
    mix, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    a = raw_upload(server, "audio", "mix.mp3", mix)["token"]
    rest = post_json(server, "/api/divein/still", design(p), raw=True).read()
    moment = post_json(server, "/api/divein/still", design(p, a, time=0.5, debug=True),
                       raw=True).read()
    assert rest != moment


def test_a_misspelt_setting_is_reported_plainly(server, media):
    _, photo = media
    token = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/still",
                  design(token, overrides={"ghost": {"opactiy": 0.3}}), raw=True)
    assert caught.value.code == 400
    assert "ghost.opactiy" in caught.value.read().decode()


def test_a_bad_token_is_refused(server):
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/still", design("../../etc/passwd"), raw=True)
    assert caught.value.code == 400


def test_the_defaults_are_available_for_the_tuning_box(server):
    data = json.load(get(server, "/api/divein/defaults"))
    assert data["defaults"]["ghost"]["opacity"] == 0.45
    assert "fonts" in data


# ── preview and export ──────────────────────────────────────────────────
def test_the_moving_preview_is_a_small_mp4(server, media):
    mix, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    a = raw_upload(server, "audio", "mix.mp3", mix)["token"]
    job = post_json(server, "/api/divein/preview",
                    design(p, a, overrides={"export": {"preview_seconds": 1}}))
    done = wait(server, job["id"])
    assert done["status"] == "done", done.get("error")
    f = done["files"][0]
    assert f["name"].endswith(".mp4")
    data = get(server, f["url"]).read()
    assert data[4:8] == b"ftyp"


def test_export_makes_one_video_per_start_time_plus_the_square_jpg(server, media):
    mix, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    a = raw_upload(server, "audio", "mix.mp3", mix)["token"]
    job = post_json(server, "/api/divein/export", design(p, a, starts="0:02, 0:10"))
    done = wait(server, job["id"])
    assert done["status"] == "done", done.get("error")
    names = [f["name"] for f in done["files"]]
    assert sum(n.endswith(".mp4") for n in names) == 2
    assert sum(n.endswith(".jpg") for n in names) == 1
    assert all(n.startswith("Dive In 02.01 - Artist Name") for n in names)
    jpg = next(f for f in done["files"] if f["name"].endswith(".jpg"))
    assert Image.open(io.BytesIO(get(server, jpg["url"]).read())).size == (1080, 1080)


def test_a_start_time_that_cannot_be_read_is_refused_before_rendering(server, media):
    mix, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    a = raw_upload(server, "audio", "mix.mp3", mix)["token"]
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/export", design(p, a, starts="0:05, banana"))
    assert caught.value.code == 400
    assert "banana" in caught.value.read().decode()


def test_a_start_after_the_end_of_the_mix_fails_with_a_readable_message(server, media):
    mix, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    a = raw_upload(server, "audio", "mix.mp3", mix)["token"]
    job = post_json(server, "/api/divein/export", design(p, a, starts="5:00"))
    done = wait(server, job["id"])
    assert done["status"] == "error"
    assert "after the end" in done["error"]


def test_export_without_audio_is_refused(server, media):
    _, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/export", design(p))
    assert caught.value.code == 400
    assert "audio" in caught.value.read().decode().lower()


def test_the_existing_app_is_untouched(server):
    """The test area adds routes; the original ones still answer as before."""
    assert json.load(get(server, "/api/health"))["ok"] is True
    assert b"DFI" in get(server, "/").read()


def test_another_website_cannot_use_the_dive_in_routes(server):
    req = urllib.request.Request(f"{server.base_url}/api/divein/upload?kind=audio",
                                 data=b"x", method="POST",
                                 headers={"Origin": "https://evil.example.com"})
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(req, timeout=10)
    assert caught.value.code == 403
