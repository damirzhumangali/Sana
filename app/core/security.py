import secrets
from typing import Annotated

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def valid_api_key(provided: str | None, expected: str) -> bool:
    return provided is not None and secrets.compare_digest(provided.encode(), expected.encode())


def require_api_key(
    request: Request,
    api_key: Annotated[str | None, Security(api_key_header)],
) -> None:
    expected = request.app.state.settings.api_key.get_secret_value()
    if len(request.headers.getlist("X-API-Key")) != 1 or not valid_api_key(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
            headers={"WWW-Authenticate": "APIKey"},
        )
