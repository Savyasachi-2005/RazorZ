"""Razorpay webhook signature verification and payload normalization.

Razorpay signs the raw request body with the webhook secret using HMAC-SHA256
and sends the hex digest in `X-Razorpay-Signature`. The digest must be computed
over the exact bytes received — re-serializing the JSON would change it.

Normalization reuses the polling mapper unchanged, so a webhook-ingested record
is byte-identical to the same record fetched by the poll adapter.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from app.integrations.razorpay.errors import RazorpayIntegrationError
from app.integrations.razorpay.mapper import (
    RECONCILABLE_PAYMENT_STATUSES,
    map_fee_from_payment,
    map_order,
    map_payment,
    map_refund,
    map_settlement,
)

SIGNATURE_HEADER = "x-razorpay-signature"
EVENT_ID_HEADER = "x-razorpay-event-id"

# Only events the current data model and mapper can actually honour.
SUPPORTED_EVENTS: Dict[str, Tuple[str, ...]] = {
    "order.paid": ("order", "payment"),
    "payment.captured": ("payment",),
    "payment.authorized": ("payment",),
    "refund.created": ("refund",),
    "refund.processed": ("refund",),
    "settlement.processed": ("settlement",),
}

# Events that are deliberately not ingested, with the reason reported honestly.
IGNORED_EVENTS: Dict[str, str] = {
    "payment.failed": "failed payments are not money movement and never enter reconciliation",
    "order.notification.delivered": "notification lifecycle carries no financial record",
    "payment.downtime.started": "downtime events carry no financial record",
    "payment.downtime.resolved": "downtime events carry no financial record",
}


class NormalizedRecord(BaseModel):
    """Same contract the reconciliation API enforces on ingested records.

    Every webhook-derived record is validated through this before it may enter
    the ingestion pipeline, so a webhook cannot bypass record validation.
    """

    source: str = Field(default="razorpay")
    record_type: str
    record_id: str = Field(min_length=1)
    reference: str = Field(default="")
    payment_reference: str = Field(default="")
    amount: str = Field(default="0.00")
    date: str = Field(default="")
    customer: str = Field(default="")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class WebhookEnvelope(BaseModel):
    """Razorpay event envelope. Unknown extra keys are accepted and ignored."""

    event: str = Field(min_length=1)
    entity: str = Field(default="event")
    account_id: Optional[str] = None
    contains: List[str] = Field(default_factory=list)
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[int] = None


def compute_signature(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, signature: str | None, secret: str | None) -> bool:
    """Constant-time HMAC-SHA256 check over the raw body."""
    if not secret or not signature:
        return False
    expected = compute_signature(body, secret)
    return hmac.compare_digest(expected, signature.strip())


def payload_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def event_id_for(headers: Dict[str, str] | None, body: bytes) -> str:
    """Razorpay's delivery id, or a body digest when the header is absent."""
    lowered = {str(key).lower(): value for key, value in (headers or {}).items()}
    delivered = str(lowered.get(EVENT_ID_HEADER) or "").strip()
    return delivered or f"digest:{payload_digest(body)}"


def parse_envelope(body: bytes) -> WebhookEnvelope:
    try:
        raw = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise RazorpayIntegrationError("Webhook body is not valid JSON", code="invalid_payload") from exc
    if not isinstance(raw, dict):
        raise RazorpayIntegrationError("Webhook body must be a JSON object", code="invalid_payload")
    try:
        return WebhookEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise RazorpayIntegrationError(
            "Webhook envelope failed validation", code="invalid_payload"
        ) from exc


def _entity(envelope: WebhookEnvelope, container: str) -> Optional[Dict[str, Any]]:
    section = envelope.payload.get(container)
    if not isinstance(section, dict):
        return None
    entity = section.get("entity")
    return entity if isinstance(entity, dict) else None


def _records_for_container(container: str, entity: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Map one provider entity with the existing polling mapper."""
    if container == "order":
        return [map_order(entity)]
    if container == "payment":
        status = str(entity.get("status") or "").lower()
        if status not in RECONCILABLE_PAYMENT_STATUSES:
            return []
        records = [map_payment(entity)]
        fee = map_fee_from_payment(entity)
        if fee is not None:
            records.append(fee)
        return records
    if container == "refund":
        return [map_refund(entity)]
    if container == "settlement":
        return [map_settlement(entity)]
    return []


def is_supported(event: str) -> bool:
    return event in SUPPORTED_EVENTS


def normalize_event(envelope: WebhookEnvelope) -> List[Dict[str, Any]]:
    """Normalized, validated records for a supported event.

    Raises `unsupported_event` for anything not in `SUPPORTED_EVENTS` so the
    caller decides the HTTP outcome.
    """
    containers = SUPPORTED_EVENTS.get(envelope.event)
    if containers is None:
        raise RazorpayIntegrationError(
            f"Unsupported Razorpay event '{envelope.event}'", code="unsupported_event"
        )

    records: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for container in containers:
        entity = _entity(envelope, container)
        if entity is None:
            continue
        for record in _records_for_container(container, entity):
            try:
                validated = NormalizedRecord.model_validate(record)
            except ValidationError as exc:
                raise RazorpayIntegrationError(
                    f"Webhook {container} failed record validation", code="invalid_payload"
                ) from exc
            if validated.record_id in seen:
                continue
            seen.add(validated.record_id)
            records.append(validated.model_dump())
    return records


def primary_entity_id(envelope: WebhookEnvelope) -> Optional[str]:
    for container in SUPPORTED_EVENTS.get(envelope.event, ()) or ("payment", "order"):
        entity = _entity(envelope, container)
        if entity and entity.get("id"):
            return str(entity["id"])
    return None
