from datetime import UTC, datetime

from starlette.types import ASGIApp, Receive, Scope, Send


class UploadTimingMiddleware:
    """Record arrival before FastAPI parses/spools the multipart upload."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"] == "/api/v1/analyze"
        ):
            scope.setdefault("state", {})["upload_started_at"] = datetime.now(UTC)
        await self.app(scope, receive, send)
