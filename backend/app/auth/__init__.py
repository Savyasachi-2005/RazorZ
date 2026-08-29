"""User authentication: password hashing and server-side sessions."""

from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.service import (
    AuthError,
    SESSION_TTL_HOURS,
    bootstrap_admin,
    login,
    logout,
    me,
)

__all__ = [
    "AuthError",
    "SESSION_TTL_HOURS",
    "bootstrap_admin",
    "hash_password",
    "login",
    "logout",
    "me",
    "needs_rehash",
    "verify_password",
]
