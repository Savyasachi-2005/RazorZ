from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from app.data_generator import generate_dataset
from app.db import create_db_and_tables
from app.exceptions.intelligence import enrich_exception
from app.reconciliation.engine import reconcile_records
from app.repositories.reconciliation_repository import ReconciliationRepository
from app.repositories.record_repository import RecordRepository


def _serialize_generated(records: int, seed: int) -> List[Dict[str, Any]]:
    dataset = generate_dataset(records=records, seed=seed)
    payload: List[Dict[str, Any]] = []
    for item in dataset:
        row = asdict(item)
        row["amount"] = str(row["amount"])
        payload.append(row)
    return payload


def _infer_type_from_id(record_id: str | None) -> str | None:
    if not record_id:
        return None
    lower = record_id.lower()
    if lower.startswith(("or-", "ord-", "order")):
        return "order"
    if lower.startswith(("pm-", "pmt-", "pay", "payment")):
        return "payment"
    if lower.startswith(("st-", "set-", "settlement")):
        return "settlement"
    if lower.startswith(("rf-", "refund")):
        return "refund"
    if lower.startswith(("fe-", "fee")):
        return "fee"
    return None


def _record_type_ids(row: Any) -> Dict[str, str | None]:
    """Map a recon row into typed id slots for the API/frontend."""
    pair = getattr(row, "pair_type", None) or "order_payment"
    source = row.source_record_id
    related = row.matched_with
    source_type = getattr(row, "source_record_type", None) or _infer_type_from_id(source)
    related_type = getattr(row, "related_record_type", None) or _infer_type_from_id(related)

    ids: Dict[str, str | None] = {
        "order_id": None,
        "payment_id": None,
        "settlement_id": None,
        "refund_id": None,
        "fee_id": None,
    }

    def _assign(record_type: str | None, record_id: str | None) -> None:
        if not record_id or not record_type:
            return
        key = f"{record_type}_id"
        if key in ids and ids[key] is None:
            ids[key] = record_id

    _assign(source_type, source)
    _assign(related_type, related)

    # Fallbacks when type metadata is missing on older rows.
    if pair == "order_payment":
        if ids["order_id"] is None and source_type != "payment":
            ids["order_id"] = source
        if ids["payment_id"] is None and related:
            ids["payment_id"] = related
        if ids["order_id"] is None and related_type == "order":
            ids["order_id"] = related
        if ids["payment_id"] is None and source_type == "payment":
            ids["payment_id"] = source
    elif pair == "payment_settlement":
        if ids["payment_id"] is None and (source_type or "payment") == "payment":
            ids["payment_id"] = source
        if ids["settlement_id"] is None and related:
            ids["settlement_id"] = related
        if ids["settlement_id"] is None and source_type == "settlement":
            ids["settlement_id"] = source
    elif pair == "payment_refund":
        if ids["payment_id"] is None and (source_type or "payment") == "payment":
            ids["payment_id"] = source
        if ids["refund_id"] is None and related:
            ids["refund_id"] = related
        if ids["refund_id"] is None and source_type == "refund":
            ids["refund_id"] = source
    elif pair == "payment_fee":
        if ids["payment_id"] is None and (source_type or "payment") == "payment":
            ids["payment_id"] = source
        if ids["fee_id"] is None and related:
            ids["fee_id"] = related
        if ids["fee_id"] is None and source_type == "fee":
            ids["fee_id"] = source

    return ids


def run_reconciliation(
    records: List[Dict[str, Any]],
    database_url: str | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    database_url = database_url or kwargs.get("database_url")
    create_db_and_tables(database_url)
    results = reconcile_records(records)
    # Store the ingested records so financial totals and relationships stay deterministic.
    # Persistence is additive and must never block a reconciliation run.
    try:
        RecordRepository(database_url).persist_source_records(records)
    except Exception:
        pass
    repo = ReconciliationRepository(database_url)
    run_id = repo.persist_results(results)
    summary = {
        "run_id": run_id,
        "total": len(results),
        "matched": sum(1 for item in results if item["status"] in {"MATCHED", "AUTO_RESOLVED"}),
        "exceptions": sum(1 for item in results if item["status"] == "EXCEPTION"),
        "review_required": sum(1 for item in results if item["status"] == "REVIEW_REQUIRED"),
        "unresolved": sum(1 for item in results if item["status"] == "UNRESOLVED"),
    }
    match_rate = (summary["matched"] / summary["total"]) if summary["total"] else 0.0
    summary["match_rate"] = round(match_rate, 4)
    return {"summary": summary, "results": results, "persisted": True, "run_id": run_id}


def generate_and_reconcile(
    records: int = 50,
    seed: int = 42,
    database_url: str | None = None,
) -> Dict[str, Any]:
    payload = _serialize_generated(records, seed)
    return run_reconciliation(payload, database_url=database_url)


def get_summary(database_url: str | None = None) -> Dict[str, Any]:
    repo = ReconciliationRepository(database_url)
    return repo.summary()


def list_records(
    limit: int = 50,
    offset: int = 0,
    database_url: str | None = None,
    *,
    pair_type: str | None = None,
) -> List[Dict[str, Any]]:
    repo = ReconciliationRepository(database_url)
    rows = repo.list_recent(limit=limit, offset=offset, pair_type=pair_type)
    payload: List[Dict[str, Any]] = []
    for row in rows:
        typed = _record_type_ids(row)
        payload.append(
            {
                "id": row.id,
                "run_id": row.run_id,
                "record_id": row.source_record_id,
                "matched_with": row.matched_with,
                "pair_type": getattr(row, "pair_type", None) or "order_payment",
                "source_record_type": getattr(row, "source_record_type", None),
                "related_record_type": getattr(row, "related_record_type", None),
                "order_id": typed["order_id"],
                "payment_id": typed["payment_id"],
                "settlement_id": typed["settlement_id"],
                "refund_id": typed["refund_id"],
                "fee_id": typed["fee_id"],
                "status": row.status,
                "confidence": str(row.confidence),
                "amount_diff": str(row.amount_diff),
                "exception_type": row.exception_type,
            }
        )
    return payload


def list_exceptions(limit: int = 50, offset: int = 0, database_url: str | None = None) -> List[Dict[str, Any]]:
    repo = ReconciliationRepository(database_url)
    return [enrich_exception(row) for row in repo.list_exceptions(limit=limit, offset=offset)]


def get_exception(exception_id: int, database_url: str | None = None) -> Dict[str, Any] | None:
    repo = ReconciliationRepository(database_url)
    row = repo.get_exception(exception_id)
    if row is None:
        return None
    return enrich_exception(row)


def review_exception(
    exception_id: int,
    action: str,
    actor: str,
    note: str,
    database_url: str | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    database_url = database_url or kwargs.get("database_url")
    repo = ReconciliationRepository(database_url)
    row = repo.review_exception(exception_id, action=action, actor=actor, note=note)
    if row is None:
        raise KeyError(exception_id)
    return enrich_exception(row)


def list_audit(limit: int = 50, offset: int = 0, database_url: str | None = None) -> List[Dict[str, Any]]:
    repo = ReconciliationRepository(database_url)
    rows = repo.list_audit(limit=limit, offset=offset)
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "entity_id": row.entity_id,
            "actor": row.actor,
            "action": row.action,
            "new_state": row.new_state,
            "confidence": str(row.confidence) if row.confidence is not None else None,
        }
        for row in rows
    ]
