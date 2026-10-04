"""
Tests for the Dive In settings object.

Every number in the Dive In spec lives in one place so it can be tuned without
hunting through code. These tests guard the two things that make that safe:
overrides merge in without wiping their neighbours, and a typo in a setting
name is caught loudly instead of being silently ignored.
"""
import copy

import pytest

from app.divein.config import DEFAULTS, ConfigError, merge_config, seed_for, rng_for


def test_no_overrides_gives_the_defaults():
    assert merge_config({}) == DEFAULTS


def test_the_defaults_are_never_modified_by_a_merge():
    before = copy.deepcopy(DEFAULTS)
    merge_config({"ghost": {"opacity": 0.1}})
    assert DEFAULTS == before


def test_an_override_changes_only_the_value_it_names():
    cfg = merge_config({"ghost": {"opacity": 0.2}})
    assert cfg["ghost"]["opacity"] == 0.2
    assert cfg["ghost"]["offset_x"] == DEFAULTS["ghost"]["offset_x"]
    assert cfg["grain"] == DEFAULTS["grain"]


def test_deeply_nested_overrides_merge():
    cfg = merge_config({"glyph": {"rings": {"stroke": 6}}})
    assert cfg["glyph"]["rings"]["stroke"] == 6
    assert cfg["glyph"]["rings"]["rx"] == DEFAULTS["glyph"]["rings"]["rx"]


def test_a_misspelt_setting_is_an_error_not_a_silent_no_op():
    """'opactiy' would otherwise do nothing and you'd wonder why."""
    with pytest.raises(ConfigError) as caught:
        merge_config({"ghost": {"opactiy": 0.2}})
    assert "ghost.opactiy" in str(caught.value)


def test_a_misspelt_section_is_an_error():
    with pytest.raises(ConfigError):
        merge_config({"ghosst": {"opacity": 0.2}})


def test_a_wrong_type_is_an_error():
    with pytest.raises(ConfigError):
        merge_config({"ghost": {"opacity": "lots"}})


def test_whole_numbers_are_accepted_where_decimals_are_expected():
    assert merge_config({"ghost": {"opacity": 1}})["ghost"]["opacity"] == 1


def test_the_spec_values_are_the_defaults():
    """The numbers from the brief, so a later edit can't drift them by accident."""
    d = DEFAULTS
    assert d["canvas"]["width"] == 1080 and d["canvas"]["height"] == 1350
    assert d["photo"]["contrast"] == 1.35 and d["photo"]["brightness"] == 0.9
    assert (d["ghost"]["offset_x"], d["ghost"]["offset_y"]) == (22, -10)
    assert d["ghost"]["contrast"] == 1.6 and d["ghost"]["brightness"] == 1.3
    assert d["ghost"]["blur"] == 5 and d["ghost"]["opacity"] == 0.45
    assert d["grain"]["sd"] == 40 and d["grain"]["opacity"] == 0.9
    assert d["grain"]["every_frames"] == 2
    assert d["vignette"]["inner"] == 0.45 and d["vignette"]["edge_alpha"] == 0.65
    rings = d["glyph"]["rings"]
    assert (rings["rx"], rings["ry"], rings["stroke"]) == (58, 10, 4.5)
    assert rings["points"] == 110 and rings["count"] == 5
    assert rings["radial_sd"] == 0.55 and rings["vertical_sd"] == 0.35
    a = d["audio"]
    assert (a["gap_silence"], a["gap_low"], a["gap_rest"], a["gap_kick"]) == (2, 5, 9, 18)
    assert (a["kick_low_hz"], a["kick_high_hz"]) == (40, 120)
    assert d["colours"] == {"black": "#000000", "white": "#ffffff"}
    assert d["accent"] == "yellow"
    assert d["accents"] == {"yellow": "#fffe01", "red": "#ea2020",
                            "green": "#3dff00", "white": "#ffffff"}


# ── seeding ────────────────────────────────────────────────────────────
def test_the_same_episode_always_gives_the_same_seed():
    assert seed_for("02.01", "rings") == seed_for("02.01", "rings")


def test_different_episodes_give_different_seeds():
    assert seed_for("02.01", "rings") != seed_for("02.02", "rings")


def test_each_purpose_gets_its_own_stream():
    """Changing how much grain is drawn must not reshuffle the ring shapes."""
    assert seed_for("02.01", "rings") != seed_for("02.01", "grain")


def test_stray_spaces_around_the_episode_do_not_change_the_look():
    assert seed_for(" 02.01 ", "rings") == seed_for("02.01", "rings")


def test_rng_for_is_reproducible():
    a = rng_for("02.01", "grain").normal(size=5)
    b = rng_for("02.01", "grain").normal(size=5)
    assert (a == b).all()


# ── range checks (found in review: bad numbers crashed deep in the renderer) ──
def test_the_defaults_pass_their_own_checks():
    from app.divein.config import validate
    validate(merge_config({}))


@pytest.mark.parametrize("override, words", [
    ({"canvas": {"fps": 0}}, "canvas.fps"),
    ({"canvas": {"width": 0}}, "canvas.width"),
    ({"canvas": {"width": 1081}}, "even"),
    ({"glyph": {"rings": {"count": 0}}}, "glyph.rings.count"),
    ({"glyph": {"mode": "bars"}}, "rings or none"),
    ({"grain": {"pool": 1000}}, "grain.pool"),
    ({"ghost": {"opacity": 2}}, "ghost.opacity"),
    ({"accents": {"yellow": "yellow"}}, "accents.yellow"),
    ({"accent": "purple"}, "accent"),
    ({"glyph": {"rings": {"anchor": "sideways"}}}, "anchor"),
    ({"tape": {"text": "   "}}, "tape.text"),
    ({"audio": {"gap_low": 20}}, "gap"),
    ({"twitch": {"min_px": 9, "max_px": 3}}, "twitch"),
    ({"export": {"preset": "turbo"}}, "export.preset"),
])
def test_out_of_range_settings_are_refused_with_their_name(override, words):
    with pytest.raises(ConfigError) as caught:
        merge_config(override)
    assert words in str(caught.value)


def test_the_rings_follow_volume_by_default():
    """Dom: volume is the default; kick drums are the alternative."""
    assert merge_config({})["audio"]["drive"] == "level"
