import io
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.schemas import AnalysisResult
from app.services.jobs import JobRepository, QueueFull, UploadDeadlineExceeded
from app.services.uploads import CHUNK_SIZE, UploadService

HEADERS = {"X-API-Key": "queue-test-key"}
NO_VIOLATION = AnalysisResult(verdict="no_violation", violations=[])


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
    repository = JobRepository(tmp_path / "jobs.sqlite3", max_active_jobs=2, clock=clock)
    repository.initialize()
    return repository


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None,
        api_key="queue-test-key",
        storage_dir=tmp_path,
        max_active_jobs=2,
        max_running_jobs=1,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def reserve(jobs):
    job_id = uuid4()
    path = jobs.database_path.parent / f"{job_id}.mov"
    jobs.reserve_upload(job_id, path)
    return job_id


def enqueue(jobs):
    job_id = reserve(jobs)
    jobs.publish_upload(job_id, 10)
    return job_id


def upload(client):
    return client.post("/api/v1/analyze", headers=HEADERS, files={"file": ("video.mov", b"video")})


def stored(jobs, job_id):
    with closing(sqlite3.connect(jobs.database_path)) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute("SELECT * FROM jobs WHERE job_id = ?", (str(job_id),)).fetchone()


def test_queue_full_is_503_without_extra_file_or_job(client, tmp_path):
    assert upload(client).status_code == 201
    assert upload(client).status_code == 201
    response = upload(client)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "QUEUE_FULL"
    assert response.headers["Retry-After"] == "5"
    assert len(list((tmp_path / "uploads").iterdir())) == 2
    with closing(sqlite3.connect(tmp_path / "jobs.sqlite3")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
    assert (
        client.post("/api/v1/analyze", files={"file": ("video.mov", b"video")}).status_code == 401
    )


def test_completed_job_releases_capacity(client):
    upload(client)
    upload(client)
    jobs = client.app.state.jobs
    assert jobs.complete(jobs.claim_next(240), NO_VIOLATION)
    assert upload(client).status_code == 201


def test_pending_jobs_expire_without_worker_and_release_capacity(jobs, clock):
    first = enqueue(jobs)
    enqueue(jobs)
    clock.advance(300)
    next_id = enqueue(jobs)
    assert jobs.get(first).error.code == "JOB_TIMEOUT"
    assert jobs.claim_next(240).job_id == next_id


def test_claim_uses_only_remaining_total_budget(jobs, clock):
    job_id = enqueue(jobs)
    original_expiry = datetime.fromisoformat(stored(jobs, job_id)["expires_at"])
    clock.advance(295)
    job = jobs.claim_next(240)
    assert job.deadline_at == original_expiry
    assert (job.deadline_at - clock()).total_seconds() == 5
    clock.advance(5)
    assert not jobs.complete(job, NO_VIOLATION)
    assert jobs.get(job_id).error.code == "JOB_TIMEOUT"


def test_execution_timeout_can_be_shorter_than_total_budget(jobs, clock):
    job_id = enqueue(jobs)
    job = jobs.claim_next(10)
    assert (job.deadline_at - clock()).total_seconds() == 10
    clock.advance(10)
    assert jobs.get(job_id).status == "failed"


def test_expired_job_is_never_claimed(jobs, clock):
    job_id = enqueue(jobs)
    clock.advance(301)
    assert jobs.claim_next(240) is None
    assert stored(jobs, job_id)["status"] == "failed"
    assert stored(jobs, job_id)["started_at"] is None


def test_upload_reservations_count_towards_capacity_but_cannot_be_claimed(jobs):
    reserve(jobs)
    reserve(jobs)
    assert jobs.claim_next(240) is None
    with pytest.raises(QueueFull):
        reserve(jobs)


def test_concurrent_admission_respects_shared_limit(jobs):
    def attempt(_):
        try:
            return reserve(jobs)
        except QueueFull:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(12)))
    assert len([job_id for job_id in results if job_id]) == 2


def test_running_limit_applies_across_repository_instances(jobs, clock):
    enqueue(jobs)
    enqueue(jobs)
    other = JobRepository(jobs.database_path, max_active_jobs=2, clock=clock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda repository: repository.claim_next(240), [jobs, other]))
    claimed = [job for job in claims if job is not None]
    assert len(claimed) == 1
    assert jobs.complete(claimed[0], NO_VIOLATION)
    assert other.claim_next(240) is not None


def test_restart_and_new_configuration_do_not_extend_deadline(jobs, clock):
    job_id = enqueue(jobs)
    expiry = stored(jobs, job_id)["expires_at"]
    clock.advance(200)
    restarted = JobRepository(jobs.database_path, total_timeout_seconds=60, clock=clock)
    restarted.initialize()
    assert stored(restarted, job_id)["expires_at"] == expiry
    clock.advance(100)
    assert restarted.get(job_id).status == "failed"


def test_stage_two_migration_caps_running_deadline_and_expires_old_queue(tmp_path, clock):
    database = tmp_path / "jobs.sqlite3"
    queued, running = uuid4(), uuid4()
    old_created = (clock() - timedelta(seconds=400)).isoformat()
    created = (clock() - timedelta(seconds=290)).isoformat()
    old_execution_deadline = (clock() + timedelta(seconds=240)).isoformat()
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            """CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL,
               video_path TEXT NOT NULL, size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL,
               started_at TEXT, deadline_at TEXT, finished_at TEXT, run_token TEXT,
               result_json TEXT, error_json TEXT)"""
        )
        connection.execute(
            "INSERT INTO jobs (job_id,status,video_path,size_bytes,created_at) VALUES (?,?,?,?,?)",
            (str(queued), "uploaded", str(tmp_path / "queued.mov"), 10, old_created),
        )
        connection.execute(
            """INSERT INTO jobs
               (job_id,status,video_path,size_bytes,created_at,started_at,deadline_at,run_token)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                str(running),
                "processing",
                str(tmp_path / "running.mov"),
                10,
                created,
                created,
                old_execution_deadline,
                "existing-token",
            ),
        )
    jobs = JobRepository(database, clock=clock)
    jobs.initialize()
    assert jobs.get(queued).error.code == "JOB_TIMEOUT"
    row = stored(jobs, running)
    assert row["deadline_at"] == row["expires_at"]
    assert datetime.fromisoformat(row["deadline_at"]) == clock() + timedelta(seconds=10)


def test_rejected_upload_does_not_read_or_create_file(jobs, tmp_path):
    reserve(jobs)
    reserve(jobs)
    uploads = tmp_path / "uploads"
    uploads.mkdir()

    class Unreadable:
        def read(self, size):
            pytest.fail("A full queue must be rejected before copying")

    with pytest.raises(QueueFull):
        UploadService(uploads, 10, jobs).save("video.mov", Unreadable())
    assert list(uploads.iterdir()) == []


def test_timeout_during_upload_removes_file_and_reservation(jobs, clock, tmp_path):
    uploads = tmp_path / "uploads"
    uploads.mkdir()

    class SlowSource(io.BytesIO):
        def read(self, size):
            chunk = super().read(size)
            clock.advance(200)
            return chunk

    with pytest.raises(UploadDeadlineExceeded):
        UploadService(uploads, CHUNK_SIZE * 2, jobs).save(
            "video.mov", SlowSource(b"x" * (CHUNK_SIZE + 1))
        )
    assert list(uploads.iterdir()) == []
    with closing(sqlite3.connect(jobs.database_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    enqueue(jobs)


def test_budget_includes_time_before_route_runs(client, tmp_path, monkeypatch):
    class DelayedArrival:
        @staticmethod
        def now(tz):
            return datetime.now(tz) - timedelta(seconds=301)

    monkeypatch.setattr("app.middleware.datetime", DelayedArrival)
    response = upload(client)
    assert response.status_code == 408
    assert response.json()["detail"]["code"] == "UPLOAD_TIMEOUT"
    assert list((tmp_path / "uploads").iterdir()) == []


def test_api_sweeper_expires_queue_without_get_or_worker(client):
    job_id = upload(client).json()["job_id"]
    jobs = client.app.state.jobs
    jobs._clock = lambda: datetime.now(UTC) + timedelta(seconds=301)
    swept = threading.Event()
    original = jobs.expire_due

    def observe():
        result = original()
        swept.set()
        return result

    jobs.expire_due = observe
    assert swept.wait(3)
    assert stored(jobs, job_id)["status"] == "failed"


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_active_jobs": 0},
        {"max_running_jobs": 0},
        {"max_active_jobs": 1, "max_running_jobs": 2},
        {"total_job_timeout_seconds": 301},
        {"total_job_timeout_seconds": 0},
    ],
)
def test_invalid_queue_settings_are_rejected(overrides):
    with pytest.raises(ValueError):
        Settings(_env_file=None, api_key="test", **overrides)
