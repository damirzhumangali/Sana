import sqlite3
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


class JobRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

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
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT")
            connection.execute("CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, created_at)")

    def create(self, job_id: UUID, video_path: Path, size_bytes: int) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO jobs (job_id, status, video_path, size_bytes, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(job_id),
                    "uploaded",
                    str(video_path.resolve()),
                    size_bytes,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def get(self, job_id: UUID) -> JobResponse | None:
        self.expire_running(job_id)
        with self._connection() as connection:
            row = connection.execute(
                "SELECT status, result_json, error_json FROM jobs WHERE job_id = ?",
                (str(job_id),),
            ).fetchone()
        if row is None:
            return None
        if row["status"] in {"uploaded", "processing"}:
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
            row = connection.execute(
                """SELECT job_id, video_path FROM jobs WHERE status = 'uploaded'
                   ORDER BY created_at, job_id LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            now = datetime.now(UTC)
            deadline = now + timedelta(seconds=timeout_seconds)
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
            now = datetime.now(UTC).isoformat()
            self._expire_in_connection(connection, now, job.job_id)
            cursor = connection.execute(
                """UPDATE jobs SET status = ?, result_json = ?, error_json = ?,
                   finished_at = ?, run_token = NULL
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

    def expire_running(self, job_id: UUID | None = None) -> int:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._expire_in_connection(connection, datetime.now(UTC).isoformat(), job_id)

    @staticmethod
    def _expire_in_connection(
        connection: sqlite3.Connection, now: str, job_id: UUID | None
    ) -> int:
        error = JobError(code="JOB_TIMEOUT", message="Video analysis exceeded its time limit")
        query = """UPDATE jobs SET status = 'failed', error_json = ?, finished_at = ?,
                   run_token = NULL WHERE status = 'processing' AND deadline_at <= ?"""
        parameters = [error.model_dump_json(), now, now]
        if job_id is not None:
            query += " AND job_id = ?"
            parameters.append(str(job_id))
        return connection.execute(query, parameters).rowcount
