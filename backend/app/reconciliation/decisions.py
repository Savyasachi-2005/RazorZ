from __future__ import annotations

from decimal import Decimal
from typing import Optional, Tuple

from app.config import settings

# Never auto-resolve — always EXCEPTION
HARD_EXCEPTIONS = {
    "PAYMENT_MISSING",
    "ORPHAN_PAYMENT",
    "ORDER_MISSING",
    "SETTLEMENT_MISSING",
    "ORPHAN_SETTLEMENT",
    "DUPLICATE_SETTLEMENT",
    "REFUND_MISSING",
    "ORPHAN_REFUND",
    "REFUND_EXCESSIVE",
    "FEE_MISSING",
    "FEE_UNEXPECTED",
}

# Identity linked but financially unsafe — REVIEW_REQUIRED
REVIEW_EXCEPTIONS = {
    "AMOUNT_MISMATCH",
    "DATE_MISMATCH",
    "AMBIGUOUS_MATCH",
    "SETTLEMENT_AMOUNT_MISMATCH",
    "REFUND_MISMATCH",
    "MULTIPLE_REFUNDS",
    "FEE_DIFFERENCE",
}


def decide_status(
    confidence: Decimal,
    exception_type: Optional[str] = None,
    *,
    exact_match: bool = False,
    ambiguous: bool = False,
) -> Tuple[str, Decimal]:
    """Map a confidence score onto a reconciliation status.

    Amount mismatches and missing records never auto-resolve, even if
    identity features score highly. Ambiguous pairs always need review.
    """
    thresholds = settings.thresholds

    if exact_match:
        return "MATCHED", max(confidence, thresholds.auto_resolve)

    if ambiguous:
        return "REVIEW_REQUIRED", min(confidence, thresholds.human_review)

    if exception_type in HARD_EXCEPTIONS:
        return "EXCEPTION", min(confidence, thresholds.human_review - Decimal("0.01"))

    if exception_type in REVIEW_EXCEPTIONS:
        return "REVIEW_REQUIRED", max(thresholds.human_review, min(confidence, thresholds.auto_resolve_warning))

    if confidence >= thresholds.auto_resolve:
        return "AUTO_RESOLVED", confidence
    if confidence >= thresholds.auto_resolve_warning:
        return "AUTO_RESOLVED", confidence
    if confidence >= thresholds.human_review:
        return "REVIEW_REQUIRED", confidence
    return "UNRESOLVED", confidence
