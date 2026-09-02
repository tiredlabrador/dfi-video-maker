"""
Tests for the tag fallback.

Filenames like "03. Hallucinations.mp3" carry no artist, so they can't be matched
confidently on name alone. Those few files get downloaded, their ID3 tags read,
and are re-scored — turning a manual job into an automatic one.
"""
import generate_video as gv

ROWS = [
    {"row": 2, "track": "Hallucinations", "artist": "Glasgow Sound System", "has_audio": False},
    {"row": 3, "track": "Breakpoints", "artist": "No Moon", "has_audio": False},
    {"row": 4, "track": "Cafe Crazy", "artist": "Pascale Project", "has_audio": False},
]


def _file(name):
    return {"filename": name, "title": None, "artist": None}


def _find(results, name):
    return next(r for r in results if r["file"]["filename"] == name)


def test_tags_rescue_a_filename_with_no_artist():
    files = [_file("03. Hallucinations.mp3")]
    first = gv.match_files_to_rows(files, ROWS)
    assert _find(first, "03. Hallucinations.mp3")["decision"] != "auto"

    tags = {"03. Hallucinations.mp3": {"title": "Hallucinations",
                                       "artist": "Glasgow Sound System"}}
    refined = gv.refine_with_tags(first, ROWS, lambda f: tags.get(f["filename"]))
    got = _find(refined, "03. Hallucinations.mp3")
    assert got["decision"] == "auto", f"still {got['decision']} @ {got['score']:.2f}"
    assert got["row"] == 2


def test_junk_tags_do_not_force_a_match():
    files = [_file("track01.mp3")]
    first = gv.match_files_to_rows(files, ROWS)
    tags = {"track01.mp3": {"title": "Track 01", "artist": "Unknown Artist"}}
    refined = gv.refine_with_tags(first, ROWS, lambda f: tags.get(f["filename"]))
    assert _find(refined, "track01.mp3")["decision"] != "auto"


def test_missing_tags_leave_the_result_alone():
    files = [_file("03. Hallucinations.mp3")]
    first = gv.match_files_to_rows(files, ROWS)
    refined = gv.refine_with_tags(first, ROWS, lambda f: None)
    assert _find(refined, "03. Hallucinations.mp3")["decision"] == \
        _find(first, "03. Hallucinations.mp3")["decision"]


def test_tags_cannot_steal_a_row_already_claimed():
    """A confident filename match wins; tags must not overwrite it."""
    files = [_file("Pascale Project - Cafe Crazy.mp3"), _file("mystery.mp3")]
    first = gv.match_files_to_rows(files, ROWS)
    assert _find(first, "Pascale Project - Cafe Crazy.mp3")["row"] == 4

    tags = {"mystery.mp3": {"title": "Cafe Crazy", "artist": "Pascale Project"}}
    refined = gv.refine_with_tags(first, ROWS, lambda f: tags.get(f["filename"]))
    claimed = [r["row"] for r in refined if r["decision"] == "auto"]
    assert len(claimed) == len(set(claimed)), "row claimed twice"
    assert _find(refined, "Pascale Project - Cafe Crazy.mp3")["row"] == 4


def test_only_unresolved_files_need_downloading():
    """The caller must only be asked for tags for files that didn't auto-match."""
    files = [_file("Pascale Project - Cafe Crazy.mp3"), _file("03. Hallucinations.mp3")]
    first = gv.match_files_to_rows(files, ROWS)
    asked = []

    def read_tags(f):
        asked.append(f["filename"])
        return {"title": "Hallucinations", "artist": "Glasgow Sound System"}

    gv.refine_with_tags(first, ROWS, read_tags)
    assert asked == ["03. Hallucinations.mp3"], asked


def test_read_audio_tags_from_a_real_file():
    tags = gv.read_audio_tags("0 - Test/02 Datassette - Blue Monday (V4).mp3")
    assert tags["artist"] == "Datassette"
    assert "Blue Monday" in tags["title"]


def test_read_audio_tags_is_safe_on_a_file_with_none(tmp_path):
    silent = tmp_path / "bare.mp3"
    gv.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=200:duration=2", "-ac", "2", "-q:a", "9",
            str(silent)], "fixture")
    tags = gv.read_audio_tags(str(silent))
    assert tags == {"title": None, "artist": None} or tags is None
