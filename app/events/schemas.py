from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Detection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bbox: tuple[float, float, float, float]
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    track_id: str | None = Field(default=None, min_length=1)

    @field_validator("bbox")
    @classmethod
    def normalized_box(cls, box):
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("bbox must be normalized xyxy")
        return box


class PersonObservation(Detection):
    track_id: str = Field(min_length=1)
    role: Literal["cashier", "customer", "unknown"]
    identity_reliable: bool = True


class ObjectObservation(Detection):
    label: Literal["cash", "id_card"]


class FrameObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp_sec: float = Field(ge=0, allow_inf_nan=False)
    people: list[PersonObservation] = Field(default_factory=list)
    objects: list[ObjectObservation] = Field(default_factory=list)
    people_observable: bool = False
    money_observable: bool = False
    id_observable: bool = False

    @model_validator(mode="after")
    def unique_people(self):
        ids = [p.track_id for p in self.people]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate person track_id")
        return self
