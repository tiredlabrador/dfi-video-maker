"""
Tests for reading Spotify track IDs out of the sheet.

The sheet already holds a Spotify link per row, so the playlist is built from
those directly — no searching by name, which would be a guess.
"""
import generate_video as gv

TRACK = "4XXo4kVEmg8kYCW0p4k5My"


def test_plain_track_link():
    assert gv.extract_spotify_track_id(
        f"https://open.spotify.com/track/{TRACK}") == TRACK


def test_track_link_with_share_parameters():
    assert gv.extract_spotify_track_id(
        f"https://open.spotify.com/track/{TRACK}?si=397553029d3d43f9") == TRACK


def test_localised_track_link():
    """Spotify sometimes hands out /intl-xx/ links."""
    assert gv.extract_spotify_track_id(
        f"https://open.spotify.com/intl-de/track/{TRACK}?si=abc") == TRACK


def test_spotify_uri():
    assert gv.extract_spotify_track_id(f"spotify:track:{TRACK}") == TRACK


def test_bare_id():
    assert gv.extract_spotify_track_id(TRACK) == TRACK


def test_album_link_is_not_a_track():
    """An album link can't go in a playlist as-is — don't pretend it can."""
    assert gv.extract_spotify_track_id(
        "https://open.spotify.com/album/7BaSHRP1rL08J1NeAnd6IS") is None


def test_blank_and_rubbish():
    for value in ("", None, "   ", "not a link", "https://bandcamp.com/x"):
        assert gv.extract_spotify_track_id(value) is None


def test_collect_keeps_sheet_order_and_reports_gaps():
    rows = [
        {"row": 2, "track": "A", "artist": "X",
         "spotify": f"https://open.spotify.com/track/{TRACK}?si=1"},
        {"row": 3, "track": "B", "artist": "Y", "spotify": ""},
        {"row": 4, "track": "C", "artist": "Z", "spotify": "spotify:track:1AAAAAAAAAAAAAAAAAAAAA"},
    ]
    found = gv.collect_spotify_tracks(rows)
    assert [t["id"] for t in found["tracks"]] == [TRACK, "1AAAAAAAAAAAAAAAAAAAAA"]
    assert [m["row"] for m in found["missing"]] == [3]


def test_collect_drops_duplicates_but_keeps_the_first():
    rows = [
        {"row": 2, "track": "A", "artist": "X", "spotify": f"spotify:track:{TRACK}"},
        {"row": 3, "track": "A again", "artist": "X",
         "spotify": f"https://open.spotify.com/track/{TRACK}"},
    ]
    found = gv.collect_spotify_tracks(rows)
    assert [t["id"] for t in found["tracks"]] == [TRACK]
    assert found["duplicates"] == [3]


def test_collect_flags_a_link_that_is_not_a_track():
    rows = [{"row": 2, "track": "A", "artist": "X",
             "spotify": "https://open.spotify.com/album/7BaSHRP1rL08J1NeAnd6IS"}]
    found = gv.collect_spotify_tracks(rows)
    assert not found["tracks"]
    assert found["missing"][0]["row"] == 2
