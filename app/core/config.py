from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_key: SecretStr
    storage_dir: Path = Path("data")
    max_upload_mb: int = Field(default=100, ge=1)
    max_request_mb: int | None = Field(default=None, ge=1)
    analyzer_callable: str | None = None
    job_timeout_seconds: int = Field(default=240, ge=1, le=300)
    total_job_timeout_seconds: int = Field(default=300, ge=1, le=300)
    max_active_jobs: int = Field(default=5, ge=1)
    max_running_jobs: int = Field(default=1, ge=1)
    worker_poll_seconds: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    cleanup_enabled: bool = True
    job_retention_seconds: int = Field(default=86400, ge=1)
    orphan_grace_seconds: int = Field(default=3600, ge=60)
    cleanup_interval_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    cleanup_batch_size: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_capacity(self):
        if self.max_running_jobs > self.max_active_jobs:
            raise ValueError("MAX_RUNNING_JOBS cannot exceed MAX_ACTIVE_JOBS")
        if self.max_request_mb is not None and self.max_request_mb <= self.max_upload_mb:
            raise ValueError("MAX_REQUEST_MB must exceed MAX_UPLOAD_MB to allow multipart headers")
        if self.orphan_grace_seconds < self.total_job_timeout_seconds + 30:
            raise ValueError("ORPHAN_GRACE_SECONDS must exceed the total job budget by 30 seconds")
        return self

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr) -> SecretStr:
        key = value.get_secret_value()
        if not key.strip() or key == "replace-with-your-generated-key":
            raise ValueError("Set API_KEY to a generated secret before starting the server")
        return value

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_request_bytes(self) -> int:
        size_mb = self.max_request_mb if self.max_request_mb is not None else self.max_upload_mb + 1
        return size_mb * 1024 * 1024
