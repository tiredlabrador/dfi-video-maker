"""Finding the footage folder without typing its location."""
import os

import pytest

from app.divein.folders import locate_folder


def make(root, rel, files):
    folder = root / rel
    folder.mkdir(parents=True, exist_ok=True)
    for name, size in files.items():
        (folder / name).write_bytes(b"x" * size)
    return str(folder)


def seen(files):
    return [{"name": n, "size": s} for n, s in files.items()]


def test_a_dropped_folder_is_found_by_its_name_and_videos(tmp_path):
    files = {"DJI_0001.MP4": 300, "DJI_0002.MP4": 120}
    want = make(tmp_path, "Drive/Edits/10.02 - Pirate October", files)
    make(tmp_path, "Drive/Edits/Other", {"song.mp3": 10})
    assert locate_folder("10.02 - Pirate October", seen(files), [str(tmp_path)]) == want


def test_of_two_folders_with_the_same_name_the_one_with_those_videos_wins(tmp_path):
    """Every session folder might be called "Footage": the videos say which."""
    make(tmp_path, "A/Footage", {"DJI_0001.MP4": 300})
    want = make(tmp_path, "B/Footage", {"DJI_0001.MP4": 301})
    assert locate_folder("Footage", seen({"DJI_0001.MP4": 301}), [str(tmp_path)]) == want


def test_dropped_videos_find_the_folder_they_live_in(tmp_path):
    files = {"DJI_0007.MP4": 50}
    want = make(tmp_path, "Drive/May", files)
    assert locate_folder(None, seen(files), [str(tmp_path)]) == want


def test_nothing_matching_finds_nothing(tmp_path):
    make(tmp_path, "Drive/Footage", {"DJI_0001.MP4": 300})
    assert locate_folder("Footage", seen({"DJI_0001.MP4": 999}), [str(tmp_path)]) is None
    assert locate_folder("Elsewhere", [], [str(tmp_path)]) is None


def test_two_copies_of_the_same_footage_are_both_fine(tmp_path):
    a = make(tmp_path, "A/Footage", {"DJI_0001.MP4": 300})
    b = make(tmp_path, "B/Footage", {"DJI_0001.MP4": 300})
    assert locate_folder("Footage", seen({"DJI_0001.MP4": 300}), [str(tmp_path)]) in (a, b)


def test_two_empty_folders_with_the_same_name_find_nothing_rather_than_guessing(tmp_path):
    make(tmp_path, "A/Footage", {})
    make(tmp_path, "B/Footage", {})
    assert locate_folder("Footage", [], [str(tmp_path)]) is None


def test_hidden_folders_and_app_bundles_are_not_searched(tmp_path):
    files = {"DJI_0001.MP4": 300}
    make(tmp_path, ".Trashes/Footage", files)
    make(tmp_path, "Premiere.app/Footage", files)
    want = make(tmp_path, "Edits/Footage", files)
    assert locate_folder("Footage", seen(files), [str(tmp_path)]) == want


def test_a_missing_root_is_skipped(tmp_path):
    files = {"DJI_0001.MP4": 300}
    want = make(tmp_path, "Footage", files)
    assert locate_folder("Footage", seen(files), [str(tmp_path / "gone"), str(tmp_path)]) == want


# ── over the app's own server ────────────────────────────────────────────
import json
import threading
import urllib.error
import urllib.request

from app.server import create_server


@pytest.fixture
def server(tmp_path):
    srv = create_server(host="127.0.0.1", port=0, work_dir=str(tmp_path / "work"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    srv.base_url = f"http://127.0.0.1:{srv.server_address[1]}"
    yield srv
    srv.shutdown()
    srv.server_close()


def post(server, path, body):
    req = urllib.request.Request(f"{server.base_url}{path}", data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def test_the_page_can_ask_where_a_dropped_folder_is(server, tmp_path, monkeypatch):
    files = {"DJI_0001.MP4": 300}
    want = make(tmp_path, "Drive/Footage", files)
    monkeypatch.setattr("app.divein.folders.search_roots", lambda: [str(tmp_path / "Drive")])
    found = post(server, "/api/divein/locate-folder", {"name": "Footage", "files": seen(files)})
    assert found == {"folder": want}


def test_a_folder_it_cannot_find_says_so(server, tmp_path, monkeypatch):
    monkeypatch.setattr("app.divein.folders.search_roots", lambda: [str(tmp_path)])
    with pytest.raises(urllib.error.HTTPError) as caught:
        post(server, "/api/divein/locate-folder", {"name": "Nowhere", "files": []})
    assert caught.value.code == 404
    assert "choose" in json.load(caught.value)["error"].lower()


def test_the_page_can_open_the_macs_folder_chooser(server, monkeypatch):
    monkeypatch.setattr("app.divein.folders.choose_folder", lambda start=None: "/Volumes/Drive/Footage")
    assert post(server, "/api/divein/choose-folder", {}) == {"folder": "/Volumes/Drive/Footage"}


def test_closing_the_chooser_without_picking_is_not_an_error(server, monkeypatch):
    monkeypatch.setattr("app.divein.folders.choose_folder", lambda start=None: None)
    assert post(server, "/api/divein/choose-folder", {}) == {"folder": None}
