from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlmodel import Session, func, select

from datetime import datetime, timezone

from app.db import create_db_and_tables, get_session
from app.exceptions.intelligence import apply_classification
from app.models import AuditEvent, ExceptionRecord, MatchCandidate, ReconciliationRecord


def _as_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value))


class ReconciliationRepository:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url
        create_db_and_tables(database_url)

    def _session(self) -> Session:
        return get_session(self.database_url)

    def persist_results(self, results: List[Dict[str, Any]], run_id: Optional[str] = None) -> str:
        batch_id = run_id or str(uuid4())
        with self._session() as session:
            for item in results:
                confidence = _as_decimal(item.get("confidence", "0"))
                amount_diff = _as_decimal(item.get("amount_diff", "0"))
                record = ReconciliationRecord(
                    run_id=batch_id,
                    source_type=str(item.get("source_type", "synthetic")),
                    source_record_id=str(item.get("record_id", "unknown")),
                    matching_key=str(item.get("matched_with") or item.get("record_id") or "unmatched"),
                    matched_with=item.get("matched_with"),
                    pair_type=str(item.get("pair_type") or "order_payment"),
                    source_record_type=item.get("source_record_type"),
                    related_record_type=item.get("related_record_type"),
                    status=str(item.get("status", "UNRESOLVED")),
                    confidence=confidence,
                    amount_diff=amount_diff,
                    exception_type=item.get("exception_type"),
                    evidence=json.dumps(
                        {
                            "pair_type": item.get("pair_type") or "order_payment",
                            "source_record_type": item.get("source_record_type"),
                            "related_record_type": item.get("related_record_type"),
                            "candidates": item.get("candidates") or [],
                        }
                    ),
                )
                session.add(record)

                for rank, candidate in enumerate(item.get("candidates") or [], start=1):
                    session.add(
                        MatchCandidate(
                            run_id=batch_id,
                            source_record_id=str(item.get("record_id", "unknown")),
                            candidate_record_id=str(candidate.get("record_id", "")),
                            score=_as_decimal(candidate.get("score", "0")),
                            rank=rank,
                            features=json.dumps(candidate.get("features") or {}),
                        )
                    )

                if item.get("status") in {"EXCEPTION", "REVIEW_REQUIRED", "UNRESOLVED"}:
                    pair_type = item.get("pair_type") or "order_payment"
                    exception = ExceptionRecord(
                        exception_type=str(item.get("exception_type") or "UNKNOWN_EXCEPTION"),
                        severity="high" if amount_diff >= Decimal("500") else "medium",
                        status="OPEN",
                        confidence=confidence,
                        amount=amount_diff,
                        description=f"{item.get('status')} for {item.get('record_id')}",
                        evidence=json.dumps(
                            {
                                "pair_type": pair_type,
                                "matched_with": item.get("matched_with"),
                                "source_record_type": item.get("source_record_type"),
                                "related_record_type": item.get("related_record_type"),
                                "candidates": item.get("candidates") or [],
                            }
                        ),
                    )
                    apply_classification(exception)
                    session.add(exception)

                session.add(
                    AuditEvent(
                        event_type="reconciliation_decision",
                        entity_type="reconciliation_record",
                        entity_id=str(item.get("record_id", "unknown")),
                        actor="system",
                        confidence=confidence,
                        new_state=str(item.get("status", "UNRESOLVED")),
                        action="persist_result",
                        evidence=json.dumps(
                            {
                                "matched_with": item.get("matched_with"),
                                "exception_type": item.get("exception_type"),
                                "pair_type": item.get("pair_type"),
                            }
                        ),
                    )
                )
            session.commit()
        return batch_id

    def list_recent(
        self,
        limit: int = 100,
        offset: int = 0,
        *,
        pair_type: str | None = None,
        latest_run_only: bool = True,
    ) -> List[ReconciliationRecord]:
        with self._session() as session:
            statement = select(ReconciliationRecord)
            if latest_run_only:
                latest_run = session.exec(
                    select(ReconciliationRecord.run_id).order_by(ReconciliationRecord.created_at.desc()).limit(1)
                ).first()
                if latest_run:
                    statement = statement.where(ReconciliationRecord.run_id == latest_run)
            if pair_type:
                statement = statement.where(ReconciliationRecord.pair_type == pair_type)
            # Stable order within a run: orders first, then settlements/refunds/fees.
            statement = (
                statement.order_by(ReconciliationRecord.id.asc()).offset(offset).limit(limit)
            )
            return list(session.exec(statement).all())

    def summary(self) -> Dict[str, Any]:
        """Aggregate KPIs in SQL — avoid loading thousands of rows over the wire."""
        with self._session() as session:
            latest_run = session.exec(
                select(ReconciliationRecord.run_id).order_by(ReconciliationRecord.created_at.desc()).limit(1)
            ).first()
            if not latest_run:
                return {
                    "total_records": 0,
                    "matched": 0,
                    "exceptions": 0,
                    "review_required": 0,
                    "unresolved": 0,
                    "match_rate": 0.0,
                }

            rows = session.exec(
                select(ReconciliationRecord.status, func.count())
                .where(ReconciliationRecord.run_id == latest_run)
                .group_by(ReconciliationRecord.status)
            ).all()

            counts = {str(status): int(count) for status, count in rows}
            matched = counts.get("MATCHED", 0) + counts.get("AUTO_RESOLVED", 0)
            exceptions = counts.get("EXCEPTION", 0)
            review_required = counts.get("REVIEW_REQUIRED", 0)
            unresolved = counts.get("UNRESOLVED", 0)
            total = sum(counts.values())
            match_rate = float(matched / total) if total else 0.0
            return {
                "total_records": total,
                "matched": matched,
                "exceptions": exceptions,
                "review_required": review_required,
                "unresolved": unresolved,
                "match_rate": round(match_rate, 4),
            }

    def pair_type_breakdown(self, pair_type: Optional[str] = None) -> Dict[str, Any]:
        """Latest-run status counts and difference totals grouped by relationship type.

        Aggregated in SQL. Read-only; reconciliation decisions are not recomputed here.
        """
        with self._session() as session:
            latest_run = session.exec(
                select(ReconciliationRecord.run_id)
                .order_by(ReconciliationRecord.created_at.desc())
                .limit(1)
            ).first()
            if not latest_run:
                return {"run_id": None, "pairs": {}}

            statement = (
                select(
                    ReconciliationRecord.pair_type,
                    ReconciliationRecord.status,
                    func.count(),
                    func.sum(ReconciliationRecord.amount_diff),
                )
                .where(ReconciliationRecord.run_id == latest_run)
                .group_by(ReconciliationRecord.pair_type, ReconciliationRecord.status)
            )
            if pair_type:
                statement = statement.where(ReconciliationRecord.pair_type == pair_type)
            rows = session.exec(statement).all()

        pairs: Dict[str, Any] = {}
        for pair, status, count, difference in rows:
            bucket = pairs.setdefault(
                str(pair or "unknown"),
                {"total": 0, "statuses": {}, "difference": Decimal("0.00")},
            )
            bucket["total"] += int(count or 0)
            bucket["statuses"][str(status)] = int(count or 0)
            bucket["difference"] += _as_decimal(difference or 0)

        for bucket in pairs.values():
            bucket["difference"] = str(bucket["difference"].quantize(Decimal("0.01")))
        return {"run_id": latest_run, "pairs": pairs}

    def list_exceptions(self, limit: int = 100, offset: int = 0) -> List[ExceptionRecord]:
        with self._session() as session:
            statement = (
                select(ExceptionRecord)
                .order_by(ExceptionRecord.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(statement).all())

    def list_audit(self, limit: int = 100, offset: int = 0) -> List[AuditEvent]:
        with self._session() as session:
            statement = (
                select(AuditEvent)
                .order_by(AuditEvent.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            return list(session.exec(statement).all())

    def get_exception(self, exception_id: int) -> Optional[ExceptionRecord]:
        with self._session() as session:
            return session.get(ExceptionRecord, exception_id)

    def review_exception(
        self,
        exception_id: int,
        *,
        action: str,
        actor: str,
        note: str,
    ) -> Optional[ExceptionRecord]:
        if action not in {"resolve", "reject"}:
            raise ValueError("action must be resolve or reject")
        target = "RESOLVED" if action == "resolve" else "REJECTED"
        with self._session() as session:
            record = session.get(ExceptionRecord, exception_id)
            if record is None:
                return None
            previous = record.status
            if previous == target:
                return record
            if previous in {"RESOLVED", "REJECTED"} and previous != target:
                raise ValueError(f"exception already {previous.lower()}")
            record.status = target
            record.reviewer_note = note
            record.resolved_by = actor
            record.updated_at = datetime.now(timezone.utc)
            session.add(
                AuditEvent(
                    event_type="human_review",
                    entity_type="exception",
                    entity_id=str(exception_id),
                    actor=actor,
                    previous_state=previous,
                    new_state=target,
                    action=action,
                    evidence=json.dumps({"note": note}),
                    details="Human review does not change source financial amounts.",
                )
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def record_ai_assistance(
        self,
        *,
        exception_id: int,
        provider: str,
        mode: str,
        success: bool,
        error_code: str | None = None,
        ai_confidence: float | None = None,
    ) -> None:
        """Append-only audit for AI assist. Does not store prompts or secrets."""
        with self._session() as session:
            session.add(
                AuditEvent(
                    event_type="ai_assistance",
                    entity_type="exception",
                    entity_id=str(exception_id),
                    actor="system/ai",
                    action="ai_assistance",
                    new_state="success" if success else "failure",
                    confidence=Decimal(str(ai_confidence)) if ai_confidence is not None else None,
                    evidence=json.dumps(
                        {
                            "provider": provider,
                            "mode": mode,
                            "success": success,
                            "error_code": error_code,
                        }
                    ),
                    details="AI assistance is advisory and does not change financial truth.",
                )
            )
            session.commit()

    def record_auth_event(
        self,
        *,
        action: str,
        email: str,
        success: bool,
        role: str | None = None,
    ) -> None:
        """Append-only audit for sign-in activity.

        Stores no password, token, token hash, or session id — only the account
        the attempt was made against and whether it succeeded.
        """
        with self._session() as session:
            session.add(
                AuditEvent(
                    event_type="auth_event",
                    entity_type="user",
                    entity_id=email,
                    actor=email or "unknown",
                    action=action,
                    new_state="success" if success else "failure",
                    evidence=json.dumps({"action": action, "role": role, "success": success}),
                    details="Authentication does not change financial truth.",
                )
            )
            session.commit()

    def record_webhook_event(
        self,
        *,
        event_id: str,
        event_type: str,
        action: str,
        outcome: str,
        provider: str = "razorpay",
        entity_id: str | None = None,
        signature_valid: bool | None = None,
        records_ingested: int = 0,
        run_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Append-only audit for webhook receipt/processing.

        Stores no signature value, secret, header set, or request body — only the
        event id, event type and outcome.
        """
        with self._session() as session:
            session.add(
                AuditEvent(
                    event_type="webhook_event",
                    entity_type="webhook",
                    entity_id=event_id,
                    actor=f"system/{provider}_webhook",
                    action=action,
                    new_state=outcome,
                    evidence=json.dumps(
                        {
                            "provider": provider,
                            "event": event_type,
                            "entity_id": entity_id,
                            "signature_valid": signature_valid,
                            "records_ingested": records_ingested,
                            "run_id": run_id,
                            "error_code": error_code,
                        }
                    ),
                    details="Webhook ingestion does not change deterministic reconciliation rules.",
                )
            )
            session.commit()

    def record_copilot_query(
        self,
        *,
        intent: str,
        tools_used: List[str],
        provider: str,
        success: bool,
        llm_used: bool,
        error_code: str | None = None,
    ) -> None:
        """Append-only audit for Copilot usage. Stores no prompt text, keys, or headers."""
        with self._session() as session:
            session.add(
                AuditEvent(
                    event_type="copilot_query",
                    entity_type="copilot",
                    entity_id=intent,
                    actor="system/ai",
                    action="copilot_query",
                    new_state="success" if success else "failure",
                    evidence=json.dumps(
                        {
                            "intent": intent,
                            "tools_used": tools_used,
                            "provider": provider,
                            "llm_used": llm_used,
                            "success": success,
                            "error_code": error_code,
                        }
                    ),
                    details="Finance Copilot is read-only and cannot change financial truth.",
                )
            )
            session.commit()
