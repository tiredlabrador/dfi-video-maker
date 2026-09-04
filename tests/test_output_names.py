"""
Output filenames should sort in sheet order, so uploading/posting follows the
running order rather than the alphabet.
"""
import generate_video as gv


def test_numbered_name_sorts_in_sheet_order():
    names = [gv.output_filename("Artist B", "Zebra", position=1, total=3),
             gv.output_filename("Artist A", "Apple", position=2, total=3),
             gv.output_filename("Artist C", "Middle", position=3, total=3)]
    assert names == sorted(names), "files would not sort into sheet order"
    assert names[0].startswith("01 ")


def test_padding_matches_the_batch_size():
    # Always at least two digits, so a batch that grows past 9 still sorts right.
    assert gv.output_filename("A", "B", position=7, total=9).startswith("07 ")
    assert gv.output_filename("A", "B", position=7, total=50).startswith("07 ")
    assert gv.output_filename("A", "B", position=7, total=120).startswith("007 ")


def test_unnumbered_when_no_position_given():
    assert gv.output_filename("Datassette", "Blue Monday") == "Datassette - Blue Monday.mp4"


def test_illegal_characters_are_still_stripped():
    name = gv.output_filename("AC/DC", 'Back? "In" Black', position=1, total=1)
    for bad in '<>:"/\\|?*':
        assert bad not in name


def test_blank_artist_and_track_still_produce_a_usable_name():
    name = gv.output_filename("", "", position=2, total=5)
    assert name.endswith(".mp4") and len(name) > 4
