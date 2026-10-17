import base64
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class JobCreated(BaseModel):
    job_id: UUID


class EvidenceFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timestamp_sec: float = Field(ge=0, allow_inf_nan=False)
    image_base64: str = Field(min_length=1)

    @field_validator("image_base64")
    @classmethod
    def validate_jpeg_base64(cls, value: str) -> str:
        data = base64.b64decode(value, validate=True)
        if not data.startswith(b"\xff\xd8\xff") or not data.endswith(b"\xff\xd9"):
            raise ValueError("Evidence must contain base64-encoded JPEG bytes")
        return value


class Violation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1)
    timecode_sec: float = Field(ge=0, allow_inf_nan=False)
    description: str = Field(min_length=1)
    evidence_frames: list[EvidenceFrame] = Field(min_length=1, max_length=3)


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["no_violation", "violation"]
    violations: list[Violation]

    @model_validator(mode="after")
    def validate_verdict(self) -> Self:
        if (self.verdict == "violation") != bool(self.violations):
            raise ValueError("Verdict must agree with the presence of violations")
        return self


class JobError(BaseModel):
    code: str
    message: str


class JobProcessing(BaseModel):
    job_id: UUID
    status: Literal["processing"] = "processing"


class JobDone(AnalysisResult):
    job_id: UUID
    status: Literal["done"] = "done"


class JobFailed(BaseModel):
    job_id: UUID
    status: Literal["failed"] = "failed"
    error: JobError


JobResponse = Annotated[JobProcessing | JobDone | JobFailed, Field(discriminator="status")]
