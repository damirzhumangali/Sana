"""Remove only owned artifacts in the configured storage root; never recurse."""

import logging
import re
import sqlite3
import stat
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from app.services.jobs import JobRepository

logger = logging.getLogger(__name__)
UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
UPLOAD_NAME = re.compile(rf"(?P<job>{UUID_PATTERN})\.mov")
WORK_NAME = re.compile(rf"(?P<job>{UUID_PATTERN})-[0-9a-f]{{32}}\.(json|tmp)")


class UnsafeCleanupPath(ValueError):
    pass


@dataclass
class CleanupStats:
    deleted_jobs: int = 0
    deleted_uploads: int = 0
    deleted_work_files: int = 0
    errors: int = 0


class StorageCleanup:
    def __init__(
        self,
        jobs: JobRepository,
        storage_dir: Path,
        retention_seconds: int = 86400,
        orphan_grace_seconds: int = 3600,
        batch_size: int = 100,
    ) -> None:
        self.jobs = jobs
        self.storage_dir = storage_dir.resolve()
        self.retention_seconds = retention_seconds
        self.orphan_grace_seconds = orphan_grace_seconds
        self.batch_size = batch_size
        if jobs.database_path.resolve().parent != self.storage_dir:
            raise UnsafeCleanupPath("Database and cleanup storage must have the same root")

    @staticmethod
    def _is_reparse(info) -> bool:
        return stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        )

    def _directory(self, name: str) -> Path:
        if name not in {"uploads", "work"}:
            raise UnsafeCleanupPath("Unknown managed directory")
        directory = self.storage_dir / name
        if self.storage_dir.resolve() != self.storage_dir:
            raise UnsafeCleanupPath("Storage root was redirected")
        try:
            info = directory.lstat()
        except FileNotFoundError:
            return directory
        if self._is_reparse(info) or not stat.S_ISDIR(info.st_mode):
            raise UnsafeCleanupPath("Managed directory is a link or is not a directory")
        if directory.resolve() != directory:
            raise UnsafeCleanupPath("Managed directory resolves outside its expected path")
        return directory

    def _file_info(self, path: Path):
        directory = self._directory(path.parent.name)
        if path.parent != directory:
            raise UnsafeCleanupPath("File is outside a managed directory")
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        if self._is_reparse(info) or not stat.S_ISREG(info.st_mode):
            raise UnsafeCleanupPath("Cleanup target is not a regular file")
        if path.resolve().parent != directory:
            raise UnsafeCleanupPath("Cleanup target resolves outside its managed directory")
        return info

    def _remove(self, path: Path, dry_run: bool) -> bool:
        if self._file_info(path) is None:
            return False
        if not dry_run:
            path.unlink()
        return True

    def run(self, *, dry_run: bool = False) -> CleanupStats:
        stats = CleanupStats()
        handled: set[Path] = set()
        # Validate both managed directories before any deletion.
        uploads = self._directory("uploads")
        work = self._directory("work")
        now = self.jobs.now()
        cutoff = (now - timedelta(seconds=self.retention_seconds)).isoformat()
        lease_cutoff = (now - timedelta(seconds=self.orphan_grace_seconds)).isoformat()
        if not dry_run:
            self.jobs.expire_due()
        with self.jobs._connection() as connection:
            rows = connection.execute(
                """SELECT job_id FROM jobs WHERE status IN ('done', 'failed')
                   AND finished_at <= ?
                   AND (run_token IS NULL OR deadline_at <= ?)
                   ORDER BY finished_at, job_id LIMIT ?""",
                (cutoff, lease_cutoff, self.batch_size),
            ).fetchall()
        for candidate in rows:
            try:
                with self.jobs._connection() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    row = connection.execute(
                        """SELECT job_id, video_path FROM jobs WHERE job_id = ?
                           AND status IN ('done', 'failed') AND finished_at <= ?
                           AND (run_token IS NULL OR deadline_at <= ?)""",
                        (candidate["job_id"], cutoff, lease_cutoff),
                    ).fetchone()
                    if row is None:
                        continue
                    job_id = str(UUID(row["job_id"]))
                    if job_id != row["job_id"]:
                        raise UnsafeCleanupPath("Job identifier is not a canonical UUID")
                    video = uploads / f"{job_id}.mov"
                    # Do not follow arbitrary paths stored in the database.
                    if Path(row["video_path"]).resolve() != video:
                        raise UnsafeCleanupPath("Stored video path is not the owned UUID path")
                    work_files = self._work_files(work, job_id)
                    for path in [video, *work_files]:
                        if self._remove(path, dry_run):
                            if path.parent == uploads:
                                stats.deleted_uploads += 1
                            else:
                                stats.deleted_work_files += 1
                            handled.add(path)
                    if not dry_run:
                        connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
                stats.deleted_jobs += 1
            except (OSError, ValueError, sqlite3.Error):
                stats.errors += 1
                logger.exception(
                    "Unable to purge job %s; its database record is retained", candidate["job_id"]
                )

        old_file_time = (now - timedelta(seconds=self.orphan_grace_seconds)).timestamp()
        for directory, pattern in [(uploads, UPLOAD_NAME), (work, WORK_NAME)]:
            removed = 0
            for path in self._entries(directory):
                if removed >= self.batch_size:
                    break
                match = pattern.fullmatch(path.name)
                if match is None or path in handled:
                    continue
                try:
                    with self.jobs._connection() as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        if directory == uploads:
                            referenced = connection.execute(
                                """SELECT 1 FROM jobs WHERE job_id = ?
                                   OR video_path = ? COLLATE NOCASE LIMIT 1""",
                                (match["job"], str(path)),
                            ).fetchone()
                            if referenced is not None:
                                continue
                        else:
                            row = connection.execute(
                                "SELECT status, run_token, deadline_at FROM jobs WHERE job_id = ?",
                                (match["job"],),
                            ).fetchone()
                            if row is not None and (
                                row["status"] not in {"done", "failed"}
                                or (
                                    row["run_token"] is not None
                                    and (
                                        row["deadline_at"] is None
                                        or row["deadline_at"] > lease_cutoff
                                    )
                                )
                            ):
                                continue
                        info = self._file_info(path)
                        if info is None or info.st_mtime > old_file_time:
                            continue
                        if self._remove(path, dry_run):
                            removed += 1
                            if directory == uploads:
                                stats.deleted_uploads += 1
                            else:
                                stats.deleted_work_files += 1
                except (OSError, ValueError, sqlite3.Error):
                    stats.errors += 1
                    logger.exception("Unable to remove stale artifact %s", path.name)
        return stats

    @staticmethod
    def _entries(directory: Path) -> list[Path]:
        try:
            return list(directory.iterdir())
        except FileNotFoundError:
            return []

    @staticmethod
    def _work_files(directory: Path, job_id: str) -> list[Path]:
        return [
            path
            for path in StorageCleanup._entries(directory)
            if (match := WORK_NAME.fullmatch(path.name)) is not None and match["job"] == job_id
        ]
