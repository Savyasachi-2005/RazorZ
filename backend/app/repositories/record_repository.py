from __future__ import annotations

"""Persistence + read-only queries for normalized source records.

Reconciliation decisions never depend on this layer. It exists so deterministic
financial totals and record relationships can be answered from stored data
instead of being recomputed or guessed.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

from sqlmodel import Session, func, select

from app.db import create_db_and_tables, get_session
from app.models import Fee, Order, Payment, Refund, Settlement, Source

RECORD_TYPES = ("order", "payment", "settlement", "refund", "fee")

_MODEL_BY_TYPE = {
    "order": Order,
    "payment": Payment,
    "settlement": Settlement,
    "refund": Refund,
    "fee": Fee,
}

_DATE_FIELD_BY_TYPE = {
    "order": "order_date",
    "payment": "payment_date",
    "settlement": "settlement_date",
    "refund": "refund_date",
    "fee": "fee_date",
}


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        return Decimal("0.00")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0.00")


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    for parser in (datetime.fromisoformat,):
        try:
            parsed = parser(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    return datetime.now(timezone.utc)


def _scalar_int(value: Any) -> int:
    """Single-column aggregates come back as a scalar; multi-column ones as a row."""
    if value is None:
        return 0
    if isinstance(value, (list, tuple)):
        return int(value[0] or 0)
    return int(value)


def _meta(record: Dict[str, Any]) -> Dict[str, Any]:
    metadata = record.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


class RecordRepository:
    """Stores and reads normalized source records. No reconciliation logic."""

    def __init__(self, database_url: str | None = None):
        self.database_url = database_url
        create_db_and_tables(database_url)

    def _session(self) -> Session:
        return get_session(self.database_url)

    # ---------- write (ingestion only; never called by the Copilot) ----------

    def _source_id(self, session: Session, name: str) -> int:
        source_name = (name or "synthetic").strip() or "synthetic"
        existing = session.exec(select(Source).where(Source.name == source_name)).first()
        if existing is not None and existing.id is not None:
            return existing.id
        source = Source(name=source_name, source_type=source_name)
        session.add(source)
        session.commit()
        session.refresh(source)
        assert source.id is not None
        return source.id

    def persist_source_records(self, records: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        """Upsert normalized records by external_id. Re-runs update, never duplicate."""
        counts = {record_type: 0 for record_type in RECORD_TYPES}
        source_cache: Dict[str, int] = {}

        with self._session() as session:
            for record in records:
                record_type = str(record.get("record_type") or "").strip().lower()
                model = _MODEL_BY_TYPE.get(record_type)
                external_id = str(record.get("record_id") or "").strip()
                if model is None or not external_id:
                    continue

                source_name = str(record.get("source") or "synthetic").strip() or "synthetic"
                if source_name not in source_cache:
                    source_cache[source_name] = self._source_id(session, source_name)
                source_id = source_cache[source_name]

                values = self._row_values(record, record_type, source_id)
                existing = session.exec(select(model).where(model.external_id == external_id)).first()
                if existing is None:
                    session.add(model(**values))
                else:
                    for key, value in values.items():
                        setattr(existing, key, value)
                    existing.updated_at = datetime.now(timezone.utc)
                    session.add(existing)
                counts[record_type] += 1
            session.commit()
        return counts

    def _row_values(self, record: Dict[str, Any], record_type: str, source_id: int) -> Dict[str, Any]:
        meta = _meta(record)
        reference = str(record.get("reference") or "").strip()
        payment_reference = str(record.get("payment_reference") or "").strip()
        common: Dict[str, Any] = {
            "source_id": source_id,
            "external_id": str(record.get("record_id") or "").strip(),
            "reference": reference,
            "amount": _to_decimal(record.get("amount")),
            "currency": str(meta.get("currency") or "INR"),
            _DATE_FIELD_BY_TYPE[record_type]: _to_datetime(record.get("date")),
        }

        if record_type == "order":
            common["customer_ref"] = str(record.get("customer") or "")
            common["status"] = str(meta.get("status") or "pending")
        elif record_type == "payment":
            common["customer_ref"] = str(record.get("customer") or "")
            common["order_reference"] = reference or None
            common["payment_method"] = str(meta.get("method") or "UPI")
            common["status"] = str(meta.get("status") or "captured")
        elif record_type == "settlement":
            common["payment_reference"] = payment_reference or None
            common["status"] = str(meta.get("status") or "pending")
        elif record_type == "refund":
            common["payment_reference"] = payment_reference or None
            common["status"] = str(meta.get("status") or "processed")
        elif record_type == "fee":
            common["payment_reference"] = payment_reference or None
            common["fee_type"] = str(meta.get("fee_type") or "processing")

        return common

    # ---------- read-only ----------

    def financial_totals(self) -> Dict[str, Any]:
        """Deterministic Decimal totals over persisted source records."""
        totals: Dict[str, Any] = {}
        with self._session() as session:
            for record_type in RECORD_TYPES:
                model = _MODEL_BY_TYPE[record_type]
                row = session.exec(select(func.count(model.id), func.sum(model.amount))).first()
                count = int(row[0] or 0) if row else 0
                total = _to_decimal(row[1]) if row and row[1] is not None else Decimal("0.00")
                totals[record_type] = {
                    "count": count,
                    "total": str(total.quantize(Decimal("0.01"))),
                }
        return totals

    def _linked_payment_refs(self, session: Session, model: Any):
        """Sub-select of payment ids referenced by a child record type."""
        return select(model.payment_reference).where(model.payment_reference.is_not(None))

    def _count_and_sum(self, session: Session, model: Any, *conditions: Any) -> Dict[str, Any]:
        statement = select(func.count(model.id), func.sum(model.amount))
        for condition in conditions:
            statement = statement.where(condition)
        row = session.exec(statement).first()
        count = int(row[0] or 0) if row else 0
        total = _to_decimal(row[1]) if row and row[1] is not None else Decimal("0.00")
        return {"count": count, "total": str(total.quantize(Decimal("0.01")))}

    def unsettled_payments(self, *, limit: int = 10) -> Dict[str, Any]:
        """Stored payments with no settlement row pointing at them.

        Aggregates are counted in SQL over every payment; only the sample rows are
        capped, so totals never depend on the row limit.
        """
        limit = max(1, min(int(limit), 25))
        with self._session() as session:
            settled_refs = self._linked_payment_refs(session, Settlement)
            unsettled = Payment.external_id.notin_(settled_refs)

            totals = self._count_and_sum(session, Payment)
            missing = self._count_and_sum(session, Payment, unsettled)
            settled = self._count_and_sum(session, Payment, ~unsettled)

            rows = session.exec(
                select(Payment)
                .where(unsettled)
                .order_by(Payment.amount.desc(), Payment.external_id)
                .limit(limit)
            ).all()

        return {
            "payments": totals,
            "settled": settled,
            "unsettled": missing,
            "sample": [self._serialize(row, "payment") for row in rows],
        }

    def settlement_linkage(self) -> Dict[str, Any]:
        """Settlement-side coverage: orphans, and settlements that disagree on amount."""
        with self._session() as session:
            known_payments = select(Payment.external_id)
            orphan = (Settlement.payment_reference.is_(None)) | (
                Settlement.payment_reference.notin_(known_payments)
            )

            totals = self._count_and_sum(session, Settlement)
            orphans = self._count_and_sum(session, Settlement, orphan)
            linked = self._count_and_sum(session, Settlement, ~orphan)

            # Amount disagreements between a settlement and the payment it points at.
            pairs = session.exec(
                select(Settlement, Payment)
                .where(Settlement.payment_reference == Payment.external_id)
                .where(Settlement.amount != Payment.amount)
                .order_by(Settlement.external_id)
                .limit(10)
            ).all()
            mismatch_count = _scalar_int(
                session.exec(
                    select(func.count(Settlement.id))
                    .select_from(Settlement)
                    .join(Payment, Settlement.payment_reference == Payment.external_id)
                    .where(Settlement.amount != Payment.amount)
                ).first()
            )

            orphan_sample = session.exec(
                select(Settlement)
                .where(orphan)
                .order_by(Settlement.amount.desc(), Settlement.external_id)
                .limit(5)
            ).all()

        mismatches = []
        for settlement, payment in pairs[:5]:
            settled_amount = _to_decimal(settlement.amount)
            paid_amount = _to_decimal(payment.amount)
            mismatches.append(
                {
                    "settlement_id": settlement.external_id,
                    "payment_id": payment.external_id,
                    "settlement_amount": str(settled_amount.quantize(Decimal("0.01"))),
                    "payment_amount": str(paid_amount.quantize(Decimal("0.01"))),
                    "difference": str((paid_amount - settled_amount).quantize(Decimal("0.01"))),
                }
            )

        return {
            "settlements": totals,
            "linked_to_payment": linked,
            "orphan_settlements": orphans,
            "amount_mismatch_count": mismatch_count,
            "amount_mismatches": mismatches,
            "orphan_sample": [self._serialize(row, "settlement") for row in orphan_sample],
        }

    def link_coverage(self) -> Dict[str, Any]:
        """Per-relationship linkage between payments and their child records."""
        coverage: Dict[str, Any] = {}
        with self._session() as session:
            payments = self._count_and_sum(session, Payment)
            for label, model in (
                ("settlement", Settlement),
                ("refund", Refund),
                ("fee", Fee),
            ):
                child_refs = self._linked_payment_refs(session, model)
                linked = Payment.external_id.in_(child_refs)
                known_payments = select(Payment.external_id)
                orphan_child = (model.payment_reference.is_(None)) | (
                    model.payment_reference.notin_(known_payments)
                )
                coverage[label] = {
                    "payments_with_link": self._count_and_sum(session, Payment, linked),
                    "payments_without_link": self._count_and_sum(session, Payment, ~linked),
                    "records": self._count_and_sum(session, model),
                    "orphan_records": self._count_and_sum(session, model, orphan_child),
                }

            payment_order_refs = select(Payment.order_reference).where(
                Payment.order_reference.is_not(None)
            )
            has_payment = Order.reference.in_(payment_order_refs)
            known_orders = select(Order.reference)
            orphan_payment = (Payment.order_reference.is_(None)) | (
                Payment.order_reference.notin_(known_orders)
            )
            coverage["order"] = {
                "orders": self._count_and_sum(session, Order),
                "orders_with_payment": self._count_and_sum(session, Order, has_payment),
                "orders_without_payment": self._count_and_sum(session, Order, ~has_payment),
                "orphan_payments": self._count_and_sum(session, Payment, orphan_payment),
            }

        coverage["payments"] = payments
        return coverage

    def search_records(
        self,
        *,
        record_type: str | None = None,
        query: str | None = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit), 25))
        needle = (query or "").strip()
        types = [record_type] if record_type in RECORD_TYPES else list(RECORD_TYPES)
        found: List[Dict[str, Any]] = []

        with self._session() as session:
            for current in types:
                model = _MODEL_BY_TYPE[current]
                statement = select(model)
                if needle:
                    like = f"%{needle}%"
                    statement = statement.where(
                        (model.external_id.ilike(like)) | (model.reference.ilike(like))
                    )
                statement = statement.limit(limit)
                for row in session.exec(statement).all():
                    found.append(self._serialize(row, current))
                    if len(found) >= limit:
                        return found
        return found

    def get_record(self, external_id: str) -> Optional[Dict[str, Any]]:
        target = (external_id or "").strip()
        if not target:
            return None
        with self._session() as session:
            for record_type in RECORD_TYPES:
                model = _MODEL_BY_TYPE[record_type]
                row = session.exec(select(model).where(model.external_id == target)).first()
                if row is not None:
                    return self._serialize(row, record_type)
        return None

    def relationships(self, external_id: str) -> Dict[str, Any]:
        """Resolve Order→Payment→(Settlement|Refund|Fee) links from stored references."""
        record = self.get_record(external_id)
        if record is None:
            return {"found": False, "record_id": external_id}

        result: Dict[str, Any] = {"found": True, "record": record, "related": {}}
        with self._session() as session:
            if record["record_type"] == "order":
                payments = session.exec(
                    select(Payment).where(Payment.order_reference == record["reference"])
                ).all()
                result["related"]["payments"] = [self._serialize(row, "payment") for row in payments]
                return result

            if record["record_type"] == "payment":
                order = session.exec(
                    select(Order).where(Order.reference == record["reference"])
                ).first()
                result["related"]["order"] = self._serialize(order, "order") if order else None
                payment_id = record["record_id"]
                for label, model, key in (
                    ("settlements", Settlement, "settlement"),
                    ("refunds", Refund, "refund"),
                    ("fees", Fee, "fee"),
                ):
                    rows = session.exec(
                        select(model).where(model.payment_reference == payment_id)
                    ).all()
                    result["related"][label] = [self._serialize(row, key) for row in rows]
                return result

            # settlement / refund / fee → parent payment
            parent_ref = record.get("payment_reference")
            payment = None
            if parent_ref:
                payment = session.exec(
                    select(Payment).where(Payment.external_id == parent_ref)
                ).first()
            result["related"]["payment"] = self._serialize(payment, "payment") if payment else None
            return result

    def _serialize(self, row: Any, record_type: str) -> Dict[str, Any]:
        date_field = _DATE_FIELD_BY_TYPE[record_type]
        date_value = getattr(row, date_field, None)
        payload: Dict[str, Any] = {
            "record_type": record_type,
            "record_id": row.external_id,
            "reference": row.reference,
            "amount": str(_to_decimal(row.amount).quantize(Decimal("0.01"))),
            "currency": getattr(row, "currency", "INR"),
            "date": date_value.date().isoformat() if isinstance(date_value, datetime) else None,
        }
        status = getattr(row, "status", None)
        if status is not None:
            payload["status"] = status
        for optional in ("order_reference", "payment_reference", "customer_ref", "fee_type", "payment_method"):
            value = getattr(row, optional, None)
            if value:
                payload[optional] = value
        return payload
