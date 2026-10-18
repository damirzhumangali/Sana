import math

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    zones: dict[str, list[tuple[float, float]]]
    reflection_regions: list[list[tuple[float, float]]] = Field(default_factory=list)
    cashier_anchor: tuple[float, float] = (0.48, 0.57)
    sample_fps: float = Field(default=4, gt=0, le=30)
    minimum_event_confidence: float = Field(default=0.75, gt=0, le=1)
    minimum_detection_confidence: float = Field(default=0.25, gt=0, le=1)
    object_duplicate_iou: float = Field(default=0.45, gt=0, le=1)
    person_min_frames: int = Field(default=3, ge=2)
    person_min_sec: float = Field(default=0.5, gt=0)
    exit_min_frames: int = Field(default=6, ge=2)
    exit_min_sec: float = Field(default=2, gt=0)
    exit_requires_boundary: bool = True
    person_track_gap_sec: float = Field(default=2, gt=0)
    person_duplicate_overlap: float = Field(default=0.9, gt=0, le=1)
    people_coverage_confidence: float = Field(default=0.5, gt=0, le=1)
    image_min_std: float = Field(default=8, ge=0)
    object_min_frames: int = Field(default=3, ge=2)
    object_min_sec: float = Field(default=0.5, gt=0)
    held_min_sec: float = Field(default=0.5, gt=0)
    id_check_min_sec: float = Field(default=1, gt=0)
    max_observation_gap_sec: float = Field(default=0.8, gt=0)
    association_radius: float = Field(default=0.22, gt=0, le=1)
    association_margin: float = Field(default=0.025, gt=0, le=1)
    min_transfer_distance: float = Field(default=0.025, gt=0, le=1)
    neutral_bridge_sec: float = Field(default=2, gt=0)
    raw_track_gap_sec: float = Field(default=0.4, gt=0)
    raw_track_distance: float = Field(default=0.035, gt=0, le=1)
    raw_ambiguity_margin: float = Field(default=0.005, gt=0, le=1)
    reconnect_gap_sec: float = Field(default=1.25, gt=0)
    reconnect_distance: float = Field(default=0.045, gt=0, le=1)
    reconnect_min_samples: int = Field(default=2, ge=2)
    reconnect_max_area_ratio: float = Field(default=2.5, ge=1)
    reconnect_min_direction_cosine: float = Field(default=0.2, ge=-1, le=1)
    reconnect_ambiguity_margin: float = Field(default=0.015, gt=0, le=1)
    split_link_enabled: bool = True
    split_origin_distance: float = Field(default=0.025, gt=0, le=1)
    split_max_area_ratio: float = Field(default=0.65, gt=0, le=1)
    split_min_frames: int = Field(default=3, ge=2)
    split_min_divergence: float = Field(default=0.025, gt=0, le=1)
    id_detector_validated: bool = False

    @model_validator(mode="after")
    def geometry(self):
        required = {"cashier", "customer", "counter", "table", "room", "exit"}
        if not required <= self.zones.keys():
            raise ValueError(f"Missing zones: {sorted(required - self.zones.keys())}")
        for polygon in [*self.zones.values(), *self.reflection_regions]:
            if len(polygon) < 3 or any(not 0 <= v <= 1 for p in polygon for v in p):
                raise ValueError("Zones must be normalized polygons with at least three points")
        if any(not 0 <= v <= 1 for v in self.cashier_anchor):
            raise ValueError("Invalid cashier anchor")
        for name, value in self.model_dump().items():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"Nonfinite setting: {name}")
        return self
