"""
Tests for the pre-flight check — catch bad rows BEFORE downloading or rendering,
so nobody waits through a long batch only to hit an avoidable error.

Errors block the run. Warnings are shown but don't stop it.
"""
import generate_video as gv


def _entry(row=2, track="Blue Monday", artist="Datassette",
           audio_link="https://drive.google.com/file/d/1AbC2dEfGhIjKlMnOpQrSt/view",
           clip_start="1:13"):
    return {"row": row, "track": track, "artist": artist,
            "audio_link": audio_link, "clip_start": clip_start}


def _levels(report, row):
    return {p["level"] for p in report["rows"][row]["problems"]}


def _messages(report, row):
    return " ".join(p["message"].lower() for p in report["rows"][row]["problems"])


def test_a_good_row_is_clean():
    report = gv.preflight([_entry()])
    assert report["errors"] == 0
    assert not report["rows"][2]["problems"]
    assert report["ok"] is True


def test_missing_clip_start_is_a_warning_not_a_blocker():
    """Dom's ask: highlight it up front, but a blank start still renders from 0:00."""
    report = gv.preflight([_entry(clip_start="")])
    assert _levels(report, 2) == {"warning"}
    assert "clip start" in _messages(report, 2)
    assert report["errors"] == 0
    assert report["ok"] is True


def test_unparseable_clip_start_is_an_error():
    report = gv.preflight([_entry(clip_start="one minute thirteen")])
    assert "error" in _levels(report, 2)
    assert report["errors"] == 1
    assert report["ok"] is False


def test_blank_audio_link_is_a_warning():
    report = gv.preflight([_entry(audio_link="")])
    assert _levels(report, 2) == {"warning"}
    assert "audio" in _messages(report, 2)
    assert report["ok"] is True


def test_audio_link_that_is_not_a_link_is_an_error():
    report = gv.preflight([_entry(audio_link="my track.mp3")])
    assert "error" in _levels(report, 2)
    assert report["ok"] is False


def test_missing_track_and_artist_is_a_warning():
    report = gv.preflight([_entry(track="", artist="")])
    assert _levels(report, 2) == {"warning"}
    assert "name" in _messages(report, 2) or "track" in _messages(report, 2)


def test_duplicate_output_names_are_flagged():
    """Two rows producing the same filename would make two identical-looking files."""
    report = gv.preflight([_entry(row=2), _entry(row=3)])
    assert "duplicate" in _messages(report, 2) or "duplicate" in _messages(report, 3)


def test_counts_and_ok_flag_summarise_the_batch():
    report = gv.preflight([
        _entry(row=2),                                  # clean
        _entry(row=3, clip_start=""),                   # warning
        _entry(row=4, clip_start="banana", track="X"),  # error
    ])
    assert report["errors"] == 1
    assert report["warnings"] >= 1
    assert report["ok"] is False


def test_bare_drive_id_is_accepted():
    report = gv.preflight([_entry(audio_link="1AbC2dEfGhIjKlMnOpQrStUv")])
    assert report["errors"] == 0


def test_plain_https_url_is_accepted():
    report = gv.preflight([_entry(audio_link="https://example.com/track.mp3")])
    assert report["errors"] == 0
