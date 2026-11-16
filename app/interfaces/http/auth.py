"""HTTP authentication for API / board / MCP when LA_API_TOKEN is set."""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app import config

log = logging.getLogger("local-agents.auth")

_bearer = HTTPBearer(auto_error=False)
_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def token_from_request(
    creds: HTTPAuthorizationCredentials | None,
    api_key: str | None,
) -> str | None:
    if creds and creds.scheme.lower() == "bearer" and creds.credentials:
        return creds.credentials.strip()
    if api_key and api_key.strip():
        return api_key.strip()
    return None


def require_api_token(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    api_key: str | None = Depends(_api_key_header),
) -> None:
    """Enforce LA_API_TOKEN when configured. Public only when token is empty (local dev)."""
    expected = config.API_TOKEN
    if not expected:
        return
    provided = token_from_request(creds, api_key)
    if not provided or provided != expected:
        raise HTTPException(status_code=401, detail="unauthorized: missing or invalid API token")


def warn_if_exposed_without_token() -> None:
    if config.API_TOKEN:
        return
    if config.is_loopback_host():
        return
    log.warning(
        "LA_HOST=%s binds beyond loopback but LA_API_TOKEN is empty — "
        "HTTP API, /board, and MCP are unauthenticated. Set LA_API_TOKEN before exposing.",
        config.HOST,
    )


async def api_token_middleware(request: Request, call_next):
    """Require LA_API_TOKEN for non-public routes when the token is configured.

    Public without token: /api/health (k8s probes).
    """
    path = request.url.path
    if not config.API_TOKEN:
        return await call_next(request)
    if path == "/api/health" or path.startswith("/api/health/"):
        return await call_next(request)
    auth = request.headers.get("authorization") or ""
    api_key = request.headers.get("x-api-key") or ""
    provided = None
    if auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
    elif api_key.strip():
        provided = api_key.strip()
    if provided != config.API_TOKEN:
        return JSONResponse(
            {"detail": "unauthorized: missing or invalid API token"},
            status_code=401,
        )
    return await call_next(request)
