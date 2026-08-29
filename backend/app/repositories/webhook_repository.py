from __future__ import annotations

"""Webhook delivery log. Exists only to make processing idempotent.

Reconciliation decisions never read this table.
"""

from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlmodel import Session, select

from app.db import create_db_and_tables, get_session
from app.models import WebhookEvent


class WebhookRepository:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url
        create_db_and_tables(database_url)

    def _session(self) -> Session:
        return get_session(self.database_url)

    def get(self, event_id: str) -> Optional[WebhookEvent]:
        with self._session() as session:
            return session.exec(select(WebhookEvent).where(WebhookEvent.event_id == event_id)).first()

    def claim(
        self,
        *,
        event_id: str,
        event_type: str,
        entity_id: str | None,
        payload_digest: str,
        provider: str = "razorpay",
    ) -> Tuple[WebhookEvent, bool]:
        """Reserve an event id. Returns (row, created).

        `created=False` means this delivery is a duplicate and must not be
        ingested again.
        """
        with self._session() as session:
            existing = session.exec(
                select(WebhookEvent).where(WebhookEvent.event_id == event_id)
            ).first()
            if existing is not None:
                session.expunge(existing)
                return existing, False

            row = WebhookEvent(
                provider=provider,
                event_id=event_id,
                event_type=event_type,
                entity_id=entity_id,
                payload_digest=payload_digest,
                status="RECEIVED",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row, True

    def complete(
        self,
        event_id: str,
        *,
        status: str,
        records_ingested: int = 0,
        run_id: str | None = None,
        error_code: str | None = None,
    ) -> Optional[WebhookEvent]:
        with self._session() as session:
            row = session.exec(select(WebhookEvent).where(WebhookEvent.event_id == event_id)).first()
            if row is None:
                return None
            row.status = status
            row.records_ingested = records_ingested
            row.run_id = run_id
            row.error_code = error_code
            row.processed_at = datetime.now(timezone.utc)
            session.add(row)
            session.commit()
            session.refresh(row)
            session.expunge(row)
            return row
