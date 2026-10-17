import copy

import pytest

from app.cv.cash_dataset import reviewed_items
from app.cv.evaluate_cash import match_boxes
from app.cv.inspect_cash import cash_class_ids


def item(split="train"):
    return {
        "id": "x01_001",
        "split": split,
        "video_sha256": "a" * 64,
        "width": 100,
        "height": 100,
        "boxes": [[10, 10, 20, 20]],
        "review": "reviewed_provisional",
    }


def test_unreviewed_frame_is_not_a_negative_example():
    pending = item()
    pending.update(review="pending", boxes=None)
    assert reviewed_items({"class": "cash", "items": [pending]}) == []


def test_same_source_video_cannot_leak_across_splits():
    first, second = item(), item("test")
    second["id"] = "x01_002"
    with pytest.raises(ValueError, match="leakage"):
        reviewed_items({"class": "cash", "items": [first, second]})


def test_explicit_negative_is_kept_but_invalid_box_rejected():
    negative = item()
    negative["boxes"] = []
    assert len(reviewed_items({"class": "cash", "items": [negative]})) == 1
    bad = copy.deepcopy(negative)
    bad["boxes"] = [[10, 10, 101, 20]]
    with pytest.raises(ValueError, match="outside"):
        reviewed_items({"class": "cash", "items": [bad]})


def test_coco_weights_cannot_silently_be_used_as_cash_weights():
    with pytest.raises(ValueError, match="no 'cash' class"):
        cash_class_ids({0: "person", 1: "bicycle"})
    assert cash_class_ids({0: "cash"}) == [0]


def test_one_target_cannot_count_as_two_true_positives():
    predictions = [
        {"box_xyxy": [10, 10, 20, 20], "confidence": 0.9},
        {"box_xyxy": [10, 10, 20, 20], "confidence": 0.8},
    ]
    assert match_boxes(predictions, [[10, 10, 20, 20], [50, 50, 60, 60]]) == (1, 1, 1)
    assert match_boxes(predictions, []) == (0, 2, 0)
