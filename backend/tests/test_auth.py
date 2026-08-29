from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.security import PUBLIC_PATHS, auth_enabled, configured_keys, key_is_valid, require_api_key
from app.integrations.razorpay.webhook import compute_signature

KEY = "razorz_test_key_primary"
SECOND_KEY = "razorz_test_key_rotating"
WEBHOOK_SECRET = "whsec_auth_test"

PROTECTED_GET = (
    "/reconciliation/summary",
    "/reconciliation/records",
    "/exceptions",
    "/audit",
    "/copilot/suggestions",
    "/integrations/razorpay/status",
)

PROTECTED_POST = (
    ("/reconciliation/run", {"records": []}),
    ("/ingestion/generate", {"records": 50, "seed": 42}),
    ("/copilot/ask", {"question": "How many open exceptions?"}),
    ("/integrations/razorpay/sync", {"count": 5}),
    ("/exceptions/1/resolve", {"actor": "reviewer", "note": "looks fine"}),
    ("/exceptions/1/reject", {"actor": "reviewer", "note": "looks wrong"}),
    ("/exceptions/1/ai-assist", {"mode": "full_analysis"}),
)


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
def auth_on():
    with _settings_override(razorz_api_keys=f"{KEY},{SECOND_KEY}"):
        with TestClient(app) as client:
            yield client


@pytest.fixture
def auth_off():
    with _settings_override(razorz_api_keys=""):
        with TestClient(app) as client:
            yield client


# --- configuration ------------------------------------------------------


def test_keys_come_from_the_environment_only():
    with _settings_override(razorz_api_keys=" k1 , k2 ,, "):
        assert configured_keys() == ["k1", "k2"]
        assert auth_enabled() is True
    with _settings_override(razorz_api_keys=""):
        assert configured_keys() == []
        assert auth_enabled() is False


def test_key_comparison_accepts_every_configured_key():
    with _settings_override(razorz_api_keys=f"{KEY},{SECOND_KEY}"):
        assert key_is_valid(KEY) is True
        assert key_is_valid(SECOND_KEY) is True
        assert key_is_valid(KEY + "x") is False
        assert key_is_valid(KEY[:-1]) is False
        assert key_is_valid("") is False


# --- missing / invalid credentials --------------------------------------


def test_protected_get_requires_a_key(auth_on):
    for path in PROTECTED_GET:
        response = auth_on.get(path)
        assert response.status_code == 401, path
        assert response.json()["detail"]["code"] == "unauthorized"


def test_protected_post_requires_a_key(auth_on):
    for path, body in PROTECTED_POST:
        response = auth_on.post(path, json=body)
        assert response.status_code == 401, path


def test_invalid_key_is_rejected(auth_on):
    for headers in (
        {"X-API-Key": "wrong-key"},
        {"X-API-Key": ""},
        {"Authorization": "Bearer wrong-key"},
        {"Authorization": "Basic " + KEY},
        {"Authorization": KEY},
    ):
        response = auth_on.get("/reconciliation/summary", headers=headers)
        assert response.status_code == 401, headers


def test_unauthorized_response_advertises_the_scheme_and_leaks_nothing(auth_on):
    response = auth_on.get("/exceptions", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"
    body = response.text
    assert KEY not in body
    assert SECOND_KEY not in body
    assert "wrong-key" not in body


def test_valid_key_in_header_is_accepted(auth_on):
    response = auth_on.get("/reconciliation/summary", headers={"X-API-Key": KEY})
    assert response.status_code == 200


def test_valid_key_as_bearer_token_is_accepted(auth_on):
    response = auth_on.get("/reconciliation/summary", headers={"Authorization": f"Bearer {KEY}"})
    assert response.status_code == 200


def test_rotated_second_key_is_accepted(auth_on):
    response = auth_on.get("/exceptions", headers={"X-API-Key": SECOND_KEY})
    assert response.status_code == 200


# --- public paths -------------------------------------------------------


def test_health_stays_public_and_reports_auth_state(auth_on):
    response = auth_on.get("/health")
    assert response.status_code == 200
    assert response.json()["auth"] == {"required": True, "scheme": "api_key"}


def test_openapi_and_docs_stay_public(auth_on):
    assert auth_on.get("/openapi.json").status_code == 200
    assert auth_on.get("/docs").status_code == 200


def test_public_paths_are_only_health_docs_login_and_the_webhook():
    assert PUBLIC_PATHS == frozenset(
        {
            "/health",
            "/docs",
            "/docs/oauth2-redirect",
            "/redoc",
            "/openapi.json",
            # Exchanges credentials, so it cannot itself require one.
            "/auth/login",
            "/integrations/razorpay/webhook",
        }
    )


# --- webhook keeps HMAC auth, not API-key auth --------------------------


def _webhook_body() -> bytes:
    return json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_AUTH1",
                        "order_id": "order_AUTH1",
                        "amount": 100000,
                        "status": "captured",
                        "method": "upi",
                        "created_at": 1767225600,
                    }
                }
            },
        }
    ).encode("utf-8")


def test_webhook_accepts_a_valid_signature_without_an_api_key(auth_on):
    body = _webhook_body()
    with _settings_override(razorpay_webhook_secret=WEBHOOK_SECRET):
        response = auth_on.post(
            "/integrations/razorpay/webhook",
            content=body,
            headers={
                "x-razorpay-signature": compute_signature(body, WEBHOOK_SECRET),
                "x-razorpay-event-id": "evt_auth_ok",
            },
        )
    assert response.status_code == 200
    assert response.json()["processed"] is True


def test_webhook_still_rejects_a_bad_signature_even_with_a_valid_api_key(auth_on):
    body = _webhook_body()
    with _settings_override(razorpay_webhook_secret=WEBHOOK_SECRET):
        response = auth_on.post(
            "/integrations/razorpay/webhook",
            content=body,
            headers={
                "x-razorpay-signature": "bad",
                "x-razorpay-event-id": "evt_auth_bad",
                "X-API-Key": KEY,
            },
        )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_signature"


def test_api_key_cannot_substitute_for_a_missing_webhook_signature(auth_on):
    body = _webhook_body()
    with _settings_override(razorpay_webhook_secret=WEBHOOK_SECRET):
        response = auth_on.post(
            "/integrations/razorpay/webhook",
            content=body,
            headers={"x-razorpay-event-id": "evt_auth_nosig", "X-API-Key": KEY},
        )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "invalid_signature"


# --- CORS + development behavior ----------------------------------------


def test_cors_preflight_still_works_with_auth_enabled(auth_on):
    response = auth_on.options(
        "/reconciliation/summary",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-api-key",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_headers_are_unchanged_on_an_authenticated_request(auth_on):
    response = auth_on.get(
        "/reconciliation/summary",
        headers={"X-API-Key": KEY, "Origin": "http://localhost:5173"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_auth_is_disabled_when_no_key_is_configured(auth_off):
    assert auth_off.get("/reconciliation/summary").status_code == 200
    assert auth_off.get("/health").json()["auth"]["required"] is False


def test_production_without_keys_refuses_to_serve_protected_routes(auth_off):
    with _settings_override(env="production"):
        response = auth_off.get("/reconciliation/summary")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "auth_not_configured"


def test_production_without_keys_keeps_health_public(auth_off):
    with _settings_override(env="production"):
        assert auth_off.get("/health").status_code == 200


# --- no credential logging ----------------------------------------------


def test_rejection_logs_the_path_but_never_the_credentials(auth_on, caplog):
    with caplog.at_level("WARNING", logger="razorz.security"):
        auth_on.get("/exceptions", headers={"X-API-Key": "super-secret-attempt"})
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "/exceptions" in messages
    assert "super-secret-attempt" not in messages
    assert KEY not in messages
    assert SECOND_KEY not in messages


def test_dependency_is_installed_application_wide():
    installed = [getattr(dep, "dependency", None) for dep in (app.router.dependencies or [])]
    assert require_api_key in installed
