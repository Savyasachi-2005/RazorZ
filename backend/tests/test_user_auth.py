from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.auth.passwords import (
    ITERATIONS,
    MIN_PASSWORD_LENGTH,
    PasswordPolicyError,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.auth.service import AuthError, bootstrap_admin, login, logout, me
from app.config import settings
from app.db import get_session
from app.main import app
from app.models import AuditEvent, User, UserSession
from app.repositories.user_repository import UserRepository, token_hash

PASSWORD = "correct-horse-battery"
EMAIL = "ops@razorz.local"

# A smaller iteration count keeps the suite fast where the KDF itself is not
# what is being tested. Strength is verified separately against the default.
FAST_ITERATIONS = 1_000


@contextmanager
def _settings_override(**values):
    original = {name: getattr(settings, name) for name in values}
    for name, value in values.items():
        object.__setattr__(settings, name, value)
    try:
        yield
    finally:
        for name, value in original.items():
            object.__setattr__(settings, name, value)


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{(tmp_path / 'auth.db').as_posix()}"


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """Hash with fewer rounds so the suite is not dominated by the KDF.

    `test_default_iterations_are_strong_and_rehash_is_detected` still asserts the
    production iteration count.
    """
    monkeypatch.setattr(
        "app.repositories.user_repository.hash_password",
        lambda password, iterations=FAST_ITERATIONS: hash_password(
            password, iterations=iterations
        ),
    )


@pytest.fixture
def users(db_url):
    return UserRepository(db_url)


@pytest.fixture
def user(users):
    return users.create_user(email=EMAIL, password=PASSWORD, full_name="Ops Lead", role="admin")


# --- password hashing ---------------------------------------------------


def test_password_hash_is_not_the_password():
    encoded = hash_password(PASSWORD, iterations=FAST_ITERATIONS)
    assert PASSWORD not in encoded
    assert encoded.startswith("pbkdf2_sha256$")
    assert verify_password(PASSWORD, encoded) is True


def test_wrong_password_is_rejected():
    encoded = hash_password(PASSWORD, iterations=FAST_ITERATIONS)
    assert verify_password("wrong-password", encoded) is False
    assert verify_password(PASSWORD.upper(), encoded) is False
    assert verify_password("", encoded) is False


def test_same_password_produces_different_hashes():
    first = hash_password(PASSWORD, iterations=FAST_ITERATIONS)
    second = hash_password(PASSWORD, iterations=FAST_ITERATIONS)
    assert first != second
    assert verify_password(PASSWORD, first) and verify_password(PASSWORD, second)


def test_malformed_or_missing_hash_fails_closed():
    for encoded in (None, "", "not-a-hash", "pbkdf2_sha256$0$$", "md5$1$a$b"):
        assert verify_password(PASSWORD, encoded) is False


def test_default_iterations_are_strong_and_rehash_is_detected():
    assert ITERATIONS >= 600_000
    assert needs_rehash(hash_password(PASSWORD, iterations=FAST_ITERATIONS)) is True
    assert needs_rehash("md5$1$a$b") is True
    assert needs_rehash(None) is True


def test_short_password_is_refused():
    with pytest.raises(PasswordPolicyError):
        hash_password("x" * (MIN_PASSWORD_LENGTH - 1))


# --- user storage -------------------------------------------------------


def test_created_user_stores_only_a_hash(db_url, user):
    with get_session(db_url) as session:
        rows = list(session.exec(select(User)).all())
    assert len(rows) == 1
    assert rows[0].email == EMAIL
    assert PASSWORD not in rows[0].password_hash
    assert verify_password(PASSWORD, rows[0].password_hash) is True


def test_serialized_user_never_exposes_the_hash(user):
    assert "password_hash" not in user
    assert "password" not in user
    assert user["role"] == "admin"


def test_email_is_normalized_and_duplicates_are_refused(users, user):
    assert users.get_by_email("  OPS@RAZORZ.LOCAL ")["id"] == user["id"]
    with pytest.raises(ValueError):
        users.create_user(email="OPS@razorz.local", password=PASSWORD)


def test_invalid_email_and_role_are_refused(users):
    with pytest.raises(ValueError):
        users.create_user(email="not-an-email", password=PASSWORD)
    with pytest.raises(ValueError):
        users.create_user(email="a@b.com", password=PASSWORD, role="superuser")


def test_authenticate_accepts_correct_credentials(users, user):
    assert users.authenticate(EMAIL, PASSWORD)["id"] == user["id"]


def test_authenticate_rejects_wrong_unknown_and_inactive(users, user, db_url):
    assert users.authenticate(EMAIL, "nope") is None
    assert users.authenticate("ghost@razorz.local", PASSWORD) is None

    with get_session(db_url) as session:
        row = session.exec(select(User)).first()
        row.is_active = False
        session.add(row)
        session.commit()
    assert users.authenticate(EMAIL, PASSWORD) is None


def test_last_login_is_recorded(users, user):
    assert user["last_login_at"] is None
    assert users.authenticate(EMAIL, PASSWORD)["last_login_at"] is not None


# --- sessions -----------------------------------------------------------


def test_session_token_is_stored_only_as_a_hash(users, user, db_url):
    token, _ = users.create_session(int(user["id"]))
    with get_session(db_url) as session:
        rows = list(session.exec(select(UserSession)).all())
    assert len(rows) == 1
    assert rows[0].token_hash != token
    assert rows[0].token_hash == token_hash(token)


def test_session_resolves_to_its_user(users, user):
    token, _ = users.create_session(int(user["id"]))
    resolved = users.resolve_session(token)
    assert resolved["email"] == EMAIL
    assert "session_expires_at" in resolved


def test_unknown_and_empty_tokens_do_not_resolve(users, user):
    users.create_session(int(user["id"]))
    assert users.resolve_session("made-up-token") is None
    assert users.resolve_session("") is None


def test_expired_session_does_not_resolve(users, user, db_url):
    token, _ = users.create_session(int(user["id"]))
    with get_session(db_url) as session:
        row = session.exec(select(UserSession)).first()
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.add(row)
        session.commit()
    assert users.resolve_session(token) is None


def test_revoked_session_does_not_resolve(users, user):
    token, _ = users.create_session(int(user["id"]))
    assert users.revoke_session(token) is True
    assert users.resolve_session(token) is None
    assert users.revoke_session(token) is False


def test_deactivating_a_user_invalidates_live_sessions(users, user, db_url):
    token, _ = users.create_session(int(user["id"]))
    with get_session(db_url) as session:
        row = session.exec(select(User)).first()
        row.is_active = False
        session.add(row)
        session.commit()
    assert users.resolve_session(token) is None


def test_revoke_all_sessions(users, user):
    first, _ = users.create_session(int(user["id"]))
    second, _ = users.create_session(int(user["id"]))
    assert users.revoke_all_sessions(int(user["id"])) == 2
    assert users.resolve_session(first) is None
    assert users.resolve_session(second) is None


# --- login / logout service --------------------------------------------


def test_login_returns_a_token_and_user(db_url, user):
    result = login(EMAIL, PASSWORD, database_url=db_url)
    assert result["token_type"] == "bearer"
    assert result["token"]
    assert result["user"]["email"] == EMAIL
    assert "password_hash" not in json.dumps(result)


def test_login_failure_message_does_not_reveal_whether_the_email_exists(db_url, user):
    with pytest.raises(AuthError) as wrong:
        login(EMAIL, "wrong-password", database_url=db_url)
    with pytest.raises(AuthError) as unknown:
        login("ghost@razorz.local", PASSWORD, database_url=db_url)
    assert wrong.value.message == unknown.value.message == "Invalid email or password"
    assert wrong.value.code == unknown.value.code == "invalid_credentials"


def test_logout_revokes_the_session(db_url, user):
    token = login(EMAIL, PASSWORD, database_url=db_url)["token"]
    assert me(token, database_url=db_url) is not None
    assert logout(token, database_url=db_url)["session_revoked"] is True
    assert me(token, database_url=db_url) is None


def test_login_and_logout_are_audited_without_credentials(db_url, user):
    token = login(EMAIL, PASSWORD, database_url=db_url)["token"]
    logout(token, database_url=db_url)
    with pytest.raises(AuthError):
        login(EMAIL, "wrong-password", database_url=db_url)

    with get_session(db_url) as session:
        events = [row for row in session.exec(select(AuditEvent)).all() if row.event_type == "auth_event"]
    actions = [row.action for row in events]
    assert "login" in actions and "logout" in actions and "login_failed" in actions
    blob = json.dumps([{"e": row.evidence, "a": row.actor, "d": row.details} for row in events])
    assert PASSWORD not in blob
    assert token not in blob
    assert token_hash(token) not in blob


def test_bootstrap_admin_creates_once_and_only_when_configured(db_url, monkeypatch):
    monkeypatch.delenv("RAZORZ_ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("RAZORZ_ADMIN_PASSWORD", raising=False)
    assert bootstrap_admin(db_url) is None

    monkeypatch.setenv("RAZORZ_ADMIN_EMAIL", "boot@razorz.local")
    monkeypatch.setenv("RAZORZ_ADMIN_PASSWORD", PASSWORD)
    created = bootstrap_admin(db_url)
    assert created["email"] == "boot@razorz.local"
    assert created["role"] == "admin"
    assert bootstrap_admin(db_url) is None
    assert UserRepository(db_url).count_users() == 1


# --- API endpoints ------------------------------------------------------


@pytest.fixture
def api_client(tmp_path):
    """Client whose auth stack points at a dedicated database file."""
    url = f"sqlite:///{(tmp_path / 'api_auth.db').as_posix()}"
    import app.auth.service as auth_service
    import app.security as security

    original_login = auth_service.login
    original_logout = auth_service.logout
    original_me = auth_service.me

    def _login(email, password, *, database_url=None):
        return original_login(email, password, database_url=url)

    def _logout(token, *, database_url=None):
        return original_logout(token, database_url=url)

    def _me(token, *, database_url=None):
        return original_me(token, database_url=url)

    import app.main as main_module

    main_module.auth_login = _login
    main_module.auth_logout = _logout
    auth_service.me = _me
    security_me = security.resolve_session_user
    try:
        UserRepository(url).create_user(email=EMAIL, password=PASSWORD, role="admin")
        with _settings_override(razorz_auth_required=True, razorz_api_keys=""):
            with TestClient(app) as client:
                yield client
    finally:
        main_module.auth_login = original_login
        main_module.auth_logout = original_logout
        auth_service.me = original_me
        security.resolve_session_user = security_me


def test_login_endpoint_issues_a_session(api_client):
    response = api_client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200
    payload = response.json()
    assert payload["token"] and payload["token_type"] == "bearer"
    assert payload["user"]["email"] == EMAIL
    assert "password_hash" not in response.text


def test_login_endpoint_rejects_bad_credentials(api_client):
    response = api_client.post("/auth/login", json={"email": EMAIL, "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_credentials"
    assert PASSWORD not in response.text


def test_login_endpoint_is_public_but_protected_routes_are_not(api_client):
    assert api_client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 200
    assert api_client.get("/reconciliation/summary").status_code == 401
    assert api_client.get("/auth/me").status_code == 401


def test_session_token_unlocks_protected_routes(api_client):
    token = api_client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert api_client.get("/reconciliation/summary", headers=headers).status_code == 200
    assert api_client.get("/exceptions", headers=headers).status_code == 200

    me_response = api_client.get("/auth/me", headers=headers)
    assert me_response.status_code == 200
    assert me_response.json()["user"]["email"] == EMAIL
    assert me_response.json()["auth_method"] == "session"


def test_logout_endpoint_makes_the_token_unusable(api_client):
    token = api_client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert api_client.post("/auth/logout", headers=headers).status_code == 200
    assert api_client.get("/reconciliation/summary", headers=headers).status_code == 401


def test_forged_token_is_rejected(api_client):
    for token in ("forged", "", "Bearer", "a.b.c"):
        response = api_client.get(
            "/reconciliation/summary", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


def test_api_key_still_works_alongside_sessions(api_client):
    with _settings_override(razorz_api_keys="machine-key"):
        assert (
            api_client.get("/reconciliation/summary", headers={"X-API-Key": "machine-key"}).status_code
            == 200
        )
        assert api_client.get("/auth/me", headers={"X-API-Key": "machine-key"}).json()[
            "auth_method"
        ] == "api_key"


def test_health_stays_public_with_user_auth_required(api_client):
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["auth"]["required"] is True


def test_webhook_keeps_hmac_auth_and_needs_no_session(api_client):
    from app.integrations.razorpay.webhook import compute_signature

    body = json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_SESS1",
                        "order_id": "order_SESS1",
                        "amount": 100000,
                        "status": "captured",
                        "created_at": 1767225600,
                    }
                }
            },
        }
    ).encode("utf-8")
    with _settings_override(razorpay_webhook_secret="whsec_session_test"):
        response = api_client.post(
            "/integrations/razorpay/webhook",
            content=body,
            headers={
                "x-razorpay-signature": compute_signature(body, "whsec_session_test"),
                "x-razorpay-event-id": "evt_session_1",
            },
        )
    assert response.status_code == 200
    assert response.json()["processed"] is True


def test_rejection_never_logs_credentials(api_client, caplog):
    with caplog.at_level("WARNING"):
        api_client.post("/auth/login", json={"email": EMAIL, "password": "attempted-secret"})
        api_client.get("/exceptions", headers={"Authorization": "Bearer forged-token"})
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "attempted-secret" not in messages
    assert "forged-token" not in messages
    assert PASSWORD not in messages
