"""Password hashing — PBKDF2-HMAC-SHA256, standard library only.

Format: `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>`. The salt is random
per password, so identical passwords never share a hash. Verification is
constant-time. Plaintext passwords are never stored, logged or returned.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 600_000
SALT_BYTES = 16
DERIVED_BYTES = 32

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200


class PasswordPolicyError(ValueError):
    """Raised when a password cannot be accepted."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, DERIVED_BYTES)


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise PasswordPolicyError(f"Password must be at most {MAX_PASSWORD_LENGTH} characters")


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    validate_password(password)
    salt = secrets.token_bytes(SALT_BYTES)
    derived = _derive(password, salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64(salt)}${_b64(derived)}"


def verify_password(password: str, encoded: str | None) -> bool:
    """Constant-time verification. Malformed or empty hashes fail closed."""
    if not password or not encoded:
        return False
    try:
        algorithm, iterations_text, salt_text, hash_text = encoded.split("$", 3)
        if algorithm != ALGORITHM:
            return False
        iterations = int(iterations_text)
        salt = _unb64(salt_text)
        expected = _unb64(hash_text)
    except Exception:
        return False
    if iterations <= 0 or not salt or not expected:
        return False
    return hmac.compare_digest(_derive(password, salt, iterations), expected)


def needs_rehash(encoded: str | None, *, iterations: int = ITERATIONS) -> bool:
    """True when a stored hash uses a weaker algorithm or fewer iterations."""
    if not encoded:
        return True
    try:
        algorithm, iterations_text, _, _ = encoded.split("$", 3)
    except ValueError:
        return True
    if algorithm != ALGORITHM:
        return True
    try:
        return int(iterations_text) < iterations
    except ValueError:
        return True
