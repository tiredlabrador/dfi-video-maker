"""
Tests for rendering a whole batch in one go.

This is what the tool is actually for: a batch is 8-10 tracks, rendered,
numbered in posting order, and downloaded together. The rules that matter are
that the numbering matches the order they were given in, and that one bad track
does not take the whole batch down with it.
"""
import io
import json
import threading
import zipfile

import pytest
from PIL import Image

import generate_video as gv
from app.batch import BatchItem, render_batch
from app.render_service import RenderService


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "tone.mp3"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=5", "-ac", "2", "-ar", "44100",
            str(path)], "test audio")
    return str(path)


@pytest.fixture
def artwork(tmp_path):
    path = tmp_path / "art.png"
    Image.new("RGB", (400, 400), (200, 50, 90)).save(path)
    return str(path)


@pytest.fixture
def service(tmp_path):
    return RenderService(work_dir=str(tmp_path / "work"))


def tiny_cfg():
    return gv.RenderConfig(canvas_w=216, canvas_h=270, circle_diameter=166,
                           hole_diameter=6, clip_length_seconds=2,
                           spin_period_seconds=1, fps=10, caption=False)


def items(audio, artwork, names):
    return [BatchItem(audio_path=audio, artwork_path=artwork,
                      clip_start="0:00", track=track, artist=artist)
            for track, artist in names]


def test_a_batch_renders_every_track(service, audio, artwork):
    batch = items(audio, artwork, [("One", "A"), ("Two", "B"), ("Three", "C")])
    results = render_batch(service, batch, tiny_cfg())
    assert len(results) == 3
    assert all(r.status == "done" for r in results)
    assert all(r.output_path for r in results)


def test_outputs_are_numbered_in_the_order_they_were_given(service, audio, artwork):
    """
    Numbering is posting order. If it does not match the order the batch was
    given in, the wrong video goes out on the wrong day.
    """
    batch = items(audio, artwork, [("Zebra", "A"), ("Apple", "B"), ("Mango", "C")])
    results = render_batch(service, batch, tiny_cfg())
    names = [r.filename for r in results]
    assert names[0].startswith("01")
    assert names[1].startswith("02")
    assert names[2].startswith("03")
    assert "Zebra" in names[0] and "Apple" in names[1] and "Mango" in names[2]


def test_numbering_is_zero_padded_so_files_sort_correctly(service, audio, artwork):
    """With 10+ tracks, '9' would sort after '10' without the padding."""
    batch = items(audio, artwork, [(f"Track {i}", "A") for i in range(1, 12)])
    results = render_batch(service, batch, tiny_cfg())
    names = [r.filename for r in results]
    assert names[0].startswith("01")
    assert names[8].startswith("09")
    assert names[9].startswith("10")
    assert sorted(names) == names


def test_one_bad_track_does_not_stop_the_others(service, audio, artwork):
    """
    A batch is 8-10 tracks and takes a minute. Losing all of it because track
    four had no artwork would be infuriating.
    """
    batch = items(audio, artwork, [("Good One", "A"), ("Good Two", "C")])
    broken = BatchItem(audio_path="/does/not/exist.mp3", artwork_path=None,
                       clip_start="0:00", track="Broken", artist="B")
    batch.insert(1, broken)

    results = render_batch(service, batch, tiny_cfg())
    assert [r.status for r in results] == ["done", "error", "done"]
    assert results[1].error
    assert "Traceback" not in results[1].error


def test_a_failed_track_still_holds_its_place_in_the_numbering(service, audio, artwork):
    """
    If track 2 fails, track 3 must still be 03. Renumbering would silently
    change the posting order of everything after the failure.
    """
    batch = items(audio, artwork, [("One", "A"), ("Three", "C")])
    batch.insert(1, BatchItem(audio_path="/nope.mp3", artwork_path=None,
                              clip_start="0:00", track="Two", artist="B"))
    results = render_batch(service, batch, tiny_cfg())
    assert results[0].filename.startswith("01")
    assert results[2].filename.startswith("03")


def test_progress_is_reported_across_the_whole_batch(service, audio, artwork):
    batch = items(audio, artwork, [("One", "A"), ("Two", "B"), ("Three", "C")])
    seen = []
    render_batch(service, batch, tiny_cfg(),
                 progress=lambda f, m="": seen.append((f, m)))
    fractions = [f for f, _ in seen]
    assert fractions == sorted(fractions)
    assert fractions[-1] == pytest.approx(1.0)
    # The message should say which track, so a long run is legible.
    assert any("One" in m for _, m in seen)
    assert any("Three" in m for _, m in seen)


def test_progress_never_jumps_backwards_between_tracks(service, audio, artwork):
    """Each track's internal 0-to-1 must be mapped into its own slice."""
    batch = items(audio, artwork, [("One", "A"), ("Two", "B")])
    fractions = []
    render_batch(service, batch, tiny_cfg(),
                 progress=lambda f, m="": fractions.append(f))
    assert fractions == sorted(fractions)
    assert all(0.0 <= f <= 1.0 for f in fractions)


def test_an_empty_batch_is_not_an_error(service):
    assert render_batch(service, [], tiny_cfg()) == []


def test_the_batch_can_be_zipped_for_download(service, audio, artwork):
    from app.batch import zip_results

    batch = items(audio, artwork, [("One", "A"), ("Two", "B")])
    results = render_batch(service, batch, tiny_cfg())
    blob = zip_results(results)

    archive = zipfile.ZipFile(io.BytesIO(blob))
    assert archive.testzip() is None
    names = archive.namelist()
    assert len(names) == 2
    assert all(n.endswith(".mp4") for n in names)
    assert archive.read(names[0])[4:8] == b"ftyp"


def test_the_zip_skips_tracks_that_failed(service, audio, artwork):
    from app.batch import zip_results

    batch = items(audio, artwork, [("Good", "A")])
    batch.append(BatchItem(audio_path="/nope.mp3", artwork_path=None,
                           clip_start="0:00", track="Bad", artist="B"))
    results = render_batch(service, batch, tiny_cfg())
    archive = zipfile.ZipFile(io.BytesIO(zip_results(results)))
    assert len(archive.namelist()) == 1
