from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.config import settings
from app.integrations.razorpay.client import RazorpayClient
from app.integrations.razorpay.errors import RazorpayIntegrationError
from app.integrations.razorpay.mapper import normalize_razorpay_payload
from app.integrations.razorpay.webhook import (
    IGNORED_EVENTS,
    SUPPORTED_EVENTS,
    event_id_for,
    normalize_event,
    parse_envelope,
    payload_digest,
    primary_entity_id,
    verify_signature,
)
from app.repositories.reconciliation_repository import ReconciliationRepository
from app.repositories.record_repository import RecordRepository
from app.repositories.webhook_repository import WebhookRepository
from app.services import reconciliation_service as recon_service


def razorpay_status(client: RazorpayClient | None = None) -> Dict[str, Any]:
    client = client or RazorpayClient()
    status: Dict[str, Any] = {
        "provider": "razorpay",
        "mode": client.mode or settings.razorpay_mode,
        "configured": client.configured,
        "connected": False,
        "key_id_prefix": (client.key_id[:12] + "…") if client.key_id else None,
        "capabilities": {
            "orders": "supported",
            "payments": "supported",
            "refunds": "supported",
            "settlements": "partial",  # often empty / batch-level in test mode
            "fees": "partial",  # derived from payment.fee when present
            "webhooks": "supported" if settings.razorpay_webhook_secret else "not_configured",
        },
        "webhooks": {
            "configured": bool(settings.razorpay_webhook_secret),
            "endpoint": "/integrations/razorpay/webhook",
            "supported_events": sorted(SUPPORTED_EVENTS),
            "ignored_events": IGNORED_EVENTS,
            "reconcile_on_receipt": settings.razorpay_webhook_reconcile,
            "polling_fallback": "/integrations/razorpay/sync remains available",
        },
        "notes": [
            "Core reconciliation engine does not depend on Razorpay.",
            "Settlements in Test Mode may be empty; gaps are reported honestly, not fabricated.",
            "Webhooks ingest immediately; polling sync stays the full-dataset fallback.",
        ],
    }
    if not client.configured:
        status["error"] = {"code": "not_configured", "message": "Missing Razorpay API keys"}
        return status
    try:
        client.ping()
        status["connected"] = True
    except RazorpayIntegrationError as exc:
        status["error"] = {"code": exc.code, "message": exc.message}
    return status


def _signature_from(headers: Dict[str, str] | None) -> Optional[str]:
    lowered = {str(key).lower(): value for key, value in (headers or {}).items()}
    value = lowered.get("x-razorpay-signature")
    return str(value) if value else None


def process_webhook(
    body: bytes,
    *,
    headers: Dict[str, str] | None = None,
    secret: str | None = None,
    database_url: str | None = None,
    reconcile: bool | None = None,
) -> Dict[str, Any]:
    """Verify, deduplicate, normalize and ingest one Razorpay webhook delivery.

    Ingestion is an idempotent upsert by `external_id`, so a duplicate delivery
    can never create a second financial record. Reconciliation rules are not
    touched: when `reconcile` is enabled the existing polling sync runs, which is
    the same full-dataset path the poll-only flow already used.
    """
    secret = settings.razorpay_webhook_secret if secret is None else secret
    reconcile = settings.razorpay_webhook_reconcile if reconcile is None else bool(reconcile)
    audit = ReconciliationRepository(database_url)
    event_id = event_id_for(headers, body)
    digest = payload_digest(body)

    if not secret:
        audit.record_webhook_event(
            event_id=event_id,
            event_type="unknown",
            action="rejected",
            outcome="not_configured",
            error_code="webhook_not_configured",
        )
        raise RazorpayIntegrationError(
            "Razorpay webhook secret is not configured (RAZORPAY_WEBHOOK_SECRET)",
            code="webhook_not_configured",
        )

    if not verify_signature(body, _signature_from(headers), secret):
        audit.record_webhook_event(
            event_id=event_id,
            event_type="unknown",
            action="rejected",
            outcome="invalid_signature",
            signature_valid=False,
            error_code="invalid_signature",
        )
        raise RazorpayIntegrationError(
            "Webhook signature verification failed", code="invalid_signature"
        )

    try:
        envelope = parse_envelope(body)
    except RazorpayIntegrationError as exc:
        audit.record_webhook_event(
            event_id=event_id,
            event_type="unknown",
            action="rejected",
            outcome="invalid_payload",
            signature_valid=True,
            error_code=exc.code,
        )
        raise

    entity_id = primary_entity_id(envelope)
    webhooks = WebhookRepository(database_url)
    row, created = webhooks.claim(
        event_id=event_id,
        event_type=envelope.event,
        entity_id=entity_id,
        payload_digest=digest,
    )
    # A previously failed delivery may be retried; a completed one may not.
    if not created and row.status != "FAILED":
        audit.record_webhook_event(
            event_id=event_id,
            event_type=envelope.event,
            action="duplicate",
            outcome="ignored_duplicate",
            signature_valid=True,
            entity_id=entity_id,
            records_ingested=row.records_ingested,
            run_id=row.run_id,
        )
        return {
            "provider": "razorpay",
            "event_id": event_id,
            "event": envelope.event,
            "accepted": True,
            "duplicate": True,
            "processed": False,
            "status": row.status,
            "records_ingested": row.records_ingested,
            "run_id": row.run_id,
            "message": "Duplicate delivery ignored; no financial record was created again.",
        }

    audit.record_webhook_event(
        event_id=event_id,
        event_type=envelope.event,
        action="received",
        outcome="accepted",
        signature_valid=True,
        entity_id=entity_id,
    )

    if envelope.event not in SUPPORTED_EVENTS:
        reason = IGNORED_EVENTS.get(
            envelope.event, "event is not mapped to the RAZORZ record model"
        )
        webhooks.complete(event_id, status="IGNORED", error_code="unsupported_event")
        audit.record_webhook_event(
            event_id=event_id,
            event_type=envelope.event,
            action="ignored",
            outcome="unsupported_event",
            signature_valid=True,
            entity_id=entity_id,
            error_code="unsupported_event",
        )
        return {
            "provider": "razorpay",
            "event_id": event_id,
            "event": envelope.event,
            "accepted": True,
            "duplicate": False,
            "processed": False,
            "supported": False,
            "status": "IGNORED",
            "records_ingested": 0,
            "reason": reason,
            "supported_events": sorted(SUPPORTED_EVENTS),
        }

    try:
        records = normalize_event(envelope)
        counts = RecordRepository(database_url).persist_source_records(records) if records else {}
        run_id: Optional[str] = None
        summary: Optional[Dict[str, Any]] = None
        if records and reconcile:
            # Reuse the existing polling sync so reconciliation stays a
            # full-dataset run with unchanged deterministic behavior.
            outcome = sync_and_reconcile(database_url=database_url)
            run_id = outcome.get("run_id")
            summary = outcome.get("summary")
    except RazorpayIntegrationError as exc:
        webhooks.complete(event_id, status="FAILED", error_code=exc.code)
        audit.record_webhook_event(
            event_id=event_id,
            event_type=envelope.event,
            action="failed",
            outcome="processing_failed",
            signature_valid=True,
            entity_id=entity_id,
            error_code=exc.code,
        )
        raise
    except Exception as exc:
        webhooks.complete(event_id, status="FAILED", error_code="processing_failed")
        audit.record_webhook_event(
            event_id=event_id,
            event_type=envelope.event,
            action="failed",
            outcome="processing_failed",
            signature_valid=True,
            entity_id=entity_id,
            error_code="processing_failed",
        )
        raise RazorpayIntegrationError(
            "Webhook processing failed after signature verification",
            code="processing_failed",
        ) from exc

    status = "PROCESSED" if records else "IGNORED"
    webhooks.complete(
        event_id,
        status=status,
        records_ingested=len(records),
        run_id=run_id,
        error_code=None if records else "no_reconcilable_record",
    )
    audit.record_webhook_event(
        event_id=event_id,
        event_type=envelope.event,
        action="processed" if records else "ignored",
        outcome=status,
        signature_valid=True,
        entity_id=entity_id,
        records_ingested=len(records),
        run_id=run_id,
    )

    return {
        "provider": "razorpay",
        "event_id": event_id,
        "event": envelope.event,
        "accepted": True,
        "duplicate": False,
        "processed": bool(records),
        "supported": True,
        "status": status,
        "entity_id": entity_id,
        "records_ingested": len(records),
        "records": records,
        "persisted_counts": {key: value for key, value in (counts or {}).items() if value},
        "reconciled": bool(run_id),
        "run_id": run_id,
        "summary": summary,
        "message": (
            "Webhook ingested. Deterministic reconciliation rules unchanged; "
            "polling sync remains available as fallback."
        ),
    }


def fetch_normalized_records(
    *,
    count: int = 50,
    client: RazorpayClient | None = None,
) -> Dict[str, Any]:
    client = client or RazorpayClient()
    client.assert_ready()
    page = max(1, min(int(count), 100))

    orders = client.fetch_collection("orders", count=page)
    payments = client.fetch_collection("payments", count=page)
    refunds = client.fetch_collection("refunds", count=page)
    settlements: List[Dict[str, Any]] = []
    settlement_error: Optional[Dict[str, str]] = None
    try:
        settlements = client.fetch_collection("settlements", count=page)
    except RazorpayIntegrationError as exc:
        # Settlements are partial in test mode — continue without inventing rows.
        settlement_error = {"code": exc.code, "message": exc.message}

    records = normalize_razorpay_payload(
        orders=orders,
        payments=payments,
        refunds=refunds,
        settlements=settlements,
    )
    return {
        "counts": {
            "orders": len(orders),
            "payments": len(payments),
            "refunds": len(refunds),
            "settlements": len(settlements),
            "normalized": len(records),
        },
        "settlement_error": settlement_error,
        "records": records,
    }


def sync_and_reconcile(
    *,
    count: int = 50,
    database_url: str | None = None,
    client: RazorpayClient | None = None,
) -> Dict[str, Any]:
    """Fetch Razorpay Test Mode data, normalize, run deterministic reconciliation."""
    fetched = fetch_normalized_records(count=count, client=client)
    records = fetched["records"]
    if not records:
        return {
            "source": "razorpay",
            "mode": (client or RazorpayClient()).mode,
            "synced": True,
            "empty": True,
            "counts": fetched["counts"],
            "settlement_error": fetched.get("settlement_error"),
            "summary": {
                "total": 0,
                "matched": 0,
                "exceptions": 0,
                "review_required": 0,
                "unresolved": 0,
                "match_rate": 0.0,
            },
            "results": [],
            "persisted": False,
            "message": (
                "Razorpay returned no orders/payments/refunds/settlements for this account. "
                "Create Test Mode transactions in the Razorpay dashboard, then sync again. "
                "Synthetic generate remains available as fallback."
            ),
        }

    outcome = recon_service.run_reconciliation(records, database_url=database_url)
    return {
        "source": "razorpay",
        "mode": (client or RazorpayClient()).mode,
        "synced": True,
        "empty": False,
        "counts": fetched["counts"],
        "settlement_error": fetched.get("settlement_error"),
        "summary": outcome["summary"],
        "results": outcome["results"],
        "persisted": outcome["persisted"],
        "run_id": outcome.get("run_id"),
        "message": "Razorpay data normalized and reconciled. Engine remains source of financial truth.",
    }
