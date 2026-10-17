import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID


class JobRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def initialize(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
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

    def create(self, job_id: UUID, video_path: Path, size_bytes: int) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
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
