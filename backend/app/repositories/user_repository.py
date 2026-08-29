from __future__ import annotations

"""Users and server-side sessions.

Reconciliation, the Copilot and the evaluation harness never read this layer.
Only password hashes and token hashes are persisted.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

from app.auth.passwords import hash_password, verify_password
from app.db import create_db_and_tables, get_session
from app.models import User, UserSession

TOKEN_BYTES = 32
ROLES = ("admin", "reviewer", "viewer")

# Hash of a throwaway password, used to keep the unknown-email path as slow as
# the known-email path so responses do not reveal which emails exist.
_DUMMY_HASH = (
    "pbkdf2_sha256$600000$AAAAAAAAAAAAAAAAAAAAAA==$"
    "Ry3H1oQb3q6t8yq0dV2b3xE0J8Wv5Nn0YqTf9pQe0mA="
)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; compare everything in UTC."""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class UserRepository:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url
        create_db_and_tables(database_url)

    def _session(self) -> Session:
        return get_session(self.database_url)

    # ---------- users ----------

    def create_user(
        self,
        *,
        email: str,
        password: str,
        full_name: str = "",
        role: str = "reviewer",
    ) -> Dict[str, Any]:
        address = normalize_email(email)
        if not address or "@" not in address:
            raise ValueError("A valid email address is required")
        if role not in ROLES:
            raise ValueError(f"role must be one of {', '.join(ROLES)}")

        with self._session() as session:
            existing = session.exec(select(User).where(User.email == address)).first()
            if existing is not None:
                raise ValueError(f"User {address} already exists")
            user = User(
                email=address,
                full_name=(full_name or "").strip(),
                password_hash=hash_password(password),
                role=role,
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return self.serialize(user)

    def count_users(self) -> int:
        with self._session() as session:
            return len(list(session.exec(select(User.id)).all()))

    def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        with self._session() as session:
            user = session.exec(select(User).where(User.email == normalize_email(email))).first()
            return self.serialize(user) if user else None

    def list_users(self) -> List[Dict[str, Any]]:
        with self._session() as session:
            return [self.serialize(row) for row in session.exec(select(User)).all()]

    def set_password(self, email: str, password: str) -> bool:
        with self._session() as session:
            user = session.exec(select(User).where(User.email == normalize_email(email))).first()
            if user is None:
                return False
            user.password_hash = hash_password(password)
            user.updated_at = _now()
            session.add(user)
            session.commit()
        return True

    def authenticate(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        """Verify credentials. Returns None for unknown, inactive or wrong password."""
        address = normalize_email(email)
        with self._session() as session:
            user = session.exec(select(User).where(User.email == address)).first()
            if user is None:
                # Spend comparable time so a missing account is not detectable.
                verify_password(password or "x", _DUMMY_HASH)
                return None
            if not verify_password(password or "", user.password_hash):
                return None
            if not user.is_active:
                return None
            user.last_login_at = _now()
            session.add(user)
            session.commit()
            session.refresh(user)
            return self.serialize(user)

    # ---------- sessions ----------

    def create_session(self, user_id: int, *, ttl_hours: int = 12) -> Tuple[str, Dict[str, Any]]:
        """Issue an opaque token. Only its hash is stored."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        expires_at = _now() + timedelta(hours=max(1, int(ttl_hours)))
        with self._session() as session:
            row = UserSession(
                user_id=user_id,
                token_hash=token_hash(token),
                expires_at=expires_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            payload = {"id": row.id, "user_id": row.user_id, "expires_at": expires_at.isoformat()}
        return token, payload

    def resolve_session(self, token: str) -> Optional[Dict[str, Any]]:
        """Return the active user for a token, or None when it cannot be used."""
        if not token:
            return None
        digest = token_hash(token)
        with self._session() as session:
            row = session.exec(select(UserSession).where(UserSession.token_hash == digest)).first()
            if row is None or row.revoked_at is not None:
                return None
            expires_at = _aware(row.expires_at)
            if expires_at is None or expires_at <= _now():
                return None
            user = session.get(User, row.user_id)
            if user is None or not user.is_active:
                return None
            row.last_seen_at = _now()
            session.add(row)
            session.commit()
            payload = self.serialize(user)
            payload["session_expires_at"] = expires_at.isoformat()
            return payload

    def revoke_session(self, token: str) -> bool:
        if not token:
            return False
        digest = token_hash(token)
        with self._session() as session:
            row = session.exec(select(UserSession).where(UserSession.token_hash == digest)).first()
            if row is None or row.revoked_at is not None:
                return False
            row.revoked_at = _now()
            session.add(row)
            session.commit()
        return True

    def revoke_all_sessions(self, user_id: int) -> int:
        revoked = 0
        with self._session() as session:
            rows = session.exec(
                select(UserSession)
                .where(UserSession.user_id == user_id)
                .where(UserSession.revoked_at.is_(None))
            ).all()
            for row in rows:
                row.revoked_at = _now()
                session.add(row)
                revoked += 1
            session.commit()
        return revoked

    def serialize(self, user: User) -> Dict[str, Any]:
        """Public user shape. Never includes the password hash."""
        return {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": user.is_active,
            "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        }
