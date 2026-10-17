"""Temporal cashier rules over confirmed observations, independent of video models.

``None`` means that a semantic fact is unknown, never that the fact is false.
For example, detecting an ID-shaped rectangle cannot establish ``id_verified``.
The caller must provide verified action/state signals for the corresponding rules.

Stable violation codes are ``multiple_clients``, ``client_not_ready``,
``unattended_laptop``, ``unattended_cash``, ``unattended_safe``, ``unattended_key``,
``cash_before_id``, ``cash_bypassed_counter``, ``client_left_during_counting``,
``cash_returned_after_counting``, ``cash_left_on_table`` and
``foreign_object_on_table``. Evidence timestamps identify the observation that
confirms a violation, not a historical frame where the condition was not yet met.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from numbers import Real


class OperationPhase(StrEnum):
    PREPARATION = "preparation"
    CLIENT_ENTRY = "client_entry"
    IDENTIFICATION = "identification"
    CASH_OPERATION = "cash_operation"
    CLIENT_DEPARTURE = "client_departure"


_SEMANTIC_SIGNALS = (
    "id_verified",
    "cash_handover",
    "counter_active",
    "cash_on_table",
    "cash_stored_in_safe",
    "cash_returned_to_client",
    "laptop_open",
    "safe_open",
    "key_in_safe",
    "foreign_object_on_table",
)


@dataclass(frozen=True)
class FrameObservation:
    """Confirmed facts at a video timestamp, measured in seconds.

    ``client_count`` and ``entering_count`` are disjoint occupancy zones. A person
    in the doorway may be leaving; doorway presence alone is not a new entrance.
    ``cash_handover`` specifically means accepting cash from the client. Signals
    may be persistent states: a run of ``True`` values is one confirmed action.
    """

    timestamp_sec: float
    cashier_present: bool
    client_count: int = 0
    entering_count: int = 0
    id_verified: bool | None = None
    cash_handover: bool | None = None
    counter_active: bool | None = None
    cash_on_table: bool | None = None
    cash_stored_in_safe: bool | None = None
    cash_returned_to_client: bool | None = None
    laptop_open: bool | None = None
    safe_open: bool | None = None
    key_in_safe: bool | None = None
    foreign_object_on_table: bool | None = None

    def __post_init__(self) -> None:
        _validate_seconds(self.timestamp_sec, "timestamp_sec", allow_zero=True)
        if not isinstance(self.cashier_present, bool):
            raise ValueError("cashier_present must be a boolean")
        for name in ("client_count", "entering_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        for name in _SEMANTIC_SIGNALS:
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be a boolean or None")


@dataclass(frozen=True)
class RuleViolation:
    code: str
    type: str
    timecode_sec: float
    description: str


@dataclass
class _Operation:
    known_start: bool
    client_seen: bool = False
    id_verified: bool = False
    cash_accepted: bool = False
    counter_seen: bool = False
    accepted_counter_seen: bool = False
    count_completed: bool = False
    counter_absence_confirmed: bool = False
    safe_storage_seen: bool = False
    exiting: bool = False
    counter_active_at_departure: bool = False
    cash_transaction_closed: bool = False
    reported: set[str] = field(default_factory=set)


def _validate_seconds(value: float, name: str, *, allow_zero: bool) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not isfinite(value)
        or value < 0
        or (not allow_zero and value == 0)
    ):
        qualifier = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{name} must be a finite {qualifier} number")


class RuleEngine:
    """Process strictly increasing observations from one video.

    Duration rules require continuous support with gaps no greater than
    ``max_observation_gap_seconds``. Conditions must last *longer* than their
    configured thresholds. An initial occupied room is a partial operation: the
    engine cannot establish whether ID verification or counting happened earlier.

    Cash bypass requires observed acceptance and explicit ``counter_active=False``
    throughout acceptance-to-storage. Returned cash requires observed acceptance
    followed by a confirmed completed count. Neither rule infers unseen history.
    A brief empty-zone flicker (at most 0.5 seconds) resumes the same operation;
    an actual doorway exit is never treated as a new entrance.
    """

    _RETURN_GRACE_SECONDS = 0.5

    def __init__(
        self,
        *,
        multi_client_seconds: float = 1.5,
        absence_seconds: float = 4.0,
        cash_left_seconds: float = 5.0,
        max_observation_gap_seconds: float = 2.5,
    ) -> None:
        for name, value in (
            ("multi_client_seconds", multi_client_seconds),
            ("absence_seconds", absence_seconds),
            ("cash_left_seconds", cash_left_seconds),
        ):
            _validate_seconds(value, name, allow_zero=True)
        _validate_seconds(
            max_observation_gap_seconds, "max_observation_gap_seconds", allow_zero=False
        )
        self.multi_client_seconds = float(multi_client_seconds)
        self.absence_seconds = float(absence_seconds)
        self.cash_left_seconds = float(cash_left_seconds)
        self.max_observation_gap_seconds = float(max_observation_gap_seconds)
        self.phase = OperationPhase.PREPARATION
        self._last: FrameObservation | None = None
        self._operation: _Operation | None = None
        self._departed_operation: _Operation | None = None
        self._empty_since: float | None = None
        self._departure_at: float | None = None
        self._cash_left_since: float | None = None
        self._cash_left_reported = False
        self._multi_since: float | None = None
        self._multi_reported = False
        self._cashier_seen = False
        self._absence_since: float | None = None
        self._absence_reported: set[str] = set()
        self._foreign_object_reported = False

    def process(self, observation: FrameObservation) -> list[RuleViolation]:
        if not isinstance(observation, FrameObservation):
            raise TypeError("observation must be a FrameObservation")
        previous = self._last
        timestamp = observation.timestamp_sec
        if previous is not None and timestamp <= previous.timestamp_sec:
            raise ValueError("Observation timestamps must be strictly increasing")
        gap = (
            previous is not None
            and timestamp - previous.timestamp_sec > self.max_observation_gap_seconds
        )
        violations: list[RuleViolation] = []
        if gap:
            self._break_continuity(observation)

        self._check_occupancy(observation, violations)
        self._check_absence(observation, violations)
        self._check_foreign_object(observation, violations)
        self._update_operation(observation, None if gap else previous, violations)
        self._check_cash_left(observation, violations)
        self._last = observation
        return violations

    @staticmethod
    def _elapsed(start: float, current: float, threshold: float) -> bool:
        # Floating point rounding must not turn an exact threshold into a breach.
        return current - start > threshold + 1e-9

    @staticmethod
    def _total(observation: FrameObservation) -> int:
        return observation.client_count + observation.entering_count

    @staticmethod
    def _emit(
        violations: list[RuleViolation],
        observation: FrameObservation,
        code: str,
        violation_type: str,
        description: str,
    ) -> None:
        violations.append(
            RuleViolation(code, violation_type, float(observation.timestamp_sec), description)
        )

    def _break_continuity(self, observation: FrameObservation) -> None:
        self._multi_since = None
        self._absence_since = None
        self._cash_left_since = None
        if self._total(observation) > 0:
            self._departure_at = None
        self._departed_operation = None
        self._operation = None
        self._empty_since = None
        self.phase = OperationPhase.PREPARATION
        # Deduplication survives an unknown gap; only a confirmed end of a hazard
        # establishes a new episode. Transaction history, however, is incomplete.
        if self._total(observation) < 2:
            self._multi_reported = False

    def _check_occupancy(
        self, observation: FrameObservation, violations: list[RuleViolation]
    ) -> None:
        timestamp = observation.timestamp_sec
        if self._total(observation) < 2:
            self._multi_since = None
            self._multi_reported = False
            return
        if self._multi_since is None:
            self._multi_since = timestamp
        if not self._multi_reported and self._elapsed(
            self._multi_since, timestamp, self.multi_client_seconds
        ):
            self._emit(
                violations,
                observation,
                "multiple_clients",
                "Постороннее лицо / нарушение очередности",
                "Два или более клиента одновременно находятся в клиентской зоне "
                f"и у двери более {self.multi_client_seconds:g} с "
                f"(наблюдаются с {self._multi_since:g} с).",
            )
            self._multi_reported = True

    def _check_absence(
        self, observation: FrameObservation, violations: list[RuleViolation]
    ) -> None:
        if observation.cashier_present:
            self._cashier_seen = True
            self._absence_since = None
            self._absence_reported.clear()
            return
        if not self._cashier_seen:
            return
        if self._absence_since is None:
            self._absence_since = observation.timestamp_sec
        if not self._elapsed(self._absence_since, observation.timestamp_sec, self.absence_seconds):
            return
        for signal, code, label, detail in (
            ("laptop_open", "unattended_laptop", "Открытый ноутбук без присмотра", "ноутбук"),
            ("cash_on_table", "unattended_cash", "Наличные без присмотра", "наличные на столе"),
            ("safe_open", "unattended_safe", "Открытый сейф без присмотра", "открытый сейф"),
            ("key_in_safe", "unattended_key", "Ключ в сейфе без присмотра", "ключ в замке сейфа"),
        ):
            if getattr(observation, signal) is True and code not in self._absence_reported:
                self._emit(
                    violations,
                    observation,
                    code,
                    label,
                    f"Кассир отсутствует более {self.absence_seconds:g} с "
                    f"(с {self._absence_since:g} с); подтверждены {detail}.",
                )
                self._absence_reported.add(code)

    def _check_foreign_object(
        self, observation: FrameObservation, violations: list[RuleViolation]
    ) -> None:
        if observation.foreign_object_on_table is False:
            self._foreign_object_reported = False
        elif observation.foreign_object_on_table is True and not self._foreign_object_reported:
            self._emit(
                violations,
                observation,
                "foreign_object_on_table",
                "Посторонний предмет на рабочем столе",
                "Подтверждён посторонний предмет на рабочем столе кассира.",
            )
            self._foreign_object_reported = True

    def _update_operation(
        self,
        observation: FrameObservation,
        previous: FrameObservation | None,
        violations: list[RuleViolation],
    ) -> None:
        timestamp = observation.timestamp_sec
        total = self._total(observation)
        previous_total = self._total(previous) if previous is not None else None
        if total == 0:
            if self._empty_since is None:
                self._empty_since = timestamp
            if self._operation is not None:
                operation = self._operation
                if not operation.exiting:
                    operation.counter_active_at_departure = observation.counter_active is True
                self._departed_operation = operation
                if operation.client_seen:
                    self._departure_at = timestamp
                    self._cash_left_reported = False
                self._operation = None
                self.phase = OperationPhase.CLIENT_DEPARTURE
            else:
                self.phase = OperationPhase.PREPARATION
            if self._departed_operation is not None:
                # A single missing detection cannot prove a client's departure.
                # An observed desk-to-door transition is handled immediately below.
                if self._departed_operation.counter_active_at_departure and self._elapsed(
                    self._empty_since, timestamp, self._RETURN_GRACE_SECONDS
                ):
                    self._check_counting_departure(
                        self._departed_operation, observation, violations
                    )
                if observation.counter_active is False:
                    self._departed_operation.counter_active_at_departure = False
                if (
                    self._departed_operation.cash_accepted
                    and not self._departed_operation.cash_transaction_closed
                ):
                    self._check_cash_actions(
                        self._departed_operation, observation, previous, violations
                    )
                if observation.cash_on_table is False and observation.counter_active is False:
                    self._departed_operation.cash_transaction_closed = True
            return

        resumed = (
            self._operation is None
            and self._departed_operation is not None
            and self._empty_since is not None
            and timestamp - self._empty_since <= self._RETURN_GRACE_SECONDS
        )
        if self._operation is None:
            if resumed:
                self._operation = self._departed_operation
            else:
                self._operation = _Operation(known_start=previous_total == 0)
            self._departed_operation = None
            self._departure_at = None
            self._cash_left_since = None
            self._cash_left_reported = False
        self._empty_since = None
        operation = self._operation
        assert operation is not None

        new_entrance = (
            previous is not None
            and not resumed
            and total > self._total(previous)
            and observation.entering_count > previous.entering_count
        )
        if new_entrance and not observation.cashier_present:
            self._operation_violation(
                operation,
                observation,
                violations,
                "client_not_ready",
                "Вход клиента при отсутствии кассира",
                "Наблюдается новый вход через дверь, когда кассир отсутствует на рабочем месте.",
            )

        doorway_exit = (
            operation.client_seen
            and observation.client_count == 0
            and observation.entering_count > 0
        )
        if doorway_exit:
            if not operation.exiting:
                operation.counter_active_at_departure = observation.counter_active is True
            operation.exiting = True
            if operation.counter_active_at_departure:
                self._check_counting_departure(operation, observation, violations)
            if observation.counter_active is False:
                operation.counter_active_at_departure = False
        elif observation.client_count > 0:
            operation.client_seen = True
            operation.exiting = False
            operation.counter_active_at_departure = False

        self._check_cash_actions(operation, observation, previous, violations)
        if operation.exiting or operation.safe_storage_seen:
            self.phase = OperationPhase.CLIENT_DEPARTURE
        elif operation.id_verified or operation.cash_accepted or operation.counter_seen:
            self.phase = OperationPhase.CASH_OPERATION
        elif observation.client_count > 0:
            self.phase = OperationPhase.IDENTIFICATION
        else:
            self.phase = OperationPhase.CLIENT_ENTRY

    def _operation_violation(
        self,
        operation: _Operation,
        observation: FrameObservation,
        violations: list[RuleViolation],
        code: str,
        label: str,
        description: str,
    ) -> None:
        if code not in operation.reported:
            self._emit(violations, observation, code, label, description)
            operation.reported.add(code)

    def _check_counting_departure(
        self,
        operation: _Operation,
        observation: FrameObservation,
        violations: list[RuleViolation],
    ) -> None:
        if operation.client_seen and observation.counter_active is True:
            self._operation_violation(
                operation,
                observation,
                violations,
                "client_left_during_counting",
                "Уход клиента во время пересчёта",
                "Клиент покидает клиентскую зону при подтверждённой работе счётчика банкнот.",
            )

    def _check_cash_actions(
        self,
        operation: _Operation,
        observation: FrameObservation,
        previous: FrameObservation | None,
        violations: list[RuleViolation],
    ) -> None:
        handover_started = observation.cash_handover is True and (
            previous is None or previous.cash_handover is not True
        )
        counter_started = observation.counter_active is True and (
            previous is None or previous.counter_active is not True
        )
        storage_started = observation.cash_stored_in_safe is True and (
            previous is None or previous.cash_stored_in_safe is not True
        )
        return_started = observation.cash_returned_to_client is True and (
            previous is None or previous.cash_returned_to_client is not True
        )
        if (
            operation.known_start
            and not operation.id_verified
            and observation.id_verified is False
            and (handover_started or counter_started)
        ):
            self._operation_violation(
                operation,
                observation,
                violations,
                "cash_before_id",
                "Приём наличных до проверки личности",
                "Подтверждено начало приёма или пересчёта наличных до проверки ID "
                "в операции, начало которой наблюдалось.",
            )
        if observation.id_verified is True:
            operation.id_verified = True

        observed_acceptance_start = handover_started and (
            operation.known_start or (previous is not None and previous.cash_handover is False)
        )
        if observed_acceptance_start and not operation.cash_accepted:
            operation.cash_accepted = True
            operation.counter_absence_confirmed = observation.counter_active is False
        if operation.cash_accepted:
            if observation.counter_active is not False:
                operation.counter_absence_confirmed = False
            if observation.counter_active is True:
                operation.counter_seen = True
                operation.accepted_counter_seen = True
            elif observation.counter_active is False and operation.accepted_counter_seen:
                operation.count_completed = True
        elif observation.counter_active is True:
            operation.counter_seen = True

        if storage_started:
            if (
                operation.cash_accepted
                and not operation.accepted_counter_seen
                and operation.counter_absence_confirmed
            ):
                self._operation_violation(
                    operation,
                    observation,
                    violations,
                    "cash_bypassed_counter",
                    "Наличные помещены в сейф без пересчёта",
                    "После наблюдаемого приёма наличных подтверждено помещение их в сейф; "
                    "на всём наблюдаемом промежутке счётчик явно не работал.",
                )
            operation.safe_storage_seen = True
        if return_started and operation.cash_accepted and operation.count_completed:
            self._operation_violation(
                operation,
                observation,
                violations,
                "cash_returned_after_counting",
                "Возврат принятых наличных после пересчёта",
                "В наблюдаемой операции приёма наличных подтверждён возврат клиенту "
                "после завершённого пересчёта.",
            )

    def _check_cash_left(
        self, observation: FrameObservation, violations: list[RuleViolation]
    ) -> None:
        if self._total(observation) == 0 and observation.cash_on_table is False:
            # Once the desk is confirmed clear, later cashier-only cash work is a
            # different activity and cannot be attributed to the departed client.
            self._departure_at = None
        if (
            self._departure_at is None
            or self._total(observation) > 0
            or observation.cash_on_table is not True
        ):
            self._cash_left_since = None
            return
        if self._cash_left_since is None:
            self._cash_left_since = observation.timestamp_sec
        if not self._cash_left_reported and self._elapsed(
            self._cash_left_since, observation.timestamp_sec, self.cash_left_seconds
        ):
            self._emit(
                violations,
                observation,
                "cash_left_on_table",
                "Наличные оставлены после ухода клиента",
                f"После наблюдаемого ухода клиента наличные остаются на столе "
                f"более {self.cash_left_seconds:g} с "
                f"(подтверждены с {self._cash_left_since:g} с).",
            )
            self._cash_left_reported = True
