from dataclasses import FrozenInstanceError

import pytest

from app.services.cashier_rules import FrameObservation, OperationPhase, RuleEngine


def frame(timestamp, *, cashier=True, clients=0, entering=0, **signals):
    return FrameObservation(
        timestamp,
        cashier_present=cashier,
        client_count=clients,
        entering_count=entering,
        **signals,
    )


def run(engine, observations):
    return [violation for observation in observations for violation in engine.process(observation)]


def codes(violations):
    return [violation.code for violation in violations]


@pytest.mark.parametrize("fps", [2, 5, 10, 30])
def test_multiple_clients_uses_elapsed_seconds_at_different_sampling_rates(fps):
    violations = run(
        RuleEngine(),
        [frame(index / fps, clients=1, entering=1) for index in range(3 * fps)],
    )
    assert codes(violations) == ["multiple_clients"]
    assert 1.5 < violations[0].timecode_sec <= 1.5 + 1 / fps


def test_two_people_in_doorway_count_and_later_episode_is_separate():
    engine = RuleEngine()
    assert not run(engine, [frame(0, entering=2), frame(1, entering=2), frame(1.5, entering=2)])
    assert codes(engine.process(frame(1.6, entering=2))) == ["multiple_clients"]
    assert not engine.process(frame(1.8, entering=2))
    engine.process(frame(2, entering=1))
    engine.process(frame(2.1, entering=2))
    assert not engine.process(frame(3.6, entering=2))
    assert codes(engine.process(frame(3.7, entering=2))) == ["multiple_clients"]


def test_multiple_clients_gap_cannot_supply_missing_duration():
    engine = RuleEngine()
    assert not run(engine, [frame(0, clients=2), frame(1, clients=2), frame(10, clients=2)])
    assert not engine.process(frame(11.5, clients=2))
    violations = engine.process(frame(11.6, clients=2))
    assert codes(violations) == ["multiple_clients"]
    assert violations[0].timecode_sec == 11.6


def test_already_reported_occupancy_is_not_repeated_after_unknown_gap():
    engine = RuleEngine()
    assert codes(run(engine, [frame(0, clients=2), frame(2, clients=2)])) == ["multiple_clients"]
    assert not run(engine, [frame(10, clients=2), frame(12, clients=2)])


def test_cashier_alone_working_is_normal_even_with_cash_and_laptop():
    assert not run(
        RuleEngine(),
        [
            frame(
                second,
                laptop_open=True,
                cash_on_table=True,
                safe_open=True,
                key_in_safe=True,
                counter_active=True,
                cash_handover=True,
                id_verified=False,
            )
            for second in range(15)
        ],
    )


@pytest.mark.parametrize(
    ("signal", "expected"),
    [
        ("laptop_open", "unattended_laptop"),
        ("cash_on_table", "unattended_cash"),
        ("safe_open", "unattended_safe"),
        ("key_in_safe", "unattended_key"),
    ],
)
def test_absence_requires_previous_presence_elapsed_time_and_current_hazard(signal, expected):
    engine = RuleEngine()
    engine.process(frame(0))
    assert not run(
        engine,
        [frame(second, cashier=False, **{signal: True}) for second in range(1, 6)],
    )
    violations = engine.process(frame(5.1, cashier=False, **{signal: True}))
    assert codes(violations) == [expected]
    assert violations[0].timecode_sec == 5.1
    assert not engine.process(frame(6, cashier=False, **{signal: True}))


def test_no_absence_violation_if_cashier_has_never_been_seen():
    assert not run(
        RuleEngine(),
        [
            frame(second, cashier=False, laptop_open=True, safe_open=True, cash_on_table=True)
            for second in range(12)
        ],
    )


def test_brief_cashier_movement_is_normal_and_return_restarts_episode():
    engine = RuleEngine()
    assert not run(
        engine,
        [
            frame(0),
            frame(1, cashier=False, laptop_open=True),
            frame(2, cashier=False, laptop_open=True),
            frame(3),
            *[frame(second, cashier=False, laptop_open=True) for second in range(4, 9)],
        ],
    )
    assert codes(engine.process(frame(8.1, cashier=False, laptop_open=True))) == [
        "unattended_laptop"
    ]
    engine.process(frame(9))
    violations = run(
        engine,
        [frame(second, cashier=False, laptop_open=True) for second in range(10, 16)],
    )
    assert codes(violations) == ["unattended_laptop"]


def test_absence_does_not_invent_or_reuse_unknown_hazards():
    engine = RuleEngine()
    assert not run(
        engine,
        [frame(0), frame(1, cashier=False, cash_on_table=True)]
        + [frame(second, cashier=False) for second in range(2, 8)],
    )
    assert codes(engine.process(frame(8, cashier=False, cash_on_table=True))) == ["unattended_cash"]
    assert codes(engine.process(frame(9, cashier=False, laptop_open=True, key_in_safe=True))) == [
        "unattended_laptop",
        "unattended_key",
    ]


def test_absence_gap_restarts_duration_from_next_observation():
    engine = RuleEngine()
    assert not run(
        engine,
        [frame(0), frame(1, cashier=False, laptop_open=True)]
        + [frame(second, cashier=False, laptop_open=True) for second in range(6, 11)],
    )
    assert codes(engine.process(frame(10.1, cashier=False, laptop_open=True))) == [
        "unattended_laptop"
    ]


def test_entry_without_cashier_requires_observed_new_entrance():
    engine = RuleEngine()
    assert not engine.process(frame(0, cashier=False, entering=1))
    assert not engine.process(frame(1, cashier=False, clients=1))
    # A seated client moving to the doorway is leaving, not a new entrant.
    assert not engine.process(frame(2, cashier=False, entering=1))
    assert codes(engine.process(frame(3, cashier=False, entering=2))) == ["client_not_ready"]


def test_new_entry_after_empty_room_and_flicker_deduplication():
    engine = RuleEngine()
    engine.process(frame(0, cashier=False))
    assert codes(engine.process(frame(1, cashier=False, entering=1))) == ["client_not_ready"]
    assert not engine.process(frame(1.1, cashier=False, entering=1))
    assert not engine.process(frame(1.2, cashier=False))
    assert not engine.process(frame(1.3, cashier=False, entering=1))
    assert not engine.process(frame(2, cashier=False, clients=1))
    engine.process(frame(3, cashier=False))
    assert codes(engine.process(frame(4, cashier=False, entering=1))) == ["client_not_ready"]


def test_entrance_cannot_be_inferred_across_video_gap():
    engine = RuleEngine()
    engine.process(frame(0, cashier=False))
    assert not engine.process(frame(10, cashier=False, entering=1))


def test_operation_progresses_through_five_phases():
    engine = RuleEngine()
    engine.process(frame(0))
    assert engine.phase is OperationPhase.PREPARATION
    engine.process(frame(1, entering=1))
    assert engine.phase is OperationPhase.CLIENT_ENTRY
    engine.process(frame(2, clients=1))
    assert engine.phase is OperationPhase.IDENTIFICATION
    engine.process(frame(3, clients=1, id_verified=True))
    assert engine.phase is OperationPhase.CASH_OPERATION
    engine.process(frame(4, clients=1, cash_stored_in_safe=True))
    assert engine.phase is OperationPhase.CLIENT_DEPARTURE
    engine.process(frame(5))
    assert engine.phase is OperationPhase.CLIENT_DEPARTURE
    engine.process(frame(6))
    assert engine.phase is OperationPhase.PREPARATION


@pytest.mark.parametrize("action", ["cash_handover", "counter_active"])
def test_cash_before_id_is_reported_at_confirmed_action_and_not_erased(action):
    engine = RuleEngine()
    assert not run(
        engine,
        [frame(0), frame(1, entering=1), frame(2, clients=1, id_verified=False)],
    )
    violations = engine.process(frame(3, clients=1, id_verified=False, **{action: True}))
    assert codes(violations) == ["cash_before_id"]
    assert violations[0].timecode_sec == 3
    assert not engine.process(frame(4, clients=1, id_verified=True, **{action: True}))
    assert not engine.process(frame(5, clients=1, id_verified=False, **{action: True}))


def test_unknown_id_is_not_a_negative_id_verification():
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1),
            frame(2, clients=1, cash_handover=True),
            frame(3, clients=1, counter_active=True),
        ],
    )


def test_id_before_cash_is_remembered_for_current_operation():
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, id_verified=True),
            frame(2, clients=1, cash_handover=True, id_verified=False),
            frame(3, clients=1, counter_active=True, id_verified=False),
        ],
    )


def test_video_starting_mid_operation_cannot_prove_missing_id_or_counting():
    assert not run(
        RuleEngine(),
        [
            frame(0, clients=1, cash_handover=True, id_verified=False, counter_active=False),
            frame(1, clients=1, cash_stored_in_safe=True, counter_active=False),
            frame(2, clients=1, counter_active=True, id_verified=False),
            frame(3, clients=1, counter_active=False, cash_returned_to_client=True),
        ],
    )


def test_partial_client_visit_can_have_new_fully_observed_cash_acceptance():
    violations = run(
        RuleEngine(),
        [
            frame(0, clients=1, cash_handover=False, counter_active=False),
            frame(1, clients=1, cash_handover=True, counter_active=False, id_verified=False),
            frame(2, clients=1, cash_stored_in_safe=True, counter_active=False),
        ],
    )
    # ID may have been verified before the video; this cash movement is fully observed.
    assert codes(violations) == ["cash_bypassed_counter"]


def test_partial_client_visit_confirmed_cash_acceptance_and_count_can_support_return_rule():
    violations = run(
        RuleEngine(),
        [
            frame(0, clients=1, cash_handover=False),
            frame(1, clients=1, cash_handover=True, counter_active=True),
            frame(2, clients=1, counter_active=False, cash_returned_to_client=True),
        ],
    )
    assert codes(violations) == ["cash_returned_after_counting"]


def test_sequential_clients_do_not_share_id_or_violation_deduplication():
    engine = RuleEngine()
    assert not run(
        engine,
        [frame(0), frame(1, clients=1, id_verified=True), frame(2), frame(3)],
    )
    assert codes(engine.process(frame(4, clients=1, id_verified=False, cash_handover=True))) == [
        "cash_before_id"
    ]
    engine.process(frame(5))
    engine.process(frame(6))
    assert codes(engine.process(frame(7, clients=1, id_verified=False, cash_handover=True))) == [
        "cash_before_id"
    ]


def test_sudden_negative_id_during_persistent_cash_action_is_not_new_cash_start():
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, cash_handover=True),
            frame(2, clients=1, cash_handover=True, id_verified=False),
        ],
    )


def test_safe_bypass_requires_observed_acceptance_and_explicit_counter_absence():
    engine = RuleEngine()
    assert not run(
        engine,
        [
            frame(0),
            frame(1, clients=1, id_verified=True, counter_active=False),
            frame(2, clients=1, cash_handover=True, counter_active=False),
        ],
    )
    violations = engine.process(frame(3, clients=1, cash_stored_in_safe=True, counter_active=False))
    assert codes(violations) == ["cash_bypassed_counter"]
    assert not engine.process(frame(4, clients=1, cash_stored_in_safe=True, counter_active=False))


@pytest.mark.parametrize("counter", [None, True])
def test_unknown_or_observed_counter_activity_prevents_bypass_inference(counter):
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, id_verified=True),
            frame(2, clients=1, cash_handover=True, counter_active=False),
            frame(3, clients=1, counter_active=counter),
            frame(4, clients=1, cash_stored_in_safe=True, counter_active=False),
        ],
    )


def test_unknown_acceptance_counter_cannot_be_repaired_by_later_negative_signal():
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, cash_handover=True),
            frame(2, clients=1, counter_active=False),
            frame(3, clients=1, cash_stored_in_safe=True, counter_active=False),
        ],
    )


def test_prior_count_does_not_excuse_later_accepted_cash_bypassing_counter():
    violations = run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, id_verified=True, counter_active=True),
            frame(2, clients=1, counter_active=False),
            frame(3, clients=1, cash_handover=True, counter_active=False),
            frame(4, clients=1, cash_stored_in_safe=True, counter_active=False),
        ],
    )
    assert codes(violations) == ["cash_bypassed_counter"]


def test_storage_already_active_before_handover_is_not_a_new_storage_action():
    assert not run(
        RuleEngine(),
        [
            frame(0, cash_stored_in_safe=True, counter_active=False),
            frame(
                1,
                clients=1,
                id_verified=True,
                cash_handover=True,
                counter_active=False,
                cash_stored_in_safe=True,
            ),
        ],
    )


def test_safe_storage_without_observed_incoming_cash_does_not_establish_bypass():
    assert not run(
        RuleEngine(),
        [frame(0), frame(1, clients=1), frame(2, clients=1, cash_stored_in_safe=True)],
    )


def test_gap_invalidates_operation_history_for_missing_id_and_counter():
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, cash_handover=True, counter_active=False),
            frame(10, clients=1, cash_stored_in_safe=True, counter_active=False, id_verified=False),
            frame(11, clients=1, counter_active=True, id_verified=False),
        ],
    )


def test_returned_cash_requires_observed_acceptance_and_completed_count():
    engine = RuleEngine()
    assert not run(
        engine,
        [
            frame(0),
            frame(1, clients=1, id_verified=True),
            frame(2, clients=1, cash_handover=True, counter_active=False),
            frame(3, clients=1, counter_active=True),
        ],
    )
    assert codes(
        engine.process(frame(4, clients=1, counter_active=False, cash_returned_to_client=True))
    ) == ["cash_returned_after_counting"]
    assert not engine.process(frame(5, clients=1, cash_returned_to_client=True))


@pytest.mark.parametrize("counter", [None, True])
def test_return_before_confirmed_count_completion_is_not_reported(counter):
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, cash_handover=True, counter_active=True),
            frame(2, clients=1, cash_returned_to_client=True, counter_active=counter),
        ],
    )


def test_count_and_cash_return_without_observed_acceptance_can_be_cash_disbursement():
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, counter_active=True),
            frame(2, clients=1, cash_returned_to_client=True, counter_active=False),
        ],
    )


def test_count_before_acceptance_does_not_prove_later_accepted_cash_was_counted():
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, counter_active=True),
            frame(2, clients=1, cash_handover=True, counter_active=False),
            frame(3, clients=1, cash_returned_to_client=True, counter_active=False),
        ],
    )


def test_return_already_active_before_completed_count_is_not_a_new_return_action():
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, cash_handover=True, counter_active=True),
            frame(2, clients=1, counter_active=True, cash_returned_to_client=True),
            frame(3, clients=1, counter_active=False, cash_returned_to_client=True),
        ],
    )


def test_cash_storage_can_be_confirmed_after_client_departure():
    violations = run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, cash_handover=True, counter_active=False),
            frame(2, counter_active=False),
            frame(3, cash_stored_in_safe=True, counter_active=False),
        ],
    )
    assert codes(violations) == ["cash_bypassed_counter"]
    assert violations[0].timecode_sec == 3


def test_count_completion_and_return_can_be_confirmed_after_client_leaves_desk():
    violations = run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, cash_handover=True, counter_active=True),
            frame(2, counter_active=False),
            frame(3, cash_returned_to_client=True),
        ],
    )
    assert codes(violations) == ["cash_returned_after_counting"]


def test_leaving_through_door_while_counting_is_one_departure_not_new_entry():
    engine = RuleEngine()
    assert not run(engine, [frame(0), frame(1, clients=1, counter_active=True)])
    violations = engine.process(frame(2, cashier=False, entering=1, counter_active=True))
    assert codes(violations) == ["client_left_during_counting"]
    assert violations[0].timecode_sec == 2
    assert engine.phase is OperationPhase.CLIENT_DEPARTURE
    assert not engine.process(frame(3, cashier=False, counter_active=True))


def test_brief_missing_client_detection_during_counting_is_not_departure():
    assert not run(
        RuleEngine(),
        [
            frame(0, clients=1, counter_active=True),
            frame(0.5, counter_active=True),
            frame(1, clients=1, counter_active=True),
        ],
    )


def test_plain_client_disappearance_requires_supported_departure():
    engine = RuleEngine()
    assert not engine.process(frame(0, clients=1, counter_active=True))
    assert not engine.process(frame(1, counter_active=True))
    assert not engine.process(frame(1.5, counter_active=True))
    violations = engine.process(frame(1.6, counter_active=True))
    assert codes(violations) == ["client_left_during_counting"]
    assert violations[0].timecode_sec == 1.6


def test_lone_cashier_starting_work_after_client_visit_is_normal():
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(0.5, clients=1),
            frame(1, counter_active=False, cash_on_table=False),
            *[
                frame(second, counter_active=True, id_verified=False, cash_on_table=True)
                for second in range(2, 10)
            ],
        ],
    )


def test_counter_starting_after_client_disappearance_is_not_counting_at_departure():
    assert not run(
        RuleEngine(),
        [
            frame(0, clients=1),
            frame(1, counter_active=False),
            frame(2, counter_active=True),
            frame(3, counter_active=True),
        ],
    )


def test_counter_starting_after_client_enters_exit_doorway_is_not_counting_at_departure():
    assert not run(
        RuleEngine(),
        [
            frame(0, clients=1),
            frame(1, entering=1, counter_active=False),
            frame(2, entering=1, counter_active=True),
            frame(3, counter_active=True),
        ],
    )


def test_counter_unknown_at_departure_cannot_be_treated_as_previously_active():
    assert not run(
        RuleEngine(),
        [frame(0, clients=1), frame(1), frame(2, counter_active=True)],
    )


def test_counter_stopping_during_departure_grace_cannot_rearm_for_later_cashier_count():
    assert not run(
        RuleEngine(),
        [
            frame(0, clients=1, counter_active=True),
            frame(1, counter_active=True),
            frame(1.2, counter_active=False),
            frame(2, counter_active=True),
            frame(3, counter_active=True),
        ],
    )


def test_confirmed_clear_inactive_desk_closes_departed_client_cash_transaction():
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(0.5, clients=1, cash_handover=True, counter_active=False),
            frame(1, counter_active=False, cash_on_table=False),
            frame(2, counter_active=True, id_verified=False),
            frame(3, counter_active=False, cash_returned_to_client=True),
        ],
    )


@pytest.mark.parametrize("counter", [None, False])
def test_client_departure_requires_current_confirmed_counter_activity(counter):
    assert not run(
        RuleEngine(),
        [
            frame(0),
            frame(1, clients=1, counter_active=True),
            frame(2, counter_active=counter),
        ],
    )


def test_cash_left_timer_starts_after_actual_exit_including_doorway():
    engine = RuleEngine()
    assert not run(
        engine,
        [
            frame(0, clients=1, cash_on_table=True),
            frame(1, entering=1, cash_on_table=True),
            *[frame(second, cash_on_table=True) for second in range(2, 8)],
        ],
    )
    violations = engine.process(frame(7.1, cash_on_table=True))
    assert codes(violations) == ["cash_left_on_table"]
    assert violations[0].timecode_sec == 7.1
    assert not engine.process(frame(8, cash_on_table=True))


def test_cash_left_on_table_needs_client_departure_not_idle_cashier():
    assert not run(RuleEngine(), [frame(second, cash_on_table=True) for second in range(12)])


def test_confirmed_clear_desk_after_departure_disarms_later_cashier_cash_work():
    assert not run(
        RuleEngine(),
        [frame(0, clients=1), frame(1, cash_on_table=False)]
        + [frame(second, cash_on_table=True) for second in range(2, 12)],
    )


def test_removing_leftover_cash_disarms_later_cashier_cash_work():
    assert not run(
        RuleEngine(),
        [
            frame(0, clients=1),
            frame(1, cash_on_table=True),
            frame(2, cash_on_table=True),
            frame(3, cash_on_table=False),
            *[frame(second, cash_on_table=True) for second in range(4, 12)],
        ],
    )


def test_doorway_visitor_without_observed_client_does_not_start_cash_left_timer():
    assert not run(
        RuleEngine(),
        [frame(0, entering=1)] + [frame(second, cash_on_table=True) for second in range(1, 9)],
    )


def test_unknown_cash_resets_continuous_cash_left_duration():
    engine = RuleEngine()
    assert not run(
        engine,
        [
            frame(0, clients=1),
            frame(1, cash_on_table=True),
            frame(2, cash_on_table=True),
            frame(3),
            *[frame(second, cash_on_table=True) for second in range(4, 10)],
        ],
    )
    assert codes(engine.process(frame(9.1, cash_on_table=True))) == ["cash_left_on_table"]


def test_cash_left_gap_restarts_duration_but_remembers_observed_departure():
    engine = RuleEngine()
    assert not run(
        engine,
        [frame(0, clients=1), frame(1, cash_on_table=True)]
        + [frame(second, cash_on_table=True) for second in range(10, 16)],
    )
    assert codes(engine.process(frame(15.1, cash_on_table=True))) == ["cash_left_on_table"]


def test_gap_from_client_presence_to_empty_room_cannot_prove_departure():
    assert not run(
        RuleEngine(),
        [frame(0, clients=1)] + [frame(second, cash_on_table=True) for second in range(10, 19)],
    )


def test_new_client_cancels_previous_departure_cash_timer():
    assert not run(
        RuleEngine(),
        [
            frame(0, clients=1),
            frame(1, cash_on_table=True),
            frame(2, cash_on_table=True),
            *[frame(second, clients=1, cash_on_table=True) for second in range(3, 10)],
        ],
    )


def test_foreign_object_requires_explicit_confirmation_and_deduplicates_episodes():
    engine = RuleEngine()
    assert not engine.process(frame(0))
    assert codes(engine.process(frame(1, foreign_object_on_table=True))) == [
        "foreign_object_on_table"
    ]
    assert not engine.process(frame(2))
    assert not engine.process(frame(3, foreign_object_on_table=True))
    assert not engine.process(frame(4, foreign_object_on_table=False))
    assert codes(engine.process(frame(5, foreign_object_on_table=True))) == [
        "foreign_object_on_table"
    ]


@pytest.mark.parametrize("timestamp", [-1, float("inf"), float("nan"), True, "1"])
def test_invalid_observation_timestamps_are_rejected(timestamp):
    with pytest.raises(ValueError):
        frame(timestamp)


@pytest.mark.parametrize("name", ["client_count", "entering_count"])
@pytest.mark.parametrize("count", [-1, 1.5, True])
def test_invalid_counts_are_rejected(name, count):
    with pytest.raises(ValueError):
        FrameObservation(0, True, **{name: count})


@pytest.mark.parametrize("signal", ["cashier_present", "cash_handover", "id_verified"])
def test_nonboolean_signals_are_rejected(signal):
    with pytest.raises(ValueError):
        FrameObservation(0, **{"cashier_present": True, signal: 1})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("multi_client_seconds", -1),
        ("absence_seconds", float("nan")),
        ("cash_left_seconds", float("inf")),
        ("absence_seconds", True),
        ("max_observation_gap_seconds", 0),
        ("max_observation_gap_seconds", -1),
    ],
)
def test_invalid_configuration_is_rejected(name, value):
    with pytest.raises(ValueError):
        RuleEngine(**{name: value})


def test_timestamps_must_increase_without_mutating_state_on_rejection():
    engine = RuleEngine()
    engine.process(frame(1, clients=2))
    with pytest.raises(ValueError):
        engine.process(frame(1))
    with pytest.raises(ValueError):
        engine.process(frame(0))
    assert codes(engine.process(frame(3, clients=2))) == ["multiple_clients"]


def test_observations_and_violations_are_immutable():
    observation = frame(0, foreign_object_on_table=True)
    with pytest.raises(FrozenInstanceError):
        observation.cash_on_table = True
    violation = RuleEngine().process(observation)[0]
    with pytest.raises(FrozenInstanceError):
        violation.timecode_sec = 5
