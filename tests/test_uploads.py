import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

TEST_KEY = "test-key-for-local-tests"
HEADERS = {"X-API-Key": TEST_KEY}


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, api_key=TEST_KEY, storage_dir=tmp_path, max_upload_mb=1)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def assert_storage_empty(settings):
    assert list((settings.storage_dir / "uploads").iterdir()) == []
    with closing(sqlite3.connect(settings.storage_dir / "jobs.sqlite3")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_upload_persists_file_and_job_after_restart(client, settings):
    # Synthetic bytes test transport/storage; video decoding belongs to the next stage.
    content = b"synthetic upload payload"
    response = client.post(
        "/api/v1/analyze",
        headers=HEADERS,
        files={"file": ("camera.mov", content, "video/quicktime")},
    )
    assert response.status_code == 201
    job_id = response.json()["job_id"]
    assert str(UUID(job_id)) == job_id
    assert set(response.json()) == {"job_id"}
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/health").status_code == 200
        with closing(sqlite3.connect(settings.storage_dir / "jobs.sqlite3")) as connection:
            row = connection.execute(
                "SELECT status, video_path, size_bytes FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        assert row[0] == "uploaded"
        assert Path(row[1]).read_bytes() == content
        assert row[2] == len(content)


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong-key"}])
def test_upload_requires_correct_key(client, settings, headers):
    response = client.post(
        "/api/v1/analyze", headers=headers, files={"file": ("video.mov", b"content")}
    )
    assert response.status_code == 401
    assert_storage_empty(settings)


@pytest.mark.parametrize(
    ("filename", "content"), [("video.mp4", b"content"), ("video.mov", b"")]
)
def test_invalid_upload_leaves_no_file_or_job(client, settings, filename, content):
    response = client.post(
        "/api/v1/analyze", headers=HEADERS, files={"file": (filename, content)}
    )
    assert response.status_code == 400
    assert_storage_empty(settings)


def test_size_limit_removes_partial_file(client, settings):
    response = client.post(
        "/api/v1/analyze",
        headers=HEADERS,
        files={"file": ("video.mov", b"x" * (settings.max_upload_bytes + 1))},
    )
    assert response.status_code == 413
    assert_storage_empty(settings)


def test_exact_size_limit_is_accepted(client, settings):
    response = client.post(
        "/api/v1/analyze",
        headers=HEADERS,
        files={"file": ("video.mov", b"x" * settings.max_upload_bytes)},
    )
    assert response.status_code == 201


def test_filename_cannot_control_storage_path_and_ids_are_unique(client, settings):
    ids = []
    for _ in range(2):
        response = client.post(
            "/api/v1/analyze",
            headers=HEADERS,
            files={"file": ("../../outside.MOV", b"content", "application/octet-stream")},
        )
        assert response.status_code == 201
        job_id = response.json()["job_id"]
        assert (settings.storage_dir / "uploads" / f"{job_id}.mov").is_file()
        ids.append(job_id)
    assert len(set(ids)) == 2
    assert not (settings.storage_dir.parent / "outside.MOV").exists()


def test_database_failure_removes_saved_file(client, settings, monkeypatch):
    def fail_insert(*args, **kwargs):
        raise sqlite3.OperationalError("simulated database failure")

    monkeypatch.setattr(client.app.state.uploads.jobs, "publish_upload", fail_insert)
    response = client.post(
        "/api/v1/analyze", headers=HEADERS, files={"file": ("video.mov", b"content")}
    )
    assert response.status_code == 503
    assert_storage_empty(settings)


def test_disk_write_failure_removes_partial_file(client, settings, monkeypatch):
    original_open = Path.open

    class FailingOutput:
        def __init__(self, output):
            self.output = output

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.output.close()

        def write(self, chunk):
            self.output.write(chunk[:1])
            raise OSError("simulated disk full")

        def close(self):
            self.output.close()

    def fail_write(path, mode="r", *args, **kwargs):
        output = original_open(path, mode, *args, **kwargs)
        return FailingOutput(output) if mode == "xb" else output

    monkeypatch.setattr(Path, "open", fail_write)
    response = client.post(
        "/api/v1/analyze", headers=HEADERS, files={"file": ("video.mov", b"content")}
    )
    assert response.status_code == 503
    assert_storage_empty(settings)


def test_missing_file_returns_validation_error(client, settings):
    response = client.post("/api/v1/analyze", headers=HEADERS)
    assert response.status_code == 422
    assert_storage_empty(settings)


def test_openapi_describes_multipart_and_key(client):
    schema = client.get("/openapi.json").json()
    endpoint = schema["paths"]["/api/v1/analyze"]["post"]
    assert "multipart/form-data" in endpoint["requestBody"]["content"]
    assert endpoint["security"] == [{"APIKeyHeader": []}]


@pytest.mark.parametrize("api_key", ["", " ", "replace-with-your-generated-key"])
def test_placeholder_or_empty_key_is_rejected(api_key, tmp_path):
    with pytest.raises(ValueError):
        Settings(_env_file=None, api_key=api_key, storage_dir=tmp_path)
