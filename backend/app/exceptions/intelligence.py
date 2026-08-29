from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional

from app.models import ExceptionRecord

TAXONOMY: Dict[str, Dict[str, Any]] = {
    "PAYMENT_MISSING": {
        "meaning": "An order has no matching payment.",
        "severity": "high",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "confirm capture in the payment source or mark as unpaid",
        "root_causes": ["payment never captured", "reference mismatch", "ingestion lag"],
    },
    "AMOUNT_MISMATCH": {
        "meaning": "Linked records share identity but not amount.",
        "severity": "high",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "compare fees, partial capture, and source totals before changing books",
        "root_causes": ["fee excluded", "partial payment", "incorrect amount in one source"],
    },
    "ORPHAN_PAYMENT": {
        "meaning": "A payment has no matching order.",
        "severity": "medium",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "search for a missing order or treat as unallocated cash",
        "root_causes": ["order not ingested", "wrong reference", "duplicate payment"],
    },
    "AMBIGUOUS_MATCH": {
        "meaning": "Two or more candidates score too closely to auto-resolve.",
        "severity": "medium",
        "certainty": "PROBABLE",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "human selects the correct candidate; do not auto-post",
        "root_causes": ["similar amounts", "shared customer", "duplicate invoices"],
    },
    "DATE_MISMATCH": {
        "meaning": "Identity looks related but event dates diverge.",
        "severity": "low",
        "certainty": "PROBABLE",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "confirm delayed capture or settlement lag",
        "root_causes": ["timezone", "settlement delay", "wrong period"],
    },
    "SETTLEMENT_MISSING": {
        "meaning": "A captured payment has no settlement record.",
        "severity": "high",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "confirm settlement batch inclusion or delayed payout",
        "root_causes": ["settlement lag", "payment excluded from batch", "ingestion gap"],
    },
    "SETTLEMENT_AMOUNT_MISMATCH": {
        "meaning": "Settlement amount differs from the captured payment amount.",
        "severity": "high",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "compare fees, FX, and partial settlement before adjusting books",
        "root_causes": ["fee netting", "partial settlement", "incorrect settlement amount"],
    },
    "ORPHAN_SETTLEMENT": {
        "meaning": "A settlement references an unknown payment.",
        "severity": "medium",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "locate the missing payment or reverse the orphan settlement",
        "root_causes": ["payment not ingested", "wrong payment reference", "duplicate settlement id"],
    },
    "DUPLICATE_SETTLEMENT": {
        "meaning": "More than one settlement is linked to the same payment.",
        "severity": "high",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "confirm which settlement is valid; do not double-post cash",
        "root_causes": ["batch replay", "duplicate ingestion", "split settlement mis-tagged"],
    },
    "REFUND_MISSING": {
        "meaning": "An expected refund was not found for the payment.",
        "severity": "high",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "confirm refund initiation in the payment source",
        "root_causes": ["refund not processed", "reference mismatch", "ingestion lag"],
    },
    "ORPHAN_REFUND": {
        "meaning": "A refund references an unknown payment.",
        "severity": "medium",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "locate the payment or treat as unallocated refund outflow",
        "root_causes": ["payment not ingested", "wrong payment reference"],
    },
    "REFUND_EXCESSIVE": {
        "meaning": "Refund total exceeds the captured payment amount.",
        "severity": "high",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "block posting until refund totals are corrected",
        "root_causes": ["duplicate refund", "incorrect refund amount", "partial capture mismatch"],
    },
    "REFUND_MISMATCH": {
        "meaning": "Refund identity is related but amounts or expectations conflict.",
        "severity": "high",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "compare expected vs actual refund before changing books",
        "root_causes": ["partial refund", "incorrect amount", "currency mismatch"],
    },
    "MULTIPLE_REFUNDS": {
        "meaning": "Multiple refunds exist against one payment within the captured amount.",
        "severity": "medium",
        "certainty": "PROBABLE",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "verify each partial refund is intentional before closing",
        "root_causes": ["partial refunds", "retry after failure", "split customer refunds"],
    },
    "FEE_MISSING": {
        "meaning": "An expected processing fee was not found for the payment.",
        "severity": "medium",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "confirm fee schedule and settlement netting",
        "root_causes": ["fee waived", "fee bundled in settlement", "ingestion gap"],
    },
    "FEE_DIFFERENCE": {
        "meaning": "Fee amount differs from the configured/expected fee.",
        "severity": "medium",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "compare fee schedule and MDR before adjusting books",
        "root_causes": ["rate change", "incorrect fee posting", "tax included/excluded"],
    },
    "FEE_UNEXPECTED": {
        "meaning": "A fee is attached to an unknown payment or was not expected.",
        "severity": "medium",
        "certainty": "CONFIRMED",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "locate the payment or reverse the unexpected fee",
        "root_causes": ["wrong payment reference", "duplicate fee", "payment not ingested"],
    },
    "UNKNOWN_EXCEPTION": {
        "meaning": "The engine could not classify the break.",
        "severity": "medium",
        "certainty": "UNKNOWN",
        "human_required": True,
        "ai_useful": True,
        "recommended_action": "leave unresolved until a reviewer provides evidence",
        "root_causes": ["incomplete data"],
    },
}

HIGH_IMPACT_TYPES = {
    "PAYMENT_MISSING",
    "AMOUNT_MISMATCH",
    "AMBIGUOUS_MATCH",
    "SETTLEMENT_MISSING",
    "SETTLEMENT_AMOUNT_MISMATCH",
    "DUPLICATE_SETTLEMENT",
    "REFUND_MISSING",
    "REFUND_EXCESSIVE",
}


def classify(exception_type: str) -> Dict[str, Any]:
    return TAXONOMY.get(exception_type, TAXONOMY["UNKNOWN_EXCEPTION"])


def certainty_for(exception_type: str) -> str:
    return str(classify(exception_type)["certainty"])


def enrich_exception(record: ExceptionRecord) -> Dict[str, Any]:
    profile = classify(record.exception_type)
    amount = record.amount if record.amount is not None else Decimal("0")
    priority = "P2"
    if amount >= Decimal("500") or record.exception_type in HIGH_IMPACT_TYPES:
        priority = "P1"
    if record.exception_type in {"DATE_MISMATCH"} and amount < Decimal("100"):
        priority = "P3"
    return {
        "id": record.id,
        "exception_type": record.exception_type,
        "status": record.status,
        "severity": record.severity or profile["severity"],
        "certainty": record.certainty or profile["certainty"],
        "confidence": str(record.confidence),
        "amount": str(record.amount) if record.amount is not None else None,
        "description": record.description,
        "evidence": record.evidence,
        "root_cause": record.root_cause,
        "recommended_action": record.recommended_action or profile["recommended_action"],
        "human_required": profile["human_required"],
        "priority": priority,
        "reviewer_note": record.reviewer_note,
        "resolved_by": record.resolved_by,
        "possible_root_causes": profile["root_causes"],
    }


def apply_classification(record: ExceptionRecord) -> None:
    profile = classify(record.exception_type)
    if not record.root_cause:
        record.root_cause = "; ".join(profile["root_causes"])
    if not record.recommended_action:
        record.recommended_action = str(profile["recommended_action"])
    if not record.certainty:
        record.certainty = str(profile["certainty"])
    if not record.severity or record.severity == "medium":
        record.severity = str(profile["severity"])
