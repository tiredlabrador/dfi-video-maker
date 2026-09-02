"""
Tests for matching dropped audio files to sheet rows.

Real-world messiness this must survive (all taken from actual files):
  "02 Datassette - Blue Monday (V4).mp3"                     leading track number
  "Aman Umber - All The Things [TELU01].mp3"                 catalogue code
  "Funk Soul - Veritas (Uk) (Original Mix) 133.mp3"          mix name + BPM
  "Grooveworks - M1 Repair Technicians - 6A - 126.0.mp3"     key + BPM
  "Mary J. Blige_Mary_15_Let No Man Put Asunder copy.mp3"    underscores + "copy"
  "Funkyjaws, Los Protos - B1. Funkyjaws & ... .mp3"         side marker + repeat

Tags are clean ~80-90% of the time; bootlegs often have none. So a file must be
matchable from EITHER its tags or its filename.
"""
import generate_video as gv


ROWS = [
    {"row": 2, "track": "Blue Monday", "artist": "Datassette", "has_audio": False},
    {"row": 3, "track": "All The Things", "artist": "Aman Umber", "has_audio": False},
    {"row": 4, "track": "Let No Man Put Asunder", "artist": "Mary J. Blige", "has_audio": False},
    {"row": 5, "track": "Veritas", "artist": "Funk Soul", "has_audio": False},
    {"row": 6, "track": "Can't Touch This", "artist": "Funkyjaws, Los Protos", "has_audio": False},
]


def _file(filename, title=None, artist=None):
    return {"filename": filename, "title": title, "artist": artist}


def _by_filename(results, filename):
    return next(r for r in results if r["file"]["filename"] == filename)


def test_clean_tags_match_confidently():
    files = [_file("02 Datassette - Blue Monday (V4).mp3", "Blue Monday (V4)", "Datassette")]
    got = _by_filename(gv.match_files_to_rows(files, ROWS), files[0]["filename"])
    assert got["decision"] == "auto"
    assert got["row"] == 2


def test_messy_filename_matches_even_with_no_tags():
    """Bootlegs often have no tags at all — the filename must carry it."""
    name = "Mary J. Blige_Mary_15_Let No Man Put Asunder copy.mp3"
    got = _by_filename(gv.match_files_to_rows([_file(name)], ROWS), name)
    assert got["decision"] == "auto", f"scored only {got['score']:.2f}"
    assert got["row"] == 4


def test_catalogue_codes_and_bpm_are_ignored():
    for name, expect_row in [
        ("Aman Umber - All The Things [TELU01].mp3", 3),
        ("Funk Soul - Veritas (Uk) (Original Mix) 133.mp3", 5),
    ]:
        got = _by_filename(gv.match_files_to_rows([_file(name)], ROWS), name)
        assert got["row"] == expect_row and got["decision"] == "auto", \
            f"{name} -> row {got['row']} @ {got['score']:.2f}"


def test_repeated_artist_in_filename_still_matches():
    name = "Funkyjaws, Los Protos - B1. Funkyjaws & Los Protos - Can't Touch This.mp3"
    got = _by_filename(gv.match_files_to_rows([_file(name)], ROWS), name)
    assert got["row"] == 6 and got["decision"] == "auto"


def test_unknown_file_is_not_forced_onto_a_row():
    name = "some_random_rip_2019.mp3"
    got = _by_filename(gv.match_files_to_rows([_file(name)], ROWS), name)
    assert got["decision"] == "none"
    assert got["row"] is None


def test_rows_that_already_have_audio_are_protected():
    """Never overwrite an existing link, however good the match."""
    rows = [dict(ROWS[0], has_audio=True)]
    files = [_file("02 Datassette - Blue Monday (V4).mp3", "Blue Monday", "Datassette")]
    got = _by_filename(gv.match_files_to_rows(files, rows), files[0]["filename"])
    assert got["decision"] == "none"
    assert got["row"] is None


def test_one_row_is_not_claimed_by_two_files():
    files = [
        _file("02 Datassette - Blue Monday (V4).mp3", "Blue Monday", "Datassette"),
        _file("Datassette - Blue Monday (alt rip).mp3", "Blue Monday", "Datassette"),
    ]
    results = gv.match_files_to_rows(files, ROWS)
    claimed = [r["row"] for r in results if r["row"] is not None]
    assert len(claimed) == len(set(claimed)), "same row claimed twice"


def test_borderline_match_is_flagged_for_review_not_auto_linked():
    """A partial match must ask a human rather than guess."""
    name = "Veritas.mp3"                      # track only, no artist
    got = _by_filename(gv.match_files_to_rows([_file(name)], ROWS), name)
    assert got["decision"] in ("review", "none")
    assert got["decision"] != "auto"


def test_ingest_report_groups_by_decision():
    files = [
        _file("02 Datassette - Blue Monday (V4).mp3"),
        _file("Veritas.mp3"),
        _file("some_random_rip_2019.mp3"),
    ]
    text = gv.format_ingest(gv.match_files_to_rows(files, ROWS), ROWS)
    assert "WILL LINK" in text
    assert "NEEDS A HUMAN" in text or "NO MATCH" in text
    assert "Blue Monday" in text


def test_ingest_report_handles_nothing_to_do():
    text = gv.format_ingest([], ROWS)
    assert "nothing" in text.lower() or "no files" in text.lower()
