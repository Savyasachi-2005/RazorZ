from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional, Set

from app.data_generator import expected_fee_amount
from app.reconciliation.decisions import decide_status
from app.reconciliation.scoring import score_pair


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _normalize_record(record: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(record)
    normalized["amount"] = _to_decimal(record.get("amount", "0"))
    normalized["reference"] = str(record.get("reference", "")).strip()
    normalized["customer"] = str(record.get("customer", "")).strip()
    normalized["record_type"] = str(record.get("record_type") or "unknown")
    normalized["record_id"] = str(record.get("record_id") or "")
    normalized["date"] = str(record.get("date", ""))
    payment_ref = record.get("payment_reference")
    if payment_ref is None or payment_ref == "":
        # Settlements/fees often store the payment id in `reference`.
        if normalized["record_type"] in {"settlement", "fee"}:
            payment_ref = normalized["reference"]
        else:
            payment_ref = ""
    normalized["payment_reference"] = str(payment_ref).strip()
    metadata = record.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    normalized["metadata"] = metadata
    return normalized


def _result(
    record_id: str,
    *,
    matched_with: str | None,
    status: str,
    confidence: Decimal,
    exception_type: str | None,
    amount_diff: Decimal,
    candidates: List[Dict[str, Any]] | None = None,
    pair_type: str = "order_payment",
    source_record_type: str | None = None,
    related_record_type: str | None = None,
) -> Dict[str, Any]:
    return {
        "record_id": record_id,
        "matched_with": matched_with,
        "status": status,
        "confidence": float(confidence),
        "exception_type": exception_type,
        "amount_diff": str(amount_diff),
        "candidates": candidates or [],
        "pair_type": pair_type,
        "source_record_type": source_record_type,
        "related_record_type": related_record_type,
    }


def _top_candidates(order: Dict[str, Any], payments: List[Dict[str, Any]], used: Set[str]) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for payment in payments:
        if payment["record_id"] in used:
            continue
        pair = score_pair(order, payment)
        scored.append(
            {
                "record_id": payment["record_id"],
                "score": float(pair["score"]),
                "features": pair["features"],
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:3]


def _reconcile_orders_payments(orders: List[Dict[str, Any]], payments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Existing Order ↔ Payment reconciliation — behavior preserved."""
    results: List[Dict[str, Any]] = []
    used_payments: Set[str] = set()

    for order in orders:
        exact = next(
            (
                candidate
                for candidate in payments
                if candidate["record_id"] not in used_payments
                and candidate["reference"] == order["reference"]
                and candidate["reference"] != ""
                and candidate["amount"] == order["amount"]
                and candidate["customer"] == order["customer"]
            ),
            None,
        )
        if exact is not None:
            used_payments.add(exact["record_id"])
            status, confidence = decide_status(Decimal("0.99"), exact_match=True)
            results.append(
                _result(
                    order["record_id"],
                    matched_with=exact["record_id"],
                    status=status,
                    confidence=confidence,
                    exception_type=None,
                    amount_diff=Decimal("0.00"),
                    pair_type="order_payment",
                    source_record_type="order",
                    related_record_type="payment",
                )
            )
            continue

        same_identity = [
            candidate
            for candidate in payments
            if candidate["record_id"] not in used_payments
            and candidate["reference"] == order["reference"]
            and candidate["reference"] != ""
            and candidate["customer"] == order["customer"]
        ]
        if len(same_identity) > 1:
            candidates = _top_candidates(order, same_identity, used_payments)
            status, confidence = decide_status(Decimal("0.72"), exception_type="AMBIGUOUS_MATCH", ambiguous=True)
            results.append(
                _result(
                    order["record_id"],
                    matched_with=None,
                    status=status,
                    confidence=confidence,
                    exception_type="AMBIGUOUS_MATCH",
                    amount_diff=Decimal("0.00"),
                    candidates=candidates,
                    pair_type="order_payment",
                    source_record_type="order",
                    related_record_type="payment",
                )
            )
            continue

        if len(same_identity) == 1:
            payment = same_identity[0]
            diff = abs(order["amount"] - payment["amount"])
            if diff > Decimal("0"):
                used_payments.add(payment["record_id"])
                status, confidence = decide_status(Decimal("0.82"), exception_type="AMOUNT_MISMATCH")
                results.append(
                    _result(
                        order["record_id"],
                        matched_with=payment["record_id"],
                        status=status,
                        confidence=confidence,
                        exception_type="AMOUNT_MISMATCH",
                        amount_diff=diff,
                        pair_type="order_payment",
                        source_record_type="order",
                        related_record_type="payment",
                    )
                )
                continue

        candidates = _top_candidates(order, payments, used_payments)
        best = candidates[0] if candidates else None
        second = candidates[1] if len(candidates) > 1 else None
        if best is not None and second is not None and abs(best["score"] - second["score"]) < 0.05 and best["score"] >= 0.70:
            status, confidence = decide_status(Decimal(str(best["score"])), exception_type="AMBIGUOUS_MATCH", ambiguous=True)
            results.append(
                _result(
                    order["record_id"],
                    matched_with=None,
                    status=status,
                    confidence=confidence,
                    exception_type="AMBIGUOUS_MATCH",
                    amount_diff=abs(order["amount"]),
                    candidates=candidates,
                    pair_type="order_payment",
                    source_record_type="order",
                    related_record_type="payment",
                )
            )
            continue

        if best is not None:
            best_score = Decimal(str(best["score"]))
            status, confidence = decide_status(best_score)
            if status in {"AUTO_RESOLVED", "MATCHED"}:
                used_payments.add(best["record_id"])
                results.append(
                    _result(
                        order["record_id"],
                        matched_with=best["record_id"],
                        status=status,
                        confidence=confidence,
                        exception_type=None,
                        amount_diff=Decimal("0.00"),
                        candidates=candidates,
                        pair_type="order_payment",
                        source_record_type="order",
                        related_record_type="payment",
                    )
                )
                continue
            if status == "REVIEW_REQUIRED":
                results.append(
                    _result(
                        order["record_id"],
                        matched_with=best["record_id"],
                        status=status,
                        confidence=confidence,
                        exception_type="AMBIGUOUS_MATCH",
                        amount_diff=abs(order["amount"]),
                        candidates=candidates,
                        pair_type="order_payment",
                        source_record_type="order",
                        related_record_type="payment",
                    )
                )
                continue

        status, confidence = decide_status(Decimal("0.40"), exception_type="PAYMENT_MISSING")
        results.append(
            _result(
                order["record_id"],
                matched_with=None,
                status=status,
                confidence=confidence,
                exception_type="PAYMENT_MISSING",
                amount_diff=abs(order["amount"]),
                candidates=candidates,
                pair_type="order_payment",
                source_record_type="order",
                related_record_type="payment",
            )
        )

    for payment in payments:
        if payment["record_id"] in used_payments:
            continue
        if any(item.get("matched_with") == payment["record_id"] for item in results):
            continue
        status, confidence = decide_status(Decimal("0.35"), exception_type="ORPHAN_PAYMENT")
        results.append(
            _result(
                payment["record_id"],
                matched_with=None,
                status=status,
                confidence=confidence,
                exception_type="ORPHAN_PAYMENT",
                amount_diff=abs(payment["amount"]),
                pair_type="order_payment",
                source_record_type="payment",
                related_record_type="order",
            )
        )

    return results


def _payment_ids(payments: List[Dict[str, Any]]) -> Set[str]:
    return {p["record_id"] for p in payments}


def _meta_flag(record: Dict[str, Any], key: str, default: bool = False) -> bool:
    value = (record.get("metadata") or {}).get(key, default)
    return bool(value)


def _should_run_settlements(normalized: List[Dict[str, Any]]) -> bool:
    if any(r["record_type"] == "settlement" for r in normalized):
        return True
    return any(
        _meta_flag(r, "expects_settlement", False) for r in normalized if r["record_type"] == "payment"
    )


def _should_run_refunds(normalized: List[Dict[str, Any]]) -> bool:
    if any(r["record_type"] == "refund" for r in normalized):
        return True
    return any(_meta_flag(r, "expects_refund", False) for r in normalized if r["record_type"] == "payment")


def _should_run_fees(normalized: List[Dict[str, Any]]) -> bool:
    if any(r["record_type"] == "fee" for r in normalized):
        return True
    return any(_meta_flag(r, "expects_fee", False) for r in normalized if r["record_type"] == "payment")


def _reconcile_payments_settlements(
    payments: List[Dict[str, Any]],
    settlements: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    known = _payment_ids(payments)
    by_payment: Dict[str, List[Dict[str, Any]]] = {}
    for settlement in settlements:
        key = settlement["payment_reference"] or settlement["reference"]
        by_payment.setdefault(key, []).append(settlement)

    used_settlements: Set[str] = set()

    for payment in payments:
        meta = payment.get("metadata") or {}
        if "expects_settlement" in meta and not bool(meta["expects_settlement"]):
            continue

        linked = by_payment.get(payment["record_id"], [])
        if not linked:
            status, confidence = decide_status(Decimal("0.40"), exception_type="SETTLEMENT_MISSING")
            results.append(
                _result(
                    payment["record_id"],
                    matched_with=None,
                    status=status,
                    confidence=confidence,
                    exception_type="SETTLEMENT_MISSING",
                    amount_diff=abs(payment["amount"]),
                    pair_type="payment_settlement",
                    source_record_type="payment",
                    related_record_type="settlement",
                )
            )
            continue

        if len(linked) > 1:
            for settlement in linked:
                used_settlements.add(settlement["record_id"])
            status, confidence = decide_status(Decimal("0.55"), exception_type="DUPLICATE_SETTLEMENT")
            results.append(
                _result(
                    payment["record_id"],
                    matched_with=linked[0]["record_id"],
                    status=status,
                    confidence=confidence,
                    exception_type="DUPLICATE_SETTLEMENT",
                    amount_diff=Decimal("0.00"),
                    candidates=[
                        {"record_id": s["record_id"], "score": 1.0, "features": {"duplicate": "true"}}
                        for s in linked
                    ],
                    pair_type="payment_settlement",
                    source_record_type="payment",
                    related_record_type="settlement",
                )
            )
            continue

        settlement = linked[0]
        used_settlements.add(settlement["record_id"])
        diff = abs(payment["amount"] - settlement["amount"])
        if diff > Decimal("0"):
            status, confidence = decide_status(Decimal("0.82"), exception_type="SETTLEMENT_AMOUNT_MISMATCH")
            results.append(
                _result(
                    payment["record_id"],
                    matched_with=settlement["record_id"],
                    status=status,
                    confidence=confidence,
                    exception_type="SETTLEMENT_AMOUNT_MISMATCH",
                    amount_diff=diff,
                    pair_type="payment_settlement",
                    source_record_type="payment",
                    related_record_type="settlement",
                )
            )
        else:
            status, confidence = decide_status(Decimal("0.99"), exact_match=True)
            results.append(
                _result(
                    payment["record_id"],
                    matched_with=settlement["record_id"],
                    status=status,
                    confidence=confidence,
                    exception_type=None,
                    amount_diff=Decimal("0.00"),
                    pair_type="payment_settlement",
                    source_record_type="payment",
                    related_record_type="settlement",
                )
            )

    for settlement in settlements:
        if settlement["record_id"] in used_settlements:
            continue
        key = settlement["payment_reference"] or settlement["reference"]
        if key not in known:
            status, confidence = decide_status(Decimal("0.35"), exception_type="ORPHAN_SETTLEMENT")
            results.append(
                _result(
                    settlement["record_id"],
                    matched_with=None,
                    status=status,
                    confidence=confidence,
                    exception_type="ORPHAN_SETTLEMENT",
                    amount_diff=abs(settlement["amount"]),
                    pair_type="payment_settlement",
                    source_record_type="settlement",
                    related_record_type="payment",
                )
            )

    return results


def _reconcile_payments_refunds(
    payments: List[Dict[str, Any]],
    refunds: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    known = _payment_ids(payments)
    by_payment: Dict[str, List[Dict[str, Any]]] = {}
    for refund in refunds:
        key = refund["payment_reference"] or refund["reference"]
        by_payment.setdefault(key, []).append(refund)

    used_refunds: Set[str] = set()

    for payment in payments:
        linked = by_payment.get(payment["record_id"], [])
        expects = _meta_flag(payment, "expects_refund", False)

        if not linked:
            if expects:
                expected_amt = _to_decimal(
                    (payment.get("metadata") or {}).get("expected_refund_amount", payment["amount"])
                )
                status, confidence = decide_status(Decimal("0.40"), exception_type="REFUND_MISSING")
                results.append(
                    _result(
                        payment["record_id"],
                        matched_with=None,
                        status=status,
                        confidence=confidence,
                        exception_type="REFUND_MISSING",
                        amount_diff=abs(expected_amt),
                        pair_type="payment_refund",
                        source_record_type="payment",
                        related_record_type="refund",
                    )
                )
            continue

        for refund in linked:
            used_refunds.add(refund["record_id"])

        total_refund = sum((r["amount"] for r in linked), Decimal("0.00"))
        primary = linked[0]

        if total_refund > payment["amount"]:
            status, confidence = decide_status(Decimal("0.50"), exception_type="REFUND_EXCESSIVE")
            results.append(
                _result(
                    payment["record_id"],
                    matched_with=primary["record_id"],
                    status=status,
                    confidence=confidence,
                    exception_type="REFUND_EXCESSIVE",
                    amount_diff=total_refund - payment["amount"],
                    candidates=[
                        {"record_id": r["record_id"], "score": 1.0, "features": {"amount": str(r["amount"])}}
                        for r in linked
                    ],
                    pair_type="payment_refund",
                    source_record_type="payment",
                    related_record_type="refund",
                )
            )
            continue

        if len(linked) > 1:
            # Multiple refunds within captured amount — review (not auto-resolve).
            status, confidence = decide_status(Decimal("0.75"), exception_type="MULTIPLE_REFUNDS")
            results.append(
                _result(
                    payment["record_id"],
                    matched_with=primary["record_id"],
                    status=status,
                    confidence=confidence,
                    exception_type="MULTIPLE_REFUNDS",
                    amount_diff=Decimal("0.00"),
                    candidates=[
                        {"record_id": r["record_id"], "score": 1.0, "features": {"amount": str(r["amount"])}}
                        for r in linked
                    ],
                    pair_type="payment_refund",
                    source_record_type="payment",
                    related_record_type="refund",
                )
            )
            continue

        status, confidence = decide_status(Decimal("0.99"), exact_match=True)
        results.append(
            _result(
                payment["record_id"],
                matched_with=primary["record_id"],
                status=status,
                confidence=confidence,
                exception_type=None,
                amount_diff=Decimal("0.00"),
                pair_type="payment_refund",
                source_record_type="payment",
                related_record_type="refund",
            )
        )

    for refund in refunds:
        if refund["record_id"] in used_refunds:
            continue
        key = refund["payment_reference"] or refund["reference"]
        if key not in known:
            status, confidence = decide_status(Decimal("0.35"), exception_type="ORPHAN_REFUND")
            results.append(
                _result(
                    refund["record_id"],
                    matched_with=None,
                    status=status,
                    confidence=confidence,
                    exception_type="ORPHAN_REFUND",
                    amount_diff=abs(refund["amount"]),
                    pair_type="payment_refund",
                    source_record_type="refund",
                    related_record_type="payment",
                )
            )

    return results


def _expected_fee_for_payment(payment: Dict[str, Any]) -> Decimal:
    meta = payment.get("metadata") or {}
    if meta.get("expected_fee_amount") is not None:
        return _to_decimal(meta["expected_fee_amount"])
    return expected_fee_amount(payment["amount"])


def _reconcile_payments_fees(
    payments: List[Dict[str, Any]],
    fees: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    known = _payment_ids(payments)
    by_payment: Dict[str, List[Dict[str, Any]]] = {}
    for fee in fees:
        key = fee["payment_reference"] or fee["reference"]
        by_payment.setdefault(key, []).append(fee)

    used_fees: Set[str] = set()

    for payment in payments:
        expects = True
        if "expects_fee" in (payment.get("metadata") or {}):
            expects = _meta_flag(payment, "expects_fee", True)
        if not expects:
            continue

        linked = by_payment.get(payment["record_id"], [])
        expected = _expected_fee_for_payment(payment)

        if not linked:
            status, confidence = decide_status(Decimal("0.40"), exception_type="FEE_MISSING")
            results.append(
                _result(
                    payment["record_id"],
                    matched_with=None,
                    status=status,
                    confidence=confidence,
                    exception_type="FEE_MISSING",
                    amount_diff=abs(expected),
                    pair_type="payment_fee",
                    source_record_type="payment",
                    related_record_type="fee",
                )
            )
            continue

        fee = linked[0]
        for item in linked:
            used_fees.add(item["record_id"])

        # Sum fees if multiple; compare to expected.
        total_fee = sum((f["amount"] for f in linked), Decimal("0.00"))
        diff = abs(total_fee - expected)
        if diff > Decimal("0"):
            status, confidence = decide_status(Decimal("0.82"), exception_type="FEE_DIFFERENCE")
            results.append(
                _result(
                    payment["record_id"],
                    matched_with=fee["record_id"],
                    status=status,
                    confidence=confidence,
                    exception_type="FEE_DIFFERENCE",
                    amount_diff=diff,
                    pair_type="payment_fee",
                    source_record_type="payment",
                    related_record_type="fee",
                )
            )
        else:
            status, confidence = decide_status(Decimal("0.99"), exact_match=True)
            results.append(
                _result(
                    payment["record_id"],
                    matched_with=fee["record_id"],
                    status=status,
                    confidence=confidence,
                    exception_type=None,
                    amount_diff=Decimal("0.00"),
                    pair_type="payment_fee",
                    source_record_type="payment",
                    related_record_type="fee",
                )
            )

    for fee in fees:
        if fee["record_id"] in used_fees:
            continue
        key = fee["payment_reference"] or fee["reference"]
        if key not in known:
            status, confidence = decide_status(Decimal("0.35"), exception_type="FEE_UNEXPECTED")
            results.append(
                _result(
                    fee["record_id"],
                    matched_with=None,
                    status=status,
                    confidence=confidence,
                    exception_type="FEE_UNEXPECTED",
                    amount_diff=abs(fee["amount"]),
                    pair_type="payment_fee",
                    source_record_type="fee",
                    related_record_type="payment",
                )
            )

    return results


def reconcile_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Deterministic multi-record reconciliation.

    Order ↔ Payment always runs. Settlement / refund / fee layers activate only when
    those record types (or explicit expects_* metadata) are present — so existing
    order/payment-only callers keep identical behavior.
    """
    normalized = [_normalize_record(record) for record in records]
    orders = [r for r in normalized if r["record_type"] == "order"]
    payments = [r for r in normalized if r["record_type"] == "payment"]
    settlements = [r for r in normalized if r["record_type"] == "settlement"]
    refunds = [r for r in normalized if r["record_type"] == "refund"]
    fees = [r for r in normalized if r["record_type"] == "fee"]

    results: List[Dict[str, Any]] = []
    results.extend(_reconcile_orders_payments(orders, payments))

    if _should_run_settlements(normalized):
        # Default expects_settlement=True for payments when layer is active unless overridden.
        for payment in payments:
            meta = payment.setdefault("metadata", {})
            if "expects_settlement" not in meta:
                meta["expects_settlement"] = True
        results.extend(_reconcile_payments_settlements(payments, settlements))

    if _should_run_refunds(normalized):
        results.extend(_reconcile_payments_refunds(payments, refunds))

    if _should_run_fees(normalized):
        for payment in payments:
            meta = payment.setdefault("metadata", {})
            if "expects_fee" not in meta:
                meta["expects_fee"] = True
        results.extend(_reconcile_payments_fees(payments, fees))

    return results
