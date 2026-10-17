import base64
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from app.services.detector import (
    classify_persons,
    create_evidence_frame,
    detect_id_card_on_desk,
    extract_evidence_frame_base64,
    is_reflection,
)


def test_reflection_filtering():
    # The original false ENTERING detection from annotated_10s.jpg is suppressed.
    reflection_box = [1.1, 221.7, 56.3, 291.6]
    assert is_reflection(reflection_box) is True

    # Real person box in doorway or at desk is not a reflection
    real_client_box = [130.0, 300.0, 210.0, 420.0]
    assert is_reflection(real_client_box) is False


def test_classify_persons_geometry():
    # Cashier in chair (cy > 450, cx > 120)
    cashier_box = [160.0, 400.0, 260.0, 600.0]
    # Client at desk (cy <= 450, cx > 100), outside doorway.
    client_box = [165.0, 300.0, 235.0, 420.0]
    # Entering person (cx in [70, 160], cy in [150, 420]).
    entering_box = [85.0, 200.0, 155.0, 380.0]
    # Glass reflection (x2 < 70).
    reflection_box = [5.0, 200.0, 50.0, 280.0]

    boxes = [cashier_box, client_box, entering_box, reflection_box]
    classified = classify_persons(boxes)

    assert len(classified["cashier"]) == 1
    assert classified["cashier"][0].role == "cashier"

    assert len(classified["client"]) == 1
    assert classified["client"][0].role == "client"

    assert len(classified["entering"]) == 1
    assert classified["entering"][0].role == "entering"

    # Reflection was cleanly suppressed
    total_classified = (
        len(classified["cashier"]) + len(classified["client"]) + len(classified["entering"])
    )
    assert total_classified == 3


@pytest.mark.parametrize(
    ("box", "expected"),
    [
        ([100, 200, 164.9, 400], True),  # Thin but tall: either size threshold rejects.
        ([100, 200, 200, 319.9], True),  # Wide but short.
        ([100, 200, 165, 320], False),  # Both exact size boundaries are accepted.
        ([0, 200, 69.9, 400], True),  # Entirely in glass despite sufficient size.
        ([0, 200, 70, 400], False),  # x2 boundary is inclusive for real candidates.
        ([0, 200, 130, 400], False),  # Center in glass, right edge outside glass.
    ],
)
def test_reflection_geometry_boundaries(box, expected):
    assert is_reflection(box) is expected


def centered_box(cx, cy):
    return [cx - 35, cy - 70, cx + 35, cy + 70]


@pytest.mark.parametrize(
    ("cx", "cy", "expected_role"),
    [
        (70, 150, "entering"),
        (160, 420, "entering"),
        (160.1, 420, "client"),
        (120, 420.1, "client"),
        (200, 450, "client"),
        (200, 450.1, "cashier"),
        (69.9, 300, None),
        (200, 149.9, None),
        (100, 430, None),
        (120, 500, None),
        (500, 300, None),
        (200, 900, None),
    ],
)
def test_only_relevant_zone_centers_are_classified(cx, cy, expected_role):
    classified = classify_persons([centered_box(cx, cy)])
    assert sum(map(len, classified.values())) == int(expected_role is not None)
    if expected_role:
        assert len(classified[expected_role]) == 1


@pytest.mark.parametrize("scale", [(0.5, 0.5), (2, 2), (2, 0.5)])
def test_classification_and_reflection_scale_with_frame(scale):
    sx, sy = scale
    reference_boxes = [
        centered_box(200, 500),
        centered_box(200, 350),
        centered_box(120, 300),
        [1.1, 221.7, 56.3, 291.6],
    ]
    boxes = [[x1 * sx, y1 * sy, x2 * sx, y2 * sy] for x1, y1, x2, y2 in reference_boxes]
    frame_size = (int(464 * sx), int(848 * sy))
    classified = classify_persons(boxes, [0.8, 0.9, 0.7, 0.73], frame_size)

    for index, role in enumerate(["cashier", "client", "entering"]):
        assert len(classified[role]) == 1
        person = classified[role][0]
        assert person.box == boxes[index]
        assert person.center == pytest.approx(
            ((boxes[index][0] + boxes[index][2]) / 2, (boxes[index][1] + boxes[index][3]) / 2)
        )
        assert person.confidence == [0.8, 0.9, 0.7][index]

    assert is_reflection(boxes[-1], frame_size)
    assert not is_reflection(boxes[1], frame_size)


@pytest.mark.parametrize(
    "box",
    [
        [100, 200, 100, 400],
        [200, 200, 100, 400],
        [100, 400, 200, 200],
        [100, 200, 200, 200],
        [100, 200, float("nan"), 400],
        [100, 200, 200, float("inf")],
        [100, 200, 200],
        [100, 200, 200, 400, 500],
        ["invalid", 200, 200, 400],
    ],
)
def test_invalid_boxes_raise_instead_of_becoming_clients(box):
    with pytest.raises(ValueError):
        is_reflection(box)
    with pytest.raises(ValueError):
        classify_persons([box])


@pytest.mark.parametrize("frame_size", [(0, 848), (464, -1), (float("nan"), 848), (464,)])
def test_invalid_frame_dimensions_raise(frame_size):
    with pytest.raises(ValueError, match="frame_size"):
        is_reflection([100, 200, 200, 400], frame_size)
    with pytest.raises(ValueError, match="frame_size"):
        classify_persons([], frame_size=frame_size)


@pytest.mark.parametrize("confidences", [[], [0.8, 0.9], [float("nan")], [-0.1], [1.1]])
def test_confidence_mismatch_and_invalid_values_raise(confidences):
    with pytest.raises(ValueError, match="confidence"):
        classify_persons([centered_box(200, 350)], confidences)


@pytest.mark.parametrize("scale", [0.5, 1, 2])
def test_document_candidate_roi_scales_with_frame(scale):
    frame = np.zeros((int(848 * scale), int(464 * scale), 3), dtype=np.uint8)
    # Card-shaped white patch inside the expanded transfer area, outside the old ROI.
    frame[round(410 * scale) : round(420 * scale), round(210 * scale) : round(228 * scale)] = 255
    assert detect_id_card_on_desk(frame)


def test_white_pixels_alone_do_not_establish_document_candidate():
    frame = np.zeros((848, 464, 3), dtype=np.uint8)
    frame[410:430, 210:230] = 255  # A square glare patch lacks card proportions.
    assert not detect_id_card_on_desk(frame)
    frame[400:480, 120:250] = 255  # A fully bright desk is not a card-sized object.
    assert not detect_id_card_on_desk(frame)


def test_document_outside_transfer_area_is_ignored():
    frame = np.zeros((848, 464, 3), dtype=np.uint8)
    frame[100:110, 210:228] = 255
    assert not detect_id_card_on_desk(frame)


def test_evidence_uses_sampled_frame_without_opening_another_decoder(monkeypatch):
    capture = Mock(side_effect=AssertionError("The analyzed image must be used directly"))
    monkeypatch.setattr("app.services.detector.cv2.VideoCapture", capture)
    image = np.full((48, 64, 3), [10, 100, 230], dtype=np.uint8)

    evidence = create_evidence_frame(Path("missing.mp4"), 1.234, frame=image)

    capture.assert_not_called()
    assert evidence.timestamp_sec == 1.23
    jpeg = np.frombuffer(base64.b64decode(evidence.image_base64), dtype=np.uint8)
    decoded = cv2.imdecode(jpeg, cv2.IMREAD_COLOR)
    assert decoded.shape == image.shape
    assert np.abs(decoded.astype(float) - image.astype(float)).max() <= 3


def test_evidence_without_supplied_frame_retains_seek_behavior(monkeypatch):
    image = np.full((48, 64, 3), 80, dtype=np.uint8)
    capture = Mock()
    capture.isOpened.return_value = True
    capture.get.return_value = 25.0
    capture.read.return_value = (True, image)
    constructor = Mock(return_value=capture)
    monkeypatch.setattr("app.services.detector.cv2.VideoCapture", constructor)

    evidence = create_evidence_frame(Path("sample.mp4"), 2)

    constructor.assert_called_once_with("sample.mp4")
    capture.set.assert_called_once_with(cv2.CAP_PROP_POS_FRAMES, 50)
    capture.release.assert_called_once()
    assert evidence.timestamp_sec == 2


@pytest.mark.parametrize("timestamp", [-1, float("nan"), float("inf"), "invalid"])
def test_invalid_evidence_timestamps_are_rejected_before_video_io(monkeypatch, timestamp):
    capture = Mock()
    monkeypatch.setattr("app.services.detector.cv2.VideoCapture", capture)
    with pytest.raises(ValueError, match="timestamp"):
        create_evidence_frame(Path("sample.mp4"), timestamp)
    with pytest.raises(ValueError, match="timestamp"):
        create_evidence_frame(Path("sample.mp4"), timestamp, frame=np.zeros((8, 8, 3)))
    with pytest.raises(ValueError, match="timestamp"):
        extract_evidence_frame_base64(Path("sample.mp4"), timestamp)
    capture.assert_not_called()


def test_empty_sampled_evidence_frame_is_rejected():
    with pytest.raises(ValueError, match="nonempty image"):
        create_evidence_frame(Path("sample.mp4"), 0, frame=np.zeros((0, 0, 3)))


def test_evidence_decoder_is_released_after_read_failure(monkeypatch):
    capture = Mock()
    capture.isOpened.return_value = True
    capture.get.return_value = 25.0
    capture.read.return_value = (False, None)
    monkeypatch.setattr("app.services.detector.cv2.VideoCapture", Mock(return_value=capture))
    with pytest.raises(RuntimeError, match="Failed to read frame"):
        create_evidence_frame(Path("sample.mp4"), 2)
    capture.release.assert_called_once()


@pytest.mark.parametrize("fps", [0, -1, float("nan"), float("inf")])
def test_evidence_rejects_unknown_frame_rate_instead_of_guessing(monkeypatch, fps):
    capture = Mock()
    capture.isOpened.return_value = True
    capture.get.return_value = fps
    monkeypatch.setattr("app.services.detector.cv2.VideoCapture", Mock(return_value=capture))

    with pytest.raises(ValueError, match="FPS must be finite and positive"):
        create_evidence_frame(Path("sample.mp4"), 2)

    capture.set.assert_not_called()
    capture.read.assert_not_called()
    capture.release.assert_called_once()
