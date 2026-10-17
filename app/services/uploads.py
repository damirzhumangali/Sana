from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from app.services.jobs import JobRepository

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

    def save(self, filename: str | None, source: BinaryIO) -> UUID:
        if not filename or Path(filename).suffix.lower() != ".mov":
            raise InvalidUpload("Only .mov files are supported")

        job_id = uuid4()
        destination = self.uploads_dir / f"{job_id}.mov"
        size_bytes = 0
        # Exclusive creation avoids overwriting an existing upload.
        output = destination.open("xb")
        try:
            with output:
                while chunk := source.read(CHUNK_SIZE):
                    size_bytes += len(chunk)
                    if size_bytes > self.max_bytes:
                        raise UploadTooLarge("File exceeds the configured upload limit")
                    output.write(chunk)
                if size_bytes == 0:
                    raise InvalidUpload("The uploaded file is empty")
            self.jobs.create(job_id, destination, size_bytes)
        except BaseException:
            # The output is closed before cleanup, including on Windows.
            destination.unlink(missing_ok=True)
            raise
        return job_id
