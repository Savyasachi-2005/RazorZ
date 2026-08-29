"""Login / logout / current-user orchestration.

Audit events record who signed in and whether it succeeded. Passwords, tokens
and token hashes are never written to the audit trail or the logs.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from app.repositories.reconciliation_repository import ReconciliationRepository
from app.repositories.user_repository import UserRepository, normalize_email

logger = logging.getLogger("razorz.auth")

SESSION_TTL_HOURS = 12


class AuthError(Exception):
    """Controlled authentication failure."""

    def __init__(self, message: str, *, code: str = "unauthorized"):
        super().__init__(message)
        self.message = message
        self.code = code


def _audit(
    database_url: str | None,
    *,
    action: str,
    email: str,
    success: bool,
    role: str | None = None,
) -> None:
    try:
        ReconciliationRepository(database_url).record_auth_event(
            action=action,
            email=email,
            success=success,
            role=role,
        )
    except Exception:  # auditing must never block or leak an auth decision
        logger.warning("Could not write auth audit event for action=%s", action)


def login(
    email: str,
    password: str,
    *,
    database_url: str | None = None,
) -> Dict[str, Any]:
    """Verify credentials and issue a session token."""
    address = normalize_email(email)
    users = UserRepository(database_url)
    user = users.authenticate(address, password)
    if user is None:
        _audit(database_url, action="login_failed", email=address, success=False)
        # Identical message for unknown email and wrong password.
        raise AuthError("Invalid email or password", code="invalid_credentials")

    token, session = users.create_session(int(user["id"]), ttl_hours=SESSION_TTL_HOURS)
    _audit(database_url, action="login", email=address, success=True, role=user["role"])
    return {
        "token": token,
        "token_type": "bearer",
        "expires_at": session["expires_at"],
        "user": user,
    }


def logout(token: str, *, database_url: str | None = None) -> Dict[str, Any]:
    users = UserRepository(database_url)
    user = users.resolve_session(token)
    revoked = users.revoke_session(token)
    _audit(
        database_url,
        action="logout",
        email=str(user["email"]) if user else "unknown",
        success=revoked,
        role=str(user["role"]) if user else None,
    )
    return {"logged_out": True, "session_revoked": revoked}


def me(token: str, *, database_url: str | None = None) -> Optional[Dict[str, Any]]:
    return UserRepository(database_url).resolve_session(token)


def bootstrap_admin(database_url: str | None = None) -> Optional[Dict[str, Any]]:
    """Create the first admin from the environment, if configured and absent.

    Credentials come from `RAZORZ_ADMIN_EMAIL` / `RAZORZ_ADMIN_PASSWORD` and are
    never logged. Existing users are left untouched.
    """
    email = (os.getenv("RAZORZ_ADMIN_EMAIL") or "").strip()
    password = os.getenv("RAZORZ_ADMIN_PASSWORD") or ""
    if not email or not password:
        return None

    users = UserRepository(database_url)
    if users.get_by_email(email) is not None:
        return None
    try:
        created = users.create_user(
            email=email,
            password=password,
            full_name=(os.getenv("RAZORZ_ADMIN_NAME") or "Administrator").strip(),
            role="admin",
        )
    except ValueError as exc:
        logger.warning("Admin bootstrap skipped: %s", exc)
        return None
    logger.info("Bootstrapped admin user %s", created["email"])
    _audit(database_url, action="user_created", email=created["email"], success=True, role="admin")
    return created
