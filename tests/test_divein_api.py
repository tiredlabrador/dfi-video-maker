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
    # Debug stays off in both, so any difference comes from the audio moment.
    rest = post_json(server, "/api/divein/still", design(p), raw=True).read()
    moment = post_json(server, "/api/divein/still", design(p, a, time=0.5),
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
    """Refused before rendering (it used to fail partway through the job)."""
    mix, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    a = raw_upload(server, "audio", "mix.mp3", mix)["token"]
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/export", design(p, a, starts="5:00"))
    assert caught.value.code == 400
    assert "after the end" in caught.value.read().decode()


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


# ── found in review ─────────────────────────────────────────────────────
def test_a_name_with_curly_quotes_or_polish_letters_still_downloads(server, media):
    """
    The filename went raw into a header that only allows Latin-1, so the
    download connection died. ’, –, Ł and emoji all triggered it.
    """
    mix, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    a = raw_upload(server, "audio", "mix.mp3", mix)["token"]
    job = post_json(server, "/api/divein/preview",
                    design(p, a, artist="Mike’s Müller – DJ 😀",
                           overrides={"export": {"preview_seconds": 1}}))
    done = wait(server, job["id"])
    assert done["status"] == "done", done.get("error")
    f = done["files"][0]
    assert "Mike’s Müller – DJ 😀" in f["name"], "the name is kept as typed"
    assert get(server, f["url"]).read()[4:8] == b"ftyp"


def test_an_upload_name_with_curly_quotes_arrives_intact(server, media):
    """The page sends the name percent-encoded, since browsers refuse others."""
    _, photo = media
    from urllib.parse import quote
    info = raw_upload(server, "photo", quote("Dom’s photo.jpg"), photo)
    assert info["name"] == "Dom’s photo.jpg"


@pytest.mark.parametrize("bad", [
    {"crop": {"zoom": None, "cx": 0.5, "cy": 0.5}},
    {"crop": [1, 2, 3]},
    {"time": "abc"},
    {"overrides": {"tape": {"text": ""}}},
    {"overrides": {"glyph": {"rings": {"count": 0}}}},
    {"overrides": {"glyph": {"bars": {"count": 0}}}},
    {"overrides": {"accents": {"yellow": "yellow"}}},
    {"overrides": {"canvas": {"width": 0}}},
    {"overrides": {"canvas": {"fps": 0}}},
    {"overrides": {"grain": {"pool": 1000}}},
])
def test_bad_values_get_a_plain_answer_not_a_dropped_connection(server, media, bad):
    mix, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    a = raw_upload(server, "audio", "mix.mp3", mix)["token"]
    body = design(p, a, **({} if "time" not in bad else {}))
    body.update(bad)
    if "time" not in body:
        body["time"] = 1.0
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/still", body, raw=True)
    assert caught.value.code == 400
    message = json.loads(caught.value.read())["error"]
    assert message and "Traceback" not in message and "/private/" not in message


def test_an_unexpected_failure_still_gets_an_answer(server, media, monkeypatch):
    """Anything unforeseen comes back as a 500 with a message, never silence."""
    _, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    import app.divein.api as api
    def boom(*a, **k):
        raise RuntimeError("something odd")
    monkeypatch.setattr(api.Scene, "frame", boom)
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/still", design(p), raw=True)
    assert caught.value.code == 500
    assert "something odd" in json.loads(caught.value.read())["error"]


def test_tuning_cannot_silently_fight_the_page_controls(server, media):
    """Glyph style, twitch and clip length are set by the page; say so."""
    _, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/still",
                  design(p, overrides={"glyph": {"mode": "bars"}}), raw=True)
    assert caught.value.code == 400
    assert "Style" in caught.value.read().decode()


def test_a_start_time_past_the_end_is_refused_before_anything_renders(server, media):
    """
    Previously the export rendered the good clips, hit the bad one, failed,
    and offered nothing. Now it's caught up front and named.
    """
    mix, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    a = raw_upload(server, "audio", "mix.mp3", mix)["token"]
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/export", design(p, a, starts="0:02, 0:05, 9:00"))
    assert caught.value.code == 400
    assert "9:00" in caught.value.read().decode()


@pytest.mark.parametrize("bad", ["inf", "nan", "-0:05"])
def test_nonsense_start_times_are_refused(server, media, bad):
    mix, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    a = raw_upload(server, "audio", "mix.mp3", mix)["token"]
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/export", design(p, a, starts=bad))
    assert caught.value.code == 400


def test_scrubbing_to_a_time_shows_that_exact_frame(server, media):
    """The slider steps in 1/30s; 4.0333s must be frame 121, not 120."""
    from app.divein.api import frame_at
    assert frame_at(4.0333, 30, 25.0) == 121
    assert frame_at(0.0333, 30, 25.0) == 1
    assert frame_at(30.0, 30, 25.0) == 749


def test_the_still_warns_about_letters_the_font_lacks(server, media):
    _, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    response = post_json(server, "/api/divein/still", design(p, artist="Жора"), raw=True)
    from urllib.parse import unquote
    assert "Ж" in unquote(response.headers.get("X-Divein-Warning", ""))


def test_a_stale_still_request_is_skipped(server, media):
    """While dragging, only the newest picture matters; older ones are dropped."""
    _, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    newer = post_json(server, "/api/divein/still", design(p, seq=10), raw=True)
    assert newer.status == 200
    older = post_json(server, "/api/divein/still", design(p, seq=9), raw=True)
    assert older.status == 204


# ── colour choice and the fast drag preview ─────────────────────────────
def test_the_colour_can_be_chosen(server, media):
    _, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    img = Image.open(io.BytesIO(post_json(server, "/api/divein/still",
                                          design(p, colour="green"), raw=True).read()))
    a = np.asarray(img.convert("RGB")).astype(int)
    green = (a[..., 1] > 230) & (a[..., 0] < 90) & (a[..., 2] < 40)
    assert green.sum() > 2000                      # tape and coil


def test_an_unknown_colour_is_refused(server, media):
    _, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/still", design(p, colour="purple"), raw=True)
    assert caught.value.code == 400


def test_the_layers_image_is_a_transparent_png(server):
    """No photo needed: it's what goes on top of the photo in the drag draft."""
    response = post_json(server, "/api/divein/layers",
                         {"artist": "A", "episode": "02.01", "format": "square",
                          "colour": "red", "glyph": "rings", "overrides": {}}, raw=True)
    assert response.headers["Content-Type"] == "image/png"
    img = Image.open(io.BytesIO(response.read()))
    assert img.mode == "RGBA" and img.size == (1080, 1080)


def test_a_reloaded_page_still_gets_its_previews(server, media):
    """
    Found by Dom: the server remembered the highest request number it had seen,
    so after a page reload (whose count starts again at 1) every preview was
    treated as stale and skipped. Each open page now keeps its own count.
    """
    _, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    before = post_json(server, "/api/divein/still", design(p, seq=250, client="page-a"), raw=True)
    assert before.status == 200
    reloaded = post_json(server, "/api/divein/still", design(p, seq=1, client="page-b"), raw=True)
    assert reloaded.status == 200
    # Within one page, an older request is still dropped.
    stale = post_json(server, "/api/divein/still", design(p, seq=200, client="page-a"), raw=True)
    assert stale.status == 204


def test_the_motion_can_follow_volume_instead_of_kicks(server, media):
    mix, photo = media
    p = raw_upload(server, "photo", "dj.jpg", photo)["token"]
    a = raw_upload(server, "audio", "mix.mp3", mix)["token"]
    ok = post_json(server, "/api/divein/still", design(p, a, time=1.0, motion="level"), raw=True)
    assert ok.status == 200
    with pytest.raises(urllib.error.HTTPError) as caught:
        post_json(server, "/api/divein/still", design(p, a, time=1.0, motion="vibes"), raw=True)
    assert caught.value.code == 400
