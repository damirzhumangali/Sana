"""Detect observable transitions; all violation decisions stay in app.rules."""

from dataclasses import dataclass, field
from statistics import mean

from app.events.config import EventConfig
from app.events.observations import suppress_object_duplicates
from app.events.schemas import FrameObservation
from app.events.tracking import LogicalObjectTracker, area, center, distance
from app.rules.engine import evaluate
from app.rules.schemas import Coverage, RuleRequest, SemanticEvent, Span
from app.services.person_roles import point_in_polygon


@dataclass
class PersonState:
    role: str
    first_seen: float
    last_seen: float
    samples: list = field(default_factory=list)
    inside: bool = False
    missing: list = field(default_factory=list)
    last_point: tuple = (0, 0)
    outside_seen: bool = False


@dataclass
class ObjectState:
    session: str | None
    group_id: str
    stable: str | None = None
    stable_samples: list = field(default_factory=list)
    candidate: str | None = None
    samples: list = field(default_factory=list)
    last_seen: float = -1
    pending_hold: list = field(default_factory=list)
    from_counter_at: float | None = None
    held_emitted: bool = False
    id_transferred: bool = False
    id_checked: bool = False
    parent_candidate: str | None = None
    origin: dict | None = None
    linked: bool = False
    counter_confirmed: bool = False
    anchor: str | None = None
    anchor_samples: list = field(default_factory=list)


def event_score(samples, required, spatial=1.0, reconnects=0):
    detection = mean(s["confidence"] for s in samples)
    count = min(1.0, len(samples) / required)
    stability = max(0.4, 1 - 0.1 * reconnects)
    return round(min(1.0, 0.45 * detection + 0.2 * count + 0.15 * stability + 0.2 * spatial), 4)


class EventDetector:
    def __init__(self, config: EventConfig, source="local_tracker"):
        if source not in {"local_tracker", "synthetic_example"}:
            raise ValueError("Invalid event source")
        self.config, self.source = config, source
        self.tracker = LogicalObjectTracker(config)
        self.people: dict[str, PersonState] = {}
        self.objects: dict[str, ObjectState] = {}
        self.events: list[SemanticEvent] = []
        self.debug_events: list[dict] = []
        self.history: list[dict] = []
        self.lineage_links: list[dict] = []
        self.coverage = Coverage()
        self.previous_frame = None
        self.timestamp = -1.0
        self.first_timestamp = None
        self.active_customer = None
        self.active_session = None
        self.cashier_id = None
        self.warnings: set[str] = set()

    def in_zone(self, point, zone):
        return point_in_polygon(point, self.config.zones[zone])

    def emit(self, kind, samples, *, person=None, customer=None, obj=None, score=None, **debug):
        start, end = samples[0]["timestamp_sec"], samples[-1]["timestamp_sec"]
        if len(samples) < 2 or end <= start:
            return None
        confidence = (
            score if score is not None else event_score(samples, self.config.person_min_frames)
        )
        event = SemanticEvent(
            event_id=f"event-{len(self.events) + 1}",
            type=kind,
            start_sec=start,
            end_sec=end,
            source=self.source,
            sample_count=len(samples),
            confidence=confidence,
            person_id=person,
            customer_id=customer,
            object_id=obj["logical_object_id"] if obj else None,
            money_group_id=self.objects[obj["logical_object_id"]].group_id
            if obj and obj["label"] == "cash"
            else None,
        )
        self.events.append(event)
        self.debug_events.append(
            {
                **event.model_dump(),
                "timestamp_sec": end,
                "accepted": confidence >= self.config.minimum_event_confidence,
                "detector_track_id": obj["track_id"] if obj else None,
                **debug,
            }
        )
        return event

    def stable_enough(self, samples, minimum_sec, minimum_frames):
        return len(samples) >= minimum_frames and (
            samples[-1]["timestamp_sec"] - samples[0]["timestamp_sec"] >= minimum_sec - 1e-8
        )

    def person_events(self, frame):
        cfg, t = self.config, frame.timestamp_sec
        visible = {
            p.track_id: p
            for p in frame.people
            if p.identity_reliable
            and p.role != "unknown"
            and p.confidence >= cfg.minimum_detection_confidence
        }
        for key, person in visible.items():
            point = center(person.bbox)
            state = self.people.setdefault(key, PersonState(person.role, t, t))
            if state.role != person.role:
                self.warnings.add("person_role_changed")
                continue
            zone = "room" if person.role == "cashier" else "customer"
            inside = self.in_zone(point, zone)
            if t - state.last_seen > cfg.max_observation_gap_sec:
                state.samples = []
            if inside or state.inside and self.in_zone(point, "room"):
                state.last_seen, state.last_point = t, point
                state.missing = []
                state.samples.append({"timestamp_sec": t, "confidence": person.confidence})
                if (
                    not state.inside
                    and inside
                    and self.stable_enough(state.samples, cfg.person_min_sec, cfg.person_min_frames)
                ):
                    # A new ID while the old client is missing can be a broken track.
                    if (
                        person.role == "customer"
                        and self.active_customer
                        and self.active_customer not in visible
                    ):
                        self.warnings.add("possible_person_track_fragment")
                        continue
                    initial = state.first_seen == self.first_timestamp and not state.outside_seen
                    kind = (
                        "CASHIER_PRESENT"
                        if person.role == "cashier"
                        else ("CUSTOMER_PRESENT" if initial else "CUSTOMER_ENTER")
                    )
                    emitted = self.emit(
                        kind,
                        state.samples,
                        person=key if person.role == "cashier" else None,
                        customer=key if person.role == "customer" else None,
                    )
                    state.inside = True
                    if (
                        person.role == "customer"
                        and self.active_customer
                        and key != self.active_customer
                        and emitted
                    ):
                        self.debug_events.append(
                            {
                                "type": "SECOND_CUSTOMER_ENTER",
                                "timestamp_sec": t,
                                "confidence": emitted.confidence,
                                "customer_id": key,
                                "trigger_event_id": emitted.event_id,
                                "accepted": emitted.confidence >= cfg.minimum_event_confidence,
                            }
                        )
            elif not state.inside:
                state.outside_seen = True
                state.samples = []
                state.last_seen, state.last_point = t, point

        for key, state in self.people.items():
            person = visible.get(key)
            if not state.inside or person and self.in_zone(center(person.bbox), "room"):
                continue
            if not frame.people_observable:
                state.missing = []
                continue
            if (
                state.missing
                and t - state.missing[-1]["timestamp_sec"] > cfg.max_observation_gap_sec
            ):
                state.missing = []
            prior_conf = state.samples[-1]["confidence"] if state.samples else 0.5
            state.missing.append({"timestamp_sec": t, "confidence": prior_conf})
            boundary = self.in_zone(state.last_point, "exit") or (
                person is not None and not self.in_zone(center(person.bbox), "room")
            )
            if self.stable_enough(state.missing, cfg.exit_min_sec, cfg.exit_min_frames) and (
                boundary or not cfg.exit_requires_boundary
            ):
                self.emit(
                    "CASHIER_EXIT" if state.role == "cashier" else "CUSTOMER_EXIT",
                    state.missing,
                    person=key if state.role == "cashier" else None,
                    customer=key if state.role == "customer" else None,
                    exit_basis="boundary_and_sustained_absence"
                    if boundary
                    else "sustained_absence",
                )
                state.inside, state.samples, state.missing = False, [], []

    def synchronize_service(self):
        report = evaluate(self.rule_request(max(self.timestamp, 0.001)))
        active = next((s for s in reversed(report.sessions) if s["end_sec"] is None), None)
        self.active_session = active["session_id"] if active else None
        self.active_customer = active["customer_id"] if active else None
        cashier = [
            e
            for e in self.events
            if e.type == "CASHIER_PRESENT" and e.confidence >= self.config.minimum_event_confidence
        ]
        self.cashier_id = cashier[-1].person_id if cashier else None
        return report

    def association(self, obs, frame):
        point, cfg = obs["center"], self.config
        distances = [
            (distance(point, center(p.bbox)), p)
            for p in frame.people
            if p.identity_reliable and p.role != "unknown"
        ]
        distances.sort(key=lambda pair: pair[0])
        nearest = distances[0][1] if distances else None
        by_id = {p.track_id: d for d, p in distances}
        zone = next(
            (
                z
                for z in ("counter", "table", "cashier", "customer", "room")
                if self.in_zone(point, z)
            ),
            "outside",
        )
        owner, quality = "unknown", 0.0
        if zone == "counter" and obs["label"] == "cash":
            owner, quality = "counter", 1.0
        elif distances:
            d = distances[0][0]
            margin = distances[1][0] - d if len(distances) > 1 else cfg.association_radius
            if d <= cfg.association_radius and margin >= cfg.association_margin:
                if nearest.track_id == self.cashier_id:
                    owner = "cashier"
                elif nearest.track_id == self.active_customer:
                    owner = "customer"
                quality = max(0, 1 - d / (2 * cfg.association_radius))
                if owner in {"cashier", "customer"} and self.in_zone(point, owner):
                    quality = min(1.0, quality + 0.1)
        if owner == "unknown" and zone == "table":
            owner, quality = "table", 0.8
        return {
            "association": owner,
            "spatial_quality": quality,
            "zone": zone,
            "nearest_person_track_id": nearest.track_id if nearest else None,
            "nearest_person_role": nearest.role if nearest else None,
            "distance_to_cashier": by_id.get(self.cashier_id),
            "distance_to_current_customer": by_id.get(self.active_customer),
        }

    def consider_part_link(self, obs, state, current_ids):
        cfg = self.config
        if not cfg.split_link_enabled or obs["label"] != "cash":
            return
        if state.origin is None:
            state.origin = dict(obs)
            parents = [
                key
                for key, parent in self.objects.items()
                if key != obs["logical_object_id"]
                and key in current_ids
                and parent.session == state.session
                and parent.held_emitted
                and parent.stable == "cashier"
                and parent.stable_samples
                and distance(parent.stable_samples[-1]["center"], obs["center"])
                <= cfg.split_origin_distance
                and area(obs["bbox"]) / area(parent.stable_samples[-1]["bbox"])
                <= cfg.split_max_area_ratio
            ]
            if len(parents) == 1:
                state.parent_candidate = parents[0]
        parent = self.objects.get(state.parent_candidate)
        if (
            parent
            and not state.linked
            and state.stable == "cashier"
            and len(state.stable_samples) >= cfg.split_min_frames
            and state.parent_candidate in current_ids
            and parent.stable == "cashier"
            and distance(state.origin["center"], obs["center"]) >= cfg.split_min_divergence
        ):
            state.group_id, state.linked = parent.group_id, True
            self.lineage_links.append(
                {
                    "timestamp_sec": self.timestamp,
                    "logical_object_id": obs["logical_object_id"],
                    "parent_logical_object_id": state.parent_candidate,
                    "money_group_id": state.group_id,
                    "reason": "small_part_origin_overlap_then_divergence_with_parent_visible",
                }
            )

    def object_event(self, kind, obs, state, samples, old_samples=None):
        if not self.active_customer:
            return None
        spatial = mean(s["spatial_quality"] for s in samples)
        score = event_score(samples, self.config.object_min_frames, spatial, obs["reconnects"])
        if old_samples:
            score = min(
                score,
                event_score(
                    old_samples,
                    self.config.object_min_frames,
                    mean(s["spatial_quality"] for s in old_samples),
                    obs["reconnects"],
                ),
            )
        return self.emit(
            kind,
            samples,
            customer=self.active_customer,
            obj=obs,
            score=score,
            from_person=self.active_customer
            if kind.endswith("TO_CASHIER")
            else self.cashier_id
            if kind.endswith("TO_CUSTOMER")
            else None,
            to_person=self.cashier_id
            if kind.endswith("TO_CASHIER")
            else self.active_customer
            if kind.endswith("TO_CUSTOMER")
            else None,
            origin_observations=[s["timestamp_sec"] for s in old_samples or []],
        )

    def object_events(self, observations, frame):
        cfg = self.config
        current_ids = {o["logical_object_id"] for o in observations}
        for obs in observations:
            obs.update(self.association(obs, frame))
            key = obs["logical_object_id"]
            state = self.objects.get(key)
            if state is None or state.session != self.active_session:
                state = ObjectState(self.active_session, f"{self.active_session}:{key}")
                self.objects[key] = state
            self.consider_part_link(obs, state, current_ids)
            previous = self.tracker.tracks[key].observations[:-1]
            zones = []
            for sample in previous:
                zone = sample.get("zone")
                if zone and (not zones or zones[-1] != zone):
                    zones.append(zone)
            obs.update(
                money_group_id=state.group_id,
                current_customer_id=self.active_customer,
                was_in_counter=state.counter_confirmed,
                previous_zones=zones[-10:],
            )
            # Update the tracker history with the semantic context for audit/replay.
            self.tracker.tracks[key].observations[-1].update(obs)
            self.history.append(dict(obs))
            association = obs["association"]
            if obs["association_ambiguous"]:
                association = "unknown"
            if (
                association != state.candidate
                or self.timestamp - state.last_seen > cfg.max_observation_gap_sec
            ):
                state.candidate, state.samples = association, []
                state.pending_hold = []
            state.last_seen = self.timestamp
            state.samples.append(obs)
            if association == "unknown":
                self.record_decision(obs, state, "ambiguous_association")
                continue
            enough = self.stable_enough(state.samples, cfg.object_min_sec, cfg.object_min_frames)
            decision = "awaiting_temporal_confirmation" if not enough else "stable_state"
            event_count = len(self.events)
            if enough and association != state.stable:
                old, old_samples = state.stable, state.stable_samples
                # A brief neutral table interval is not a new owner. Preserve a
                # confirmed source only while the same logical object is observed
                # and the last source observation is recent. Never bridge counter
                # occupancy or a different person as if it were neutral space.
                if (
                    old == "table"
                    and state.anchor_samples
                    and self.timestamp - state.anchor_samples[-1]["timestamp_sec"]
                    <= cfg.neutral_bridge_sec
                ):
                    old, old_samples = state.anchor, state.anchor_samples
                decision = (
                    "no_confirmed_transition_origin" if old is None else "no_action_transition"
                )
                moved = old_samples and distance(
                    old_samples[-1]["center"], state.samples[-1]["center"]
                )
                prefix = "MONEY" if obs["label"] == "cash" else "ID"
                if (
                    old == "customer"
                    and association == "cashier"
                    and moved >= cfg.min_transfer_distance
                ):
                    self.object_event(
                        f"{prefix}_TRANSFER_TO_CASHIER", obs, state, state.samples, old_samples
                    )
                    state.id_transferred = obs["label"] == "id_card"
                elif old == "cashier" and association == "counter" and obs["label"] == "cash":
                    self.object_event("MONEY_TO_COUNTER", obs, state, state.samples, old_samples)
                    state.held_emitted, state.from_counter_at = False, None
                elif old == "counter" and association == "cashier" and obs["label"] == "cash":
                    self.object_event("MONEY_FROM_COUNTER", obs, state, state.samples, old_samples)
                    state.from_counter_at, state.held_emitted = self.timestamp, False
                elif (
                    old == "cashier"
                    and association == "customer"
                    and moved >= cfg.min_transfer_distance
                ):
                    if obs["label"] == "cash":
                        self.object_event(
                            "MONEY_TRANSFER_TO_CUSTOMER", obs, state, state.samples, old_samples
                        )
                elif old == "cashier" and association == "table" and state.id_checked:
                    self.object_event("ID_ON_TABLE", obs, state, state.samples, old_samples)
                state.stable, state.stable_samples = association, list(state.samples)
                if association == "counter":
                    state.counter_confirmed = True
                state.pending_hold = []
            elif association == state.stable:
                state.stable_samples.append(obs)
                state.pending_hold.append(obs)
                if (
                    association == "cashier"
                    and obs["label"] == "cash"
                    and state.from_counter_at is not None
                    and not state.held_emitted
                    and self.stable_enough(
                        state.pending_hold, cfg.held_min_sec, cfg.object_min_frames
                    )
                ):
                    self.object_event("MONEY_HELD_BY_CASHIER", obs, state, state.pending_hold)
                    state.held_emitted = True
                if (
                    association == "cashier"
                    and obs["label"] == "id_card"
                    and state.id_transferred
                    and not state.id_checked
                    and self.stable_enough(
                        state.pending_hold, cfg.id_check_min_sec, cfg.object_min_frames
                    )
                ):
                    self.object_event("ID_CHECK", obs, state, state.pending_hold)
                    state.id_checked = True
            if association == state.stable and association in {"customer", "cashier", "counter"}:
                state.anchor, state.anchor_samples = association, list(state.stable_samples)
            if len(self.events) > event_count:
                decision = (
                    "event_accepted"
                    if self.events[-1].confidence >= cfg.minimum_event_confidence
                    else "event_below_confidence_threshold"
                )
            self.record_decision(obs, state, decision)

    def record_decision(self, obs, state, decision):
        details = {
            "was_in_counter": state.counter_confirmed,
            "semantic_decision": decision,
            "candidate_association": state.candidate,
            "candidate_frames": len(state.samples),
            "candidate_duration_sec": round(
                state.samples[-1]["timestamp_sec"] - state.samples[0]["timestamp_sec"], 6
            ),
            "stable_association": state.stable,
            "transition_anchor": state.anchor,
            "last_anchor_sec": state.anchor_samples[-1]["timestamp_sec"]
            if state.anchor_samples
            else None,
        }
        obs.update(details)
        self.history[-1].update(details)
        self.tracker.tracks[obs["logical_object_id"]].observations[-1].update(details)

    def add_coverage(self, frame):
        prev = self.previous_frame
        if (
            prev is None
            or frame.timestamp_sec - prev.timestamp_sec > self.config.max_observation_gap_sec
        ):
            return
        for channel, attribute in (
            ("people", "people_observable"),
            ("money_actions", "money_observable"),
            ("id_actions", "id_observable"),
        ):
            if channel == "id_actions" and not self.config.id_detector_validated:
                continue
            if getattr(prev, attribute) and getattr(frame, attribute):
                spans = getattr(self.coverage, channel)
                if spans and abs(spans[-1].end_sec - prev.timestamp_sec) < 1e-8:
                    spans[-1].end_sec = frame.timestamp_sec
                else:
                    spans.append(Span(start_sec=prev.timestamp_sec, end_sec=frame.timestamp_sec))

    def update(self, frame: FrameObservation | dict):
        frame = FrameObservation.model_validate(frame)
        if frame.timestamp_sec <= self.timestamp:
            raise ValueError("Frame timestamps must be strictly increasing")
        if self.first_timestamp is None:
            self.first_timestamp = frame.timestamp_sec
        self.timestamp = frame.timestamp_sec
        self.add_coverage(frame)
        self.person_events(frame)
        live_report = self.synchronize_service()
        objects = [
            o.model_dump()
            for o in frame.objects
            if o.confidence >= self.config.minimum_detection_confidence
            and (o.label == "cash" or self.config.id_detector_validated)
        ]
        if not self.config.id_detector_validated:
            self.warnings.add("id_detector_unavailable_or_unvalidated")
        objects, suppressed = suppress_object_duplicates(objects, self.config.object_duplicate_iou)
        if suppressed:
            self.warnings.add("duplicate_object_detections_suppressed")
        tracked = self.tracker.update(objects, self.timestamp)
        self.object_events(tracked, frame)
        self.previous_frame = frame
        return {
            "timestamp_sec": self.timestamp,
            "people": [p.model_dump() for p in frame.people],
            "objects": tracked,
            "suppressed_object_detections": suppressed,
            "active_session": self.active_session,
            "current_customer": self.active_customer,
            "current_customer_visible": any(
                p.track_id == self.active_customer and p.identity_reliable for p in frame.people
            ),
            "recent_events": sorted(
                self.debug_events
                + [{**d, "timestamp_sec": d["timecode_sec"]} for d in live_report.derived_events],
                key=lambda e: e["timestamp_sec"],
            )[-4:],
        }

    def rule_request(self, duration):
        return RuleRequest(
            duration_sec=duration,
            events=sorted(self.events, key=lambda e: (e.end_sec, e.type != "CASHIER_PRESENT")),
            coverage=self.coverage.model_copy(deep=True),
            minimum_confidence=self.config.minimum_event_confidence,
        )

    def finish(self, duration):
        request = self.rule_request(duration)
        report = evaluate(request)
        event_lookup = {e.event_id: e for e in self.events}
        derived = [
            {
                **d,
                "timestamp_sec": d["timecode_sec"],
                "confidence": event_lookup[d["trigger_event_id"]].confidence,
                "accepted": True,
            }
            for d in report.derived_events
        ]
        timeline = sorted(self.debug_events + derived, key=lambda e: e["timestamp_sec"])
        return {
            "events": timeline,
            "rule_request": request.model_dump(),
            "rule_result": report.model_dump(),
            "warnings": sorted(self.warnings),
            "track_reconnections": self.tracker.links,
            "lineage_links": self.lineage_links,
            "track_history": self.history,
        }
