import math

from app.events.config import EventConfig
from app.events.detector import EventDetector


def rectangle(x1, y1, x2, y2):
    return [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]


def config(**overrides):
    return EventConfig(
        zones={
            "cashier": rectangle(0.55, 0.2, 0.9, 0.9),
            "customer": rectangle(0.1, 0.2, 0.4, 0.9),
            "counter": rectangle(0.44, 0.04, 0.56, 0.16),
            "table": rectangle(0.2, 0.7, 0.8, 0.95),
            "room": rectangle(0, 0, 1, 1),
            "exit": rectangle(0, 0, 0.1, 1),
        },
        **overrides,
    )


def person(key="client", role="customer", point=(0.25, 0.5)):
    x, y = point
    return {
        "track_id": key,
        "role": role,
        "bbox": [x - 0.06, y - 0.15, x + 0.06, y + 0.15],
        "confidence": 0.98,
    }


def money(point, key="notes", label="cash", size=0.012, confidence=0.98):
    x, y = point
    return {
        "label": label,
        "track_id": key,
        "bbox": [x - size, y - size, x + size, y + size],
        "confidence": confidence,
    }


class Scenario:
    def __init__(self, id_enabled=True, **overrides):
        self.detector = EventDetector(
            config(id_detector_validated=id_enabled, **overrides), source="synthetic_example"
        )
        self.t = 0
        self.people = [person("cashier", "cashier", (0.7, 0.5))]
        self.extras = []
        self.frames = []

    def frame(self, objects=(), **flags):
        frame = {
            "timestamp_sec": self.t,
            "people": self.people,
            "objects": list(objects) + self.extras,
            "people_observable": True,
            "money_observable": True,
            "id_observable": True,
            **flags,
        }
        self.frames.append(frame)
        self.detector.update(frame)
        self.t = round(self.t + 0.25, 6)

    def hold(self, point=None, frames=5, key="notes", label="cash", size=0.012):
        for _ in range(frames):
            self.frame([money(point, key, label, size)] if point is not None else [])

    def move(self, start, end, key="notes", label="cash", size=0.012):
        steps = math.ceil(math.dist(start, end) / 0.02)
        for i in range(1, steps + 1):
            point = tuple(start[j] + (end[j] - start[j]) * i / steps for j in (0, 1))
            self.frame([money(point, key, label, size)])

    def start(self):
        self.hold(frames=5)
        self.people.append(person())
        self.hold(frames=5)
        return self

    def id_sequence(self):
        self.hold((0.25, 0.5), key="card", label="id_card")
        self.move((0.25, 0.5), (0.7, 0.5), key="card", label="id_card")
        self.hold((0.7, 0.5), frames=10, key="card", label="id_card")
        self.move((0.7, 0.5), (0.5, 0.85), key="card", label="id_card")
        self.hold((0.5, 0.85), key="card", label="id_card")
        return self

    def deposit(self):
        self.hold((0.25, 0.5))
        self.move((0.25, 0.5), (0.7, 0.5))
        self.hold((0.7, 0.5))
        self.move((0.7, 0.5), (0.5, 0.1))
        self.hold((0.5, 0.1))
        self.move((0.5, 0.1), (0.7, 0.5))
        self.hold((0.7, 0.5), frames=8)
        return self

    def finish(self):
        return self.detector.finish(self.t - 0.25)

    def kinds(self):
        return [event.type for event in self.detector.events]
