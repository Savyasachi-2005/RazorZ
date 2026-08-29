from __future__ import annotations

"""Lightweight intent routing.

Maps a question to the minimum set of read-only tools needed to answer it, so we
never dump the database into the prompt. Routing is deterministic and testable —
the LLM does not choose its own tools.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

EXCEPTION_ID_PATTERN = re.compile(r"\bex[-_ ]?(\d{1,9})\b", re.IGNORECASE)
# Razorpay ids (pay_XXX, order_XXX, fee_pay_XXX) and synthetic ids (OR-00001, PM-00001).
# The prefix must be followed by _ or - so plain words like "payment" never match.
RECORD_ID_PATTERN = re.compile(
    r"\b((?:pay|order|rfnd|rfd|fee)_[A-Za-z0-9_]{5,}"
    r"|(?:OR|ORD|PM|PMT|ST|RF|FE)-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)\b",
    re.IGNORECASE,
)

MUTATION_PATTERNS = (
    r"\bresolve\b",
    r"\breject\b",
    r"\bapprove\b",
    r"\brefund (?:this|the|it)\b",
    r"\bissue a refund\b",
    r"\bcreate (?:a )?(?:refund|payment|order)\b",
    r"\bupdate (?:the )?(?:payment|order|settlement|fee)\b",
    r"\bdelete\b",
    r"\bmark (?:it|this|them) (?:as )?(?:resolved|rejected)\b",
    r"\bpost (?:the )?(?:entry|entries|journal)\b",
)

# Topics RAZORZ genuinely has no data for — answered honestly, without an LLM guess.
UNSUPPORTED_TOPICS = {
    "bank balance": ("bank balance", "bank_balance"),
    "bank account": ("bank account data", "bank_balance"),
    "gst": ("GST or tax filing data", "tax"),
    "tax filing": ("tax filing data", "tax"),
    "invoice pdf": ("invoice documents", "documents"),
    "profit": ("profit and loss data", "pnl"),
    "revenue forecast": ("forecasting data", "forecast"),
    "customer name": ("customer master data", "customer_master"),
}


# Record families a question can name, used to pick source-specific tools.
SOURCE_WORDS = {
    "order": ("order",),
    "payment": ("payment", "paid in", "captured"),
    "settlement": ("settlement", "settled", "settle", "payout"),
    "refund": ("refund",),
    "fee": ("fee",),
}

# Follow-ups that only make sense against the exception discussed a moment ago.
FOLLOW_UP_PATTERNS = (
    r"\breviewer\b",
    r"\bwho (?:did|rejected|resolved|reviewed|approved)\b",
    r"\bwhat did (?:the )?(?:reviewer|human|they)\b",
    r"\bwhy was it\b",
    r"\bwhy did (?:it|they)\b",
    r"\bhow much was involved\b",
    r"\bhow much (?:is|was) (?:it|that|this)\b",
    r"\bwhat happened (?:to|with) (?:it|that|this)\b",
    r"\bthat (?:one|exception)\b",
    r"\bthis (?:one|exception)\b",
    r"\bits status\b",
    r"\bwhat about it\b",
)


@dataclass
class RoutedPlan:
    intent: str
    tool_calls: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)
    refusal: str | None = None
    unavailable: str | None = None
    needs_llm: bool = True
    context: Dict[str, Any] = field(default_factory=dict)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def detect_mutation_request(question: str) -> bool:
    lowered = question.lower()
    return any(re.search(pattern, lowered) for pattern in MUTATION_PATTERNS)


def detect_unsupported_topic(question: str) -> str | None:
    lowered = question.lower()
    for needle, (label, _key) in UNSUPPORTED_TOPICS.items():
        if needle in lowered:
            return label
    return None


def _history_exception_id(history: Optional[Sequence[Any]]) -> Optional[str]:
    """Most recent exception discussed, newest turn first."""
    for turn in reversed(list(history or [])):
        if isinstance(turn, dict):
            text = str(turn.get("content") or "")
        else:
            text = str(getattr(turn, "content", "") or "")
        match = EXCEPTION_ID_PATTERN.search(text)
        if match:
            return match.group(1)
    return None


def _mentioned_sources(lowered: str) -> set[str]:
    """Which record families the question actually names."""
    found = set()
    for label, needles in SOURCE_WORDS.items():
        if _contains_any(lowered, needles):
            found.add(label)
    return found


def _is_follow_up(lowered: str) -> bool:
    return any(re.search(pattern, lowered) for pattern in FOLLOW_UP_PATTERNS)


def _exception_detail_plan(exception_id: str, *, carried: bool = False) -> RoutedPlan:
    return RoutedPlan(
        intent="exception_detail",
        tool_calls=[
            ("get_exception", {"exception_id": exception_id}),
            ("get_audit_events", {"entity_id": exception_id, "limit": 5}),
        ],
        context={"exception_id": exception_id, "carried_context": carried},
    )


def route(question: str, history: Optional[Sequence[Any]] = None) -> RoutedPlan:
    lowered = question.strip().lower()

    if detect_mutation_request(question):
        return RoutedPlan(
            intent="mutation_refused",
            refusal=(
                "The Finance Copilot is read-only and cannot resolve, reject, refund, or change "
                "any record. Use the Exceptions review workflow, where a human reviewer approves "
                "or rejects with an audited note."
            ),
            needs_llm=False,
        )

    unsupported = detect_unsupported_topic(question)
    if unsupported:
        return RoutedPlan(
            intent="unsupported_data",
            unavailable=(
                f"The current RAZORZ dataset does not contain {unsupported}, so I can't determine it. "
                "RAZORZ tracks orders, payments, settlements, refunds, fees, reconciliation results, "
                "exceptions, and audit events."
            ),
            needs_llm=False,
        )

    # Specific exception investigation
    exception_match = EXCEPTION_ID_PATTERN.search(question)
    if exception_match:
        return _exception_detail_plan(exception_match.group(1))

    # Short follow-up ("what did the reviewer do?") reuses the exception already in play.
    if _is_follow_up(lowered):
        carried_id = _history_exception_id(history)
        if carried_id:
            return _exception_detail_plan(carried_id, carried=True)

    # Specific record investigation
    record_match = RECORD_ID_PATTERN.search(question)
    if record_match:
        record_id = record_match.group(1)
        return RoutedPlan(
            intent="record_investigation",
            tool_calls=[
                ("get_record_relationships", {"record_id": record_id}),
                ("search_records", {"query": record_id, "limit": 5}),
            ],
        )

    # Source-specific questions get source-specific tools rather than the generic summary.
    sources = _mentioned_sources(lowered)
    wants_cross_source = len(sources) >= 3 or _contains_any(
        lowered, ("across", "cross-source", "cross source", "each source", "between sources")
    )
    if wants_cross_source and _contains_any(
        lowered, ("reconcil", "problem", "break", "issue", "exception", "mismatch", "compare", "worst", "biggest")
    ):
        return RoutedPlan(
            intent="cross_source_analysis",
            tool_calls=[("get_cross_source_summary", {})],
        )

    if "settlement" in sources:
        unsettled_phrasing = _contains_any(
            lowered,
            (
                "not been settled",
                "not settled",
                "unsettled",
                "never settled",
                "yet to be settled",
                "awaiting settlement",
                "pending settlement",
                "missing from settlement",
                "missing settlement",
                "no settlement",
                "without settlement",
                "haven't been settled",
                "have not been settled",
                "still waiting",
            ),
        )
        if unsettled_phrasing and "payment" in sources:
            return RoutedPlan(
                intent="unsettled_payments",
                tool_calls=[("get_unsettled_payments", {"limit": 10})],
            )
        return RoutedPlan(
            intent="settlement_analysis",
            tool_calls=[("get_settlement_summary", {})],
        )

    financial_words = (
        "how much",
        "collected",
        "refunded",
        "fees",
        "fee total",
        "exposure",
        "tied up",
        "financial position",
        "settlement total",
        "money",
        "amount",
        "total",
        "cash",
    )
    exception_words = (
        "exception",
        "unresolved",
        "mismatch",
        "orphan",
        "missing",
        "break",
        "high-priority",
        "high priority",
        "review queue",
    )
    reconciliation_words = (
        "reconcil",
        "match rate",
        "matched",
        "review",
        "rate",
        "summary",
        "summarize",
        "overview",
        "today",
    )

    wants_financial = _contains_any(lowered, financial_words)
    wants_exceptions = _contains_any(lowered, exception_words)
    wants_reconciliation = _contains_any(lowered, reconciliation_words)

    if wants_financial and not wants_reconciliation:
        calls: List[Tuple[str, Dict[str, Any]]] = [("get_financial_summary", {})]
        if wants_exceptions or "exposure" in lowered or "tied up" in lowered or "unresolved" in lowered:
            calls.append(("search_exceptions", {"status": "OPEN", "limit": 5}))
        return RoutedPlan(intent="financial_position", tool_calls=calls)

    if wants_exceptions and not wants_reconciliation:
        filters: Dict[str, Any] = {"limit": 10}
        if "unresolved" in lowered or "open" in lowered:
            filters["status"] = "OPEN"
        if "high" in lowered and "priority" in lowered:
            filters["priority"] = "P1"
        for label in (
            "amount_mismatch",
            "amount mismatch",
            "payment_missing",
            "payment missing",
            "orphan_payment",
            "orphan payment",
            "fee_difference",
            "fee difference",
            "ambiguous_match",
            "ambiguous match",
        ):
            if label in lowered:
                filters["exception_type"] = label.replace(" ", "_").upper()
                break
        calls = [("search_exceptions", filters)]
        if "impact" in lowered or "highest" in lowered or "most" in lowered:
            calls.append(("get_reconciliation_summary", {}))
        return RoutedPlan(intent="exception_analysis", tool_calls=calls)

    if "audit" in lowered or "who " in lowered or "history" in lowered:
        return RoutedPlan(intent="audit_history", tool_calls=[("get_audit_events", {"limit": 10})])

    # Reconciliation health / summary / "what should I look at first" and general fallback.
    calls = [("get_reconciliation_summary", {})]
    if any(word in lowered for word in ("why", "cause", "fall", "fell", "low", "first", "summary", "summarize")):
        calls.append(("search_exceptions", {"status": "OPEN", "limit": 5}))
    if wants_financial:
        calls.append(("get_financial_summary", {}))
    intent = "reconciliation_summary"
    if "why" in lowered or "cause" in lowered:
        intent = "reconciliation_diagnosis"
    return RoutedPlan(intent=intent, tool_calls=calls)
