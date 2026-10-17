import copy

import pytest
from pydantic import ValidationError

from app.rules.engine import evaluate
from app.rules.schemas import RuleRequest


def event(kind, t, customer="client-a", obj=None, group="deposit-a", **extra):
    value = {
        "event_id": f"{kind}-{t}-{customer}",
        "type": kind,
        "start_sec": t,
        "end_sec": t + 0.4,
        "sample_count": 3,
        "source": "synthetic_example",
    }
    if kind in {"CASHIER_PRESENT", "CASHIER_EXIT"}:
        value["person_id"] = "cashier-a"
    if kind.startswith(("CUSTOMER_", "ID_", "MONEY_")):
        value["customer_id"] = customer
    if kind.startswith(("ID_", "MONEY_")):
        value["object_id"] = obj or ("card-a" if kind.startswith("ID_") else "stack-a")
    if kind.startswith("MONEY_"):
        value["money_group_id"] = group
    value.update(extra)
    return value


def timeline(events, duration=30):
    return {
        "duration_sec": duration,
        "events": events,
        "coverage": {
            channel: [{"start_sec": 0, "end_sec": duration}]
            for channel in ("people", "id_actions", "money_actions")
        },
    }


def normal_events():
    return [
        event(kind, i)
        for i, kind in enumerate(
            [
                "CASHIER_PRESENT",
                "CUSTOMER_ENTER",
                "ID_TRANSFER_TO_CASHIER",
                "ID_CHECK",
                "ID_ON_TABLE",
                "MONEY_TRANSFER_TO_CASHIER",
                "MONEY_TO_COUNTER",
                "MONEY_FROM_COUNTER",
                "MONEY_HELD_BY_CASHIER",
            ]
        )
    ]


def result(data):
    return evaluate(RuleRequest.model_validate(data))


def rules(report):
    return [v.rule_id for v in report.violations]


def test_normal_sequence_completes_scenario_but_service_stays_active():
    report = result(timeline(normal_events()))
    assert report.verdict == "no_violation"
    assert [e["type"] for e in report.derived_events] == ["SERVICE_STARTED", "SCENARIO_COMPLETED"]
    assert report.sessions[0]["end_sec"] is None


def test_money_before_id_is_detected_at_acceptance_not_at_counter():
    events = [
        event("CASHIER_PRESENT", 0),
        event("CUSTOMER_ENTER", 1),
        event("MONEY_TRANSFER_TO_CASHIER", 2),
        event("MONEY_TO_COUNTER", 3),
    ]
    report = result(timeline(events))
    assert rules(report) == ["R03"]
    assert report.violations[0].timecode_sec == 2.4


@pytest.mark.parametrize("missing", ["ID_CHECK", "ID_ON_TABLE"])
def test_partial_id_procedure_does_not_authorize_money(missing):
    events = [e for e in normal_events() if e["type"] != missing]
    report = result(timeline(events))
    assert "R03" in rules(report)


def test_return_after_completed_scenario_is_still_checked_without_split_event():
    events = normal_events() + [event("MONEY_TRANSFER_TO_CUSTOMER", 10)]
    report = result(timeline(events))
    assert rules(report) == ["R04"]
    assert report.violations[0].evidence_event_ids == [events[i]["event_id"] for i in (6, 7, 8, 9)]


def test_return_of_linked_part_is_detected_but_split_alone_is_not():
    events = normal_events() + [event("MONEY_STACK_SPLIT", 9, obj="part-b")]
    assert rules(result(timeline(events))) == []
    events.append(event("MONEY_TRANSFER_TO_CUSTOMER", 10, obj="part-b"))
    assert rules(result(timeline(events))) == ["R04"]


@pytest.mark.parametrize("change", ["different_group", "different_customer", "no_held"])
def test_return_requires_same_money_current_customer_and_complete_counter_history(change):
    events = normal_events()
    returned = event("MONEY_TRANSFER_TO_CUSTOMER", 10)
    if change == "different_group":
        returned["money_group_id"] = "unrelated-money"
    elif change == "different_customer":
        returned["customer_id"] = "another-client"
    else:
        events = [e for e in events if e["type"] != "MONEY_HELD_BY_CASHIER"]
    report = result(timeline(events + [returned]))
    assert "R04" not in rules(report)
    assert report.verdict == "insufficient_evidence"


def test_second_customer_and_exit_of_intruder_do_not_end_first_service():
    events = normal_events()[:2] + [
        event("CUSTOMER_ENTER", 2, customer="client-b"),
        event("CUSTOMER_EXIT", 3, customer="client-b"),
    ]
    report = result(timeline(events))
    assert rules(report) == ["R02"]
    assert report.sessions[0]["customer_id"] == "client-a"
    assert report.sessions[0]["end_sec"] is None


def test_next_customer_after_exit_gets_new_session_and_fresh_id_state():
    events = normal_events() + [
        event("CUSTOMER_EXIT", 9),
        event("CUSTOMER_ENTER", 10, customer="client-b"),
        event("MONEY_TRANSFER_TO_CASHIER", 11, customer="client-b"),
    ]
    report = result(timeline(events))
    assert rules(report) == ["R03"]
    assert len(report.sessions) == 2
    assert report.sessions[0]["end_sec"] == 9.4
    assert report.sessions[1]["id_completed"] is False


def test_customer_does_not_turn_into_cashier_when_cashier_leaves():
    events = normal_events() + [
        event("CASHIER_EXIT", 9),
        event("CASHIER_PRESENT", 10, person_id="client-a"),
    ]
    report = result(timeline(events))
    assert rules(report) == ["R01"]
    assert report.violations[0].customer_id == "client-a"


def test_unknown_cashier_status_is_not_confirmed_absence():
    report = result(timeline([event("CUSTOMER_ENTER", 1)]))
    assert report.verdict == "insufficient_evidence"
    assert rules(report) == []


def test_short_detection_gap_is_not_an_exit_or_another_entry():
    events = normal_events() + [event("CUSTOMER_PRESENT", 10)]
    report = result(timeline(events))
    assert rules(report) == []
    assert len(report.sessions) == 1
    assert report.sessions[0]["end_sec"] is None


def test_people_gap_prevents_claim_that_first_customer_still_remained():
    events = normal_events() + [event("CUSTOMER_ENTER", 12, customer="client-b")]
    data = timeline(events)
    data["coverage"]["people"] = [{"start_sec": 0, "end_sec": 9}, {"start_sec": 11, "end_sec": 30}]
    report = result(data)
    assert "R02" not in rules(report)
    assert report.verdict == "insufficient_evidence"


def test_uncertain_exit_prevents_claim_of_two_clients_even_with_declared_coverage():
    events = normal_events() + [
        event("CUSTOMER_EXIT", 10, confidence=0.2),
        event("CUSTOMER_ENTER", 12, customer="client-b"),
    ]
    assert "R02" not in rules(result(timeline(events)))


@pytest.mark.parametrize("exit_first", [True, False])
def test_overlapping_customer_exit_and_entry_do_not_invent_an_order(exit_first):
    exit_event = event("CUSTOMER_EXIT", 10)
    entry_event = event("CUSTOMER_ENTER", 10, customer="client-b")
    boundary = [exit_event, entry_event] if exit_first else [entry_event, exit_event]
    report = result(timeline(normal_events() + boundary))
    assert "R02" not in rules(report)
    assert report.verdict == "insufficient_evidence"
    assert any(u["code"] == "entrance_exit_order_unconfirmed" for u in report.uncertainties)


@pytest.mark.parametrize("cause", ["coverage_gap", "low_confidence", "partial_session"])
def test_unobserved_id_is_not_proof_of_skipped_id(cause):
    events = [
        event("CASHIER_PRESENT", 0),
        event("CUSTOMER_ENTER", 1),
        event("MONEY_TRANSFER_TO_CASHIER", 5),
    ]
    data = timeline(events)
    if cause == "coverage_gap":
        data["coverage"]["id_actions"] = [{"start_sec": 4, "end_sec": 30}]
    elif cause == "low_confidence":
        data["events"].insert(2, event("ID_TRANSFER_TO_CASHIER", 2, confidence=0.3))
    else:
        data["events"][1]["type"] = "CUSTOMER_PRESENT"
    report = result(data)
    assert "R03" not in rules(report)
    assert report.verdict == "insufficient_evidence"


def test_coverage_missing_never_becomes_a_clean_verdict():
    data = timeline(normal_events())
    data["coverage"] = {}
    assert result(data).verdict == "insufficient_evidence"


def test_normal_base_does_not_hide_an_unfinished_second_deposit():
    events = normal_events() + [event("MONEY_TRANSFER_TO_CASHIER", 10, group="deposit-b")]
    assert result(timeline(events)).verdict == "insufficient_evidence"


def test_overlapping_id_and_money_intervals_cannot_establish_order():
    events = normal_events()
    events[5]["start_sec"] = 4.1
    report = result(timeline(events))
    assert "R03" not in rules(report)
    assert report.verdict == "insufficient_evidence"


@pytest.mark.parametrize("scale,offset", [(0.1, 0), (1, 37), (4, 200)])
def test_decision_is_invariant_to_timeline_translation_and_scaling(scale, offset):
    data = timeline(normal_events() + [event("MONEY_TRANSFER_TO_CUSTOMER", 10)])
    for e in data["events"]:
        e["start_sec"] = offset + e["start_sec"] * scale
        e["end_sec"] = offset + e["end_sec"] * scale
    data["duration_sec"] = offset + 30 * scale
    for spans in data["coverage"].values():
        spans[0]["end_sec"] = data["duration_sec"]
    assert rules(result(data)) == ["R04"]


@pytest.mark.parametrize("bad", ["one_frame", "replayed_id", "out_of_order", "nan", "filename"])
def test_invalid_event_stream_is_rejected(bad):
    data = copy.deepcopy(timeline(normal_events()))
    if bad == "one_frame":
        data["events"][0]["sample_count"] = 1
    elif bad == "replayed_id":
        data["events"][1]["event_id"] = data["events"][0]["event_id"]
    elif bad == "out_of_order":
        data["events"].reverse()
    elif bad == "nan":
        data["events"][0]["end_sec"] = float("nan")
    else:
        data["filename"] = "X-1.mov"
    with pytest.raises(ValidationError):
        RuleRequest.model_validate(data)
