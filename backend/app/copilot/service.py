from __future__ import annotations

"""Finance Copilot orchestration.

Question → intent routing → read-only tools → compact context → LLM explanation
→ schema validation → grounding check → audit. The Copilot never writes financial
data and never calculates money itself.
"""

import json
import re
from typing import Any, Dict, List, Optional

from app.ai.providers.factory import get_provider
from app.copilot.router import route
from app.copilot.schemas import (
    READ_ONLY_TOOLS,
    ConversationTurn,
    CopilotAnswer,
    CopilotContext,
    CopilotError,
    DataPoint,
    ToolResult,
)
from app.copilot.tools import run_tool
from app.repositories.reconciliation_repository import ReconciliationRepository

MAX_HISTORY_TURNS = 4

DISCLAIMER = (
    "The Finance Copilot is read-only. Deterministic reconciliation remains financial truth; "
    "the Copilot explains it and cannot resolve, reject, or modify any record."
)

SUGGESTED_QUESTIONS = (
    "Summarize today's reconciliation",
    "Show unresolved exceptions",
    "What is causing the most exceptions?",
    "How much money is unresolved?",
    "Are there any payments that have not been settled?",
    "What are the biggest reconciliation problems across payments, settlements, refunds, and fees?",
)

_MONEY_PATTERN = re.compile(r"(?:INR|₹)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)")
_EXCEPTION_ID_PATTERN = re.compile(r"\bEX-(\d+)\b")


def ask(
    question: str,
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    database_url: str | None = None,
    provider_name: str | None = None,
) -> Dict[str, Any]:
    """Answer one read-only finance question grounded in RAZORZ data."""
    cleaned = (question or "").strip()
    if not cleaned:
        raise CopilotError("question must not be empty", code="empty_question")

    plan = route(cleaned, history=history)
    repo = ReconciliationRepository(database_url)

    # Refusals and known data gaps need no tools and no LLM call.
    if plan.refusal or plan.unavailable:
        message = plan.refusal or plan.unavailable or ""
        answer = CopilotAnswer(
            answer=message,
            key_findings=[],
            data_points=[],
            sources_used=[],
            confidence=1.0 if plan.refusal else 0.9,
        )
        repo.record_copilot_query(
            intent=plan.intent,
            tools_used=[],
            provider="none",
            success=True,
            llm_used=False,
        )
        return _envelope(
            question=cleaned,
            intent=plan.intent,
            provider="none",
            answer=answer,
            tool_results=[],
            llm_used=False,
            read_only=True,
            refused=bool(plan.refusal),
        )

    tool_results = [
        run_tool(name, params, database_url=database_url) for name, params in plan.tool_calls
    ]
    tools_used = [result.tool for result in tool_results if result.ok]

    if not tools_used:
        failures = "; ".join(filter(None, (result.error for result in tool_results))) or "no data"
        answer = CopilotAnswer(
            answer=(
                "I could not read the data needed to answer that, so I won't guess. "
                f"Reason: {failures}."
            ),
            confidence=0.2,
        )
        repo.record_copilot_query(
            intent=plan.intent,
            tools_used=[],
            provider="none",
            success=False,
            llm_used=False,
            error_code="tool_failure",
        )
        return _envelope(
            question=cleaned,
            intent=plan.intent,
            provider="none",
            answer=answer,
            tool_results=tool_results,
            llm_used=False,
            read_only=True,
        )

    # Token efficiency: a plain count or total needs no LLM.
    deterministic = _deterministic_answer(cleaned, plan.intent, tool_results)
    if deterministic is not None:
        repo.record_copilot_query(
            intent=plan.intent,
            tools_used=tools_used,
            provider="deterministic",
            success=True,
            llm_used=False,
        )
        return _envelope(
            question=cleaned,
            intent=plan.intent,
            provider="deterministic",
            answer=deterministic,
            tool_results=tool_results,
            llm_used=False,
            read_only=True,
        )

    context = CopilotContext(
        question=cleaned,
        intent=plan.intent,
        tool_results=tool_results,
        recent_turns=_recent_turns(history),
    )

    provider = get_provider(provider_name)
    provider_label = getattr(provider, "name", "unknown")
    if not hasattr(provider, "copilot"):
        repo.record_copilot_query(
            intent=plan.intent,
            tools_used=tools_used,
            provider=provider_label,
            success=False,
            llm_used=False,
            error_code="provider_unavailable",
        )
        raise CopilotError(
            f"AI provider '{provider_label}' does not support the Copilot", code="provider_unavailable"
        )

    try:
        answer = provider.copilot(context)
    except CopilotError as exc:
        repo.record_copilot_query(
            intent=plan.intent,
            tools_used=tools_used,
            provider=provider_label,
            success=False,
            llm_used=True,
            error_code=exc.code,
        )
        raise
    except Exception as exc:
        repo.record_copilot_query(
            intent=plan.intent,
            tools_used=tools_used,
            provider=provider_label,
            success=False,
            llm_used=True,
            error_code="provider_unavailable",
        )
        raise CopilotError("AI provider failed", code="provider_unavailable") from exc

    ungrounded = find_ungrounded_claims(answer, tool_results)
    if ungrounded:
        # The model cited values that are not in the evidence — drop its narrative
        # rather than surface an unverifiable financial statement.
        answer = _grounded_fallback(plan.intent, tool_results)
        repo.record_copilot_query(
            intent=plan.intent,
            tools_used=tools_used,
            provider=provider_label,
            success=False,
            llm_used=True,
            error_code="ungrounded_response",
        )
        return _envelope(
            question=cleaned,
            intent=plan.intent,
            provider=provider_label,
            answer=answer,
            tool_results=tool_results,
            llm_used=True,
            read_only=True,
            grounding_warning=(
                "The model referenced values that were not in the retrieved evidence, "
                "so a deterministic summary is shown instead."
            ),
        )

    # Only cite tools that actually returned data.
    answer.sources_used = [name for name in answer.sources_used if name in tools_used] or tools_used

    repo.record_copilot_query(
        intent=plan.intent,
        tools_used=tools_used,
        provider=provider_label,
        success=True,
        llm_used=True,
    )
    return _envelope(
        question=cleaned,
        intent=plan.intent,
        provider=provider_label,
        answer=answer,
        tool_results=tool_results,
        llm_used=True,
        read_only=True,
    )


def suggestions() -> Dict[str, Any]:
    return {
        "suggestions": list(SUGGESTED_QUESTIONS),
        "tools": list(READ_ONLY_TOOLS),
        "read_only": True,
        "disclaimer": DISCLAIMER,
    }


# ------------------------------------------------------------------ helpers


def _recent_turns(history: Optional[List[Dict[str, Any]]]) -> List[ConversationTurn]:
    """Keep only the last few turns so follow-ups work without unbounded context."""
    if not history:
        return []
    turns: List[ConversationTurn] = []
    for item in history[-MAX_HISTORY_TURNS:]:
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        turns.append(ConversationTurn(role=role, content=content[:2000]))
    return turns


def _evidence_text(tool_results: List[ToolResult]) -> str:
    return json.dumps([result.data for result in tool_results if result.ok], default=str)


def find_ungrounded_claims(answer: CopilotAnswer, tool_results: List[ToolResult]) -> List[str]:
    """Return money amounts / exception ids cited in the answer but absent from evidence."""
    evidence = _evidence_text(tool_results)
    evidence_numbers = set(re.findall(r"[0-9]+(?:\.[0-9]{1,2})?", evidence))
    text = " ".join(
        [answer.answer]
        + answer.key_findings
        + [f"{point.label} {point.value}" for point in answer.data_points]
    )

    unsupported: List[str] = []
    for raw in _MONEY_PATTERN.findall(text):
        normalized = raw.replace(",", "")
        if normalized in evidence_numbers:
            continue
        # Tolerate trailing-zero formatting differences such as 500 vs 500.00.
        if any(candidate.startswith(normalized) for candidate in evidence_numbers):
            continue
        try:
            if f"{float(normalized):.2f}" in evidence_numbers:
                continue
        except ValueError:
            pass
        unsupported.append(f"amount {raw}")

    for exception_id in _EXCEPTION_ID_PATTERN.findall(text):
        if f"EX-{exception_id}" not in evidence:
            unsupported.append(f"exception EX-{exception_id}")

    return unsupported


def _deterministic_answer(
    question: str, intent: str, tool_results: List[ToolResult]
) -> Optional[CopilotAnswer]:
    """Answer pure count/total questions from the backend without calling the LLM."""
    lowered = question.lower()
    data = {result.tool: result.data for result in tool_results if result.ok}

    if lowered.startswith("how many") and "search_exceptions" in data:
        payload = data["search_exceptions"]
        count = payload.get("match_count", 0)
        status_filter = (payload.get("filters") or {}).get("status")
        # State the count exactly as the backend filtered it — no reinterpretation.
        if status_filter:
            headline = f"{count} exceptions are currently {status_filter}."
        else:
            headline = f"{count} exceptions match that filter in the current RAZORZ data."
        return CopilotAnswer(
            answer=headline,
            key_findings=[
                f"Those exceptions represent INR {payload.get('total_amount', '0.00')} in recorded "
                "reconciliation difference, which is exposure under review rather than confirmed loss."
            ],
            data_points=[
                DataPoint(
                    label=f"{status_filter} exceptions" if status_filter else "Matching exceptions",
                    value=str(count),
                ),
                DataPoint(
                    label="Recorded difference",
                    value=f"INR {payload.get('total_amount', '0.00')}",
                ),
            ],
            sources_used=["search_exceptions"],
            confidence=1.0,
        )

    if lowered.startswith("how many") and "get_reconciliation_summary" in data:
        payload = data["get_reconciliation_summary"]
        return CopilotAnswer(
            answer=(
                f"{payload.get('matched', 0)} of {payload.get('total_records', 0)} relationships are matched "
                f"({payload.get('match_rate', '0.00%')}); {payload.get('review_required', 0)} need review."
            ),
            key_findings=[],
            data_points=[
                DataPoint(label="Matched", value=str(payload.get("matched", 0))),
                DataPoint(label="Total records", value=str(payload.get("total_records", 0))),
                DataPoint(label="Review required", value=str(payload.get("review_required", 0))),
            ],
            sources_used=["get_reconciliation_summary"],
            confidence=1.0,
        )

    return None


def _grounded_fallback(intent: str, tool_results: List[ToolResult]) -> CopilotAnswer:
    """Deterministic summary used when the model's narrative cannot be trusted."""
    data = {result.tool: result.data for result in tool_results if result.ok}
    points: List[DataPoint] = []
    findings: List[str] = []

    summary = data.get("get_reconciliation_summary")
    if summary:
        points.append(DataPoint(label="Match rate", value=str(summary.get("match_rate", "0.00%"))))
        points.append(DataPoint(label="Total records", value=str(summary.get("total_records", 0))))
        points.append(DataPoint(label="Review required", value=str(summary.get("review_required", 0))))
        findings.append(f"{summary.get('matched', 0)} matched of {summary.get('total_records', 0)}.")

    financial = data.get("get_financial_summary")
    if financial:
        points.append(
            DataPoint(label="Unresolved exposure", value=f"INR {financial.get('unresolved_exposure', '0.00')}")
        )
        points.append(
            DataPoint(label="Total payments", value=f"INR {financial.get('total_payments', '0.00')}")
        )

    search = data.get("search_exceptions")
    if search:
        points.append(DataPoint(label="Matching exceptions", value=str(search.get("match_count", 0))))
        findings.extend(
            f"{row['exception_id']} · {row['exception_type']} · INR {row.get('amount') or '0.00'}"
            for row in search.get("exceptions", [])[:5]
        )

    unsettled = data.get("get_unsettled_payments")
    if unsettled:
        points.append(
            DataPoint(label="Unsettled payments", value=str(unsettled.get("unsettled_count", 0)))
        )
        points.append(
            DataPoint(
                label="Unsettled amount", value=f"INR {unsettled.get('unsettled_amount', '0.00')}"
            )
        )
        findings.extend(
            f"{row['payment_id']} · INR {row['amount']}"
            for row in unsettled.get("unsettled_payments", [])[:5]
        )

    settlement = data.get("get_settlement_summary")
    if settlement:
        points.append(
            DataPoint(label="Settlements", value=str(settlement.get("settlement_count", 0)))
        )
        points.append(
            DataPoint(
                label="Payments not settled",
                value=str(settlement.get("payments_missing_from_settlements", 0)),
            )
        )

    cross = data.get("get_cross_source_summary")
    if cross:
        findings.extend(
            f"{pair['label']} · {pair['open_exception_count']} open · "
            f"INR {pair['open_exception_amount']}"
            for pair in cross.get("pairs", [])
        )

    return CopilotAnswer(
        answer=(
            "Showing the deterministic figures retrieved from RAZORZ. The AI explanation was "
            "discarded because it referenced values not present in the retrieved data."
        ),
        key_findings=findings,
        data_points=points,
        sources_used=list(data.keys()),
        confidence=1.0,
    )


def _envelope(
    *,
    question: str,
    intent: str,
    provider: str,
    answer: CopilotAnswer,
    tool_results: List[ToolResult],
    llm_used: bool,
    read_only: bool,
    refused: bool = False,
    grounding_warning: str | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "question": question,
        "intent": intent,
        "provider": provider,
        "llm_used": llm_used,
        "read_only": read_only,
        "refused": refused,
        "answer": answer.model_dump(),
        "tools_used": [result.tool for result in tool_results if result.ok],
        "tool_errors": [
            {"tool": result.tool, "error": result.error} for result in tool_results if not result.ok
        ],
        "evidence": {result.tool: result.data for result in tool_results if result.ok},
        "disclaimer": DISCLAIMER,
    }
    if grounding_warning:
        payload["grounding_warning"] = grounding_warning
    return payload
