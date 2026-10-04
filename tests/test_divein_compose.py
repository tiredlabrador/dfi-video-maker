"""
Tests for the Dive In picture: the photo treatment and every layer above it.

The layout numbers asserted here were measured from R1 (the target design), so
a test failing means the output has drifted from the design, not just that
some code changed.
"""
import numpy as np
import pytest
from PIL import Image

from app.divein.compose import Scene, crop_window, layout_name, load_photo
from app.divein.config import merge_config


@pytest.fixture
def cfg():
    return merge_config({})


@pytest.fixture
def colourful_photo(tmp_path):
    """A loud, saturated photo, so any colour leaking through is obvious."""
    h, w = 900, 1400
    y, x = np.mgrid[0:h, 0:w]
    arr = np.stack([(x * 255 // w), (y * 255 // h), ((x + y) * 255 // (w + h))],
                   axis=2).astype(np.uint8)
    path = tmp_path / "photo.jpg"
    Image.fromarray(arr).save(path, quality=95)
    return str(path)


@pytest.fixture
def black_photo(tmp_path):
    path = tmp_path / "black.png"
    Image.new("RGB", (1080, 1350), (0, 0, 0)).save(path)
    return str(path)


@pytest.fixture
def grey_photo(tmp_path):
    path = tmp_path / "grey.png"
    Image.new("RGB", (1080, 1350), (128, 128, 128)).save(path)
    return str(path)


def scene(cfg, photo, **kw):
    args = dict(artist="Artist\nName", episode="02.01", crop=None, fmt="portrait")
    args.update(kw)
    return Scene(cfg, load_photo(photo), **args)


# ── size and colour ─────────────────────────────────────────────────────
def test_the_still_is_1080_by_1350(cfg, colourful_photo):
    img = scene(cfg, colourful_photo).frame(0)
    assert img.size == (1080, 1350) and img.mode == "RGB"


def test_the_square_version_is_1080_by_1080(cfg, colourful_photo):
    img = scene(cfg, colourful_photo, fmt="square").frame(0)
    assert img.size == (1080, 1080)


@pytest.mark.parametrize("accent", ["yellow", "red", "green", "white"])
def test_no_colour_other_than_black_white_and_the_accent_appears(cfg, colourful_photo,
                                                                 accent):
    """
    Every pixel is a grey (photo, logo, name), the accent, or a blend of the
    two (tape texture, glow). A blend of grey and the accent only ever leans
    in the accent's direction, so each pixel's tint must point the same way
    as the accent's. Anything pointing elsewhere is a colour that shouldn't be
    there.
    """
    from app.divein.glyph import hex_to_rgb
    cfg["accent"] = accent
    arr = np.asarray(scene(cfg, colourful_photo).frame(0)).astype(int).reshape(-1, 3)
    assert stray_colour(arr, hex_to_rgb(cfg["accents"][accent])) == 0


def stray_colour(pixels, accent):
    """How many pixels are tinted in any direction other than the accent's."""
    tint = np.stack([pixels[:, 0] - pixels[:, 2], pixels[:, 1] - pixels[:, 2]], 1)
    ref = np.array([accent[0] - accent[2], accent[1] - accent[2]])
    if not ref.any():                               # white: everything must be grey
        return int((np.abs(pixels - pixels[:, :1]).max(axis=1) > 2).sum())
    sideways = np.abs(tint[:, 0] * ref[1] - tint[:, 1] * ref[0])
    backwards = tint @ ref < -3 * np.abs(ref).sum()  # e.g. blue against yellow
    allowed = 3 * (np.abs(ref).sum() + 1)           # rounding of 8-bit blends
    return int(((sideways > allowed) | backwards).sum())


@pytest.mark.parametrize("accent, intruder", [((255, 254, 1), (0, 0, 255)),
                                              ((255, 254, 1), (255, 0, 0)),
                                              ((234, 32, 32), (61, 255, 0)),
                                              ((61, 255, 0), (234, 32, 32)),
                                              ((255, 255, 255), (255, 254, 1))])
def test_the_colour_check_itself_catches_a_stray_colour(accent, intruder):
    a = np.array(accent)
    good = np.array([[120, 120, 120], (0.5 * a + 60).astype(int), a])
    assert stray_colour(good, accent) == 0
    assert stray_colour(np.vstack([good, [intruder]]), accent) == 1


def test_the_photo_is_greyscale(cfg, colourful_photo):
    arr = np.asarray(scene(cfg, colourful_photo).frame(0)).astype(int)
    patch = arr[300:500, 300:700]                       # clear of all overlays
    assert (patch[..., 0] == patch[..., 1]).all() and (patch[..., 1] == patch[..., 2]).all()


def test_the_glyph_colour_is_the_brand_yellow(cfg, black_photo):
    arr = np.asarray(scene(cfg, black_photo).frame(0)).astype(int)
    coil = arr[60:150, 890:1040].reshape(-1, 3)
    strongest = coil[coil[:, 0].argmax()]
    assert tuple(strongest) == (255, 254, 1)


def test_the_tape_takes_the_chosen_colour(cfg, black_photo):
    cfg["accent"] = "red"
    cfg["tape"]["texture_mix"] = 0.0
    arr = np.asarray(scene(cfg, black_photo).frame(0)).astype(int)
    assert tuple(arr[int(775 - 300 * 171 / 1079) + 10, 300]) == (234, 32, 32)


# ── layout against R1 ───────────────────────────────────────────────────
def test_the_tape_runs_where_r1_has_it(cfg, black_photo):
    """R1: top edge at y 775 on the left and 604 on the right; 130px deep."""
    arr = np.asarray(scene(cfg, black_photo).frame(0)).astype(int)
    tape = (arr[..., 0] > 120) & (arr[..., 2] < arr[..., 0] - 60)   # yellowish
    for x, top in ((0, 775), (540, 689), (1079, 604)):
        rows = np.nonzero(tape[:, x])[0]
        rows = rows[(rows > top - 40) & (rows < top + 170)]
        assert abs(rows.min() - top) <= 3, (x, rows.min())
        assert abs((rows.max() - rows.min()) - 130) <= 4


def test_the_tape_reads_with_the_episode_number(cfg):
    from app.divein.compose import tape_text
    text = tape_text(cfg, "02.01")
    assert "DON'T FALL IN • DIVE IN SERIES • 02.01 • " in text


def test_the_tape_is_textured_like_r1_by_default(cfg, black_photo):
    """R1's tape averages about (217, 216, 62) with visible grain."""
    arr = np.asarray(scene(cfg, black_photo).frame(0)).astype(float)
    strip = np.array([arr[int(775 - x * 171 / 1079) + 10, x] for x in range(0, 1080, 3)])
    mean = strip.mean(0)
    assert abs(mean[0] - 217) < 12 and abs(mean[2] - 62) < 15
    assert strip[:, 0].std() > 3


def test_the_tape_can_be_flat_brand_yellow(cfg, black_photo):
    cfg["tape"]["texture_mix"] = 0.0
    arr = np.asarray(scene(cfg, black_photo).frame(0)).astype(int)
    assert tuple(arr[int(775 - 300 * 171 / 1079) + 10, 300]) == (255, 254, 1)


def test_the_logo_matches_the_dig(cfg, black_photo):
    """Dom: same size and place as The Dig's logo (ink x 49-218, y 66-141)."""
    arr = np.asarray(scene(cfg, black_photo).frame(0)).astype(int)
    white = arr[:200, :400].min(axis=2) > 200
    ys, xs = np.nonzero(white)
    assert abs(xs.min() - 49) <= 1 and abs(xs.max() - 218) <= 1
    assert abs(ys.min() - 66) <= 1 and abs(ys.max() - 141) <= 1


def test_the_name_lines_up_with_the_logo(cfg, black_photo):
    """The name's left edge sits on the same 49px margin as the logo."""
    arr = np.asarray(scene(cfg, black_photo).frame(0)).astype(int)
    white = arr[950:1350, :700].min(axis=2) > 200
    cols = np.nonzero(white.any(axis=0))[0]
    assert abs(cols.min() - 49) <= 1


def test_the_name_sits_where_r1_has_it(cfg, black_photo):
    """Measured from R1: two lines, cap tops near y 1038 and 1170."""
    arr = np.asarray(scene(cfg, black_photo).frame(0)).astype(int)
    white = arr[950:1350, :700].min(axis=2) > 200
    rows = np.nonzero(white.any(axis=1))[0] + 950
    assert abs(rows.min() - 1035) <= 6
    assert abs(rows.max() - 1278) <= 4


# ── the name ────────────────────────────────────────────────────────────
def test_names_are_uppercase(cfg):
    lines, _ = layout_name("artist name", cfg)
    assert all(line == line.upper() for line in lines)


def test_a_manual_line_break_is_respected(cfg):
    lines, size = layout_name("Artist\nName", cfg)
    assert lines == ["ARTIST", "NAME"] and size == 152


def test_a_short_name_stays_on_one_line(cfg):
    assert layout_name("Ezra", cfg)[0] == ["EZRA"]


def test_a_long_name_wraps_onto_two_balanced_lines(cfg):
    """Splits at the space that makes the longer line as short as possible."""
    from PIL import ImageFont
    from app.divein.compose import font_path
    lines, size = layout_name("Crazy P Soundsystem Live", cfg)
    assert len(lines) == 2 and all(lines)
    assert " ".join(lines) == "CRAZY P SOUNDSYSTEM LIVE"
    font = ImageFont.truetype(font_path(cfg["name"]["font"]), size)
    words = "CRAZY P SOUNDSYSTEM LIVE".split()
    best = min(max(font.getlength(" ".join(words[:i])), font.getlength(" ".join(words[i:])))
               for i in range(1, len(words)))
    assert max(font.getlength(l) for l in lines) == pytest.approx(best)


def test_a_name_too_long_for_two_lines_shrinks_rather_than_overflowing(cfg):
    from PIL import ImageFont
    from app.divein.compose import font_path
    for name in ("Supercalifragilisticexpialidocious",
                 "Kerri Chandler & Jerome Sydenham presents Ibadan"):
        lines, size = layout_name(name, cfg)
        assert size < 152
        font = ImageFont.truetype(font_path(cfg["name"]["font"]), size)
        assert all(font.getlength(l) <= cfg["name"]["max_width"] for l in lines), name


def test_a_long_name_never_runs_off_the_canvas(cfg, black_photo):
    arr = np.asarray(scene(cfg, black_photo, artist="Supercalifragilisticexpialidocious")
                     .frame(0)).astype(int)
    white = arr[950:1350].min(axis=2) > 200
    cols = np.nonzero(white.any(axis=0))[0]
    assert cols.max() <= 1080 - 40


def test_letters_the_font_lacks_are_reported(cfg):
    """
    Squid Boy V4 covers Western European letters and punctuation, but not
    Cyrillic, emoji, or most Central/Eastern European letters such as Ł.
    Those would silently vanish from the picture, so they're reported.
    """
    from app.divein.compose import missing_glyphs
    assert missing_glyphs("Ezra Müller’s – Mix & Ñoño", cfg) == []
    assert set(missing_glyphs("Жора 😀 Łukasz", cfg)) >= {"Ж", "😀", "Ł"}


def test_more_than_two_manual_lines_are_folded_into_two(cfg):
    lines, _ = layout_name("A\nB\nC", cfg)
    assert lines == ["A", "B C"]


def test_a_one_line_name_sits_on_the_bottom_line(cfg, black_photo):
    arr = np.asarray(scene(cfg, black_photo, artist="Ezra").frame(0)).astype(int)
    white = arr[950:1350, :700].min(axis=2) > 200
    rows = np.nonzero(white.any(axis=1))[0] + 950
    assert abs(rows.max() - 1278) <= 4 and rows.min() > 1150


# ── grain and determinism ───────────────────────────────────────────────
def test_the_same_inputs_give_identical_pictures(cfg, colourful_photo):
    a = np.asarray(scene(cfg, colourful_photo).frame(0))
    b = np.asarray(scene(cfg, colourful_photo).frame(0))
    assert np.array_equal(a, b)


def test_a_different_episode_gives_different_grain(cfg, grey_photo):
    a = np.asarray(scene(cfg, grey_photo, episode="02.01").frame(0))[300:500, 300:500]
    b = np.asarray(scene(cfg, grey_photo, episode="02.02").frame(0))[300:500, 300:500]
    assert not np.array_equal(a, b)


def test_grain_changes_every_second_frame(cfg, grey_photo):
    s = scene(cfg, grey_photo)
    f0, f1, f2 = (np.asarray(s.frame(i))[300:500, 300:500] for i in (0, 1, 2))
    assert np.array_equal(f0, f1)
    assert not np.array_equal(f0, f2)


@pytest.mark.parametrize("episode", ["02.01", "03.10", "01.01"])
def test_grain_never_holds_for_more_than_two_frames(cfg, grey_photo, episode):
    """Checked over two minutes of frames, not just the first few."""
    s = scene(cfg, grey_photo, episode=episode)
    picks = [s.grain_index(f) for f in range(0, 3600, 2)]
    assert all(a != b for a, b in zip(picks, picks[1:]))


def test_frames_are_identical_however_the_cache_was_warmed(cfg, colourful_photo):
    """
    The export must not depend on what the live preview happened to draw first.
    A scene warmed out of order (including twitch frames) must match a fresh one.
    """
    a = fake_analysis()
    warm = scene(cfg, colourful_photo)
    for f in (60, 30, 31, 2, 88, 45):
        warm.frame(f, a)
    fresh = scene(cfg, colourful_photo)
    for f in (0, 30, 31, 45, 60, 88):
        assert np.array_equal(np.asarray(warm.frame(f, a)), np.asarray(fresh.frame(f, a))), f


def test_grain_is_film_like_not_flat(cfg, grey_photo):
    patch = np.asarray(scene(cfg, grey_photo).frame(0))[300:500, 300:500, 0].astype(float)
    assert patch.std() > 4


def test_grain_can_be_switched_off(cfg, grey_photo):
    cfg["grain"]["enabled"] = False
    patch = np.asarray(scene(cfg, grey_photo).frame(0))[300:340, 520:560, 0].astype(float)
    assert patch.std() < 1.0


def test_the_vignette_darkens_the_corners(cfg, grey_photo):
    cfg["grain"]["enabled"] = False
    arr = np.asarray(scene(cfg, grey_photo).frame(0)).astype(float)
    assert arr[1300:1340, 1000:1070].mean() < arr[480:520, 520:560].mean() - 30


# ── crop ────────────────────────────────────────────────────────────────
def test_a_centred_cover_crop_uses_the_middle_of_the_photo():
    x0, y0, w, h = crop_window(2000, 1000, 1080, 1350, {"zoom": 1, "cx": 0.5, "cy": 0.5})
    assert h == pytest.approx(1000) and w == pytest.approx(800)
    assert x0 == pytest.approx(600) and y0 == pytest.approx(0)


def test_a_crop_cannot_run_off_the_edge_of_the_photo():
    x0, _, w, _ = crop_window(2000, 1000, 1080, 1350, {"zoom": 1, "cx": 0.0, "cy": 0.5})
    assert x0 == 0
    x0, _, w, _ = crop_window(2000, 1000, 1080, 1350, {"zoom": 1, "cx": 1.0, "cy": 0.5})
    assert x0 + w == pytest.approx(2000)


def test_zooming_in_shows_less_of_the_photo():
    _, _, w1, _ = crop_window(2000, 1000, 1080, 1350, {"zoom": 1, "cx": 0.5, "cy": 0.5})
    _, _, w2, _ = crop_window(2000, 1000, 1080, 1350, {"zoom": 2, "cx": 0.5, "cy": 0.5})
    assert w2 == pytest.approx(w1 / 2)


def test_a_phone_photo_is_turned_the_right_way_up(tmp_path):
    """Phones store photos sideways plus a note saying 'rotate me'."""
    path = tmp_path / "sideways.jpg"
    img = Image.new("RGB", (400, 300), (90, 90, 90))
    exif = Image.Exif()
    exif[0x0112] = 6                                  # "rotate 90 clockwise"
    img.save(path, exif=exif.tobytes())
    assert load_photo(str(path)).size == (300, 400)


# ── reactive bits and debug ─────────────────────────────────────────────
def fake_analysis(n=90, kick_at=30):
    gap = np.full(n, 9.0); gap[kick_at:kick_at + 3] = 18.0
    tx = np.zeros(n); ty = np.zeros(n); tx[kick_at] = 5.0
    return {"gap": gap, "glow": (gap - 9) / 9, "kick": gap == 18.0,
            "level": np.full(n, 0.5), "rms_db": np.full(n, -12.0),
            "twitch_x": tx, "twitch_y": ty, "bands": np.full((n, 4), 0.5),
            "mono": np.zeros(n * 1470, dtype=np.float32), "sample_rate": 44100,
            "fps": 30}


def test_the_coil_opens_on_a_kick_frame(cfg, black_photo):
    s = scene(cfg, black_photo)
    a = fake_analysis()
    calm = np.asarray(s.frame(28, a))[:220, 880:]
    kick = np.asarray(s.frame(30, a))[:220, 880:]
    rows = lambda arr: np.nonzero((arr[..., 0] > 200) & (arr[..., 2] < 60))[0]
    # Gap 9 -> 18 opens the coil from its middle: 18px up and 18px down.
    assert abs((rows(calm).min() - rows(kick).min()) - 18) <= 2
    assert abs((rows(kick).max() - rows(calm).max()) - 18) <= 2


def test_the_ghost_twitches_on_a_kick(cfg, colourful_photo):
    s = scene(cfg, colourful_photo)
    a = fake_analysis()
    a["gap"][:] = 9.0                                    # isolate the twitch
    plain = np.asarray(s.frame(30, dict(a, twitch_x=np.zeros(90))))[300:500, 300:700]
    nudged = np.asarray(s.frame(30, a))[300:500, 300:700]
    assert not np.array_equal(plain, nudged)


def test_the_debug_overlay_shows_up_only_when_asked(cfg, black_photo):
    s = scene(cfg, black_photo)
    a = fake_analysis()
    off = np.asarray(s.frame(30, a))
    on = np.asarray(s.frame(30, a, debug=True))
    assert not np.array_equal(off, on)
    assert np.array_equal(off, np.asarray(s.frame(30, a, debug=False)))


def test_a_16_bit_photo_is_not_turned_white(tmp_path):
    path = tmp_path / "deep.png"
    Image.fromarray(np.full((400, 300), 32768, dtype=np.uint16)).save(path)
    grey = np.asarray(load_photo(str(path)))
    assert 110 <= grey.mean() <= 145


# ── the square JPG uses the hole motif ──────────────────────────────────
@pytest.mark.parametrize("mode", ["rings", "bars", "line", "none"])
def test_the_square_jpg_shows_the_hole_motif_whatever_the_glyph(cfg, black_photo, mode):
    """Dom: the still JPG uses the hole motif, never the waveform or coil."""
    cfg["glyph"]["mode"] = mode
    img = np.asarray(scene(cfg, black_photo, fmt="square").frame(0)).astype(int)
    cfg2 = merge_config({})
    ref = np.asarray(scene(cfg2, black_photo, fmt="square").frame(0)).astype(int)
    corner = (slice(30, 180), slice(880, 1060))
    assert np.array_equal(img[corner], ref[corner])
    # ...and it isn't the coil: the 4:5 at-rest corner looks different.
    port = np.asarray(scene(cfg2, black_photo).frame(0)).astype(int)
    assert not np.array_equal(img[corner], port[corner])


def test_the_hole_motif_sits_where_the_coil_does_and_is_brand_coloured(cfg, black_photo):
    img = np.asarray(scene(cfg, black_photo, fmt="square").frame(0)).astype(int)
    strong = (img[..., 0] > 240) & (img[..., 1] > 240) & (img[..., 2] < 20)
    ys, xs = np.nonzero(strong[:250, 700:])
    xs = xs + 700
    assert abs(xs.max() - (1080 - 49)) <= 3
    assert abs((ys.min() + ys.max()) / 2 - 103.5) <= 3


def test_the_hole_motif_follows_the_colour_choice(cfg, black_photo):
    cfg["accent"] = "red"
    img = np.asarray(scene(cfg, black_photo, fmt="square").frame(0)).astype(int)
    red = (img[..., 0] > 225) & (np.abs(img[..., 1] - 32) < 6) & (np.abs(img[..., 2] - 32) < 6)
    assert red[:250, 850:].sum() > 500


# ── layers for the fast drag preview ────────────────────────────────────
def test_the_layers_above_the_photo_can_be_drawn_without_a_photo(cfg):
    """
    The page draws a quick draft while you drag: your photo, plus everything
    above it from this image. Where the photo shows through it's transparent
    (apart from the vignette's darkening).
    """
    layers = Scene(cfg, None, "Artist\nName", "02.01").layers()
    assert layers.mode == "RGBA" and layers.size == (1080, 1350)
    a = np.asarray(layers)
    assert a[400, 540, 3] < 10                 # middle: photo shows through
    assert a[1340, 1070, 3] > 100              # corner: vignette darkens
    assert a[754, 540, 3] == 255               # the tape is solid
    assert a[66:142, 49:219, 3].max() == 255   # the logo
