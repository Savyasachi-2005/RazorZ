from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.ai.evidence import build_evidence_packet
from app.ai.providers.factory import get_provider
from app.ai.schemas import AIAssistError, AIAssistResult, AssistMode, EvidencePacket
from app.models import ExceptionRecord
from app.repositories.reconciliation_repository import ReconciliationRepository


def validate_assist_result(payload: Any) -> AIAssistResult:
    try:
        return AIAssistResult.model_validate(payload)
    except Exception as exc:
        raise AIAssistError("AI response failed schema validation", code="invalid_response") from exc


def assist_exception(
    exception_id: int,
    *,
    mode: AssistMode = "full_analysis",
    database_url: str | None = None,
    provider_name: str | None = None,
) -> Dict[str, Any]:
    """Advisory AI assist. Never mutates financial amounts or exception resolve/reject status."""
    repo = ReconciliationRepository(database_url)
    record = repo.get_exception(exception_id)
    if record is None:
        raise KeyError(exception_id)

    # Snapshot financial fields before AI call for mutation guards in tests/callers.
    before = _financial_snapshot(record)
    packet = build_evidence_packet(record)

    provider = get_provider(provider_name)
    success = False
    error_code: Optional[str] = None
    result: Optional[AIAssistResult] = None

    try:
        raw = provider.assist(packet, mode=mode)
        result = validate_assist_result(raw if isinstance(raw, dict) else raw.model_dump())
        success = True
    except AIAssistError as exc:
        error_code = exc.code
        repo.record_ai_assistance(
            exception_id=exception_id,
            provider=getattr(provider, "name", "unknown"),
            mode=mode,
            success=False,
            error_code=exc.code,
        )
        raise
    except Exception as exc:
        error_code = "provider_unavailable"
        repo.record_ai_assistance(
            exception_id=exception_id,
            provider=getattr(provider, "name", "unknown"),
            mode=mode,
            success=False,
            error_code=error_code,
        )
        raise AIAssistError("AI provider failed", code=error_code) from exc

    # Re-load and assert AI path did not mutate financial truth.
    after_record = repo.get_exception(exception_id)
    if after_record is None or _financial_snapshot(after_record) != before:
        raise AIAssistError("AI path attempted to mutate financial data", code="integrity_violation")

    repo.record_ai_assistance(
        exception_id=exception_id,
        provider=getattr(provider, "name", "unknown"),
        mode=mode,
        success=True,
        error_code=None,
        ai_confidence=result.ai_confidence if result else None,
    )

    assert result is not None
    return {
        "exception_id": exception_id,
        "mode": mode,
        "provider": getattr(provider, "name", "unknown"),
        "evidence_packet": packet.model_dump(exclude_none=True),
        "deterministic_confidence": packet.confidence,
        "assistance": result.model_dump(),
        "advisory_only": True,
        "disclaimer": (
            "AI assistance is advisory. Deterministic reconciliation remains financial truth. "
            "AI confidence is not reconciliation confidence and cannot resolve or reject."
        ),
    }


def _financial_snapshot(record: ExceptionRecord) -> Dict[str, Any]:
    return {
        "status": record.status,
        "amount": str(record.amount) if record.amount is not None else None,
        "confidence": str(record.confidence),
        "exception_type": record.exception_type,
        "reviewer_note": record.reviewer_note,
        "resolved_by": record.resolved_by,
    }
