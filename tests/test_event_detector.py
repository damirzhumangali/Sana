import pytest

from app.events.detector import EventDetector
from app.events.tracking import LogicalObjectTracker
from tests.event_fixtures import Scenario, config, money, person


def test_person_debounce_short_miss_exit_and_service_end():
    s = Scenario(exit_requires_boundary=False)
    s.frame()
    assert s.kinds() == []
    s.hold(frames=4)
    s.people.append(person())
    s.frame()
    assert "CUSTOMER_ENTER" not in s.kinds()
    s.hold(frames=4)
    assert s.kinds().count("CUSTOMER_ENTER") == 1
    s.people.pop()
    s.hold(frames=3)
    assert "CUSTOMER_EXIT" not in s.kinds()
    s.people.append(person())
    s.hold(frames=4)
    assert s.kinds().count("CUSTOMER_ENTER") == 1
    s.people.pop()
    s.hold(frames=12)
    final = s.finish()
    assert s.kinds().count("CUSTOMER_EXIT") == 1
    assert [e["type"] for e in final["events"]].count("SERVICE_ENDED") == 1
    s.people.append(person("next-client"))
    s.hold(frames=5)
    assert s.detector.active_customer == "next-client"


def test_loss_inside_room_is_not_an_exit_with_boundary_gate():
    s = Scenario().start()
    s.people.pop()
    s.hold(frames=20)
    assert "CUSTOMER_EXIT" not in s.kinds()


def test_second_customer_generates_observation_and_existing_R02():
    s = Scenario().start()
    s.people.append(person("second", point=(0.35, 0.5)))
    s.hold(frames=5)
    final = s.finish()
    assert "SECOND_CUSTOMER_ENTER" in [e["type"] for e in final["events"]]
    assert [v["rule_id"] for v in final["rule_result"]["violations"]] == ["R02"]
    assert s.detector.active_customer == "client"


def test_person_track_fragment_does_not_create_second_customer():
    s = Scenario().start()
    s.people[1] = person("new-client-id")
    s.hold(frames=6)
    assert s.kinds().count("CUSTOMER_ENTER") == 1
    assert "possible_person_track_fragment" in s.detector.warnings


def test_object_present_near_cashier_is_not_a_transfer_and_boundary_flicker_is_not_counter():
    s = Scenario().start()
    s.hold((0.7, 0.5), frames=8)
    assert "MONEY_TRANSFER_TO_CASHIER" not in s.kinds()
    s.frame([money((0.5, 0.1))])
    s.hold((0.7, 0.5), frames=4)
    assert "MONEY_TO_COUNTER" not in s.kinds()
    assert not any(row["was_in_counter"] for row in s.detector.history)


@pytest.mark.parametrize(
    ("with_id", "returned", "expected"),
    [
        (True, False, []),
        (False, False, ["R03"]),
        (True, True, ["R04"]),
    ],
)
def test_tracks_through_detector_into_existing_rule_engine(with_id, returned, expected):
    # Fully observed synthetic scenes explicitly declare validated ID coverage.
    s = Scenario(id_enabled=True).start()
    if with_id:
        s.id_sequence()
    s.deposit()
    if returned:
        s.move((0.7, 0.5), (0.25, 0.5))
        s.hold((0.25, 0.5))
    final = s.finish()
    kinds = s.kinds()
    for kind in (
        "MONEY_TRANSFER_TO_CASHIER",
        "MONEY_TO_COUNTER",
        "MONEY_FROM_COUNTER",
        "MONEY_HELD_BY_CASHIER",
    ):
        assert kinds.count(kind) == 1
    assert [v["rule_id"] for v in final["rule_result"]["violations"]] == expected
    assert final["rule_result"]["verdict"] == ("violation" if expected else "no_violation")
    assert "MONEY_STACK_SPLIT" not in kinds


def test_no_id_detector_does_not_invent_id_events_or_R03():
    s = Scenario(id_enabled=False).start().id_sequence().deposit()
    final = s.finish()
    assert not any(kind.startswith("ID_") for kind in s.kinds())
    assert final["rule_request"]["coverage"]["id_actions"] == []
    assert final["rule_result"]["verdict"] == "insufficient_evidence"
    assert not final["rule_result"]["violations"]


def test_low_confidence_object_events_are_not_used_as_reliable_evidence():
    s = Scenario().start()
    s.hold((0.25, 0.5))
    # The transition is visible, but all new observations have weak detections.
    for step in range(1, 24):
        s.frame([money((0.25 + 0.45 * step / 23, 0.5), confidence=0.26)])
    for _ in range(5):
        s.frame([money((0.7, 0.5), confidence=0.26)])
    final = s.finish()
    transfers = [e for e in final["events"] if e["type"] == "MONEY_TRANSFER_TO_CASHIER"]
    assert transfers and transfers[0]["accepted"] is False
    assert not final["rule_result"]["violations"]


def test_temporary_money_loss_keeps_history_and_suppresses_duplicate_counter_events():
    s = Scenario().start().id_sequence().deposit()
    s.hold(frames=2)
    s.hold((0.7, 0.5), frames=8, key="new-detector-id")
    s.move((0.7, 0.5), (0.25, 0.5), key="new-detector-id")
    s.hold((0.25, 0.5), key="new-detector-id")
    final = s.finish()
    assert final["track_reconnections"]
    assert s.kinds().count("MONEY_TO_COUNTER") == 1
    assert s.kinds().count("MONEY_FROM_COUNTER") == 1
    assert s.kinds().count("MONEY_TRANSFER_TO_CUSTOMER") == 1
    assert [v["rule_id"] for v in final["rule_result"]["violations"]] == ["R04"]


def test_part_can_share_money_history_without_requiring_split_event():
    s = Scenario().start().id_sequence().deposit()
    s.extras = [money((0.7, 0.5))]
    s.hold((0.705, 0.5), frames=5, key="small-part", size=0.006)
    s.move((0.705, 0.5), (0.25, 0.5), key="small-part", size=0.006)
    s.hold((0.25, 0.5), key="small-part", size=0.006)
    final = s.finish()
    assert final["lineage_links"]
    assert "MONEY_STACK_SPLIT" not in s.kinds()
    assert [v["rule_id"] for v in final["rule_result"]["violations"]] == ["R04"]


def test_money_history_has_required_geometry_and_people_fields():
    s = Scenario().start()
    s.hold((0.25, 0.5))
    row = s.finish()["track_history"][-1]
    assert {
        "track_id",
        "logical_object_id",
        "timestamp_sec",
        "bbox",
        "center",
        "zone",
        "nearest_person_track_id",
        "nearest_person_role",
        "distance_to_cashier",
        "distance_to_current_customer",
        "was_in_counter",
        "previous_zones",
    } <= row.keys()


@pytest.mark.parametrize("gap", [0.75, 3.0])
def test_reconnection_respects_maximum_gap(gap):
    tracker = LogicalObjectTracker(config())
    tracker.update([money((0.3, 0.5), "7")], 0)
    before = tracker.update([money((0.3, 0.5), "7")], 0.25)[0]
    after = tracker.update([money((0.305, 0.5), "12")], 0.25 + gap)[0]
    assert (before["logical_object_id"] == after["logical_object_id"]) == (gap < 1.25)
    assert before["track_id"] != after["track_id"]


def test_reconnection_rejects_ambiguous_donors_and_wrong_direction():
    tracker = LogicalObjectTracker(config())
    for t in (0, 0.25):
        tracker.update([money((0.3, 0.5), "7"), money((0.34, 0.5), "8")], t)
    new = tracker.update([money((0.32, 0.5), "12")], 0.75)[0]
    assert new["association_ambiguous"]
    assert not tracker.links
    other = LogicalObjectTracker(config())
    old = other.update([money((0.3, 0.5), "7")], 0)[0]
    other.update([money((0.32, 0.5), "7")], 0.25)
    new = other.update([money((0.29, 0.5), "12")], 0.75)[0]
    assert new["logical_object_id"] != old["logical_object_id"]


def test_objects_of_different_classes_are_never_joined():
    tracker = LogicalObjectTracker(config())
    before = tracker.update([money((0.3, 0.5), "7")], 0)[0]
    after = tracker.update([money((0.3, 0.5), "7", label="id_card")], 0.25)[0]
    assert before["logical_object_id"] != after["logical_object_id"]


def test_time_order_is_validated():
    detector = EventDetector(config())
    detector.update({"timestamp_sec": 1})
    with pytest.raises(ValueError, match="increasing"):
        detector.update({"timestamp_sec": 1})


@pytest.mark.parametrize("pause_frames,expected", [(3, 1), (16, 0)])
def test_neutral_table_interval_preserves_recent_owner_but_expires(pause_frames, expected):
    s = Scenario(raw_track_distance=0.5, neutral_bridge_sec=2).start()
    s.hold((0.25, 0.5))
    s.hold((0.5, 0.85), frames=pause_frames)
    s.hold((0.7, 0.5))
    assert s.kinds().count("MONEY_TRANSFER_TO_CASHIER") == expected
    assert "MONEY_FROM_COUNTER" not in s.kinds()
    assert "MONEY_HELD_BY_CASHIER" not in s.kinds()


def test_diagnostic_history_explains_unconfirmed_short_source():
    s = Scenario(raw_track_distance=0.5).start()
    s.hold((0.25, 0.5), frames=2)
    row = s.detector.history[-1]
    assert row["semantic_decision"] == "awaiting_temporal_confirmation"
    assert row["candidate_frames"] == 2
    assert row["candidate_duration_sec"] == 0.25
    assert row["transition_anchor"] is None
    s.hold((0.7, 0.5))
    assert "MONEY_TRANSFER_TO_CASHIER" not in s.kinds()
