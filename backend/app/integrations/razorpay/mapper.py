from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

# Payments that represent real money movement for order↔payment reconciliation.
RECONCILABLE_PAYMENT_STATUSES = frozenset({"captured", "authorized", "refunded"})


def _paise_to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return (Decimal(str(value)) / Decimal("100")).quantize(Decimal("0.01"))


def _ts_to_date(value: Any) -> str:
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()


def _notes_dict(raw: Dict[str, Any]) -> Dict[str, Any]:
    notes = raw.get("notes")
    return notes if isinstance(notes, dict) else {}


def map_order(raw: Dict[str, Any]) -> Dict[str, Any]:
    order_id = str(raw.get("id") or "")
    receipt = str(raw.get("receipt") or "").strip()
    notes = _notes_dict(raw)
    # Matching identity must align with payments. Razorpay orders rarely share email;
    # keep customer empty for matching and preserve notes in metadata.
    return {
        "source": "razorpay",
        "record_type": "order",
        "record_id": order_id,
        # Razorpay order id is the deterministic join key with payment.order_id.
        "reference": order_id,
        "payment_reference": "",
        "amount": str(_paise_to_decimal(raw.get("amount"))),
        "date": _ts_to_date(raw.get("created_at")),
        "customer": "",
        "metadata": {
            "provider": "razorpay",
            "status": raw.get("status"),
            "currency": raw.get("currency"),
            "receipt": receipt or None,
            "customer_id": raw.get("customer_id"),
            "notes": notes or None,
            "external_id": order_id,
        },
    }


def map_payment(raw: Dict[str, Any]) -> Dict[str, Any]:
    payment_id = str(raw.get("id") or "")
    order_id = str(raw.get("order_id") or "").strip()
    # Prefer order_id as reference so order↔payment exact matching can work.
    reference = order_id or str(raw.get("invoice_id") or payment_id)
    fee_paise = raw.get("fee")
    expected_fee = _paise_to_decimal(fee_paise) if fee_paise is not None else None
    meta: Dict[str, Any] = {
        "provider": "razorpay",
        "status": raw.get("status"),
        "method": raw.get("method"),
        "currency": raw.get("currency"),
        "external_id": payment_id,
        "order_id": order_id or None,
        "email": raw.get("email"),
        "contact": raw.get("contact"),
        "expects_settlement": False,  # settlements often empty in test mode
        "expects_fee": fee_paise is not None,
        "expects_refund": False,
    }
    if expected_fee is not None:
        meta["expected_fee_amount"] = str(expected_fee)
    return {
        "source": "razorpay",
        "record_type": "payment",
        "record_id": payment_id,
        "reference": reference,
        "payment_reference": "",
        "amount": str(_paise_to_decimal(raw.get("amount"))),
        "date": _ts_to_date(raw.get("created_at")),
        # Do NOT put payer email into `customer` — orders have no matching email and
        # the generic engine requires customer equality for exact MATCHED.
        "customer": "",
        "metadata": meta,
    }


def map_refund(raw: Dict[str, Any]) -> Dict[str, Any]:
    refund_id = str(raw.get("id") or "")
    payment_id = str(raw.get("payment_id") or "").strip()
    return {
        "source": "razorpay",
        "record_type": "refund",
        "record_id": refund_id,
        "reference": refund_id,
        "payment_reference": payment_id,
        "amount": str(_paise_to_decimal(raw.get("amount"))),
        "date": _ts_to_date(raw.get("created_at")),
        "customer": "",
        "metadata": {
            "provider": "razorpay",
            "status": raw.get("status"),
            "external_id": refund_id,
        },
    }


def map_settlement(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Settlements in Test Mode are often empty or lack payment linkage.

    We map what Razorpay returns without inventing payment references.
    Orphan detection then stays honest.
    """
    settlement_id = str(raw.get("id") or "")
    return {
        "source": "razorpay",
        "record_type": "settlement",
        "record_id": settlement_id,
        "reference": settlement_id,
        "payment_reference": "",  # unknown — do not invent
        "amount": str(_paise_to_decimal(raw.get("amount"))),
        "date": _ts_to_date(raw.get("created_at")),
        "customer": "",
        "metadata": {
            "provider": "razorpay",
            "status": raw.get("status"),
            "utr": raw.get("utr"),
            "external_id": settlement_id,
            "batch_level": True,
        },
    }


def map_fee_from_payment(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Derive a fee record from payment.fee when present (paise)."""
    fee_paise = raw.get("fee")
    if fee_paise is None:
        return None
    fee_amount = _paise_to_decimal(fee_paise)
    if fee_amount <= Decimal("0.00"):
        return None
    payment_id = str(raw.get("id") or "")
    tax = raw.get("tax")
    return {
        "source": "razorpay",
        "record_type": "fee",
        "record_id": f"fee_{payment_id}",
        "reference": payment_id,
        "payment_reference": payment_id,
        "amount": str(fee_amount),
        "date": _ts_to_date(raw.get("created_at")),
        "customer": "",
        "metadata": {
            "provider": "razorpay",
            "fee_type": "processing",
            "tax": str(_paise_to_decimal(tax)) if tax is not None else None,
            "external_id": f"fee_{payment_id}",
            "expected_amount": str(fee_amount),
        },
    }


def normalize_razorpay_payload(
    *,
    orders: List[Dict[str, Any]],
    payments: List[Dict[str, Any]],
    refunds: List[Dict[str, Any]],
    settlements: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Map provider payloads into engine-ready normalized records.

    Only reconcilable payment statuses enter order↔payment matching. Fees are still
    derived only when fee paise is present (typically captured payments).
    """
    records: List[Dict[str, Any]] = []
    for order in orders:
        records.append(map_order(order))
    for payment in payments:
        status = str(payment.get("status") or "").lower()
        if status not in RECONCILABLE_PAYMENT_STATUSES:
            continue
        records.append(map_payment(payment))
        fee = map_fee_from_payment(payment)
        if fee is not None:
            records.append(fee)
    for refund in refunds:
        records.append(map_refund(refund))
    for settlement in settlements:
        records.append(map_settlement(settlement))
    return records
