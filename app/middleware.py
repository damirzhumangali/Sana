import asyncio
import time
from datetime import UTC, datetime

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.security import valid_api_key


class UploadProtectionMiddleware:
    """Authenticate and bound upload data before the multipart parser sees it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        # Also protect the trailing-slash URL before FastAPI redirects it.
        if scope["path"].rstrip("/") != "/api/v1/analyze":
            return await self.app(scope, receive, send)

        scope.setdefault("state", {})["upload_started_at"] = datetime.now(UTC)
        settings = scope["app"].state.settings
        deadline = time.monotonic() + settings.total_job_timeout_seconds
        headers: dict[bytes, list[bytes]] = {}
        for name, value in scope["headers"]:
            headers.setdefault(name.lower(), []).append(value)

        keys = headers.get(b"x-api-key", [])
        if len(keys) != 1 or not valid_api_key(
            keys[0].decode("latin-1"), settings.api_key.get_secret_value()
        ):
            response = JSONResponse(
                {"detail": "Missing or invalid API key"},
                status_code=401,
                headers={"WWW-Authenticate": "APIKey"},
            )
            return await response(scope, receive, send)

        lengths = headers.get(b"content-length", [])
        encodings = headers.get(b"transfer-encoding", [])
        if (
            len(lengths) > 1
            or len(encodings) > 1
            or (lengths and encodings)
            or (encodings and encodings[0].strip().lower() != b"chunked")
        ):
            return await JSONResponse(
                {
                    "detail": {
                        "code": "INVALID_REQUEST_HEADERS",
                        "message": "Ambiguous request framing",
                    }
                },
                status_code=400,
            )(scope, receive, send)

        declared_length = None
        if lengths:
            raw_length = lengths[0].strip()
            if not raw_length or not raw_length.isdigit():
                return await JSONResponse(
                    {
                        "detail": {
                            "code": "INVALID_CONTENT_LENGTH",
                            "message": "Invalid Content-Length",
                        }
                    },
                    status_code=400,
                )(scope, receive, send)
            # Compare digits before int(): a huge header must not hit Python's integer limit.
            normalized = raw_length.lstrip(b"0") or b"0"
            maximum = str(settings.max_request_bytes).encode("ascii")
            if len(normalized) > len(maximum) or (
                len(normalized) == len(maximum) and normalized > maximum
            ):
                return await JSONResponse(
                    {
                        "detail": {
                            "code": "REQUEST_TOO_LARGE",
                            "message": "Request body exceeds its limit",
                        }
                    },
                    status_code=413,
                )(scope, receive, send)
            declared_length = int(normalized)

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HTTPException(
                    408,
                    detail={
                        "code": "UPLOAD_TIMEOUT",
                        "message": "Upload exhausted the job time budget",
                    },
                )
            try:
                message = await asyncio.wait_for(receive(), timeout=remaining)
            except TimeoutError as exc:
                raise HTTPException(
                    408,
                    detail={
                        "code": "UPLOAD_TIMEOUT",
                        "message": "Upload exhausted the job time budget",
                    },
                ) from exc
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > settings.max_request_bytes:
                    raise HTTPException(
                        413,
                        detail={
                            "code": "REQUEST_TOO_LARGE",
                            "message": "Request body exceeds its limit",
                        },
                    )
                if declared_length is not None and (
                    received > declared_length
                    or (not message.get("more_body", False) and received != declared_length)
                ):
                    raise HTTPException(
                        400,
                        detail={
                            "code": "INVALID_CONTENT_LENGTH",
                            "message": "Body length does not match Content-Length",
                        },
                    )
            return message

        await self.app(scope, limited_receive, send)
