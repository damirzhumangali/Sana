"""Short-term geometric person tracking; no face recognition or exit inference."""

import math

from app.services.person_roles import assign_single_cashier


def box_iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(
        0, min(a[3], b[3]) - max(a[1], b[1])
    )
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0


class GeometricRoleTracker:
    """Bootstrap once, then keep each role on its matched track.

    A single real person initially is the cashier per the user's assumption.
    With several people initially, use calibrated zones to choose the cashier.
    A missed detection does not mean a confirmed departure. Ambiguous matching
    abstains; long occlusion/re-entry needs upstream re-identification or review.
    """

    def __init__(self, max_gap_sec=2.0):
        self.max_gap_sec = max_gap_sec
        self.tracks = {}
        self.next_id = 1
        self.cashier_id = None
        self.last_timestamp = -1.0

    def update(self, people, timestamp, width, height, zones, anchor=(0.48, 0.57)):
        if not math.isfinite(timestamp) or timestamp <= self.last_timestamp:
            raise ValueError("Tracking timestamps must be finite and strictly increasing")
        self.last_timestamp = timestamp
        real = [p for p in people if p["counted"]]
        pairs = []
        for index, person in enumerate(real):
            box = person["box_xyxy"]
            choices = []
            for track_id, track in self.tracks.items():
                if timestamp - track["last_seen"] > self.max_gap_sec:
                    continue
                old = track["box"]
                overlap = box_iou(box, old)
                distance = math.hypot(
                    (box[0] + box[2] - old[0] - old[2]) / (2 * width),
                    (box[1] + box[3] - old[1] - old[3]) / (2 * height),
                )
                if overlap >= 0.15 or distance <= 0.08:
                    choices.append((overlap + 0.2 * max(0, 1 - distance / 0.08), index, track_id))
            choices.sort(reverse=True)
            if len(choices) >= 2 and choices[0][0] - choices[1][0] < 0.05:
                # Do not silently swap two known identities during overlap.
                person["tracking_ambiguous"] = True
                continue
            pairs.extend(choices)
        assigned, used = {}, set()
        for _, index, track_id in sorted(pairs, reverse=True):
            if index not in assigned and track_id not in used:
                assigned[index] = track_id
                used.add(track_id)
        for index, person in enumerate(real):
            if index not in assigned:
                track_id = f"person-{self.next_id}"
                self.next_id += 1
                assigned[index] = track_id
                self.tracks[track_id] = {"role": "unknown", "samples": 0}
            track = self.tracks[assigned[index]]
            track.update(box=person["box_xyxy"], last_seen=timestamp, samples=track["samples"] + 1)
            person["track_id"] = assigned[index]

        if self.cashier_id is None:
            selected = None
            if len(real) == 1:
                selected = real[0]
            elif real:
                assign_single_cashier(real, width, height, zones, anchor)
                selected = next((p for p in real if p["role"] == "cashier"), None)
            if selected is not None:
                self.cashier_id = selected["track_id"]
                self.tracks[self.cashier_id]["role"] = "cashier"

        cashier_visible = any(p["track_id"] == self.cashier_id for p in real)
        for person in real:
            track = self.tracks[person["track_id"]]
            if cashier_visible and track["role"] == "unknown":
                track["role"] = "customer"
            person["role"] = track["role"]
            person["track_samples"] = track["samples"]
            person["identity_status"] = (
                "uncertain" if person.get("tracking_ambiguous") else "geometric_match"
            )
        for person in people:
            if not person["counted"]:
                person.update(role="unknown", track_id=None, track_samples=0)
