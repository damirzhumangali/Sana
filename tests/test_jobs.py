import base64
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.main import create_app
from app.schemas import AnalysisResult, EvidenceFrame, JobError
from app.services.jobs import JobRepository
from app.worker import Worker, main

KEY = "test-key-for-local-tests"
HEADERS = {"X-API-Key": KEY}
NO_VIOLATION = AnalysisResult(verdict="no_violation", violations=[])


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, api_key=KEY, storage_dir=tmp_path, max_upload_mb=1)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def upload(client):
    response = client.post(
        "/api/v1/analyze",
        headers=HEADERS,
        files={"file": ("video.mov", b"synthetic video", "video/quicktime")},
    )
    assert response.status_code == 201
    return UUID(response.json()["job_id"])


def read_job(client, job_id):
    response = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS)
    assert response.status_code == 200
    return response.json()


def test_uploaded_job_reports_processing_without_private_fields(client):
    job_id = upload(client)
    assert read_job(client, job_id) == {"job_id": str(job_id), "status": "processing"}


def test_job_errors_and_auth(client):
    url = f"/api/v1/jobs/{uuid4()}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get(url, headers=HEADERS).status_code == 404
    assert client.get("/api/v1/jobs/not-a-uuid", headers=HEADERS).status_code == 422


def test_done_result_survives_api_restart(client, settings):
    job_id = upload(client)
    jobs = client.app.state.jobs
    job = jobs.claim_next(30)
    assert job.job_id == job_id
    assert jobs.complete(job, NO_VIOLATION)
    with TestClient(create_app(settings)) as restarted:
        assert read_job(restarted, job_id) == {
            "job_id": str(job_id),
            "status": "done",
            "verdict": "no_violation",
            "violations": [],
        }


def test_failed_job_has_no_verdict(client):
    job_id = upload(client)
    jobs = client.app.state.jobs
    job = jobs.claim_next(30)
    assert jobs.fail(job, JobError(code="VIDEO_DECODE_ERROR", message="Cannot decode video"))
    response = read_job(client, job_id)
    assert response["status"] == "failed"
    assert response["error"]["code"] == "VIDEO_DECODE_ERROR"
    assert "verdict" not in response


def test_concurrent_claims_cannot_take_the_same_job(client):
    job_id = upload(client)
    jobs = client.app.state.jobs
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: jobs.claim_next(30), range(4)))
    successful = [job for job in claims if job is not None]
    assert len(successful) == 1
    assert successful[0].job_id == job_id


def test_expired_job_fails_and_rejects_late_results(client, settings):
    job_id = upload(client)
    jobs = client.app.state.jobs
    job = jobs.claim_next(30)
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    with closing(sqlite3.connect(settings.storage_dir / "jobs.sqlite3")) as connection, connection:
        connection.execute("UPDATE jobs SET deadline_at = ? WHERE job_id = ?", (past, str(job_id)))
    result = read_job(client, job_id)
    assert result["status"] == "failed"
    assert result["error"]["code"] == "JOB_TIMEOUT"
    assert not jobs.complete(job, NO_VIOLATION)
    assert read_job(client, job_id) == result


def test_invalid_claim_token_and_finished_job_cannot_overwrite_result(client):
    job_id = upload(client)
    jobs = client.app.state.jobs
    job = jobs.claim_next(30)
    assert not jobs.complete(replace(job, run_token="different-worker"), NO_VIOLATION)
    assert jobs.complete(job, NO_VIOLATION)
    assert not jobs.fail(job, JobError(code="LATE_ERROR", message="Late update"))
    assert read_job(client, job_id)["status"] == "done"


def test_old_database_migration_keeps_existing_upload(tmp_path):
    job_id = uuid4()
    database = tmp_path / "jobs.sqlite3"
    video = tmp_path / "existing.mov"
    video.write_bytes(b"original")
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            """CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL,
               video_path TEXT NOT NULL, size_bytes INTEGER NOT NULL, created_at TEXT NOT NULL)"""
        )
        connection.execute(
            "INSERT INTO jobs VALUES (?, ?, ?, ?, ?)",
            (str(job_id), "uploaded", str(video), 8, datetime.now(UTC).isoformat()),
        )
    jobs = JobRepository(database)
    jobs.initialize()
    jobs.initialize()
    assert jobs.get(job_id).status == "processing"
    assert jobs.claim_next(30).video_path == video
    assert video.read_bytes() == b"original"


@pytest.mark.parametrize(
    ("analyzer", "status", "error"),
    [
        ("no_violation", "done", None),
        ("invalid_result", "failed", "INVALID_RESULT"),
        ("raises_error", "failed", "ANALYZER_ERROR"),
        ("crashes", "failed", "WORKER_ERROR"),
    ],
)
def test_spawned_worker_outcomes(client, settings, analyzer, status, error):
    job_id = upload(client)
    worker = Worker(
        client.app.state.jobs,
        f"tests.worker_fixtures:{analyzer}",
        settings.storage_dir / "work",
        timeout_seconds=15,
    )
    assert worker.run_once()
    result = read_job(client, job_id)
    assert result["status"] == status
    if error:
        assert result["error"]["code"] == error
        assert "private diagnostic" not in str(result)
    else:
        assert result["verdict"] == "no_violation"
    assert not worker.run_once()
    assert list((settings.storage_dir / "work").iterdir()) == []


def test_worker_timeout_stops_process_and_next_job_can_run(client, settings):
    first_id = upload(client)
    jobs = client.app.state.jobs
    worker = Worker(jobs, "tests.worker_fixtures:slow", settings.storage_dir / "work", 1)
    started = time.monotonic()
    assert worker.run_once()
    assert time.monotonic() - started < 8
    assert read_job(client, first_id)["error"]["code"] == "JOB_TIMEOUT"
    next_id = upload(client)
    worker.analyzer_callable = "tests.worker_fixtures:no_violation"
    worker.timeout_seconds = 15
    assert worker.run_once()
    assert read_job(client, next_id)["status"] == "done"


def test_missing_video_returns_failed(client, settings):
    job_id = upload(client)
    (settings.storage_dir / "uploads" / f"{job_id}.mov").unlink()
    worker = Worker(
        client.app.state.jobs,
        "tests.worker_fixtures:no_violation",
        settings.storage_dir / "work",
        15,
    )
    assert worker.run_once()
    assert read_job(client, job_id)["error"]["code"] == "VIDEO_NOT_FOUND"


def test_unconfigured_worker_does_not_claim_jobs(client, settings, monkeypatch):
    job_id = upload(client)
    settings.analyzer_callable = None
    monkeypatch.setattr("app.worker.Settings", lambda: settings)
    assert main() == 2
    assert client.app.state.jobs.claim_next(30).job_id == job_id


def test_storage_error_is_503(client, monkeypatch):
    def unavailable(job_id):
        raise sqlite3.OperationalError("private storage details")

    monkeypatch.setattr(client.app.state.jobs, "get", unavailable)
    response = client.get(f"/api/v1/jobs/{uuid4()}", headers=HEADERS)
    assert response.status_code == 503
    assert response.json() == {"detail": "Job storage is unavailable"}


def test_done_violation_preserves_evidence_and_description(client):
    job_id = upload(client)
    # Marker bytes exercise transport validation, not JPEG decoding.
    frame = base64.b64encode(b"\xff\xd8\xffsynthetic\xff\xd9").decode()
    data = {
        "verdict": "violation",
        "violations": [
            {
                "type": "Synthetic event",
                "timecode_sec": 1.2,
                "description": "Visible action; document and rule reference",
                "evidence_frames": [{"timestamp_sec": 1.2, "image_base64": frame}],
            }
        ],
    }
    jobs = client.app.state.jobs
    assert jobs.complete(jobs.claim_next(30), AnalysisResult.model_validate(data))
    assert read_job(client, job_id) == {"job_id": str(job_id), "status": "done", **data}


@pytest.mark.parametrize("frame", ["not base64!", "data:image/jpeg;base64,/9j/", "dGV4dA=="])
def test_invalid_evidence_is_rejected(frame):
    with pytest.raises(ValidationError):
        EvidenceFrame(timestamp_sec=1, image_base64=frame)


@pytest.mark.parametrize("verdict", ["violation", "unknown", "insufficient_data"])
def test_inconsistent_or_unknown_verdict_is_rejected(verdict):
    with pytest.raises(ValidationError):
        AnalysisResult(verdict=verdict, violations=[])


def test_openapi_has_status_discriminator(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/jobs/{job_id}"]["get"]
    result = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert result["discriminator"]["propertyName"] == "status"
    assert operation["security"] == [{"APIKeyHeader": []}]
