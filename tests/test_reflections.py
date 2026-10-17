import json
from pathlib import Path

import pytest

from app.services.person_roles import is_reflection_candidate, reflection_overlap


def test_polygon_overlap_clips_both_axes():
    square = [(0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5)]
    assert reflection_overlap((0.25, 0.25, 0.75, 0.75), square) == pytest.approx(0.25)
    assert reflection_overlap((0.6, 0.6, 0.8, 0.8), square) == 0
    assert reflection_overlap((0, 0, 0, 0), square) == 0
    assert reflection_overlap((0.1, 0.1, 0.2, 0.2), square[::-1]) == pytest.approx(1)


def test_real_people_in_front_of_glass_are_kept():
    path = Path(__file__).resolve().parents[1] / "config/fortebank_camera.json"
    regions = json.loads(path.read_text(encoding="utf-8"))["reflection_regions"]
    # Manually reviewed detections from X-1 and X-14 at approximately 5 seconds.
    assert is_reflection_candidate((73, 236, 115, 295), 464, 848, regions)
    assert is_reflection_candidate((9, 150, 53, 260), 464, 848, regions)
    assert not is_reflection_candidate((20, 234, 124, 416), 464, 848, regions)
    assert not is_reflection_candidate((160, 257, 260, 407), 464, 848, regions)
    assert not is_reflection_candidate((172, 388, 286, 594), 464, 848, regions)
    assert not is_reflection_candidate((73, 236, 115, 295), 464, 848, [])
