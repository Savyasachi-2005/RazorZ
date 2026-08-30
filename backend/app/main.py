from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, List, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.ai.schemas import AIAssistError
from app.ai.service import assist_exception
from app.config import settings
from app.copilot import service as copilot_service
from app.copilot.schemas import CopilotError
from app.db import create_db_and_tables, ping_database
from app.integrations.razorpay.errors import RazorpayIntegrationError
from app.integrations.razorpay.service import process_webhook, razorpay_status, sync_and_reconcile
from app.auth.service import AuthError, bootstrap_admin
from app.auth.service import login as auth_login
from app.auth.service import logout as auth_logout
from app.security import auth_enabled, bearer_token, require_api_key, resolve_session_user
from app.services import reconciliation_service as service


class ReconciliationInputRecord(BaseModel):
    source: str = Field(default="synthetic")
    record_type: str = Field(..., description="order, payment, settlement, refund, fee")
    record_id: str
    reference: str = Field(default="")
    payment_reference: str = Field(default="")
    amount: str = Field(default="0.00")
    date: str = Field(default="")
    customer: str = Field(default="")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReconciliationRequest(BaseModel):
    records: List[ReconciliationInputRecord]


class GenerateRequest(BaseModel):
    records: int = Field(default=50, ge=50, le=10000)
    seed: int = Field(default=42)


class ReviewRequest(BaseModel):
    actor: str = Field(default="reviewer")
    note: str = Field(..., min_length=3)


class AIAssistRequest(BaseModel):
    mode: Literal["full_analysis", "suggest_note", "investigation_steps"] = "full_analysis"


class RazorpaySyncRequest(BaseModel):
    count: int = Field(default=50, ge=1, le=100)


class CopilotTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class CopilotAskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)
    history: List[CopilotTurn] = Field(default_factory=list, max_length=8)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=200)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        create_db_and_tables()
    except Exception as exc:
        # Database initialization may fail in development or on first deploy.
        # The app can still start; /health will report the database status.
        import traceback
        print(f"⚠️  Database initialization failed:")
        print(f"   Error: {exc.__class__.__name__}: {exc}")
        print(f"   Traceback: {traceback.format_exc()}")
    
    try:
        bootstrap_admin()
    except Exception:
        # A missing admin must not stop the API from starting.
        pass
    yield


# Auth is applied app-wide so any new route is protected by default. The health
# probe, the docs and the HMAC-verified Razorpay webhook are exempt (app.security).
app = FastAPI(
    title="RAZORZ API",
    version="0.8.0",
    lifespan=lifespan,
    dependencies=[Depends(require_api_key)],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check() -> dict[str, Any]:
    try:
        database = ping_database()
        return {
            "status": "ok",
            "service": "razorz-backend",
            "database": database,
            "ai_provider": settings.ai_provider,
            "auth": {"required": auth_enabled(), "scheme": "api_key"},
            "razorpay": {
                "configured": bool(settings.razorpay_key_id and settings.razorpay_key_secret),
                "mode": settings.razorpay_mode,
            },
        }
    except Exception as exc:
        return {
            "status": "degraded",
            "service": "razorz-backend",
            "database": {"ok": False, "error": exc.__class__.__name__},
            "ai_provider": settings.ai_provider,
            "auth": {"required": auth_enabled(), "scheme": "api_key"},
            "razorpay": {
                "configured": bool(settings.razorpay_key_id and settings.razorpay_key_secret),
                "mode": settings.razorpay_mode,
            },
        }


@app.post("/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    """Exchange email + password for a session token."""
    try:
        return auth_login(payload.email, payload.password)
    except AuthError as exc:
        raise HTTPException(
            status_code=401,
            detail={"message": exc.message, "code": exc.code},
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


@app.post("/auth/logout")
def logout(request: Request) -> dict[str, Any]:
    """Revoke the presented session token. An API-key caller has no session."""
    return auth_logout(bearer_token(request))


@app.get("/auth/me")
def current_user(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None) or resolve_session_user(bearer_token(request))
    if user is None:
        return {"authenticated": True, "user": None, "auth_method": "api_key"}
    return {"authenticated": True, "user": user, "auth_method": "session"}


@app.post("/reconciliation/run")
def run_reconciliation(payload: ReconciliationRequest) -> dict[str, Any]:
    return service.run_reconciliation([record.model_dump() for record in payload.records])


@app.post("/ingestion/generate")
def generate_ingestion(payload: GenerateRequest) -> dict[str, Any]:
    return service.generate_and_reconcile(records=payload.records, seed=payload.seed)


@app.get("/reconciliation/summary")
def reconciliation_summary() -> dict[str, Any]:
    return service.get_summary()


@app.get("/reconciliation/records")
def reconciliation_records(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    pair_type: str | None = Query(
        default=None,
        description="Optional filter: order_payment, payment_settlement, payment_refund, payment_fee",
    ),
) -> dict[str, Any]:
    rows = service.list_records(limit=limit, offset=offset, pair_type=pair_type)
    return {"items": rows, "limit": limit, "offset": offset, "pair_type": pair_type}


@app.get("/exceptions")
def list_exceptions(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    rows = service.list_exceptions(limit=limit, offset=offset)
    return {"items": rows, "limit": limit, "offset": offset}


@app.get("/exceptions/{exception_id}")
def get_exception(exception_id: int) -> dict[str, Any]:
    row = service.get_exception(exception_id)
    if row is None:
        raise HTTPException(status_code=404, detail="exception not found")
    return row


@app.post("/exceptions/{exception_id}/ai-assist")
def exception_ai_assist(
    exception_id: int,
    payload: AIAssistRequest = AIAssistRequest(),
) -> dict[str, Any]:
    try:
        return assist_exception(exception_id, mode=payload.mode)
    except KeyError:
        raise HTTPException(status_code=404, detail="exception not found") from None
    except AIAssistError as exc:
        raise HTTPException(
            status_code=503 if exc.code in {"provider_unavailable", "timeout"} else 422,
            detail={"message": exc.message, "code": exc.code, "advisory_only": True},
        ) from None


@app.post("/exceptions/{exception_id}/resolve")
def resolve_exception(exception_id: int, payload: ReviewRequest) -> dict[str, Any]:
    return _review(exception_id, "resolve", payload)


@app.post("/exceptions/{exception_id}/reject")
def reject_exception(exception_id: int, payload: ReviewRequest) -> dict[str, Any]:
    return _review(exception_id, "reject", payload)


def _review(exception_id: int, action: str, payload: ReviewRequest) -> dict[str, Any]:
    try:
        return service.review_exception(
            exception_id,
            action=action,
            actor=payload.actor,
            note=payload.note,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="exception not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.get("/audit")
def list_audit(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    rows = service.list_audit(limit=limit, offset=offset)
    return {"items": rows, "limit": limit, "offset": offset}


@app.get("/copilot/suggestions")
def copilot_suggestions() -> dict[str, Any]:
    return copilot_service.suggestions()


@app.post("/copilot/ask")
def copilot_ask(payload: CopilotAskRequest) -> dict[str, Any]:
    """Read-only finance Q&A grounded in RAZORZ data. Exposes no mutation tools."""
    try:
        return copilot_service.ask(
            payload.question,
            history=[turn.model_dump() for turn in payload.history],
        )
    except CopilotError as exc:
        raise HTTPException(
            status_code=503 if exc.code in {"provider_unavailable", "timeout"} else 422,
            detail={"message": exc.message, "code": exc.code, "read_only": True},
        ) from None


@app.get("/integrations/razorpay/status")
def get_razorpay_status() -> dict[str, Any]:
    return razorpay_status()


@app.post("/integrations/razorpay/sync")
def post_razorpay_sync(payload: RazorpaySyncRequest = RazorpaySyncRequest()) -> dict[str, Any]:
    try:
        return sync_and_reconcile(count=payload.count)
    except RazorpayIntegrationError as exc:
        raise HTTPException(
            status_code=503 if exc.code in {"timeout", "network_error", "rate_limited"} else 422,
            detail={"message": exc.message, "code": exc.code},
        ) from None


WEBHOOK_ERROR_STATUS = {
    "invalid_signature": 401,
    "webhook_not_configured": 503,
    "invalid_payload": 422,
    "processing_failed": 500,
}


@app.post("/integrations/razorpay/webhook")
async def post_razorpay_webhook(request: Request) -> dict[str, Any]:
    """Razorpay webhook receiver. The signature is verified over the raw body."""
    body = await request.body()
    try:
        return process_webhook(body, headers=dict(request.headers))
    except RazorpayIntegrationError as exc:
        raise HTTPException(
            status_code=WEBHOOK_ERROR_STATUS.get(exc.code, 422),
            detail={"message": exc.message, "code": exc.code},
        ) from None
