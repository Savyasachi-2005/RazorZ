"""Request authentication.

Two credential types are accepted on protected routes:

1. **User session token** — `Authorization: Bearer <token>` from `POST /auth/login`.
   Opaque, server-side, revocable on logout.
2. **API key** — `X-API-Key: <key>` (or `Authorization: Bearer <key>`) for
   machine clients. Keys live only in `RAZORZ_API_KEYS`.

Credentials are never logged, echoed in responses, or stored in plaintext.

The dependency is installed application-wide so a newly added route is
protected by default. Only `PUBLIC_PATHS` are exempt: the health probe, the
OpenAPI docs, the login endpoint, and the Razorpay webhook — which
authenticates with its own HMAC signature over the raw body and must not be
given a second scheme.
"""

from __future__ import annotations

import hmac
import logging
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request

from app.config import settings

logger = logging.getLogger("razorz.security")

API_KEY_HEADER = "x-api-key"
BEARER_PREFIX = "bearer "

PUBLIC_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
        # Credentials are exchanged here; it cannot require a credential.
        "/auth/login",
        # Authenticated by Razorpay HMAC signature verification, not by API key.
        "/integrations/razorpay/webhook",
    }
)


def configured_keys() -> List[str]:
    """API keys from the environment. Read per request so rotation needs no restart."""
    raw = settings.razorz_api_keys or ""
    return [key.strip() for key in raw.split(",") if key.strip()]


def auth_enabled() -> bool:
    """True when protected routes require a credential."""
    return bool(configured_keys()) or settings.razorz_auth_required


def is_public_path(path: str) -> bool:
    return path.rstrip("/") in {p.rstrip("/") for p in PUBLIC_PATHS} or path in PUBLIC_PATHS


def bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization") or ""
    if authorization.lower().startswith(BEARER_PREFIX):
        return authorization[len(BEARER_PREFIX) :].strip()
    return ""


def _presented_key(request: Request) -> str:
    header = request.headers.get(API_KEY_HEADER)
    if header and header.strip():
        return header.strip()
    return bearer_token(request)


def key_is_valid(presented: str) -> bool:
    if not presented:
        return False
    # compare_digest against every configured key: no early exit on length.
    return any(hmac.compare_digest(presented, known) for known in configured_keys())


def resolve_session_user(token: str) -> Optional[Dict[str, Any]]:
    """Look up an active session. Imported lazily to keep this module import-light."""
    if not token:
        return None
    from app.auth.service import me

    try:
        return me(token)
    except Exception:
        logger.warning("Session lookup failed")
        return None


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"message": "Missing or invalid credentials", "code": "unauthorized"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_api_key(request: Request) -> None:
    """Reject unauthenticated requests to protected routes with 401.

    Accepts a user session token or a machine API key. The name is kept for
    backwards compatibility with existing callers.
    """
    path = request.url.path
    # CORS preflight carries no credentials by design.
    if request.method == "OPTIONS" or is_public_path(path):
        return

    token = bearer_token(request)
    user = resolve_session_user(token) if token else None
    if user is not None:
        request.state.user = user
        return

    if key_is_valid(_presented_key(request)):
        request.state.user = None
        return

    if not auth_enabled():
        if settings.env.lower() in {"production", "prod"}:
            # Refuse to serve an unauthenticated production API.
            raise HTTPException(
                status_code=503,
                detail={
                    "message": "RAZORZ_API_KEYS is not configured; API authentication is required "
                    "in production",
                    "code": "auth_not_configured",
                },
            )
        return

    # Log the path only — never the presented value or the configured keys.
    logger.warning("Rejected unauthenticated request to %s", path)
    raise _unauthorized()
