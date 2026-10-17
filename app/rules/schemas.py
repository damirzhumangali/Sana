from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

EventType = Literal[
    "CASHIER_PRESENT",
    "CASHIER_EXIT",
    "CASHIER_ABSENT",
    "CUSTOMER_ENTER",
    "CUSTOMER_PRESENT",
    "CUSTOMER_EXIT",
    "ID_TRANSFER_TO_CASHIER",
    "ID_CHECK",
    "ID_ON_TABLE",
    "MONEY_TRANSFER_TO_CASHIER",
    "MONEY_TO_COUNTER",
    "MONEY_FROM_COUNTER",
    "MONEY_HELD_BY_CASHIER",
    "MONEY_TRANSFER_TO_CUSTOMER",
    "MONEY_STACK_SPLIT",
]
RuleType = Literal[
    "CUSTOMER_WITHOUT_CASHIER",
    "SECOND_CUSTOMER_DURING_SERVICE",
    "MONEY_BEFORE_ID",
    "MONEY_RETURNED_TO_CUSTOMER_AFTER_COUNTER",
]


class Span(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_sec: float = Field(ge=0, allow_inf_nan=False)
    end_sec: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end_sec <= self.start_sec:
            raise ValueError("A temporal observation must span more than one instant")
        return self


class SemanticEvent(Span):
    event_id: str = Field(min_length=1, max_length=100)
    type: EventType
    source: Literal["manual_annotation", "local_tracker", "synthetic_example"]
    sample_count: int = Field(ge=2)
    confidence: float = Field(default=1.0, ge=0, le=1, allow_inf_nan=False)
    person_id: str | None = Field(default=None, min_length=1)
    customer_id: str | None = Field(default=None, min_length=1)
    object_id: str | None = Field(default=None, min_length=1)
    money_group_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def identifiers(self) -> Self:
        if self.type in {"CASHIER_PRESENT", "CASHIER_EXIT"} and not self.person_id:
            raise ValueError("Cashier events require a persistent person_id")
        if self.type.startswith(("CUSTOMER_", "ID_", "MONEY_")) and not self.customer_id:
            raise ValueError("Customer and action events require customer_id")
        if self.type.startswith(("ID_", "MONEY_")) and not self.object_id:
            raise ValueError("Object actions require a persistent object_id")
        if self.type.startswith("MONEY_") and not self.money_group_id:
            raise ValueError("Money actions require a confirmed common money_group_id")
        return self


class Coverage(BaseModel):
    """Intervals where the producer can reliably observe each action channel.

    A successfully decoded frame or a running detector does not establish coverage.
    Occlusion, missed actions and uncertain track associations leave gaps here.
    """

    model_config = ConfigDict(extra="forbid")
    people: list[Span] = Field(default_factory=list)
    id_actions: list[Span] = Field(default_factory=list)
    money_actions: list[Span] = Field(default_factory=list)


class RuleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    duration_sec: float = Field(gt=0, allow_inf_nan=False)
    events: list[SemanticEvent] = Field(default_factory=list, max_length=20000)
    coverage: Coverage = Field(default_factory=Coverage)
    minimum_confidence: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def timeline(self) -> Self:
        ids = [event.event_id for event in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("event_id must be unique; remove replayed events")
        if any(event.end_sec > self.duration_sec for event in self.events):
            raise ValueError("Event outside video duration")
        if any(a.end_sec > b.end_sec for a, b in zip(self.events, self.events[1:], strict=False)):
            raise ValueError("Events must be ordered by end_sec")
        for channel in ("people", "id_actions", "money_actions"):
            if any(span.end_sec > self.duration_sec for span in getattr(self.coverage, channel)):
                raise ValueError("Coverage outside video duration")
        return self


class RuleViolation(BaseModel):
    rule_id: Literal["R01", "R02", "R03", "R04"]
    type: RuleType
    timecode_sec: float
    customer_id: str
    session_id: str | None
    description: str
    evidence_event_ids: list[str]


class RuleReport(BaseModel):
    scope: str = "User-specified R01-R04; confirmed semantic events, not raw video inference"
    input_sources: list[str]
    verdict: Literal["violation", "no_violation", "insufficient_evidence"]
    violations: list[RuleViolation]
    sessions: list[dict]
    derived_events: list[dict]
    uncertainties: list[dict]
