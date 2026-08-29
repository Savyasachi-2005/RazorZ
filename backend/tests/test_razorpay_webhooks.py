from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import get_session, reset_engine_cache
from app.integrations.razorpay import service as razorpay_service
from app.integrations.razorpay.errors import RazorpayIntegrationError
from app.integrations.razorpay.webhook import (
    IGNORED_EVENTS,
    SUPPORTED_EVENTS,
    compute_signature,
    normalize_event,
    parse_envelope,
    verify_signature,
)
from app.models import AuditEvent, Order, Payment, Refund, Settlement, WebhookEvent

SECRET = "whsec_test_razorz"


@pytest.fixture
def db_url(tmp_path):
    url = f"sqlite:///{(tmp_path / 'webhooks.db').as_posix()}"
    yield url
    reset_engine_cache()


def _order_paid_body(order_id: str = "order_WH1", payment_id: str = "pay_WH1") -> bytes:
    payload = {
        "entity": "event",
        "account_id": "acc_test",
        "event": "order.paid",
        "contains": ["payment", "order"],
        "payload": {
            "order": {
                "entity": {
                    "id": order_id,
                    "amount": 150000,
                    "currency": "INR",
                    "status": "paid",
                    "receipt": "rcpt-1",
                    "created_at": 1767225600,
                }
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": order_id,
                    "amount": 150000,
                    "currency": "INR",
                    "status": "captured",
                    "method": "upi",
                    "fee": 3000,
                    "tax": 460,
                    "email": "payer@example.com",
                    "contact": "+919999999999",
                    "created_at": 1767225600,
                }
            },
        },
    }
    return json.dumps(payload).encode("utf-8")


def _headers(body: bytes, *, secret: str = SECRET, event_id: str = "evt_WH1") -> dict:
    return {
        "x-razorpay-signature": compute_signature(body, secret),
        "x-razorpay-event-id": event_id,
    }


def _rows(db_url, model):
    with get_session(db_url) as session:
        return list(session.exec(select(model)).all())


# --- signature verification ---------------------------------------------


def test_valid_signature_is_accepted():
    body = _order_paid_body()
    assert verify_signature(body, compute_signature(body, SECRET), SECRET) is True


def test_invalid_signature_is_rejected():
    body = _order_paid_body()
    assert verify_signature(body, "deadbeef", SECRET) is False
    assert verify_signature(body, compute_signature(body, "other_secret"), SECRET) is False
    assert verify_signature(body, None, SECRET) is False
    assert verify_signature(body, compute_signature(body, SECRET), "") is False


def test_signature_is_bound_to_the_exact_bytes():
    body = _order_paid_body()
    signature = compute_signature(body, SECRET)
    tampered = body.replace(b"150000", b"250000")
    assert verify_signature(tampered, signature, SECRET) is False


def test_processing_rejects_an_invalid_signature_without_ingesting(db_url):
    body = _order_paid_body()
    with pytest.raises(RazorpayIntegrationError) as exc:
        razorpay_service.process_webhook(
            body,
            headers={"x-razorpay-signature": "bad", "x-razorpay-event-id": "evt_bad"},
            secret=SECRET,
            database_url=db_url,
        )
    assert exc.value.code == "invalid_signature"
    assert _rows(db_url, Order) == []
    assert _rows(db_url, Payment) == []
    assert _rows(db_url, WebhookEvent) == []


def test_missing_secret_is_reported_as_not_configured(db_url):
    body = _order_paid_body()
    with pytest.raises(RazorpayIntegrationError) as exc:
        razorpay_service.process_webhook(body, headers=_headers(body), secret="", database_url=db_url)
    assert exc.value.code == "webhook_not_configured"


# --- supported events ---------------------------------------------------


def test_supported_event_ingests_order_payment_and_fee(db_url):
    body = _order_paid_body()
    result = razorpay_service.process_webhook(
        body, headers=_headers(body), secret=SECRET, database_url=db_url
    )

    assert result["processed"] is True
    assert result["duplicate"] is False
    assert result["status"] == "PROCESSED"
    assert result["records_ingested"] == 3

    types = sorted(record["record_type"] for record in result["records"])
    assert types == ["fee", "order", "payment"]

    orders = _rows(db_url, Order)
    payments = _rows(db_url, Payment)
    assert [row.external_id for row in orders] == ["order_WH1"]
    assert [row.external_id for row in payments] == ["pay_WH1"]
    # paise -> Decimal rupees, exactly as the polling mapper does.
    assert orders[0].amount == Decimal("1500.00")
    assert payments[0].amount == Decimal("1500.00")


def test_refund_and_settlement_events_are_supported(db_url):
    refund_body = json.dumps(
        {
            "event": "refund.processed",
            "payload": {
                "refund": {
                    "entity": {
                        "id": "rfnd_WH1",
                        "payment_id": "pay_WH1",
                        "amount": 50000,
                        "status": "processed",
                        "created_at": 1767225600,
                    }
                }
            },
        }
    ).encode("utf-8")
    settlement_body = json.dumps(
        {
            "event": "settlement.processed",
            "payload": {
                "settlement": {
                    "entity": {
                        "id": "setl_WH1",
                        "amount": 145000,
                        "status": "processed",
                        "utr": "UTR123",
                        "created_at": 1767225600,
                    }
                }
            },
        }
    ).encode("utf-8")

    refund = razorpay_service.process_webhook(
        refund_body,
        headers=_headers(refund_body, event_id="evt_refund"),
        secret=SECRET,
        database_url=db_url,
    )
    settlement = razorpay_service.process_webhook(
        settlement_body,
        headers=_headers(settlement_body, event_id="evt_settlement"),
        secret=SECRET,
        database_url=db_url,
    )

    assert refund["records_ingested"] == 1
    assert settlement["records_ingested"] == 1
    assert [row.external_id for row in _rows(db_url, Refund)] == ["rfnd_WH1"]
    assert [row.external_id for row in _rows(db_url, Settlement)] == ["setl_WH1"]


def test_failed_payment_event_is_ignored_by_design(db_url):
    body = json.dumps(
        {
            "event": "payment.failed",
            "payload": {"payment": {"entity": {"id": "pay_FAIL", "amount": 1000, "status": "failed"}}},
        }
    ).encode("utf-8")
    result = razorpay_service.process_webhook(
        body, headers=_headers(body, event_id="evt_failed"), secret=SECRET, database_url=db_url
    )
    assert result["supported"] is False
    assert result["status"] == "IGNORED"
    assert result["records_ingested"] == 0
    assert result["reason"] == IGNORED_EVENTS["payment.failed"]
    assert _rows(db_url, Payment) == []


def test_unsupported_event_is_accepted_but_not_ingested(db_url):
    body = json.dumps({"event": "subscription.charged", "payload": {}}).encode("utf-8")
    result = razorpay_service.process_webhook(
        body, headers=_headers(body, event_id="evt_unsupported"), secret=SECRET, database_url=db_url
    )
    assert result["accepted"] is True
    assert result["supported"] is False
    assert result["processed"] is False
    assert result["status"] == "IGNORED"
    assert "subscription.charged" not in result["supported_events"]
    stored = _rows(db_url, WebhookEvent)
    assert [row.status for row in stored] == ["IGNORED"]
    assert stored[0].error_code == "unsupported_event"


def test_normalize_event_refuses_unsupported_events():
    envelope = parse_envelope(json.dumps({"event": "invoice.paid", "payload": {}}).encode("utf-8"))
    with pytest.raises(RazorpayIntegrationError) as exc:
        normalize_event(envelope)
    assert exc.value.code == "unsupported_event"


def test_only_reconcilable_payment_statuses_enter_ingestion():
    body = json.dumps(
        {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": "pay_X", "amount": 100, "status": "created"}}},
        }
    ).encode("utf-8")
    assert normalize_event(parse_envelope(body)) == []


# --- idempotency --------------------------------------------------------


def test_duplicate_delivery_creates_no_second_record(db_url):
    body = _order_paid_body()
    headers = _headers(body)

    first = razorpay_service.process_webhook(
        body, headers=headers, secret=SECRET, database_url=db_url
    )
    second = razorpay_service.process_webhook(
        body, headers=headers, secret=SECRET, database_url=db_url
    )

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["processed"] is False
    assert len(_rows(db_url, Order)) == 1
    assert len(_rows(db_url, Payment)) == 1
    assert len(_rows(db_url, WebhookEvent)) == 1


def test_redelivery_without_event_id_is_deduplicated_by_body_digest(db_url):
    body = _order_paid_body()
    headers = {"x-razorpay-signature": compute_signature(body, SECRET)}

    first = razorpay_service.process_webhook(
        body, headers=headers, secret=SECRET, database_url=db_url
    )
    second = razorpay_service.process_webhook(
        body, headers=headers, secret=SECRET, database_url=db_url
    )

    assert first["event_id"].startswith("digest:")
    assert second["duplicate"] is True
    assert len(_rows(db_url, Order)) == 1


def test_repeated_delivery_of_the_same_entity_upserts_instead_of_duplicating(db_url):
    body = _order_paid_body()
    updated = _order_paid_body()
    razorpay_service.process_webhook(
        body, headers=_headers(body, event_id="evt_1"), secret=SECRET, database_url=db_url
    )
    # A different delivery id carrying the same entity ids must still upsert.
    razorpay_service.process_webhook(
        updated, headers=_headers(updated, event_id="evt_2"), secret=SECRET, database_url=db_url
    )
    assert len(_rows(db_url, Payment)) == 1
    assert len(_rows(db_url, WebhookEvent)) == 2


# --- failure handling ---------------------------------------------------


def test_processing_failure_is_recorded_and_surfaced(db_url, monkeypatch):
    class BrokenRepository:
        def __init__(self, *_args, **_kwargs):
            pass

        def persist_source_records(self, records):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(razorpay_service, "RecordRepository", BrokenRepository)
    body = _order_paid_body()

    with pytest.raises(RazorpayIntegrationError) as exc:
        razorpay_service.process_webhook(
            body, headers=_headers(body, event_id="evt_fail"), secret=SECRET, database_url=db_url
        )
    assert exc.value.code == "processing_failed"

    stored = _rows(db_url, WebhookEvent)
    assert [row.status for row in stored] == ["FAILED"]
    assert stored[0].error_code == "processing_failed"


def test_failed_delivery_can_be_retried(db_url, monkeypatch):
    class BrokenRepository:
        def __init__(self, *_args, **_kwargs):
            pass

        def persist_source_records(self, records):
            raise RuntimeError("transient")

    body = _order_paid_body()
    monkeypatch.setattr(razorpay_service, "RecordRepository", BrokenRepository)
    with pytest.raises(RazorpayIntegrationError):
        razorpay_service.process_webhook(
            body, headers=_headers(body, event_id="evt_retry"), secret=SECRET, database_url=db_url
        )

    monkeypatch.undo()
    result = razorpay_service.process_webhook(
        body, headers=_headers(body, event_id="evt_retry"), secret=SECRET, database_url=db_url
    )
    assert result["duplicate"] is False
    assert result["status"] == "PROCESSED"
    assert len(_rows(db_url, Payment)) == 1


def test_malformed_body_is_rejected_as_invalid_payload(db_url):
    body = b"{not json"
    with pytest.raises(RazorpayIntegrationError) as exc:
        razorpay_service.process_webhook(
            body, headers=_headers(body, event_id="evt_bad_json"), secret=SECRET, database_url=db_url
        )
    assert exc.value.code == "invalid_payload"
    assert _rows(db_url, WebhookEvent) == []


def test_envelope_without_event_is_rejected(db_url):
    body = json.dumps({"payload": {}}).encode("utf-8")
    with pytest.raises(RazorpayIntegrationError) as exc:
        razorpay_service.process_webhook(
            body, headers=_headers(body, event_id="evt_no_event"), secret=SECRET, database_url=db_url
        )
    assert exc.value.code == "invalid_payload"


# --- audit --------------------------------------------------------------


def test_audit_records_receipt_and_processing_without_secrets(db_url):
    body = _order_paid_body()
    razorpay_service.process_webhook(
        body, headers=_headers(body), secret=SECRET, database_url=db_url
    )
    events = [row for row in _rows(db_url, AuditEvent) if row.event_type == "webhook_event"]
    actions = {row.action for row in events}
    assert {"received", "processed"} <= actions
    for row in events:
        blob = json.dumps({"evidence": row.evidence, "details": row.details, "actor": row.actor})
        assert SECRET not in blob
        assert compute_signature(body, SECRET) not in blob
        assert "x-razorpay-signature" not in blob.lower()
        assert "payer@example.com" not in blob


def test_audit_records_an_invalid_signature_rejection(db_url):
    body = _order_paid_body()
    with pytest.raises(RazorpayIntegrationError):
        razorpay_service.process_webhook(
            body,
            headers={"x-razorpay-signature": "bad", "x-razorpay-event-id": "evt_reject"},
            secret=SECRET,
            database_url=db_url,
        )
    events = [row for row in _rows(db_url, AuditEvent) if row.event_type == "webhook_event"]
    assert [row.action for row in events] == ["rejected"]
    assert json.loads(events[0].evidence or "{}")["signature_valid"] is False


# --- pipeline guarantees ------------------------------------------------


def test_webhook_reuses_the_polling_mapper_output(db_url):
    from app.integrations.razorpay.mapper import map_order, map_payment

    body = _order_paid_body()
    envelope = parse_envelope(body)
    records = normalize_event(envelope)
    by_type = {record["record_type"]: record for record in records}

    raw = json.loads(body)
    assert by_type["order"] == map_order(raw["payload"]["order"]["entity"])
    assert by_type["payment"] == map_payment(raw["payload"]["payment"]["entity"])


def test_webhook_does_not_reconcile_unless_enabled(db_url, monkeypatch):
    called = {"sync": 0}

    def _sync(**kwargs):
        called["sync"] += 1
        return {"run_id": "run-1", "summary": {"total": 0}}

    monkeypatch.setattr(razorpay_service, "sync_and_reconcile", _sync)
    body = _order_paid_body()
    result = razorpay_service.process_webhook(
        body, headers=_headers(body), secret=SECRET, database_url=db_url, reconcile=False
    )
    assert called["sync"] == 0
    assert result["reconciled"] is False
    assert result["run_id"] is None


def test_webhook_reconcile_delegates_to_the_existing_polling_sync(db_url, monkeypatch):
    called = {"sync": 0}

    def _sync(**kwargs):
        called["sync"] += 1
        return {"run_id": "run-1", "summary": {"total": 3, "matched": 3}}

    monkeypatch.setattr(razorpay_service, "sync_and_reconcile", _sync)
    body = _order_paid_body()
    result = razorpay_service.process_webhook(
        body, headers=_headers(body), secret=SECRET, database_url=db_url, reconcile=True
    )
    assert called["sync"] == 1
    assert result["reconciled"] is True
    assert result["run_id"] == "run-1"


def test_polling_sync_is_still_available():
    assert callable(razorpay_service.sync_and_reconcile)
    assert callable(razorpay_service.fetch_normalized_records)


def test_supported_event_list_is_limited_to_the_current_data_model():
    assert set(SUPPORTED_EVENTS) == {
        "order.paid",
        "payment.captured",
        "payment.authorized",
        "refund.created",
        "refund.processed",
        "settlement.processed",
    }
    assert set(SUPPORTED_EVENTS).isdisjoint(IGNORED_EVENTS)


def test_reconciliation_thresholds_untouched():
    from app.config import settings

    assert settings.thresholds.auto_resolve == Decimal("0.99")
    assert settings.thresholds.human_review == Decimal("0.70")


# --- API endpoint -------------------------------------------------------


def test_webhook_endpoint_accepts_a_valid_signature():
    from app.config import settings

    original = settings.razorpay_webhook_secret
    object.__setattr__(settings, "razorpay_webhook_secret", SECRET)
    try:
        from app.main import app

        body = _order_paid_body(order_id="order_API", payment_id="pay_API")
        with TestClient(app) as client:
            response = client.post(
                "/integrations/razorpay/webhook",
                content=body,
                headers=_headers(body, event_id="evt_api_ok"),
            )
        assert response.status_code == 200
        payload = response.json()
        assert payload["processed"] is True
        assert payload["event"] == "order.paid"
    finally:
        object.__setattr__(settings, "razorpay_webhook_secret", original)


def test_webhook_endpoint_rejects_an_invalid_signature():
    from app.config import settings

    original = settings.razorpay_webhook_secret
    object.__setattr__(settings, "razorpay_webhook_secret", SECRET)
    try:
        from app.main import app

        body = _order_paid_body(order_id="order_API2", payment_id="pay_API2")
        with TestClient(app) as client:
            response = client.post(
                "/integrations/razorpay/webhook",
                content=body,
                headers={"x-razorpay-signature": "nope", "x-razorpay-event-id": "evt_api_bad"},
            )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "invalid_signature"
    finally:
        object.__setattr__(settings, "razorpay_webhook_secret", original)


def test_status_endpoint_reports_webhook_support():
    from app.config import settings

    original = settings.razorpay_webhook_secret
    object.__setattr__(settings, "razorpay_webhook_secret", SECRET)
    try:
        status = razorpay_service.razorpay_status(client=_UnconfiguredClient())
        assert status["capabilities"]["webhooks"] == "supported"
        assert status["webhooks"]["configured"] is True
        assert "order.paid" in status["webhooks"]["supported_events"]
    finally:
        object.__setattr__(settings, "razorpay_webhook_secret", original)


class _UnconfiguredClient:
    mode = "test"
    configured = False
    key_id = ""

    def ping(self):  # pragma: no cover - not reached when unconfigured
        raise AssertionError("ping must not run when unconfigured")
