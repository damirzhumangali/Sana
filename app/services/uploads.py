from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from app.services.jobs import JobRepository, UploadDeadlineExceeded

CHUNK_SIZE = 1024 * 1024


class InvalidUpload(ValueError):
    pass


class UploadTooLarge(ValueError):
    pass


class UploadService:
    def __init__(self, uploads_dir: Path, max_bytes: int, jobs: JobRepository) -> None:
        self.uploads_dir = uploads_dir
        self.max_bytes = max_bytes
        self.jobs = jobs

    def save(
        self, filename: str | None, source: BinaryIO, started_at: datetime | None = None
    ) -> UUID:
        if not filename or Path(filename).suffix.lower() != ".mov":
            raise InvalidUpload("Only .mov files are supported")

        job_id = uuid4()
        destination = self.uploads_dir / f"{job_id}.mov"
        size_bytes = 0
        expires_at = self.jobs.reserve_upload(job_id, destination, started_at)
        owns_file = False
        try:
            # Reserve capacity before writing; workers cannot claim an incomplete file.
            with destination.open("xb") as output:
                owns_file = True
                while chunk := source.read(CHUNK_SIZE):
                    if self.jobs.now() >= expires_at:
                        raise UploadDeadlineExceeded()
                    size_bytes += len(chunk)
                    if size_bytes > self.max_bytes:
                        raise UploadTooLarge("File exceeds the configured upload limit")
                    output.write(chunk)
                if size_bytes == 0:
                    raise InvalidUpload("The uploaded file is empty")
            self.jobs.publish_upload(job_id, size_bytes)
        except BaseException:
            # The output is closed before cleanup, including on Windows.
            try:
                if owns_file:
                    destination.unlink(missing_ok=True)
            finally:
                self.jobs.cancel_upload(job_id)
            raise
        return job_id
