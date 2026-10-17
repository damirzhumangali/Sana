import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.cleanup import main
from app.core.config import Settings
from app.main import create_app
from app.schemas import AnalysisResult, JobError
from app.services.cleanup import StorageCleanup, UnsafeCleanupPath
from app.services.jobs import JobRepository
from app.worker import Worker

RESULT = AnalysisResult(verdict="no_violation", violations=[])


@dataclass
class Clock:
    value: datetime

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock(datetime.now(UTC))


@pytest.fixture
def jobs(tmp_path, clock):
    (tmp_path / "uploads").mkdir()
    (tmp_path / "work").mkdir()
    repository = JobRepository(tmp_path / "jobs.sqlite3", clock=clock)
    repository.initialize()
    return repository


@pytest.fixture
def cleanup(jobs, tmp_path):
    return StorageCleanup(jobs, tmp_path, retention_seconds=10, orphan_grace_seconds=3600)


def upload(jobs, *, publish=True):
    job_id = uuid4()
    video = jobs.database_path.parent / "uploads" / f"{job_id}.mov"
    jobs.reserve_upload(job_id, video)
    video.write_bytes(b"synthetic video")
    if publish:
        jobs.publish_upload(job_id, video.stat().st_size)
    return job_id, video


def finish(jobs, *, failed=False, release=True):
    job_id, video = upload(jobs)
    claim = jobs.claim_next(240)
    assert claim.job_id == job_id
    if failed:
        jobs.fail(claim, JobError(code="TEST_ERROR", message="Synthetic failure"))
    else:
        jobs.complete(claim, RESULT)
    if release:
        jobs.release_execution(claim)
    return claim, video


def artifact(jobs, job_id=None, suffix="json", age=0):
    path = jobs.database_path.parent / "work" / f"{job_id or uuid4()}-{uuid4().hex}.{suffix}"
    path.write_text('{"private_result":"synthetic"}', encoding="utf-8")
    stamp = jobs.now().timestamp() - age
    os.utime(path, (stamp, stamp))
    return path


def row(jobs, job_id):
    with closing(sqlite3.connect(jobs.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute("SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)).fetchone()


@pytest.mark.parametrize("failed", [False, True])
def test_purges_terminal_video_results_and_work_files(cleanup, jobs, clock, failed):
    claim, video = finish(jobs, failed=failed)
    result_file = artifact(jobs, claim.job_id)
    temporary = artifact(jobs, claim.job_id, suffix="tmp")
    assert row(jobs, claim.job_id)["result_json" if not failed else "error_json"]
    clock.advance(10)
    stats = cleanup.run()
    assert stats.deleted_jobs == 1
    assert stats.deleted_uploads == 1
    assert stats.deleted_work_files == 2
    assert stats.errors == 0
    assert row(jobs, claim.job_id) is None
    assert not video.exists() and not result_file.exists() and not temporary.exists()
    assert cleanup.run().deleted_jobs == 0
    assert jobs.database_path.exists()


def test_retention_starts_at_finish_and_keeps_recent_results(cleanup, jobs, clock):
    claim, video = finish(jobs)
    with closing(sqlite3.connect(jobs.database_path)) as connection, connection:
        connection.execute(
            "UPDATE jobs SET created_at = ? WHERE job_id = ?",
            ((clock() - timedelta(days=2)).isoformat(), str(claim.job_id)),
        )
    clock.advance(9)
    assert cleanup.run().deleted_jobs == 0
    assert video.exists()
    clock.advance(1)
    assert cleanup.run().deleted_jobs == 1


@pytest.mark.parametrize("state", ["uploading", "uploaded", "processing"])
def test_active_jobs_and_their_old_files_are_preserved(cleanup, jobs, state):
    job_id, video = upload(jobs, publish=state != "uploading")
    if state == "processing":
        jobs.claim_next(240)
    work = artifact(jobs, job_id, age=100000)
    os.utime(video, (jobs.now().timestamp() - 100000,) * 2)
    stats = cleanup.run()
    assert stats.deleted_jobs == stats.deleted_uploads == stats.deleted_work_files == 0
    assert row(jobs, job_id)["status"] == state
    assert video.exists() and work.exists()


def test_worker_ownership_protects_terminal_files_until_release(cleanup, jobs, clock):
    claim, video = finish(jobs, release=False)
    work = artifact(jobs, claim.job_id, age=100000)
    clock.advance(10)
    assert cleanup.run().deleted_jobs == 0
    assert video.exists() and work.exists()
    jobs.release_execution(claim)
    assert cleanup.run().deleted_jobs == 1


def test_crashed_worker_ownership_expires_only_after_deadline_plus_grace(cleanup, jobs, clock):
    job_id, video = upload(jobs)
    jobs.claim_next(240)
    work = artifact(jobs, job_id)
    clock.advance(300)
    assert cleanup.run().deleted_jobs == 0
    assert row(jobs, job_id)["status"] == "failed"
    clock.advance(3500)  # 3800 < deadline 240 + grace 3600.
    assert cleanup.run().deleted_jobs == 0
    assert video.exists() and work.exists()
    clock.advance(40)
    assert cleanup.run().deleted_jobs == 1


def test_timeout_gets_full_retention_period(cleanup, jobs, clock):
    job_id, video = upload(jobs)
    clock.advance(301)
    assert cleanup.run().deleted_jobs == 0
    assert row(jobs, job_id)["status"] == "failed"
    assert video.exists()
    clock.advance(10)
    assert cleanup.run().deleted_jobs == 1


def test_only_old_owned_orphans_are_removed(cleanup, jobs, tmp_path):
    orphan = tmp_path / "uploads" / f"{uuid4()}.mov"
    orphan.write_bytes(b"orphan")
    old_time = jobs.now().timestamp() - 3601
    os.utime(orphan, (old_time, old_time))
    old_json = artifact(jobs, age=3601)
    old_tmp = artifact(jobs, suffix="tmp", age=3601)
    recent = artifact(jobs)
    unknown = tmp_path / "uploads" / "user-original.mov"
    unknown.write_bytes(b"keep")
    nested = tmp_path / "uploads" / "nested"
    nested.mkdir()
    nested_video = nested / f"{uuid4()}.mov"
    nested_video.write_bytes(b"keep")
    other_storage = tmp_path / "queue-check"
    other_storage.mkdir()
    other_video = other_storage / f"{uuid4()}.mov"
    other_video.write_bytes(b"keep")
    stats = cleanup.run()
    assert stats.deleted_uploads == 1 and stats.deleted_work_files == 2
    assert not orphan.exists() and not old_json.exists() and not old_tmp.exists()
    assert recent.exists() and unknown.exists() and nested_video.exists() and other_video.exists()


def test_orphan_scan_preserves_a_file_referenced_by_another_job(cleanup, jobs, tmp_path):
    job_id, _ = upload(jobs)
    referenced = tmp_path / "uploads" / f"{uuid4()}.mov"
    referenced.write_bytes(b"referenced by active job")
    os.utime(referenced, (jobs.now().timestamp() - 100000,) * 2)
    with closing(sqlite3.connect(jobs.database_path)) as connection, connection:
        connection.execute(
            "UPDATE jobs SET video_path = ? WHERE job_id = ?", (str(referenced), str(job_id))
        )
    assert cleanup.run().deleted_uploads == 0
    assert referenced.exists()


def test_dry_run_counts_candidates_without_changing_data(cleanup, jobs, clock):
    claim, video = finish(jobs)
    work = artifact(jobs, claim.job_id, age=4000)
    clock.advance(10)
    before = dict(row(jobs, claim.job_id))
    stats = cleanup.run(dry_run=True)
    assert (stats.deleted_jobs, stats.deleted_uploads, stats.deleted_work_files) == (1, 1, 1)
    assert dict(row(jobs, claim.job_id)) == before
    assert video.exists() and work.exists()
    assert cleanup.run().deleted_jobs == 1


def test_dry_run_does_not_even_expire_pending_jobs(cleanup, jobs, clock):
    job_id, _ = upload(jobs)
    clock.advance(400)
    cleanup.run(dry_run=True)
    assert row(jobs, job_id)["status"] == "uploaded"


def test_filesystem_failure_keeps_record_and_is_retried(cleanup, jobs, clock, monkeypatch):
    claim, video = finish(jobs)
    clock.advance(10)
    original = cleanup._remove

    def deny(path, dry_run):
        raise PermissionError("simulated open file")

    monkeypatch.setattr(cleanup, "_remove", deny)
    assert cleanup.run().errors == 1
    assert row(jobs, claim.job_id) is not None and video.exists()
    monkeypatch.setattr(cleanup, "_remove", original)
    assert cleanup.run().deleted_jobs == 1


def test_partial_file_deletion_can_be_retried(cleanup, jobs, clock, monkeypatch):
    claim, video = finish(jobs)
    work = artifact(jobs, claim.job_id)
    clock.advance(10)
    original = cleanup._remove

    def deny_work(path, dry_run):
        if path == work:
            raise PermissionError("simulated locked work file")
        return original(path, dry_run)

    monkeypatch.setattr(cleanup, "_remove", deny_work)
    assert cleanup.run().errors == 1
    assert not video.exists() and work.exists() and row(jobs, claim.job_id) is not None
    monkeypatch.setattr(cleanup, "_remove", original)
    assert cleanup.run().deleted_jobs == 1


def test_database_path_cannot_delete_an_external_video(cleanup, jobs, clock, tmp_path):
    claim, video = finish(jobs)
    external = tmp_path / "outside-managed-folders.mov"
    external.write_bytes(b"keep")
    with closing(sqlite3.connect(jobs.database_path)) as connection, connection:
        connection.execute(
            "UPDATE jobs SET video_path = ? WHERE job_id = ?", (str(external), str(claim.job_id))
        )
    clock.advance(10)
    assert cleanup.run().errors == 1
    assert external.read_bytes() == b"keep"
    assert video.exists() and row(jobs, claim.job_id) is not None


def test_linked_managed_directory_is_rejected_before_any_deletion(cleanup, jobs, clock, tmp_path):
    claim, video = finish(jobs)
    clock.advance(10)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / f"{claim.job_id}-{uuid4().hex}.json"
    target.write_text("keep", encoding="utf-8")
    link = tmp_path / "work"
    link.rmdir()
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    try:
        with pytest.raises(UnsafeCleanupPath):
            cleanup.run()
        assert target.read_text(encoding="utf-8") == "keep"
        assert video.exists() and row(jobs, claim.job_id) is not None
    finally:
        if link.is_symlink():
            link.unlink()
        else:
            link.rmdir()  # Remove only the test junction, never recurse into its target.


def test_wrong_storage_root_is_rejected(jobs, tmp_path):
    with pytest.raises(UnsafeCleanupPath):
        StorageCleanup(jobs, tmp_path / "another-root")


def test_batch_limit_and_concurrent_cleaners(jobs, clock, tmp_path):
    for _ in range(4):
        finish(jobs)
    clock.advance(10)
    cleanup = StorageCleanup(jobs, tmp_path, retention_seconds=10, batch_size=2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: cleanup.run(), range(2)))
    assert sum(stats.deleted_jobs for stats in results) <= 4
    assert all(stats.deleted_jobs <= 2 and stats.errors == 0 for stats in results)
    for _ in range(2):
        cleanup.run()
    with closing(sqlite3.connect(jobs.database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_real_worker_releases_execution_ownership(jobs, tmp_path):
    job_id, _ = upload(jobs)
    worker = Worker(jobs, "tests.worker_fixtures:no_violation", tmp_path / "work", 15)
    assert worker.run_once()
    assert row(jobs, job_id)["status"] == "done"
    assert row(jobs, job_id)["run_token"] is None


def test_cli_dry_run_and_apply(cleanup, jobs, clock, tmp_path, monkeypatch, capsys):
    claim, video = finish(jobs)
    old_finish = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
    with closing(sqlite3.connect(jobs.database_path)) as connection, connection:
        connection.execute(
            "UPDATE jobs SET finished_at = ? WHERE job_id = ?", (old_finish, str(claim.job_id))
        )
    settings = Settings(
        _env_file=None, api_key="test", storage_dir=tmp_path, job_retention_seconds=10
    )
    monkeypatch.setattr("app.cleanup.Settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["cleanup", "--dry-run"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["deleted_jobs"] == 1
    assert video.exists()
    monkeypatch.setattr(sys, "argv", ["cleanup"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["deleted_jobs"] == 1
    assert not video.exists()


def test_periodic_cleanup_makes_get_return_404(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        api_key="test-key",
        storage_dir=tmp_path,
        job_retention_seconds=1,
        cleanup_interval_seconds=0.05,
    )
    removed = threading.Event()
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/analyze",
            headers={"X-API-Key": "test-key"},
            files={"file": ("video.mov", b"synthetic video")},
        )
        job_id = response.json()["job_id"]
        jobs = client.app.state.jobs
        claim = jobs.claim_next(240)
        jobs.complete(claim, RESULT)
        jobs.release_execution(claim)
        cleanup = client.app.state.cleanup
        original = cleanup.run

        def observe():
            stats = original()
            if stats.deleted_jobs:
                removed.set()
            return stats

        monkeypatch.setattr(cleanup, "run", observe)
        assert removed.wait(4)
        assert (
            client.get(f"/api/v1/jobs/{job_id}", headers={"X-API-Key": "test-key"}).status_code
            == 404
        )
        assert not (tmp_path / "uploads" / f"{job_id}.mov").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"job_retention_seconds": 0},
        {"cleanup_interval_seconds": 0},
        {"cleanup_batch_size": 0},
        {"cleanup_batch_size": 1001},
        {"orphan_grace_seconds": 100},
    ],
)
def test_invalid_cleanup_settings_are_rejected(overrides):
    with pytest.raises(ValueError):
        Settings(_env_file=None, api_key="test", **overrides)
