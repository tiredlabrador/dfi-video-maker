"""
Tests for finding a mix inside camera footage, by listening.

The camera's mic heard the room: the same music, but muddier, quieter and
with chatter on top. Synthetic audio stands in for both, because it has a
right answer: a mix placed 50.000s into a recording must be found at 50.000s.
"""
import os
import threading

import numpy as np
import pytest

import generate_video as gv
from app.divein.footage import (MATCH_SR, FootageError, camera_audio, describe,
                                find_mix, list_videos, locate, video_time)

SR = MATCH_SR


def music(seconds, seed=1):
    """Noise shaped into beats and bars: never repeats, like a real mix."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    beat = np.exp(-(t % 0.5) * 12)                       # 120 bpm
    bars = 0.5 + 0.5 * rng.random(int(seconds / 2) + 1)[(t // 2).astype(int)]
    return (rng.normal(size=n) * beat * bars).astype(np.float32)


def room(clean, seed=2):
    """What a camera across the room hears: echoey, quieter, with chatter."""
    rng = np.random.default_rng(seed)
    echo = np.zeros(int(0.05 * SR))                 # the speaker, then the walls
    for ms, level in [(0, 1.0), (7, 0.6), (15, 0.4), (31, 0.3), (46, 0.2)]:
        echo[int(ms / 1000 * SR)] = level
    heard = np.convolve(clean, echo)[:clean.size] * 0.3
    return (heard + rng.normal(size=clean.size) * heard.std()).astype(np.float32)


def chatter(seconds, seed=3):
    return (np.random.default_rng(seed).normal(size=int(seconds * SR)) * 0.1).astype(np.float32)


def test_a_mix_that_starts_after_the_camera_is_found_at_the_right_moment():
    mix = music(300)
    cam = chatter(400)
    cam[50 * SR:350 * SR] += room(mix)
    [match] = locate(mix, [cam])
    assert match["video"] == 0
    assert match["offset"] == pytest.approx(50.0, abs=0.01)
    assert match["mix_from"] == pytest.approx(0.0, abs=0.01)
    assert match["mix_to"] == pytest.approx(300.0, abs=0.01)


def test_a_camera_switched_on_late_is_found_with_the_part_it_missed():
    """Tom's case: the camera started 7½ minutes into his set."""
    mix = music(300)
    cam = chatter(400)
    cam[:260 * SR] += room(mix[40 * SR:])
    [match] = locate(mix, [cam])
    assert match["offset"] == pytest.approx(-40.0, abs=0.01)
    assert match["mix_from"] == pytest.approx(40.0, abs=0.01)
    assert match["mix_to"] == pytest.approx(300.0, abs=0.01)


def test_footage_of_a_different_mix_finds_nothing():
    cam = chatter(400)
    cam[50 * SR:350 * SR] += room(music(300, seed=9))
    assert locate(music(300), [cam]) == []


def test_a_mix_that_runs_from_one_video_into_the_next_is_found_in_both():
    mix = music(300)
    first, second = chatter(400), chatter(400, seed=4)
    first[250 * SR:] += room(mix[:150 * SR])
    second[:150 * SR] += room(mix[150 * SR:], seed=5)
    matches = locate(mix, [first, second])
    assert [m["video"] for m in matches] == [0, 1]
    assert matches[0]["offset"] == pytest.approx(250.0, abs=0.01)
    assert matches[0]["mix_to"] == pytest.approx(150.0, abs=0.01)
    assert matches[1]["offset"] == pytest.approx(-150.0, abs=0.01)
    assert matches[1]["mix_from"] == pytest.approx(150.0, abs=0.01)


def test_a_short_mix_can_still_be_found():
    mix = music(40)
    cam = chatter(200)
    cam[100 * SR:140 * SR] += room(mix)
    [match] = locate(mix, [cam])
    assert match["offset"] == pytest.approx(100.0, abs=0.01)


def test_one_shared_track_with_a_different_set_is_not_a_match():
    """Two DJs playing the same record mustn't be mistaken for one another."""
    mix = music(300)
    other = music(1400, seed=9)
    other[700 * SR:760 * SR] = mix[100 * SR:160 * SR]
    assert locate(mix, [room(other)]) == []


def test_a_loud_stretch_of_other_music_does_not_drown_out_the_real_match():
    mix = music(300)
    cam = chatter(900)
    cam[50 * SR:350 * SR] += room(mix)
    cam[400 * SR:700 * SR] += 10 * room(music(300, seed=9))
    [match] = locate(mix, [cam])
    assert match["offset"] == pytest.approx(50.0, abs=0.01)


def test_a_loud_stretch_of_other_music_alone_finds_nothing():
    cam = chatter(900)
    cam[400 * SR:700 * SR] += 10 * room(music(300, seed=9))
    assert locate(music(300), [cam]) == []


def test_a_camera_clock_running_slightly_fast_is_followed_to_the_end():
    """
    The recorder and camera clocks never tick at exactly the same speed: 40
    parts per million is 0.14s over an hour, several video frames.
    """
    mix = music(3600)
    rate = 1 + 40e-6
    filmed = np.interp(np.arange(int(3600 * rate * SR)) / rate, np.arange(mix.size),
                       mix).astype(np.float32)
    cam = chatter(3700)
    cam[30 * SR:30 * SR + filmed.size] += room(filmed)
    [match] = locate(mix, [cam])
    for t in (0, 1800, 3590):
        assert video_time(match, t) == pytest.approx(30 + t * rate, abs=0.01)


def test_a_mix_too_short_to_search_for_says_so():
    with pytest.raises(FootageError, match="too short"):
        locate(music(10), [chatter(100)])


def test_matches_are_described_in_plain_words():
    matches = [{"video": 0, "offset": -451.18, "mix_from": 451.18, "mix_to": 2140.3}]
    text = describe(matches, ["DJI_0001.MP4", "DJI_0002.MP4"])
    assert "7:31" in text and "35:40" in text and "DJI_0001.MP4" in text


def test_no_match_is_described_as_such():
    text = describe([], ["DJI_0001.MP4", "DJI_0002.MP4"])
    assert "couldn't find" in text.lower() and "2 videos" in text


# ── the folder of footage ────────────────────────────────────────────────
def test_only_videos_are_picked_from_the_folder_in_name_order(tmp_path):
    for name in ["DJI_0002.MP4", "DJI_0001.mp4", "clip.MOV", "notes.txt",
                 "._DJI_0001.mp4", ".hidden.mp4"]:
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "sub.mp4").mkdir()
    names = [os.path.basename(p) for p in list_videos(str(tmp_path))]
    assert names == ["DJI_0001.mp4", "DJI_0002.MP4", "clip.MOV"]


def test_a_folder_that_does_not_exist_says_so(tmp_path):
    with pytest.raises(FootageError, match="find that folder"):
        list_videos(str(tmp_path / "nope"))


def test_a_folder_with_no_videos_says_so(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")
    with pytest.raises(FootageError, match="no videos"):
        list_videos(str(tmp_path))


def test_a_blank_folder_is_refused():
    with pytest.raises(FootageError):
        list_videos("  ")


# ── real files, end to end ───────────────────────────────────────────────
def write_wav(path, mono):
    import wave
    pcm = (np.clip(mono / (np.abs(mono).max() + 1e-9), -1, 1) * 32000).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def write_video(path, mono, tmp_path):
    wav = tmp_path / (path.stem + ".wav")
    write_wav(wav, mono)
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            f"color=c=black:s=64x80:r=5:d={mono.size / SR}", "-i", str(wav),
            "-c:v", "libx264", "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
            "-shortest", str(path)], "test video")


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    """A 'mix' and a folder of two 'camera' videos, the mix inside the second."""
    tmp = tmp_path_factory.mktemp("footage")
    mix = music(90)
    folder = tmp / "Camera"
    folder.mkdir()
    write_video(folder / "DJI_0001.MP4", chatter(60), tmp)
    cam = chatter(120, seed=7)
    cam[20 * SR:110 * SR] += room(mix)
    write_video(folder / "DJI_0002.MP4", cam, tmp)
    write_wav(tmp / "mix.wav", mix)
    return tmp / "mix.wav", folder


def test_a_mix_is_found_in_a_folder_of_real_video_files(session, tmp_path):
    mix, folder = session
    seen = []
    found = find_mix(str(mix), str(folder), str(tmp_path / "cache"),
                     progress=lambda f, m="": seen.append((f, m)))
    [match] = found["matches"]
    assert match["name"] == "DJI_0002.MP4"
    # The video's audio is re-encoded, so allow a millisecond or two.
    assert match["offset"] == pytest.approx(20.0, abs=0.01)
    assert found["videos"] == ["DJI_0001.MP4", "DJI_0002.MP4"]
    assert "DJI_0002.MP4" in found["message"]
    assert seen and all(0 <= f <= 1 for f, _ in seen)


def test_the_camera_audio_is_kept_so_a_second_search_is_quick(session, tmp_path):
    _, folder = session
    video = str(folder / "DJI_0002.MP4")
    cache = str(tmp_path / "cache")
    first = camera_audio(video, cache)
    saved = os.listdir(cache)
    assert len(saved) == 1
    # A fresh read would land as a new file (written aside, then swapped in).
    inode = os.stat(os.path.join(cache, saved[0])).st_ino
    again = camera_audio(video, cache)
    assert np.array_equal(first, again)
    assert os.stat(os.path.join(cache, saved[0])).st_ino == inode
    assert first.size == pytest.approx(120 * SR, abs=SR)


def test_two_searches_of_the_same_video_at_once_both_get_all_of_it(session, tmp_path):
    _, folder = session
    video = str(folder / "DJI_0002.MP4")
    cache = str(tmp_path / "cache")
    results = []
    threads = [threading.Thread(target=lambda: results.append(camera_audio(video, cache)))
               for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 3
    assert all(r.size == pytest.approx(120 * SR, abs=SR) for r in results)
    assert [n for n in os.listdir(cache) if not n.endswith(".f32")] == []


def test_saved_camera_audio_that_was_cut_short_is_read_again(session, tmp_path):
    _, folder = session
    video = str(folder / "DJI_0002.MP4")
    cache = str(tmp_path / "cache")
    full = camera_audio(video, cache)
    saved = os.path.join(cache, os.listdir(cache)[0])
    with open(saved, "r+b") as f:
        f.truncate(1000)
    assert camera_audio(video, cache).size == full.size


def test_sound_that_starts_after_the_picture_is_allowed_for(tmp_path):
    """The cut is made in video time, so a late-starting sound track shifts it."""
    mix = music(90)
    cam = chatter(120, seed=7)
    cam[20 * SR:110 * SR] += room(mix)
    folder = tmp_path / "Camera"
    folder.mkdir()
    write_wav(tmp_path / "cam.wav", cam)
    write_wav(tmp_path / "mix.wav", mix)
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            "color=c=black:s=64x80:r=5:d=121", "-itsoffset", "0.5", "-i", str(tmp_path / "cam.wav"),
            "-c:v", "libx264", "-c:a", "aac", "-ar", "48000", str(folder / "late.mp4")],
           "late sound")
    [match] = find_mix(str(tmp_path / "mix.wav"), str(folder), str(tmp_path / "cache"))["matches"]
    assert match["offset"] == pytest.approx(20.5, abs=0.03)


def test_a_damaged_video_is_reported_by_name_without_its_folder(tmp_path):
    folder = tmp_path / "Camera"
    folder.mkdir()
    (folder / "DJI_bad.MP4").write_bytes(os.urandom(5000))
    write_wav(tmp_path / "mix.wav", music(30))
    with pytest.raises(FootageError) as caught:
        find_mix(str(tmp_path / "mix.wav"), str(folder), str(tmp_path / "cache"))
    assert "DJI_bad.MP4" in str(caught.value) and str(folder) not in str(caught.value)


def test_a_video_with_no_sound_is_skipped_not_fatal(tmp_path):
    folder = tmp_path / "Camera"
    folder.mkdir()
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
            "color=c=black:s=64x80:r=5:d=3", "-c:v", "libx264",
            str(folder / "silent.mp4")], "silent video")
    write_wav(tmp_path / "mix.wav", music(30))
    found = find_mix(str(tmp_path / "mix.wav"), str(folder), str(tmp_path / "cache"))
    assert found["matches"] == []
    assert "no sound" in found["message"].lower()


# ── over the app's own server ────────────────────────────────────────────
import json
import threading
import time
import urllib.error
import urllib.request

from app.server import create_server


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
    req = urllib.request.Request(server.base_url + path, data=data,
                                 headers={"Content-Type": "application/json"},
                                 method="POST" if data else "GET")
    try:
        response = urllib.request.urlopen(req, timeout=60)
        return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def upload_mix(server, path):
    req = urllib.request.Request(f"{server.base_url}/api/divein/upload?kind=audio",
                                 data=path.read_bytes(), method="POST",
                                 headers={"Content-Type": "application/octet-stream",
                                          "X-Filename": "mix.wav"})
    return json.load(urllib.request.urlopen(req, timeout=60))["token"]


def finished(server, job_id):
    deadline = time.time() + 120
    while time.time() < deadline:
        _, job = call(server, f"/api/divein/jobs/{job_id}")
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.2)
    raise AssertionError("job never finished")


def test_the_page_can_ask_where_the_mix_is_in_the_footage(server, session):
    mix, folder = session
    status, job = call(server, "/api/divein/footage",
                       {"audio": upload_mix(server, mix), "folder": str(folder)})
    assert status == 202
    job = finished(server, job["id"])
    assert job["status"] == "done", job.get("error")
    found = job["footage"]
    assert "DJI_0002.MP4" in found["message"]
    [match] = found["matches"]
    assert match["name"] == "DJI_0002.MP4"
    assert match["offset"] == pytest.approx(20.0, abs=0.01)
    assert str(folder) not in json.dumps(job)          # no private paths sent back


def test_a_footage_folder_that_is_not_there_is_refused_straight_away(server, session):
    mix, folder = session
    status, body = call(server, "/api/divein/footage",
                        {"audio": upload_mix(server, mix), "folder": str(folder / "gone")})
    assert status == 400
    assert "find that folder" in body["error"] and str(folder) not in body["error"]


def test_looking_for_footage_needs_the_mix_first(server, session):
    _, folder = session
    status, body = call(server, "/api/divein/footage", {"folder": str(folder)})
    assert status == 400 and "mix" in body["error"].lower()


def search(server, mix, folder):
    token = upload_mix(server, mix)
    _, job = call(server, "/api/divein/footage", {"audio": token, "folder": str(folder)})
    assert finished(server, job["id"])["status"] == "done"
    return token


def fetch(server, path, headers=None):
    req = urllib.request.Request(server.base_url + path, headers=headers or {})
    try:
        response = urllib.request.urlopen(req, timeout=60)
        return response.status, response.headers, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def test_the_found_footage_can_be_watched_in_the_page(server, session):
    """The page plays the camera's own file, so it must be able to jump around in it."""
    mix, folder = session
    token = search(server, mix, folder)
    status, headers, body = fetch(server, f"/api/divein/footage/video?audio={token}&video=1",
                                  {"Range": "bytes=0-99"})
    assert status == 206
    assert headers["Content-Type"] == "video/mp4"
    assert body == (folder / "DJI_0002.MP4").read_bytes()[:100]


def test_footage_for_a_mix_that_was_never_searched_is_not_served(server, session):
    mix, _ = session
    token = upload_mix(server, mix)
    status, _, _ = fetch(server, f"/api/divein/footage/video?audio={token}&video=0")
    assert status == 404


@pytest.mark.parametrize("video", ["2", "-1", "abc", ""])
def test_only_the_videos_that_were_searched_are_served(server, session, video):
    mix, folder = session
    token = search(server, mix, folder)
    status, _, _ = fetch(server, f"/api/divein/footage/video?audio={token}&video={video}")
    assert status == 404


@pytest.fixture
def huge_video(server, session, tmp_path):
    """A 40MB stand-in for a 17GB camera file, already 'found' for a mix."""
    mix, _ = session
    token = upload_mix(server, mix)
    path = tmp_path / "DJI_huge.MP4"
    with open(path, "wb") as f:
        f.write(b"head")
        f.truncate(40 * 1024 * 1024)
        f.seek(-4, 2)
        f.write(b"tail")
    server.divein._footage[token] = ([str(path)], [])
    return token, path


def test_an_open_ended_request_for_a_huge_video_gets_a_manageable_piece(server, huge_video):
    """Browsers ask for 'from byte 0 to the end' first: 17GB can't go in one reply."""
    token, path = huge_video
    status, headers, body = fetch(server, f"/api/divein/footage/video?audio={token}&video=0",
                                  {"Range": "bytes=0-"})
    assert status == 206
    assert 0 < len(body) <= 16 * 1024 * 1024
    assert headers["Content-Range"] == f"bytes 0-{len(body) - 1}/{path.stat().st_size}"
    assert body.startswith(b"head")


def test_the_end_of_a_huge_video_can_be_read(server, huge_video):
    """Camera files keep their index at the very end; the browser jumps there first."""
    token, path = huge_video
    size = path.stat().st_size
    status, _, body = fetch(server, f"/api/divein/footage/video?audio={token}&video=0",
                            {"Range": f"bytes={size - 1000}-"})
    assert status == 206 and len(body) == 1000 and body.endswith(b"tail")


def test_a_whole_huge_file_still_arrives_complete_when_asked_for_whole(server, huge_video):
    token, path = huge_video
    status, _, body = fetch(server, f"/api/divein/footage/video?audio={token}&video=0")
    assert status == 200 and len(body) == path.stat().st_size and body.endswith(b"tail")


def test_saved_camera_sound_outlives_a_restart_of_the_app():
    """The app's own scratch folder is wiped on every start; this mustn't be."""
    srv = create_server(host="127.0.0.1", port=0)
    try:
        cache = srv.divein.footage_cache
        assert not cache.startswith(srv.render_service.work_dir)
        assert cache.startswith(os.path.expanduser("~/Library/Caches"))
    finally:
        srv.server_close()


def test_a_test_server_keeps_its_saved_sound_in_its_own_folder(server, tmp_path):
    assert server.divein.footage_cache.startswith(str(tmp_path))


def test_a_slow_old_search_finishing_last_does_not_replace_the_newer_one(server, session,
                                                                        monkeypatch):
    """Search folder A (slow), then B (quick): the page must keep B's videos."""
    import app.divein.api as api
    mix, folder = session
    token = upload_mix(server, mix)
    other = folder.parent / "Other"
    other.mkdir(exist_ok=True)
    (other / "OTHER.MP4").write_bytes((folder / "DJI_0001.MP4").read_bytes())
    release = threading.Event()
    real = api.find_mix

    def find(mix_path, folder_path, cache, progress=None, videos=None):
        if folder_path == str(folder):
            release.wait(30)
        return real(mix_path, folder_path, cache, progress, videos=videos)
    monkeypatch.setattr(api, "find_mix", find)
    _, slow = call(server, "/api/divein/footage", {"audio": token, "folder": str(folder)})
    _, quick = call(server, "/api/divein/footage", {"audio": token, "folder": str(other)})
    assert finished(server, quick["id"])["status"] == "done"
    release.set()
    assert finished(server, slow["id"])["status"] == "done"
    _, _, body = fetch(server, f"/api/divein/footage/video?audio={token}&video=0")
    assert body == (other / "OTHER.MP4").read_bytes()


def test_another_website_cannot_show_the_footage(server, huge_video):
    token, _ = huge_video
    status, _, _ = fetch(server, f"/api/divein/footage/video?audio={token}&video=0",
                         {"Sec-Fetch-Site": "cross-site", "Range": "bytes=0-9"})
    assert status == 403
    status, _, _ = fetch(server, f"/api/divein/footage/video?audio={token}&video=0",
                         {"Sec-Fetch-Site": "same-origin", "Range": "bytes=0-9"})
    assert status == 206


def test_a_file_that_shrinks_mid_reply_hangs_up_rather_than_leaving_the_player_waiting(
        server, huge_video, monkeypatch):
    import http.client
    token, path = huge_video
    real = os.path.getsize
    monkeypatch.setattr(os.path, "getsize",
                        lambda p: real(p) + 5000 if str(p) == str(path) else real(p))
    size = real(path)
    # Kept open between requests, as a browser does (urllib always hangs up).
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request("GET", f"/api/divein/footage/video?audio={token}&video=0",
                 headers={"Range": f"bytes={size - 1000}-{size + 3999}"})
    with pytest.raises(http.client.IncompleteRead):
        conn.getresponse().read()


def test_the_download_all_link_of_a_footage_search_is_a_plain_404(server, session):
    mix, folder = session
    token = upload_mix(server, mix)
    _, job = call(server, "/api/divein/footage", {"audio": token, "folder": str(folder)})
    finished(server, job["id"])
    status, _, _ = fetch(server, f"/api/divein/jobs/{job['id']}/zip")
    assert status == 404


def test_leftover_half_reads_are_cleared_and_the_saved_sound_is_kept_in_bounds(tmp_path):
    from app.divein.footage import tidy_cache
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "abc.part").write_bytes(b"x" * 100)
    for i, age in enumerate([300, 200, 100]):                 # seconds ago
        f = cache / f"{i}.f32"
        f.write_bytes(b"x" * 1000)
        os.utime(f, (time.time() - age, time.time() - age))
    tidy_cache(str(cache), max_bytes=2500)
    assert sorted(os.listdir(cache)) == ["1.f32", "2.f32"]     # the oldest went


# ── D-Log footage ────────────────────────────────────────────────────────
def test_footage_filmed_flat_in_ten_bit_is_marked_for_colour_correction(tmp_path):
    """DJI's flat D-Log M mode records 10-bit; its normal colour mode 8-bit."""
    mix = music(60)
    cam = chatter(80, seed=7)
    cam[10 * SR:70 * SR] += room(mix)
    folder = tmp_path / "Camera"
    folder.mkdir()
    write_wav(tmp_path / "cam.wav", cam)
    write_wav(tmp_path / "mix.wav", mix)
    # DJI's HDR mode (HLG) is 10-bit too, but it isn't D-Log and mustn't get its LUT.
    for name, fmt, extra in [("flat.mp4", "yuv420p10le", []), ("normal.mp4", "yuv420p", []),
                             ("hdr.mp4", "yuv420p10le", ["-x264-params", "colorprim=bt2020:"
                                                          "transfer=arib-std-b67:colormatrix=bt2020nc"])]:
        gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "color=c=gray:s=64x80:r=5:d=80", "-i", str(tmp_path / "cam.wav"),
                "-c:v", "libx264", "-pix_fmt", fmt, *extra, "-c:a", "aac", "-shortest",
                str(folder / name)], "test video")
    found = find_mix(str(tmp_path / "mix.wav"), str(folder), str(tmp_path / "cache"))
    flat = {m["name"]: m["log"] for m in found["matches"]}
    assert flat == {"flat.mp4": True, "normal.mp4": False, "hdr.mp4": False}


def test_the_colour_correction_file_is_served_to_the_page(server, tmp_path):
    lut = tmp_path / "dlogm.cube"
    lut.write_text("LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n")
    server.divein.lut_path = str(lut)
    status, _, body = fetch(server, "/api/divein/lut")
    assert status == 200 and body.startswith(b"LUT_3D_SIZE 2")


def test_a_missing_colour_correction_file_says_where_it_goes(server, tmp_path):
    server.divein.lut_path = str(tmp_path / "nothing.cube")
    status, _, body = fetch(server, "/api/divein/lut")
    assert status == 404 and b"assets/luts" in body


def test_a_camera_whose_sound_cannot_be_read_alone_is_still_read_the_long_way(tmp_path):
    """Uncompressed sound (PCM) has no index-friendly AAC: ffmpeg reads the whole file."""
    write_wav(tmp_path / "cam.wav", chatter(30))
    video = tmp_path / "cam.mov"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x80:r=5:d=30",
            "-i", str(tmp_path / "cam.wav"), "-c:v", "libx264", "-c:a", "pcm_s16le",
            "-shortest", str(video)], "pcm video")
    sound = camera_audio(str(video), str(tmp_path / "cache"))
    assert sound.size == pytest.approx(30 * SR, abs=SR // 10)


def test_the_quick_read_is_used_for_camera_style_files(session, tmp_path, monkeypatch):
    import app.divein.footage as footage
    _, folder = session
    calls = []
    real = footage.read_sound
    monkeypatch.setattr(footage, "read_sound", lambda *a: calls.append(a) or real(*a))
    camera_audio(str(folder / "DJI_0002.MP4"), str(tmp_path / "cache"))
    assert calls
