"""Switching each graphic on or off: the logo, the tape, the name."""
import numpy as np
import pytest

from app.divein.compose import Scene
from app.divein.config import merge_config


def overlay_alpha(cfg, box):
    x0, y0, x1, y1 = box
    scene = Scene(cfg, None, "Artist Name", "02.01")
    return np.asarray(scene.layers())[y0:y1, x0:x1, 3].astype(int)


LOGO = (60, 75, 200, 120)
NAME = (49, 1150, 600, 1290)
TAPE = (400, 730, 680, 780)


@pytest.mark.parametrize("piece, box", [("logo", LOGO), ("name", NAME), ("tape", TAPE)])
def test_each_graphic_can_be_switched_off(piece, box):
    on = overlay_alpha(merge_config({}), box)
    off = overlay_alpha(merge_config({piece: {"enabled": False}}), box)
    assert on.max() > 200
    assert off.max() < on.max() - 100


def test_the_switches_belong_to_the_page_not_tuning():
    import tempfile
    from app.divein.api import BadRequest, DiveInService
    from app.jobs import JobStore
    svc = DiveInService(tempfile.mkdtemp(), JobStore())
    for piece in ("logo", "tape", "name"):
        with pytest.raises(BadRequest):
            svc._config({"overrides": {piece: {"enabled": False}}})
    cfg = svc._config({"graphics": {"logo": False, "tape": True, "name": False}})
    assert (cfg["logo"]["enabled"], cfg["tape"]["enabled"], cfg["name"]["enabled"]) == \
        (False, True, False)


def test_graphics_are_on_unless_switched_off():
    import tempfile
    from app.divein.api import DiveInService
    from app.jobs import JobStore
    cfg = DiveInService(tempfile.mkdtemp(), JobStore())._config({})
    assert cfg["logo"]["enabled"] and cfg["tape"]["enabled"] and cfg["name"]["enabled"]
