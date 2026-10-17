"""Evaluate event history without knowing filenames or example-video timestamps."""

from dataclasses import dataclass, field

from app.rules.schemas import RuleReport, RuleRequest, RuleViolation, SemanticEvent, Span


def covers(spans: list[Span], start: float, end: float) -> bool:
    cursor = start
    for span in sorted(spans, key=lambda s: s.start_sec):
        if span.end_sec < cursor:
            continue
        if span.start_sec > cursor + 1e-9:
            return False
        cursor = max(cursor, span.end_sec)
        if cursor >= end:
            return True
    return False


def follows(previous: SemanticEvent, current: SemanticEvent) -> bool:
    return previous.end_sec <= current.start_sec and previous.end_sec < current.end_sec


@dataclass
class MoneyHistory:
    transfer: SemanticEvent | None = None
    counter: list[SemanticEvent] = field(default_factory=list)
    split: list[SemanticEvent] = field(default_factory=list)


@dataclass
class Session:
    session_id: str
    customer_id: str
    entry: SemanticEvent
    history_complete: bool
    id_steps: list[SemanticEvent] = field(default_factory=list)
    money: dict[str, MoneyHistory] = field(default_factory=dict)
    end_sec: float | None = None
    completed: bool = False


def evaluate(request: RuleRequest) -> RuleReport:
    return RuleEngine(request).run()


class RuleEngine:
    def __init__(self, request: RuleRequest):
        self.request = request
        self.cashier_id = None
        self.cashier_state = "unknown"
        self.cashier_evidence = None
        self.customers: dict[str, SemanticEvent] = {}
        self.active: Session | None = None
        self.sessions: list[Session] = []
        self.violations: list[RuleViolation] = []
        self.derived: list[dict] = []
        self.uncertainties: list[dict] = []
        self.alarms = set()
        self.absence_episode = 0
        self.unreliable_id_spans: list[Span] = []
        self.unreliable_people_spans: list[Span] = []

    def people_history(self, start, end):
        return covers(self.request.coverage.people, start, end) and not any(
            span.start_sec < end and span.end_sec > start for span in self.unreliable_people_spans
        )

    def uncertain(self, code, event=None):
        record = {"code": code, "event_id": event.event_id if event else None}
        if record not in self.uncertainties:
            self.uncertainties.append(record)

    def derive(self, kind, event, session):
        self.derived.append(
            {
                "type": kind,
                "timecode_sec": event.end_sec,
                "session_id": session.session_id,
                "customer_id": session.customer_id,
                "trigger_event_id": event.event_id,
            }
        )

    def alarm(self, rule, kind, event, customer, proof, description, key):
        if key in self.alarms:
            return
        self.alarms.add(key)
        self.violations.append(
            RuleViolation(
                rule_id=rule,
                type=kind,
                timecode_sec=event.end_sec,
                customer_id=customer,
                session_id=self.active.session_id if self.active else None,
                description=description,
                evidence_event_ids=list(dict.fromkeys(e.event_id for e in proof if e)),
            )
        )

    def check_absence(self, event):
        if self.cashier_state != "absent":
            return
        for customer, entry in self.customers.items():
            start = min(entry.end_sec, self.cashier_evidence.end_sec)
            if not self.people_history(start, event.end_sec):
                self.uncertain("presence_history_incomplete_for_R01", event)
                continue
            self.alarm(
                "R01",
                "CUSTOMER_WITHOUT_CASHIER",
                event,
                customer,
                [entry, self.cashier_evidence],
                "Клиент находится в кабинете после подтверждённого отсутствия кассира.",
                ("R01", customer, self.absence_episode),
            )

    def presence(self, event):
        kind = event.type
        if kind == "CASHIER_PRESENT":
            if self.cashier_id is not None and self.cashier_id != event.person_id:
                self.uncertain("cashier_identity_changed", event)
                return
            if event.person_id in self.customers:
                self.uncertain("known_customer_cannot_become_cashier", event)
                return
            self.cashier_id = event.person_id
            self.cashier_state = "present"
            self.cashier_evidence = event
        elif kind in {"CASHIER_EXIT", "CASHIER_ABSENT"}:
            if kind == "CASHIER_EXIT" and event.person_id != self.cashier_id:
                self.uncertain("exit_of_unknown_cashier", event)
                return
            if self.cashier_state != "absent":
                self.absence_episode += 1
            self.cashier_state = "absent"
            self.cashier_evidence = event
            self.check_absence(event)
        elif kind in {"CUSTOMER_ENTER", "CUSTOMER_PRESENT"}:
            customer = event.customer_id
            if customer == self.cashier_id:
                self.uncertain("cashier_cannot_become_customer", event)
                return
            if customer in self.customers:
                return  # Repeated presence does not mean another entrance.
            self.customers[customer] = event
            if self.active is not None:
                if kind != "CUSTOMER_ENTER":
                    self.uncertain("second_customer_entry_unobserved", event)
                elif self.people_history(self.active.entry.end_sec, event.end_sec):
                    self.alarm(
                        "R02",
                        "SECOND_CUSTOMER_DURING_SERVICE",
                        event,
                        customer,
                        [self.active.entry, event],
                        "Другой клиент вошёл до подтверждённого выхода обслуживаемого клиента.",
                        ("R02", self.active.session_id, event.event_id),
                    )
                else:
                    self.uncertain("current_customer_exit_unobservable", event)
            elif self.cashier_state == "present":
                self.active = Session(
                    f"service-{len(self.sessions) + 1}", customer, event, kind == "CUSTOMER_ENTER"
                )
                self.sessions.append(self.active)
                self.derive("SERVICE_STARTED", event, self.active)
                if kind == "CUSTOMER_PRESENT":
                    self.uncertain("service_started_before_observation", event)
            else:
                self.uncertain("service_start_unobserved", event)
            self.check_absence(event)
        elif kind == "CUSTOMER_EXIT":
            if event.customer_id not in self.customers:
                self.uncertain("exit_of_unknown_customer", event)
                return
            del self.customers[event.customer_id]
            if self.active and self.active.customer_id == event.customer_id:
                self.active.end_sec = event.end_sec
                self.derive("SERVICE_ENDED", event, self.active)
                self.active = None

    def actions(self, event):
        session = self.active
        if session is None or session.customer_id != event.customer_id:
            self.uncertain("action_outside_current_service", event)
            return
        if self.cashier_state != "present":
            self.uncertain("cashier_not_confirmed_for_action", event)
            return
        if event.start_sec < session.entry.end_sec:
            self.uncertain("action_overlaps_service_start", event)
            return
        if event.type.startswith("ID_"):
            self.id_action(session, event)
        else:
            self.money_action(session, event)

    def id_action(self, session, event):
        steps = session.id_steps
        if event.type == "ID_TRANSFER_TO_CASHIER":
            if not steps or steps[0].object_id != event.object_id:
                session.id_steps = [event]
                session.completed = False
            return
        expected = {"ID_CHECK": 1, "ID_ON_TABLE": 2}[event.type]
        if (
            len(steps) == expected
            and steps[0].object_id == event.object_id
            and follows(steps[-1], event)
        ):
            steps.append(event)
        elif not any(s.type == event.type and s.object_id == event.object_id for s in steps):
            self.uncertain("id_sequence_or_identity_unconfirmed", event)
            if steps and (steps[0].object_id != event.object_id or not follows(steps[-1], event)):
                self.unreliable_id_spans.append(event)

    def money_action(self, session, event):
        money = session.money.setdefault(event.money_group_id, MoneyHistory())
        kind = event.type
        if kind == "MONEY_TRANSFER_TO_CASHIER":
            id_ready = len(session.id_steps) == 3 and follows(session.id_steps[-1], event)
            if not id_ready:
                reliable = session.history_complete and covers(
                    self.request.coverage.id_actions, session.entry.end_sec, event.end_sec
                )
                reliable = reliable and not any(
                    s.start_sec < event.end_sec and s.end_sec > session.entry.end_sec
                    for s in self.unreliable_id_spans
                )
                if len(session.id_steps) == 3:
                    # Overlapping action intervals do not establish their order.
                    reliable = False
                if reliable:
                    self.alarm(
                        "R03",
                        "MONEY_BEFORE_ID",
                        event,
                        session.customer_id,
                        [session.entry, *session.id_steps, event],
                        "Деньги приняты до завершения передачи ID, проверки "
                        "и помещения ID на стол.",
                        ("R03", session.session_id, event.money_group_id),
                    )
                else:
                    self.uncertain("id_history_incomplete_before_money", event)
            money.transfer = event
            session.completed = False
            # A new deposit cannot inherit an earlier counter cycle.
            money.counter, money.split = [], []
        elif kind == "MONEY_TO_COUNTER":
            money.counter, money.split = [event], []
        elif kind in {"MONEY_FROM_COUNTER", "MONEY_HELD_BY_CASHIER"}:
            expected = 1 if kind == "MONEY_FROM_COUNTER" else 2
            if len(money.counter) == expected and follows(money.counter[-1], event):
                money.counter.append(event)
            elif not any(s.type == kind for s in money.counter):
                self.uncertain("counter_sequence_or_money_identity_unconfirmed", event)
            if (
                kind == "MONEY_HELD_BY_CASHIER"
                and len(money.counter) == 3
                and money.transfer
                and follows(money.transfer, money.counter[0])
                and len(session.id_steps) == 3
                and follows(session.id_steps[-1], money.transfer)
            ):
                if not session.completed:
                    session.completed = True
                    self.derive("SCENARIO_COMPLETED", event, session)
        elif kind == "MONEY_STACK_SPLIT":
            if len(money.counter) == 3 and follows(money.counter[-1], event):
                money.split.append(event)
        elif kind == "MONEY_TRANSFER_TO_CUSTOMER":
            if len(money.counter) == 3 and follows(money.counter[-1], event):
                self.alarm(
                    "R04",
                    "MONEY_RETURNED_TO_CUSTOMER_AFTER_COUNTER",
                    event,
                    session.customer_id,
                    [*money.counter, *money.split, event],
                    "Деньги после счётчика находились у кассира и затем переданы текущему клиенту.",
                    ("R04", session.session_id, event.money_group_id),
                )
                # Repeated proximity must not create repeated returns.
                money.counter, money.split = [], []
            else:
                self.uncertain("return_without_confirmed_counter_history", event)

    def run(self):
        # Low-confidence ID events invalidate absence-based conclusions, even if
        # their confirmation timestamp follows the money transfer timestamp.
        self.unreliable_id_spans = [
            e
            for e in self.request.events
            if e.type.startswith("ID_") and e.confidence < self.request.minimum_confidence
        ]
        self.unreliable_people_spans = [
            e
            for e in self.request.events
            if e.type.startswith(("CASHIER_", "CUSTOMER_"))
            and e.confidence < self.request.minimum_confidence
        ]
        # Overlapping entrance/exit intervals cannot establish who crossed first.
        # Keep this ambiguity independent of the JSON order when end times tie.
        boundaries = sorted(
            (e for e in self.request.events if e.type in {"CUSTOMER_ENTER", "CUSTOMER_EXIT"}),
            key=lambda e: e.start_sec,
        )
        latest = {}
        for event in boundaries:
            opposite = "CUSTOMER_EXIT" if event.type == "CUSTOMER_ENTER" else "CUSTOMER_ENTER"
            previous = latest.get(opposite)
            if previous and previous.end_sec > event.start_sec:
                for ambiguous in (previous, event):
                    self.unreliable_people_spans.append(ambiguous)
                    self.uncertain("entrance_exit_order_unconfirmed", ambiguous)
            if event.type not in latest or event.end_sec > latest[event.type].end_sec:
                latest[event.type] = event
        for event in self.request.events:
            if event.confidence < self.request.minimum_confidence:
                self.uncertain("low_confidence_event_ignored", event)
                continue
            if event.type.startswith(("CASHIER_", "CUSTOMER_")):
                self.presence(event)
            else:
                self.actions(event)
        for channel in ("people", "id_actions", "money_actions"):
            if not covers(getattr(self.request.coverage, channel), 0, self.request.duration_sec):
                self.uncertain(f"incomplete_{channel}_coverage")
        if self.cashier_state == "unknown":
            self.uncertain("cashier_role_unresolved")
        for session in self.sessions:
            if not session.completed:
                self.uncertain("scenario_not_completed", session.entry)
        verdict = (
            "violation"
            if self.violations
            else ("insufficient_evidence" if self.uncertainties else "no_violation")
        )
        return RuleReport(
            input_sources=sorted({e.source for e in self.request.events}),
            verdict=verdict,
            violations=self.violations,
            derived_events=self.derived,
            uncertainties=self.uncertainties,
            sessions=[
                {
                    "session_id": s.session_id,
                    "customer_id": s.customer_id,
                    "start_sec": s.entry.end_sec,
                    "end_sec": s.end_sec,
                    "id_completed": len(s.id_steps) == 3,
                    "scenario_completed": s.completed,
                    "history_complete": s.history_complete,
                }
                for s in self.sessions
            ],
        )
