from app.cv.analyze_events import suppress_nested_people
from app.events.detector import EventDetector
from app.services.role_tracking import GeometricRoleTracker
from tests.event_fixtures import config


def detection(box, confidence=0.95):
    return {"box_xyxy": box, "confidence": confidence, "counted": True}


def test_nested_boxes_are_suppressed_but_separate_people_remain():
    original = detection([20, 20, 40, 70])
    nested = detection([24, 21, 40, 68], 0.8)
    separate = detection([38, 20, 55, 70], 0.9)
    kept, suppressed = suppress_nested_people([nested, separate, original], 0.9)
    assert kept == [original, separate]
    assert suppressed == [nested]
    assert nested["counted"]  # The raw evidence is not modified.


def test_duplicate_person_boxes_do_not_create_a_second_service_customer():
    cfg = config()
    roles = GeometricRoleTracker()
    detector = EventDetector(cfg)
    for step in range(16):
        people = [detection([64, 35, 76, 65])]
        if step >= 5:
            people += [detection([19, 35, 31, 65]), detection([21, 36, 31, 64], 0.8)]
        people, suppressed = suppress_nested_people(people, cfg.person_duplicate_overlap)
        roles.update(people, step * 0.25, 100, 100, cfg.zones, cfg.cashier_anchor)
        detector.update(
            {
                "timestamp_sec": step * 0.25,
                "people": [
                    {
                        "track_id": p["track_id"],
                        "role": p["role"],
                        "bbox": [v / 100 for v in p["box_xyxy"]],
                        "confidence": p["confidence"],
                        "identity_reliable": not p.get("tracking_ambiguous", False),
                    }
                    for p in people
                ],
                "people_observable": not suppressed,
            }
        )
    result = detector.finish(3.75)
    kinds = [e["type"] for e in result["events"]]
    assert kinds.count("CUSTOMER_ENTER") == 1
    assert kinds.count("SERVICE_STARTED") == 1
    assert "SECOND_CUSTOMER_ENTER" not in kinds
    assert not result["rule_result"]["violations"]
