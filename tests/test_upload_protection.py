import asyncio
import json
import sqlite3
from contextlib import closing

import pytest
import starlette.formparsers
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

KEY = b"upload-protection-test-key"
BOUNDARY = b"sana-test-boundary"
MIB = 1024 * 1024


@pytest.fixture
def settings(tmp_path, request):
    overrides = {"max_upload_mb": 2, **getattr(request, "param", {})}
    return Settings(_env_file=None, api_key=KEY.decode(), storage_dir=tmp_path, **overrides)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def temporary_files(monkeypatch):
    opened = []
    original = starlette.formparsers.SpooledTemporaryFile

    def tracked(*args, **kwargs):
        file = original(*args, **kwargs)
        opened.append(file)
        return file

    monkeypatch.setattr(starlette.formparsers, "SpooledTemporaryFile", tracked)
    return opened


def multipart(parts):
    data = bytearray()
    for name, filename, content in parts:
        data.extend(b"--" + BOUNDARY + b"\r\n")
        data.extend(f'Content-Disposition: form-data; name="{name}"'.encode())
        if filename is not None:
            data.extend(f'; filename="{filename}"'.encode())
        data.extend(b"\r\n\r\n" + content + b"\r\n")
    data.extend(b"--" + BOUNDARY + b"--\r\n")
    return bytes(data)


def prefix(content):
    return (
        b"--"
        + BOUNDARY
        + b'\r\nContent-Disposition: form-data; name="file"; filename="video.mov"\r\n\r\n'
        + content
    )


def headers(extra=(), keys=(KEY,)):
    return [
        (b"host", b"testserver"),
        (b"content-type", b"multipart/form-data; boundary=" + BOUNDARY),
        *[(b"x-api-key", key) for key in keys],
        *extra,
    ]


def request_event(body, more_body=False):
    return {"type": "http.request", "body": body, "more_body": more_body}


async def exchange(app, raw_headers, events, *, path="/api/v1/analyze"):
    sent = []
    receive_calls = 0
    iterator = iter(events)

    async def receive():
        nonlocal receive_calls
        receive_calls += 1
        event = next(iterator)  # Exhaustion is a test failure: do not drain rejected requests.
        if isinstance(event, BaseException):
            raise event
        if event == "stall":
            await asyncio.Event().wait()
        return event

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=3)
    start = next(item for item in sent if item["type"] == "http.response.start")
    body = b"".join(item.get("body", b"") for item in sent if item["type"] == "http.response.body")
    return start["status"], json.loads(body), receive_calls


def raw_request(client, raw_headers, events, **kwargs):
    return asyncio.run(exchange(client.app, raw_headers, events, **kwargs))


def assert_no_job_or_file(settings):
    assert list((settings.storage_dir / "uploads").iterdir()) == []
    with closing(sqlite3.connect(settings.storage_dir / "jobs.sqlite3")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("keys", [(), (b"wrong",), (KEY, KEY)])
@pytest.mark.parametrize("path", ["/api/v1/analyze", "/api/v1/analyze/"])
def test_rejects_key_before_reading_even_an_oversized_request(
    client,
    settings,
    temporary_files,
    keys,
    path,
):
    result, data, reads = raw_request(
        client,
        headers([(b"content-length", b"999999999")], keys=keys),
        [],
        path=path,
    )
    assert result == 401
    assert data == {"detail": "Missing or invalid API key"}
    assert reads == 0
    assert temporary_files == []
    assert_no_job_or_file(settings)


@pytest.mark.parametrize("length", [b"", b"-1", b"+1", b"1.0", b"abc", b"1, 1"])
def test_invalid_length_is_400_without_reading(client, settings, length):
    status, data, reads = raw_request(client, headers([(b"content-length", length)]), [])
    assert status == 400
    assert data["detail"]["code"] == "INVALID_CONTENT_LENGTH"
    assert reads == 0
    assert_no_job_or_file(settings)


@pytest.mark.parametrize(
    "extra",
    [
        [(b"content-length", b"10"), (b"content-length", b"10")],
        [(b"content-length", b"10"), (b"transfer-encoding", b"chunked")],
        [(b"transfer-encoding", b"chunked"), (b"transfer-encoding", b"chunked")],
        [(b"transfer-encoding", b"gzip, chunked")],
    ],
)
def test_ambiguous_headers_are_rejected(client, extra):
    status, data, reads = raw_request(client, headers(extra), [])
    assert status == 400
    assert data["detail"]["code"] == "INVALID_REQUEST_HEADERS"
    assert reads == 0


@pytest.mark.parametrize("length", [b"3145729", b"9" * 5000])
def test_declared_oversize_is_413_without_reading(client, settings, temporary_files, length):
    status, data, reads = raw_request(client, headers([(b"content-length", length)]), [])
    assert status == 413
    assert data["detail"]["code"] == "REQUEST_TOO_LARGE"
    assert reads == 0
    assert temporary_files == []
    assert_no_job_or_file(settings)


@pytest.mark.parametrize("extra", [[], [(b"transfer-encoding", b"chunked")]])
def test_stream_limit_without_length_stops_reading_and_closes_disk_spool(
    client,
    settings,
    temporary_files,
    extra,
):
    first = prefix(b"x" * (MIB + 10))
    status, data, reads = raw_request(
        client,
        headers(extra),
        [
            request_event(first, True),
            request_event(b"x" * (3 * MIB), True),
            request_event(b"must not be read"),
        ],
    )
    assert status == 413
    assert data["detail"]["code"] == "REQUEST_TOO_LARGE"
    assert reads == 2
    assert len(temporary_files) == 1
    assert temporary_files[0]._rolled
    assert temporary_files[0].closed
    assert_no_job_or_file(settings)


def test_file_limit_is_enforced_during_parsing(client, settings, temporary_files):
    body = multipart([("file", "video.mov", b"x" * (2 * MIB + 1))])
    status, data, _ = raw_request(
        client, headers([(b"content-length", str(len(body)).encode())]), [request_event(body)]
    )
    assert status == 413
    assert data["detail"]["code"] == "FILE_TOO_LARGE"
    assert temporary_files and all(file.closed for file in temporary_files)
    assert_no_job_or_file(settings)


@pytest.mark.parametrize("extra", [[], [(b"transfer-encoding", b"chunked")]])
def test_small_stream_without_length_is_accepted(client, settings, temporary_files, extra):
    body = multipart([("file", "video.MOV", b"transport test")])
    status, data, reads = raw_request(
        client,
        headers(extra),
        [
            request_event(body[:80], True),
            request_event(body[80:]),
        ],
    )
    assert status == 201
    assert reads == 2
    saved = settings.storage_dir / "uploads" / f"{data['job_id']}.mov"
    assert saved.read_bytes() == b"transport test"
    assert all(file.closed for file in temporary_files)


def test_exact_file_limit_is_accepted_with_multipart_overhead(client, settings):
    response = client.post(
        "/api/v1/analyze",
        headers={"X-API-Key": KEY.decode()},
        files={"file": ("video.mov", b"x" * settings.max_upload_bytes)},
    )
    assert response.status_code == 201


@pytest.mark.parametrize("difference", [-1, 1])
def test_delivered_body_must_match_declared_length(client, settings, temporary_files, difference):
    body = multipart([("file", "video.mov", b"x" * (MIB + 10))])
    length = str(len(body) + difference).encode()
    status, data, _ = raw_request(
        client,
        headers([(b"content-length", length)]),
        [
            request_event(body[:-60], True),
            request_event(body[-60:]),
        ],
    )
    assert status == 400
    assert data["detail"]["code"] == "INVALID_CONTENT_LENGTH"
    assert temporary_files and all(file.closed for file in temporary_files)
    assert_no_job_or_file(settings)


def test_truncated_multipart_closes_unfinished_file(client, settings, temporary_files):
    body = prefix(b"x" * (MIB + 10))
    status, data, _ = raw_request(
        client,
        headers([(b"content-length", str(len(body)).encode())]),
        [request_event(body)],
    )
    assert status == 400
    assert data == {"detail": "Incomplete multipart body"}
    assert temporary_files[0]._rolled and temporary_files[0].closed
    assert_no_job_or_file(settings)


@pytest.mark.parametrize("last", [{"type": "http.disconnect"}, asyncio.CancelledError()])
def test_disconnect_or_cancellation_closes_temporary_file(client, settings, temporary_files, last):
    events = [request_event(prefix(b"x" * (MIB + 10)), True), last]
    if isinstance(last, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            raw_request(client, headers(), events)
    else:
        status, _, _ = raw_request(client, headers(), events)
        assert status == 400
    assert temporary_files[0]._rolled and temporary_files[0].closed
    assert_no_job_or_file(settings)


@pytest.mark.parametrize("settings", [{"total_job_timeout_seconds": 1}], indirect=True)
def test_stalled_upload_times_out_and_closes_temporary_file(client, settings, temporary_files):
    status, data, reads = raw_request(
        client,
        headers(),
        [
            request_event(prefix(b"x" * (MIB + 10)), True),
            "stall",
        ],
    )
    assert status == 408
    assert data["detail"]["code"] == "UPLOAD_TIMEOUT"
    assert reads == 2
    assert temporary_files[0]._rolled and temporary_files[0].closed
    assert_no_job_or_file(settings)


@pytest.mark.parametrize(
    "parts",
    [
        [("file", "first.mov", b"x" * (MIB + 10)), ("file", "second.mov", b"second")],
        [("file", "first.mov", b"x" * (MIB + 10)), ("extra", None, b"field")],
        [("wrong", "video.mov", b"payload")],
        [("file", "video.mp4", b"payload")],
    ],
)
def test_accepts_only_one_mov_file_field(client, settings, temporary_files, parts):
    body = multipart(parts)
    status, _, _ = raw_request(client, headers(), [request_event(body)])
    assert status == 400
    assert temporary_files and all(file.closed for file in temporary_files)
    assert_no_job_or_file(settings)


def test_missing_boundary_is_400(client, settings):
    response = client.post(
        "/api/v1/analyze",
        headers={"X-API-Key": KEY.decode(), "Content-Type": "multipart/form-data"},
        content=b"malformed body",
    )
    assert response.status_code == 400
    assert_no_job_or_file(settings)


def test_swagger_keeps_binary_file_input_and_security(client):
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/analyze"]["post"]
    body_schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert body_schema["properties"]["file"]["format"] == "binary"
    assert body_schema["required"] == ["file"]
    assert operation["security"] == [{"APIKeyHeader": []}]


def test_body_limit_settings_include_overhead():
    settings = Settings(_env_file=None, api_key="test", max_upload_mb=1)
    assert settings.max_request_bytes == 2 * MIB
    settings = Settings(_env_file=None, api_key="test", max_upload_mb=1, max_request_mb=3)
    assert settings.max_request_bytes == 3 * MIB
    for request_size in (0, 1):
        with pytest.raises(ValueError):
            Settings(_env_file=None, api_key="test", max_upload_mb=1, max_request_mb=request_size)
