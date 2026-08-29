from __future__ import annotations

"""Read-only Copilot tools.

Every tool here is a predefined backend function with validated parameters.
There is no arbitrary SQL, no model-generated SQL, and no write path. Financial
arithmetic happens here with Decimal so the LLM only ever explains numbers.
"""

import json
from collections import Counter
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional

from app.copilot.schemas import CopilotError, ToolResult
from app.exceptions.intelligence import classify, enrich_exception
from app.repositories.reconciliation_repository import ReconciliationRepository
from app.repositories.record_repository import RECORD_TYPES, RecordRepository

MAX_ROWS = 10

# Exception lifecycle states actually stored on ExceptionRecord.status.
VALID_EXCEPTION_STATUSES = {"OPEN", "RESOLVED", "REJECTED"}
# The reconciliation decision that created the exception. REVIEW_REQUIRED lives here,
# not on the exception row — the two are different concepts and must not be conflated.
VALID_ORIGIN_DECISIONS = {"REVIEW_REQUIRED", "EXCEPTION", "UNRESOLVED"}
VALID_PRIORITIES = {"P1", "P2", "P3"}
VALID_PAIR_TYPES = {"order_payment", "payment_settlement", "payment_refund", "payment_fee"}

# One authoritative description per lifecycle state.
STATUS_SEMANTICS: Dict[str, Dict[str, Any]] = {
    "OPEN": {
        "meaning": "Open — no final human decision has been recorded yet.",
        "state": "pending",
        "is_unresolved": True,
        "human_decision": None,
    },
    "RESOLVED": {
        "meaning": "Resolved — a human reviewer accepted and closed this exception.",
        "state": "final",
        "is_unresolved": False,
        "human_decision": "resolved",
    },
    "REJECTED": {
        "meaning": "Rejected — a human reviewer rejected this exception. It is closed, not pending.",
        "state": "final",
        "is_unresolved": False,
        "human_decision": "rejected",
    },
}

ORIGIN_DECISION_MEANINGS = {
    "REVIEW_REQUIRED": "the engine found a candidate but confidence was too low to post automatically",
    "EXCEPTION": "the engine found no acceptable match",
    "UNRESOLVED": "the engine could not reach a decision",
}

# Aggregated exception amounts are recorded differences, not proven cash loss.
AMOUNT_BASIS = "recorded reconciliation difference (not confirmed monetary loss)"


def _money(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0.00")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0.00")


def _as_str(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _int_param(value: Any, *, default: int, low: int, high: int, name: str) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise CopilotError(f"{name} must be an integer", code="invalid_parameters") from None
    if parsed < low or parsed > high:
        raise CopilotError(f"{name} must be between {low} and {high}", code="invalid_parameters")
    return parsed


def _choice_param(value: Any, allowed: set[str], name: str) -> Optional[str]:
    """Normalize a filter to one of `allowed`, matching the casing used by `allowed`."""
    if value in (None, "", "ALL"):
        return None
    lookup = {item.casefold(): item for item in allowed}
    normalized = lookup.get(str(value).strip().casefold())
    if normalized is None:
        raise CopilotError(f"{name} must be one of {sorted(allowed)}", code="invalid_parameters")
    return normalized


def _evidence_dict(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def status_semantics(status: str, *, resolved_by: str | None = None) -> Dict[str, Any]:
    """Describe a lifecycle state so the Copilot cannot conflate REJECTED with OPEN."""
    key = str(status or "").strip().upper()
    profile = STATUS_SEMANTICS.get(
        key,
        {
            "meaning": f"{key or 'UNKNOWN'} — state not recognized by the current implementation.",
            "state": "unknown",
            "is_unresolved": True,
            "human_decision": None,
        },
    )
    return {
        "status": key,
        "meaning": profile["meaning"],
        "state": profile["state"],
        "is_unresolved": profile["is_unresolved"],
        "human_decision": profile["human_decision"],
        "decided_by": resolved_by if profile["human_decision"] else None,
        "reopenable": False,
    }


def _origin_decision(description: str | None) -> Optional[str]:
    """Recover the reconciliation decision that created the exception.

    Descriptions are written as "<DECISION> for <record_id>", so the leading token is
    the engine decision (REVIEW_REQUIRED / EXCEPTION / UNRESOLVED) — never a guess.
    """
    head = str(description or "").strip().split(" ", 1)[0].upper()
    return head if head in VALID_ORIGIN_DECISIONS else None


def _compact_exception(row: Any) -> Dict[str, Any]:
    enriched = enrich_exception(row)
    evidence = _evidence_dict(enriched.get("evidence"))
    # Numeric columns can round-trip with trailing precision; present money as 2dp.
    amount = enriched["amount"]
    origin = _origin_decision(enriched.get("description"))
    return {
        "exception_id": f"EX-{enriched['id']}",
        "id": enriched["id"],
        "exception_type": enriched["exception_type"],
        "status": enriched["status"],
        "status_semantics": status_semantics(
            enriched["status"], resolved_by=enriched.get("resolved_by")
        ),
        "origin_decision": origin,
        "origin_decision_meaning": ORIGIN_DECISION_MEANINGS.get(origin) if origin else None,
        "severity": enriched["severity"],
        "priority": enriched["priority"],
        "amount": _as_str(_money(amount)) if amount is not None else None,
        "amount_basis": AMOUNT_BASIS if amount is not None else None,
        "pair_type": evidence.get("pair_type"),
        "matched_with": evidence.get("matched_with"),
        "recommended_action": enriched["recommended_action"],
    }


# ---------------------------------------------------------------- tools


def get_reconciliation_summary(*, database_url: str | None = None, **_: Any) -> Dict[str, Any]:
    """Deterministic reconciliation metrics for the latest run, plus exception mix."""
    repo = ReconciliationRepository(database_url)
    summary = repo.summary()
    total = int(summary.get("total_records") or 0)

    exceptions = repo.list_exceptions(limit=500)
    open_exceptions = [row for row in exceptions if row.status == "OPEN"]
    type_counts = Counter(row.exception_type for row in open_exceptions)

    review_rate = round((int(summary.get("review_required") or 0) / total), 4) if total else 0.0
    unresolved_total = int(summary.get("review_required") or 0) + int(summary.get("exceptions") or 0)

    return {
        "total_records": total,
        "matched": int(summary.get("matched") or 0),
        "review_required": int(summary.get("review_required") or 0),
        "exceptions": int(summary.get("exceptions") or 0),
        "unresolved": int(summary.get("unresolved") or 0),
        "match_rate": f"{float(summary.get('match_rate') or 0.0) * 100:.2f}%",
        "review_rate": f"{review_rate * 100:.2f}%",
        "unresolved_count": unresolved_total,
        "open_exception_count": len(open_exceptions),
        "top_exception_types": [
            {"type": name, "count": count} for name, count in type_counts.most_common(5)
        ],
        "scope": "latest reconciliation run",
    }


def search_exceptions(
    *,
    status: Any = None,
    exception_type: Any = None,
    priority: Any = None,
    pair_type: Any = None,
    origin_decision: Any = None,
    min_amount: Any = None,
    limit: Any = None,
    database_url: str | None = None,
    **_: Any,
) -> Dict[str, Any]:
    """Filtered exception search. Filters are validated; no free-form query reaches SQL."""
    status_filter = _choice_param(status, VALID_EXCEPTION_STATUSES, "status")
    priority_filter = _choice_param(priority, VALID_PRIORITIES, "priority")
    pair_filter = _choice_param(pair_type, VALID_PAIR_TYPES, "pair_type")
    origin_filter = _choice_param(origin_decision, VALID_ORIGIN_DECISIONS, "origin_decision")
    row_limit = _int_param(limit, default=MAX_ROWS, low=1, high=MAX_ROWS, name="limit")

    type_filter = None
    if exception_type not in (None, "", "ALL"):
        type_filter = str(exception_type).strip().upper()

    floor = _money(min_amount) if min_amount not in (None, "") else None

    repo = ReconciliationRepository(database_url)
    rows = repo.list_exceptions(limit=500)

    matches: List[Dict[str, Any]] = []
    for row in rows:
        compact = _compact_exception(row)
        if status_filter and compact["status"] != status_filter:
            continue
        if type_filter and compact["exception_type"] != type_filter:
            continue
        if priority_filter and compact["priority"] != priority_filter:
            continue
        if pair_filter and compact.get("pair_type") != pair_filter:
            continue
        if origin_filter and compact.get("origin_decision") != origin_filter:
            continue
        if floor is not None and _money(compact["amount"]) < floor:
            continue
        matches.append(compact)

    exposure = sum((_money(item["amount"]) for item in matches), Decimal("0.00"))

    # Largest exposure first, so the rows shown are the ones a reviewer should open.
    matches.sort(key=lambda item: (-_money(item["amount"]), item["id"]))
    highest = matches[0] if matches else None

    status_mix = Counter(item["status"] for item in matches)

    return {
        "match_count": len(matches),
        "returned": min(len(matches), row_limit),
        "total_amount": _as_str(exposure),
        "amount_basis": AMOUNT_BASIS,
        "highest_amount_exception": highest["exception_id"] if highest else None,
        "status_mix": {status: count for status, count in sorted(status_mix.items())},
        "type_breakdown": _type_breakdown(matches, exposure),
        "filters": {
            "status": status_filter,
            "exception_type": type_filter,
            "priority": priority_filter,
            "pair_type": pair_filter,
            "origin_decision": origin_filter,
            "min_amount": _as_str(floor) if floor is not None else None,
        },
        "exceptions": matches[:row_limit],
    }


def _percentage(part: Decimal, whole: Decimal) -> str:
    """Exact 2dp percentage as a string, e.g. "27.59". Decimal only — never float."""
    if whole <= 0:
        return "0.00"
    return str((part / whole * Decimal("100")).quantize(Decimal("0.01")))


def _share(part: Decimal, whole: Decimal) -> str:
    """Percentage share, never rounding a real contribution down to a flat 0%."""
    if whole <= 0:
        return "0%"
    percent = part / whole * Decimal("100")
    if percent > 0 and percent < 1:
        return "<1%"
    return f"{percent:.0f}%"


def _type_breakdown(matches: List[Dict[str, Any]], exposure: Decimal) -> List[Dict[str, Any]]:
    """Per-type counts, money and share — computed here so the LLM never does arithmetic."""
    grouped: Dict[str, Dict[str, Any]] = {}
    total = len(matches)
    for row in matches:
        bucket = grouped.setdefault(
            row["exception_type"],
            {"type": row["exception_type"], "count": 0, "amount": Decimal("0.00")},
        )
        bucket["count"] += 1
        bucket["amount"] += _money(row["amount"])

    breakdown: List[Dict[str, Any]] = []
    for bucket in sorted(grouped.values(), key=lambda item: (-item["count"], item["type"])):
        profile = classify(bucket["type"])
        breakdown.append(
            {
                "type": bucket["type"],
                "count": bucket["count"],
                "total_amount": _as_str(bucket["amount"]),
                "share_of_count": _share(Decimal(bucket["count"]), Decimal(total)),
                "share_of_amount": _share(bucket["amount"], exposure),
                # Exact backend-computed percentages; the LLM must never divide.
                "queue_percentage": _percentage(Decimal(bucket["count"]), Decimal(total)),
                "amount_percentage": _percentage(bucket["amount"], exposure),
                "meaning": profile["meaning"],
                "likely_causes": profile["root_causes"],
                "recommended_action": profile["recommended_action"],
            }
        )
    return breakdown[:5]


def get_exception(*, exception_id: Any = None, database_url: str | None = None, **_: Any) -> Dict[str, Any]:
    """Compact detail for one exception, including deterministic evidence."""
    if exception_id in (None, ""):
        raise CopilotError("exception_id is required", code="invalid_parameters")
    text = str(exception_id).strip().upper().removeprefix("EX-")
    try:
        numeric = int(text)
    except ValueError:
        raise CopilotError("exception_id must look like 12 or EX-12", code="invalid_parameters") from None

    repo = ReconciliationRepository(database_url)
    row = repo.get_exception(numeric)
    if row is None:
        return {"found": False, "exception_id": f"EX-{numeric}"}

    enriched = enrich_exception(row)
    compact = _compact_exception(row)
    compact.update(
        {
            "found": True,
            "exception_meaning": classify(enriched["exception_type"])["meaning"],
            "confidence_label": "deterministic reconciliation confidence",
            "confidence": (
                str(Decimal(str(enriched["confidence"])).quantize(Decimal("0.01")))
                if enriched["confidence"] is not None
                else None
            ),
            "description": enriched["description"],
            "certainty": enriched["certainty"],
            "possible_root_causes": enriched["possible_root_causes"],
            "human_required": enriched["human_required"],
            "reviewer_note": enriched["reviewer_note"],
            "resolved_by": enriched["resolved_by"],
        }
    )
    return compact


def _event_category(event_type: str, actor: str) -> str:
    """Separate machine detection, AI advice, and human decisions — never conflate them."""
    kind = str(event_type or "").lower()
    if kind == "human_review":
        return "human_decision"
    if kind == "ai_assistance":
        return "ai_assistance"
    if kind in {"reconciliation_decision", "exception_created"}:
        return "system_detection"
    if kind == "copilot_query":
        return "ai_query"
    return "other"


def get_audit_events(
    *,
    entity_id: Any = None,
    event_type: Any = None,
    limit: Any = None,
    database_url: str | None = None,
    **_: Any,
) -> Dict[str, Any]:
    """Recent audit history, optionally scoped to one entity or event type."""
    row_limit = _int_param(limit, default=MAX_ROWS, low=1, high=MAX_ROWS, name="limit")
    entity = str(entity_id).strip() if entity_id not in (None, "") else None
    if entity:
        entity = entity.upper().removeprefix("EX-")
    kind = str(event_type).strip() if event_type not in (None, "") else None

    repo = ReconciliationRepository(database_url)
    rows = repo.list_audit(limit=200)

    events: List[Dict[str, Any]] = []
    for row in rows:
        if entity and row.entity_id != entity:
            continue
        if kind and row.event_type != kind:
            continue
        evidence = _evidence_dict(row.evidence)
        category = _event_category(row.event_type, row.actor)
        events.append(
            {
                "event_type": row.event_type,
                "category": category,
                "entity_type": row.entity_type,
                "entity_id": row.entity_id,
                "actor": row.actor,
                "action": row.action,
                "new_state": row.new_state,
                # Reviewer note is the only free text worth surfacing as evidence.
                "note": evidence.get("note") if category == "human_decision" else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
        )
        if len(events) >= row_limit:
            break

    # `rows` is newest-first, so the first human decision found is the latest one.
    human_decision = next(
        (event for event in events if event["category"] == "human_decision"), None
    )
    return {
        "returned": len(events),
        "categories": {
            category: sum(1 for event in events if event["category"] == category)
            for category in sorted({event["category"] for event in events})
        },
        "latest_human_decision": human_decision,
        "ai_assistance_count": sum(1 for event in events if event["category"] == "ai_assistance"),
        "events": events,
    }


def search_records(
    *,
    record_type: Any = None,
    query: Any = None,
    limit: Any = None,
    database_url: str | None = None,
    **_: Any,
) -> Dict[str, Any]:
    """Controlled lookup of stored orders/payments/settlements/refunds/fees."""
    row_limit = _int_param(limit, default=MAX_ROWS, low=1, high=MAX_ROWS, name="limit")
    kind = None
    if record_type not in (None, "", "ALL"):
        kind = str(record_type).strip().lower()
        if kind not in RECORD_TYPES:
            raise CopilotError(
                f"record_type must be one of {sorted(RECORD_TYPES)}", code="invalid_parameters"
            )

    needle = str(query).strip() if query not in (None, "") else None
    repo = RecordRepository(database_url)
    rows = repo.search_records(record_type=kind, query=needle, limit=row_limit)
    return {
        "returned": len(rows),
        "filters": {"record_type": kind, "query": needle},
        "records": rows,
    }


def get_record_relationships(
    *, record_id: Any = None, database_url: str | None = None, **_: Any
) -> Dict[str, Any]:
    """Order → Payment → Settlement/Refund/Fee links for one stored record."""
    if record_id in (None, ""):
        raise CopilotError("record_id is required", code="invalid_parameters")
    target = str(record_id).strip()
    repo = RecordRepository(database_url)
    return repo.relationships(target)


def get_financial_summary(*, database_url: str | None = None, **_: Any) -> Dict[str, Any]:
    """Deterministic Decimal totals plus unresolved financial exposure."""
    records = RecordRepository(database_url)
    totals = records.financial_totals()

    recon = ReconciliationRepository(database_url)
    exceptions = recon.list_exceptions(limit=500)
    open_rows = [row for row in exceptions if row.status == "OPEN"]
    exposure = sum((_money(row.amount) for row in open_rows), Decimal("0.00"))

    payments_total = _money(totals["payment"]["total"])
    settlements_total = _money(totals["settlement"]["total"])

    return {
        "total_payments": totals["payment"]["total"],
        "payment_count": totals["payment"]["count"],
        "total_orders": totals["order"]["total"],
        "order_count": totals["order"]["count"],
        "total_refunds": totals["refund"]["total"],
        "refund_count": totals["refund"]["count"],
        "total_fees": totals["fee"]["total"],
        "fee_count": totals["fee"]["count"],
        "total_settlements": totals["settlement"]["total"],
        "settlement_count": totals["settlement"]["count"],
        "payments_minus_settlements": _as_str(payments_total - settlements_total),
        "unresolved_exposure": _as_str(exposure),
        "unresolved_exposure_basis": (
            "sum of recorded differences on OPEN exceptions only; "
            "resolved and rejected exceptions are excluded"
        ),
        "open_exception_count": len(open_rows),
        "currency": "INR",
        "scope": "all ingested records",
    }


PAIR_LABELS = {
    "order_payment": "Order ↔ Payment",
    "payment_settlement": "Payment ↔ Settlement",
    "payment_refund": "Payment ↔ Refund",
    "payment_fee": "Payment ↔ Fee",
}

# One neutral sentence per relationship, so aggregate answers stay specific.
PAIR_MEANINGS = {
    "order_payment": "an order should have a captured payment of the same value",
    "payment_settlement": "a captured payment should arrive in a settlement payout",
    "payment_refund": "a refund should trace back to the payment it reverses",
    "payment_fee": "a payment should carry the processing fee charged against it",
}


def get_unsettled_payments(
    *, limit: Any = None, database_url: str | None = None, **_: Any
) -> Dict[str, Any]:
    """Stored payments with no settlement record pointing at them."""
    row_limit = _int_param(limit, default=MAX_ROWS, low=1, high=MAX_ROWS, name="limit")
    records = RecordRepository(database_url)
    linkage = records.unsettled_payments(limit=row_limit)

    payments = linkage["payments"]
    unsettled = linkage["unsettled"]
    settled = linkage["settled"]
    total_count = int(payments["count"])

    if not total_count:
        return {
            "payment_count": 0,
            "unsettled_count": 0,
            "unsettled_amount": "0.00",
            "settled_count": 0,
            "settled_amount": "0.00",
            "settlement_coverage_percentage": "0.00",
            "unsettled_share_percentage": "0.00",
            "amount_basis": "sum of payment amounts with no linked settlement record",
            "unsettled_payments": [],
            "scope": "all ingested payments",
            "note": "No payments are stored yet, so settlement coverage cannot be assessed.",
        }

    return {
        "payment_count": total_count,
        "unsettled_count": int(unsettled["count"]),
        "unsettled_amount": unsettled["total"],
        "settled_count": int(settled["count"]),
        "settled_amount": settled["total"],
        "settlement_coverage_percentage": _percentage(
            Decimal(settled["count"]), Decimal(total_count)
        ),
        "unsettled_share_percentage": _percentage(
            Decimal(unsettled["count"]), Decimal(total_count)
        ),
        "amount_basis": "sum of payment amounts with no linked settlement record",
        "unsettled_payments": [
            {
                "payment_id": row["record_id"],
                "amount": row["amount"],
                "date": row.get("date"),
                "status": row.get("status"),
                "order_reference": row.get("order_reference"),
            }
            for row in linkage["sample"]
        ],
        "scope": "all ingested payments",
    }


def get_settlement_summary(*, database_url: str | None = None, **_: Any) -> Dict[str, Any]:
    """Settlement-side reconciliation: coverage, orphans, amount mismatches, open breaks."""
    records = RecordRepository(database_url)
    linkage = records.settlement_linkage()
    unsettled = records.unsettled_payments(limit=5)

    recon = ReconciliationRepository(database_url)
    breakdown = recon.pair_type_breakdown("payment_settlement")
    pair = breakdown["pairs"].get("payment_settlement", {})

    # Open settlement-side exceptions, taken from the exception queue as recorded.
    open_rows = [
        row
        for row in (_compact_exception(item) for item in recon.list_exceptions(limit=500))
        if row["status"] == "OPEN" and row.get("pair_type") == "payment_settlement"
    ]
    open_amount = sum((_money(row["amount"]) for row in open_rows), Decimal("0.00"))
    open_types = Counter(row["exception_type"] for row in open_rows)

    settlements = linkage["settlements"]
    payments_total = int(unsettled["payments"]["count"])

    payload: Dict[str, Any] = {
        "settlement_count": int(settlements["count"]),
        "settlement_amount": settlements["total"],
        "linked_to_payment_count": int(linkage["linked_to_payment"]["count"]),
        "orphan_settlement_count": int(linkage["orphan_settlements"]["count"]),
        "orphan_settlement_amount": linkage["orphan_settlements"]["total"],
        "orphan_settlements": [
            {"settlement_id": row["record_id"], "amount": row["amount"], "date": row.get("date")}
            for row in linkage["orphan_sample"]
        ],
        "payments_missing_from_settlements": int(unsettled["unsettled"]["count"]),
        "payments_missing_amount": unsettled["unsettled"]["total"],
        "settlement_coverage_percentage": (
            _percentage(Decimal(unsettled["settled"]["count"]), Decimal(payments_total))
            if payments_total
            else "0.00"
        ),
        "amount_mismatch_count": int(linkage["amount_mismatch_count"]),
        "amount_mismatches": linkage["amount_mismatches"],
        "reconciled_pairs": int(pair.get("total") or 0),
        "reconciliation_status_mix": pair.get("statuses") or {},
        "reconciliation_difference": pair.get("difference") or "0.00",
        "open_exception_count": len(open_rows),
        "open_exception_amount": _as_str(open_amount),
        "open_exception_types": [
            {"type": name, "count": count} for name, count in open_types.most_common(5)
        ],
        "unresolved_basis": (
            "OPEN payment↔settlement exceptions only; resolved and rejected are excluded"
        ),
        "amount_basis": AMOUNT_BASIS,
        "scope": "all ingested settlements; reconciliation counts from the latest run",
    }
    if not payload["settlement_count"] and not payload["payments_missing_from_settlements"]:
        payload["note"] = "No settlement records are stored yet."
    return payload


def get_cross_source_summary(*, database_url: str | None = None, **_: Any) -> Dict[str, Any]:
    """Compare reconciliation health across each source relationship."""
    recon = ReconciliationRepository(database_url)
    breakdown = recon.pair_type_breakdown()
    coverage = RecordRepository(database_url).link_coverage()

    open_by_pair: Dict[str, Dict[str, Any]] = {}
    for item in recon.list_exceptions(limit=500):
        row = _compact_exception(item)
        if row["status"] != "OPEN":
            continue
        bucket = open_by_pair.setdefault(
            str(row.get("pair_type") or "unknown"),
            {"count": 0, "amount": Decimal("0.00"), "types": Counter()},
        )
        bucket["count"] += 1
        bucket["amount"] += _money(row["amount"])
        bucket["types"][row["exception_type"]] += 1

    # Structural linkage per relationship, straight from stored records.
    structural = {
        "payment_settlement": (
            coverage["settlement"]["payments_without_link"],
            coverage["settlement"]["orphan_records"],
            "payments with no settlement",
        ),
        "payment_refund": (
            None,
            coverage["refund"]["orphan_records"],
            "refunds with no parent payment",
        ),
        "payment_fee": (
            coverage["fee"]["payments_without_link"],
            coverage["fee"]["orphan_records"],
            "payments with no fee record",
        ),
        "order_payment": (
            coverage["order"]["orders_without_payment"],
            coverage["order"]["orphan_payments"],
            "orders with no payment",
        ),
    }

    pairs: List[Dict[str, Any]] = []
    for pair_type in ("order_payment", "payment_settlement", "payment_refund", "payment_fee"):
        stats = breakdown["pairs"].get(pair_type, {})
        total = int(stats.get("total") or 0)
        statuses = stats.get("statuses") or {}
        matched = int(statuses.get("MATCHED", 0)) + int(statuses.get("AUTO_RESOLVED", 0))
        review = int(statuses.get("REVIEW_REQUIRED", 0))
        hard = int(statuses.get("EXCEPTION", 0))
        unresolved = int(statuses.get("UNRESOLVED", 0))
        open_bucket = open_by_pair.get(pair_type, {})
        missing, orphans, missing_label = structural[pair_type]

        pairs.append(
            {
                "pair_type": pair_type,
                "label": PAIR_LABELS[pair_type],
                "expectation": PAIR_MEANINGS[pair_type],
                "reconciled_pairs": total,
                "matched": matched,
                "review_required": review,
                "exceptions": hard,
                "unresolved": unresolved,
                "match_percentage": _percentage(Decimal(matched), Decimal(total)) if total else None,
                "unmatched_count": review + hard + unresolved,
                "reconciliation_difference": stats.get("difference") or "0.00",
                "open_exception_count": int(open_bucket.get("count") or 0),
                "open_exception_amount": _as_str(open_bucket.get("amount") or Decimal("0.00")),
                "top_open_type": (
                    open_bucket["types"].most_common(1)[0][0] if open_bucket.get("types") else None
                ),
                "missing_link_label": missing_label,
                "missing_link_count": int(missing["count"]) if missing else None,
                "orphan_record_count": int(orphans["count"]),
            }
        )

    ranked = sorted(
        pairs,
        key=lambda item: (
            -item["open_exception_count"],
            -_money(item["open_exception_amount"]),
            -item["unmatched_count"],
        ),
    )
    worst = ranked[0] if ranked and ranked[0]["open_exception_count"] else None
    if worst is None:
        worst = next((item for item in ranked if item["unmatched_count"]), None)

    return {
        "run_id": breakdown["run_id"],
        "pairs": pairs,
        "worst_pair": worst["pair_type"] if worst else None,
        "worst_pair_label": worst["label"] if worst else None,
        "total_open_exceptions": sum(item["open_exception_count"] for item in pairs),
        "total_open_amount": _as_str(
            sum(
                (_money(item["open_exception_amount"]) for item in pairs),
                Decimal("0.00"),
            )
        ),
        "amount_basis": AMOUNT_BASIS,
        "scope": "reconciliation counts from the latest run; linkage over all ingested records",
        "note": (
            None
            if breakdown["run_id"]
            else "No reconciliation run has been recorded yet, so only stored-record linkage applies."
        ),
    }


TOOL_REGISTRY: Dict[str, Callable[..., Dict[str, Any]]] = {
    "get_reconciliation_summary": get_reconciliation_summary,
    "search_exceptions": search_exceptions,
    "get_exception": get_exception,
    "get_audit_events": get_audit_events,
    "search_records": search_records,
    "get_record_relationships": get_record_relationships,
    "get_financial_summary": get_financial_summary,
    "get_unsettled_payments": get_unsettled_payments,
    "get_settlement_summary": get_settlement_summary,
    "get_cross_source_summary": get_cross_source_summary,
}


def run_tool(name: str, params: Dict[str, Any] | None = None, *, database_url: str | None = None) -> ToolResult:
    """Execute one whitelisted read-only tool. Failures are contained, never raised as 500s."""
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        return ToolResult(tool=name, ok=False, error="unknown tool")
    try:
        data = tool(database_url=database_url, **(params or {}))
        return ToolResult(tool=name, ok=True, data=data)
    except CopilotError as exc:
        return ToolResult(tool=name, ok=False, error=exc.message)
    except Exception as exc:
        return ToolResult(tool=name, ok=False, error=f"tool failed: {exc.__class__.__name__}")
