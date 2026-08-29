from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class Source(SQLModel, table=True):
    __tablename__ = "sources"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    source_type: str = Field(index=True)  # synthetic, razorpay, bank
    status: str = Field(default="active")
    source_metadata: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="sources.id", index=True)
    external_id: str = Field(index=True, unique=True)
    reference: str = Field(index=True)
    customer_ref: str = Field(index=True)
    amount: Decimal = Field(default=Decimal("0.00"), nullable=False)
    currency: str = Field(default="INR")
    order_date: datetime = Field(index=True)
    status: str = Field(default="pending")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Payment(SQLModel, table=True):
    __tablename__ = "payments"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="sources.id", index=True)
    external_id: str = Field(index=True, unique=True)
    reference: str = Field(index=True)
    order_reference: Optional[str] = Field(default=None, index=True)
    customer_ref: str = Field(index=True)
    amount: Decimal = Field(default=Decimal("0.00"), nullable=False)
    currency: str = Field(default="INR")
    payment_date: datetime = Field(index=True)
    payment_method: str = Field(default="UPI")
    status: str = Field(default="captured")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Settlement(SQLModel, table=True):
    __tablename__ = "settlements"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="sources.id", index=True)
    external_id: str = Field(index=True, unique=True)
    reference: str = Field(index=True)
    payment_reference: Optional[str] = Field(default=None, index=True)
    settlement_date: datetime = Field(index=True)
    amount: Decimal = Field(default=Decimal("0.00"), nullable=False)
    currency: str = Field(default="INR")
    status: str = Field(default="pending")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Refund(SQLModel, table=True):
    __tablename__ = "refunds"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="sources.id", index=True)
    external_id: str = Field(index=True, unique=True)
    reference: str = Field(index=True)
    payment_reference: Optional[str] = Field(default=None, index=True)
    amount: Decimal = Field(default=Decimal("0.00"), nullable=False)
    currency: str = Field(default="INR")
    refund_date: datetime = Field(index=True)
    status: str = Field(default="processed")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Fee(SQLModel, table=True):
    __tablename__ = "fees"

    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="sources.id", index=True)
    external_id: str = Field(index=True, unique=True)
    reference: str = Field(index=True)
    payment_reference: Optional[str] = Field(default=None, index=True)
    fee_type: str = Field(default="processing")
    amount: Decimal = Field(default=Decimal("0.00"), nullable=False)
    currency: str = Field(default="INR")
    fee_date: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReconciliationRecord(SQLModel, table=True):
    __tablename__ = "reconciliation_records"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    source_type: str = Field(index=True)
    source_record_id: str = Field(index=True)
    matching_key: str = Field(index=True)
    matched_with: Optional[str] = Field(default=None, index=True)
    pair_type: Optional[str] = Field(default="order_payment", index=True)
    source_record_type: Optional[str] = Field(default=None, index=True)
    related_record_type: Optional[str] = Field(default=None, index=True)
    status: str = Field(default="UNRESOLVED", index=True)
    confidence: Decimal = Field(default=Decimal("0.00"))
    amount_diff: Decimal = Field(default=Decimal("0.00"))
    exception_type: Optional[str] = Field(default=None, index=True)
    evidence: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MatchCandidate(SQLModel, table=True):
    __tablename__ = "match_candidates"

    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: str = Field(index=True)
    source_record_id: str = Field(index=True)
    candidate_record_id: str = Field(index=True)
    score: Decimal = Field(default=Decimal("0.00"))
    rank: int = Field(default=1)
    features: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExceptionRecord(SQLModel, table=True):
    __tablename__ = "exceptions"

    id: Optional[int] = Field(default=None, primary_key=True)
    exception_type: str = Field(index=True)
    severity: str = Field(default="medium")
    status: str = Field(default="OPEN", index=True)
    certainty: str = Field(default="UNKNOWN")
    confidence: Decimal = Field(default=Decimal("0.00"))
    amount: Optional[Decimal] = None
    description: str = ""
    evidence: Optional[str] = None
    root_cause: Optional[str] = None
    recommended_action: Optional[str] = None
    reviewer_note: Optional[str] = None
    resolved_by: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class User(SQLModel, table=True):
    """Console user. Only a derived password hash is stored, never the password."""

    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    full_name: str = Field(default="")
    password_hash: str
    role: str = Field(default="reviewer", index=True)  # admin | reviewer | viewer
    is_active: bool = Field(default=True, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_login_at: Optional[datetime] = None


class UserSession(SQLModel, table=True):
    """Server-side session. Stores a hash of the token so a DB leak cannot log in."""

    __tablename__ = "user_sessions"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(index=True, unique=True)
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class WebhookEvent(SQLModel, table=True):
    """Delivery log for provider webhooks. `event_id` makes processing idempotent.

    Stores no secrets: no signature, no headers, no request body — only a digest.
    """

    __tablename__ = "webhook_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(default="razorpay", index=True)
    event_id: str = Field(index=True, unique=True)
    event_type: str = Field(index=True)
    entity_id: Optional[str] = Field(default=None, index=True)
    payload_digest: str = Field(default="")
    status: str = Field(default="RECEIVED", index=True)
    records_ingested: int = Field(default=0)
    run_id: Optional[str] = None
    error_code: Optional[str] = None
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    processed_at: Optional[datetime] = None


class AuditEvent(SQLModel, table=True):
    __tablename__ = "audit_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str = Field(index=True)
    entity_type: str = Field(index=True)
    entity_id: str = Field(index=True)
    actor: str = Field(default="system")
    confidence: Optional[Decimal] = None
    previous_state: Optional[str] = None
    new_state: Optional[str] = None
    action: str = ""
    evidence: Optional[str] = None
    details: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
