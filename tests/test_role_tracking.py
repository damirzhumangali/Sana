from app.services.role_tracking import GeometricRoleTracker

ZONES = {"cashier": [(0.4, 0.4), (0.8, 0.4), (0.8, 0.9), (0.4, 0.9)]}


def person(box, counted=True):
    return {"box_xyxy": box, "counted": counted, "role": "unknown"}


def test_lone_person_is_bootstrapped_as_cashier_even_outside_chair_zone():
    tracker = GeometricRoleTracker()
    people = [person([0, 0, 20, 30])]
    tracker.update(people, 0, 100, 100, ZONES)
    assert people[0]["role"] == "cashier"


def test_known_client_is_not_reclassified_when_cashier_disappears():
    tracker = GeometricRoleTracker()
    tracker.update([person([40, 40, 60, 80])], 0, 100, 100, ZONES)
    people = [person([40, 40, 60, 80]), person([0, 0, 20, 30])]
    tracker.update(people, 0.5, 100, 100, ZONES)
    client_id = people[1]["track_id"]
    remaining = [person([0, 0, 20, 30])]
    tracker.update(remaining, 1, 100, 100, ZONES)
    assert remaining[0]["track_id"] == client_id
    assert remaining[0]["role"] == "customer"


def test_short_gap_keeps_cashier_track_and_movement_keeps_role():
    tracker = GeometricRoleTracker()
    first = [person([40, 40, 60, 80])]
    tracker.update(first, 0, 100, 100, ZONES)
    tracker.update([], 0.5, 100, 100, ZONES)
    moved = [person([35, 35, 55, 75])]
    tracker.update(moved, 1, 100, 100, ZONES)
    assert moved[0]["track_id"] == first[0]["track_id"]
    assert moved[0]["role"] == "cashier"


def test_reflection_is_not_bootstrapped_and_long_gap_is_unresolved():
    tracker = GeometricRoleTracker()
    people = [person([0, 0, 20, 30], False), person([40, 40, 60, 80])]
    tracker.update(people, 0, 100, 100, ZONES)
    assert people[0]["track_id"] is None
    assert people[1]["role"] == "cashier"
    later = [person([40, 40, 60, 80])]
    tracker.update(later, 5, 100, 100, ZONES)
    assert later[0]["role"] == "unknown"
