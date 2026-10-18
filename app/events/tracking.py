"""Conservative object fragment association in normalized image coordinates."""

import math
from dataclasses import dataclass, field

from app.events.config import EventConfig


def center(box):
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def area(box):
    return max(1e-9, (box[2] - box[0]) * (box[3] - box[1]))


@dataclass
class LogicalTrack:
    logical_id: str
    label: str
    raw_id: str
    observations: list[dict] = field(default_factory=list)
    raw_ids: list[str] = field(default_factory=list)
    reconnects: int = 0


class LogicalObjectTracker:
    def __init__(self, config: EventConfig):
        self.config = config
        self.tracks: dict[str, LogicalTrack] = {}
        self.links: list[dict] = []
        self.next_raw = 1
        self.next_logical = 1
        self.timestamp = -1.0

    def match_cost(self, track, observation, timestamp, direct=False):
        cfg, last = self.config, track.observations[-1]
        gap = timestamp - last["timestamp_sec"]
        if not 0 < gap <= cfg.reconnect_gap_sec or track.label != observation["label"]:
            return None
        a, b = area(last["bbox"]), area(observation["bbox"])
        if max(a / b, b / a) > cfg.reconnect_max_area_ratio:
            return None
        motion = (0.0, 0.0)
        predicted = last["center"]
        if len(track.observations) >= 2:
            previous = track.observations[-2]
            dt = last["timestamp_sec"] - previous["timestamp_sec"]
            motion = tuple(last["center"][i] - previous["center"][i] for i in (0, 1))
            predicted = tuple(last["center"][i] + motion[i] * gap / dt for i in (0, 1))
        moved = tuple(observation["center"][i] - last["center"][i] for i in (0, 1))
        norm = math.hypot(*motion) * math.hypot(*moved)
        if (
            not direct
            and norm > 1e-5
            and sum(a * b for a, b in zip(motion, moved, strict=True)) / norm
            < cfg.reconnect_min_direction_cosine
        ):
            return None
        cost = min(
            distance(predicted, observation["center"]),
            distance(last["center"], observation["center"]),
        )
        limit = cfg.raw_track_distance if direct else cfg.reconnect_distance
        return cost if cost <= limit else None

    def update(self, observations: list[dict], timestamp: float) -> list[dict]:
        if not math.isfinite(timestamp) or timestamp <= self.timestamp:
            raise ValueError("Object timestamps must be finite and increasing")
        self.timestamp = timestamp
        external = [(o["label"], str(o["track_id"])) for o in observations if o.get("track_id")]
        if len(external) != len(set(external)):
            raise ValueError("Duplicate detector track_id in one frame")
        used, output = set(), []
        # A currently visible donor cannot be absorbed into a second detection.
        visible_raw = set(external)
        for original in observations:
            obs = dict(original, timestamp_sec=timestamp, center=center(original["bbox"]))
            if obs["label"] not in {"cash", "id_card"}:
                raise ValueError("Unsupported object class")
            given = str(obs["track_id"]) if obs.get("track_id") is not None else None
            choices = []
            for key, track in self.tracks.items():
                if key in used:
                    continue
                gap = timestamp - track.observations[-1]["timestamp_sec"]
                same = given is not None and track.raw_id == given
                direct = same or (given is None and gap <= self.config.raw_track_gap_sec)
                if not direct and (
                    (track.label, track.raw_id) in visible_raw
                    or len(track.observations) < self.config.reconnect_min_samples
                ):
                    continue
                cost = self.match_cost(track, obs, timestamp, direct)
                if cost is not None:
                    choices.append((cost, key, direct))
            # Prefer continuity in adjacent frames over reconnecting an older
            # fragment. Reconnection retains its stricter ambiguity threshold.
            direct_choices = [candidate for candidate in choices if candidate[2]]
            if direct_choices:
                choices = direct_choices
            choices.sort()
            ambiguity_margin = (
                self.config.raw_ambiguity_margin
                if direct_choices
                else self.config.reconnect_ambiguity_margin
            )
            ambiguous = len(choices) > 1 and (choices[1][0] - choices[0][0] < ambiguity_margin)
            if choices and not ambiguous:
                _, key, direct = choices[0]
                track = self.tracks[key]
                raw = given or (track.raw_id if direct else f"det-{self.next_raw}")
                if raw != track.raw_id:
                    self.links.append(
                        {
                            "timestamp_sec": timestamp,
                            "logical_object_id": key,
                            "from_track_id": track.raw_id,
                            "to_track_id": raw,
                            "reason": "unique_temporal_spatial_match",
                        }
                    )
                    track.reconnects += 1
            else:
                key = f"{obs['label']}-{self.next_logical}"
                self.next_logical += 1
                raw = given or f"det-{self.next_raw}"
                track = LogicalTrack(key, obs["label"], raw)
                self.tracks[key] = track
            if given is None and raw == f"det-{self.next_raw}":
                self.next_raw += 1
            track.raw_id = raw
            if raw not in track.raw_ids:
                track.raw_ids.append(raw)
            obs.update(
                track_id=raw,
                logical_object_id=key,
                reconnects=track.reconnects,
                association_ambiguous=ambiguous,
                track_samples=len(track.observations) + 1,
            )
            track.observations.append(obs)
            used.add(key)
            output.append(obs)
        return output
