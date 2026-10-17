import sqlite3
from collections.abc import Callable
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from app.schemas import (
    AnalysisResult,
    JobDone,
    JobError,
    JobFailed,
    JobProcessing,
    JobResponse,
)


@dataclass(frozen=True)
class ClaimedJob:
    job_id: UUID
    video_path: Path
    run_token: str
    deadline_at: datetime


class QueueFull(Exception):
    retry_after_seconds = 5


class UploadDeadlineExceeded(Exception):
    pass


class JobRepository:
    def __init__(
        self,
        database_path: Path,
        total_timeout_seconds: int = 300,
        max_active_jobs: int = 5,
        max_running_jobs: int = 1,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_path = database_path
        self.total_timeout_seconds = total_timeout_seconds
        self.max_active_jobs = max_active_jobs
        self.max_running_jobs = max_running_jobs
        self._clock = clock or (lambda: datetime.now(UTC))

    def now(self) -> datetime:
        return self._clock()

    @contextmanager
    def _connection(self):
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Additive migration preserves uploads created by stage 1.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
            for name in (
                "started_at",
                "deadline_at",
                "finished_at",
                "run_token",
                "result_json",
                "error_json",
                "expires_at",
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT")
            # Backfill once: restarts/config changes must not renew an existing budget.
            for row in connection.execute(
                "SELECT job_id, created_at FROM jobs WHERE expires_at IS NULL"
            ).fetchall():
                expires = datetime.fromisoformat(row["created_at"]) + timedelta(
                    seconds=self.total_timeout_seconds
                )
                connection.execute(
                    "UPDATE jobs SET expires_at = ? WHERE job_id = ?",
                    (expires.isoformat(), row["job_id"]),
                )
            connection.execute(
                """UPDATE jobs SET deadline_at = expires_at WHERE status = 'processing'
                   AND (deadline_at IS NULL OR deadline_at > expires_at)"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, created_at)")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS jobs_retention ON jobs(status, finished_at)"
            )
            self._expire_in_connection(connection, self.now().isoformat(), None)

    def reserve_upload(
        self, job_id: UUID, video_path: Path, started_at: datetime | None = None
    ) -> datetime:
        rejected: Exception | None = None
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self.now()
            created_at = started_at if started_at is not None else now
            expires_at = created_at + timedelta(seconds=self.total_timeout_seconds)
            self._expire_in_connection(connection, now.isoformat(), None)
            active_count = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('uploading', 'uploaded', 'processing')"
            ).fetchone()[0]
            if expires_at <= now:
                rejected = UploadDeadlineExceeded()
            elif active_count >= self.max_active_jobs:
                rejected = QueueFull()
            else:
                connection.execute(
                    """INSERT INTO jobs
                       (job_id, status, video_path, size_bytes, created_at, expires_at)
                       VALUES (?, 'uploading', ?, 0, ?, ?)""",
                    (
                        str(job_id),
                        str(video_path.resolve()),
                        created_at.isoformat(),
                        expires_at.isoformat(),
                    ),
                )
        # Expiration changes are committed even when admission is rejected.
        if rejected is not None:
            raise rejected
        return expires_at

    def publish_upload(self, job_id: UUID, size_bytes: int) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire_in_connection(connection, self.now().isoformat(), job_id)
            changed = connection.execute(
                """UPDATE jobs SET status = 'uploaded', size_bytes = ?
                   WHERE job_id = ? AND status = 'uploading'""",
                (size_bytes, str(job_id)),
            ).rowcount
        if changed != 1:
            raise UploadDeadlineExceeded()

    def cancel_upload(self, job_id: UUID) -> None:
        with self._connection() as connection:
            connection.execute(
                """DELETE FROM jobs WHERE job_id = ? AND started_at IS NULL
                   AND status IN ('uploading', 'failed')""",
                (str(job_id),),
            )

    def get(self, job_id: UUID) -> JobResponse | None:
        self.expire_due(job_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT status, result_json, error_json FROM jobs WHERE job_id = ?",
                (str(job_id),),
            ).fetchone()
        if row is None:
            return None
        if row["status"] in {"uploading", "uploaded", "processing"}:
            return JobProcessing(job_id=job_id)
        if row["status"] == "done":
            result = AnalysisResult.model_validate_json(row["result_json"])
            return JobDone(job_id=job_id, **result.model_dump())
        if row["status"] == "failed":
            return JobFailed(job_id=job_id, error=JobError.model_validate_json(row["error_json"]))
        raise ValueError("Unknown stored job status")

    def claim_next(self, timeout_seconds: int) -> ClaimedJob | None:
        token = uuid4().hex
        with self._connection() as connection:
            # Serialize SELECT + UPDATE across separate worker processes.
            connection.execute("BEGIN IMMEDIATE")
            now = self.now()
            self._expire_in_connection(connection, now.isoformat(), None)
            running = connection.execute(
                "SELECT COUNT(*) FROM jobs WHERE status = 'processing'"
            ).fetchone()[0]
            if running >= self.max_running_jobs:
                return None
            row = connection.execute(
                """SELECT job_id, video_path, expires_at FROM jobs WHERE status = 'uploaded'
                   ORDER BY created_at, job_id LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            deadline = min(
                datetime.fromisoformat(row["expires_at"]),
                now + timedelta(seconds=timeout_seconds),
            )
            connection.execute(
                """UPDATE jobs SET status = 'processing', started_at = ?,
                   deadline_at = ?, run_token = ? WHERE job_id = ?""",
                (now.isoformat(), deadline.isoformat(), token, row["job_id"]),
            )
        return ClaimedJob(UUID(row["job_id"]), Path(row["video_path"]), token, deadline)

    def complete(self, job: ClaimedJob, result: AnalysisResult) -> bool:
        return self._finish(job, "done", result.model_dump_json(), None)

    def fail(self, job: ClaimedJob, error: JobError) -> bool:
        return self._finish(job, "failed", None, error.model_dump_json())

    def _finish(
        self, job: ClaimedJob, status: str, result_json: str | None, error_json: str | None
    ) -> bool:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = self.now().isoformat()
            self._expire_in_connection(connection, now, job.job_id)
            cursor = connection.execute(
                """UPDATE jobs SET status = ?, result_json = ?, error_json = ?,
                   finished_at = ?
                   WHERE job_id = ? AND status = 'processing' AND run_token = ?""",
                (
                    status,
                    result_json,
                    error_json,
                    now,
                    str(job.job_id),
                    job.run_token,
                ),
            )
            return cursor.rowcount == 1

    def release_execution(self, job: ClaimedJob) -> None:
        """Acknowledge that the worker has stopped its child and closed its artifacts."""
        with self._connection() as connection:
            connection.execute(
                """UPDATE jobs SET run_token = NULL WHERE job_id = ? AND run_token = ?
                   AND status IN ('done', 'failed')""",
                (str(job.job_id), job.run_token),
            )

    def expire_due(self, job_id: UUID | None = None) -> int:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._expire_in_connection(connection, self.now().isoformat(), job_id)

    @staticmethod
    def _expire_in_connection(connection: sqlite3.Connection, now: str, job_id: UUID | None) -> int:
        error = JobError(code="JOB_TIMEOUT", message="Job exceeded its time limit")
        # Keep the ownership token until the worker confirms its child has stopped.
        query = """UPDATE jobs SET status = 'failed', error_json = ?, finished_at = ? WHERE
                   (status IN ('uploading', 'uploaded') AND expires_at <= ?
                    OR status = 'processing' AND deadline_at <= ?)"""
        parameters = [error.model_dump_json(), now, now, now]
        if job_id is not None:
            query += " AND job_id = ?"
            parameters.append(str(job_id))
        return connection.execute(query, parameters).rowcount
