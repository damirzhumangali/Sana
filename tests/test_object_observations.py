from app.events.observations import suppress_object_duplicates


def test_duplicate_cash_does_not_create_competing_logical_tracks():
    raw = [
        {"bbox": [0.3, 0.4, 0.35, 0.43], "confidence": 0.8, "label": "cash"},
        {"bbox": [0.302, 0.401, 0.352, 0.431], "confidence": 0.5, "label": "cash"},
        {"bbox": [0.4, 0.4, 0.45, 0.43], "confidence": 0.7, "label": "cash"},
    ]
    kept, suppressed = suppress_object_duplicates(raw, 0.45)
    assert kept == [raw[0], raw[2]]
    assert len(suppressed) == 1
    assert suppressed[0]["kept_bbox"] == raw[0]["bbox"]
    assert "suppression_iou" not in raw[1]


def test_different_classes_and_explicit_separate_track_ids_are_preserved():
    base = {"bbox": [0.3, 0.4, 0.35, 0.43], "confidence": 0.8, "label": "cash"}
    for second in ({**base, "label": "id_card"}, {**base, "track_id": "separate-part"}):
        kept, suppressed = suppress_object_duplicates([base, second], 0.45)
        assert len(kept) == 2
        assert not suppressed
