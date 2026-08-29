from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, Optional


WEIGHTS = {
    "reference": Decimal("0.35"),
    "amount": Decimal("0.30"),
    "date": Decimal("0.15"),
    "customer": Decimal("0.20"),
}


def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value)[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None


def reference_similarity(left: str, right: str) -> Decimal:
    if left and left == right:
        return Decimal("1.00")
    if not left or not right:
        return Decimal("0.00")
    longest = max(len(left), len(right))
    shared = 0
    for a, b in zip(left, right):
        if a == b:
            shared += 1
        else:
            break
    return (Decimal(shared) / Decimal(longest)).quantize(Decimal("0.01"))


def amount_similarity(left: Decimal, right: Decimal) -> Decimal:
    maximum = max(abs(left), abs(right))
    if maximum == 0:
        return Decimal("1.00")
    ratio = (abs(left - right) / maximum).quantize(Decimal("0.0001"))
    score = Decimal("1.00") - ratio
    return max(Decimal("0.00"), score)


def date_similarity(left: Any, right: Any) -> Decimal:
    start = _parse_date(left)
    end = _parse_date(right)
    if start is None or end is None:
        return Decimal("0.50")
    days = abs((start - end).days)
    if days == 0:
        return Decimal("1.00")
    if days <= 2:
        return Decimal("0.85")
    if days <= 7:
        return Decimal("0.60")
    if days <= 14:
        return Decimal("0.30")
    return Decimal("0.00")


def customer_similarity(left: str, right: str) -> Decimal:
    if left and left == right:
        return Decimal("1.00")
    return Decimal("0.00")


def score_pair(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    features = {
        "reference": reference_similarity(str(left.get("reference", "")), str(right.get("reference", ""))),
        "amount": amount_similarity(left["amount"], right["amount"]),
        "date": date_similarity(left.get("date"), right.get("date")),
        "customer": customer_similarity(str(left.get("customer", "")), str(right.get("customer", ""))),
    }
    total = sum((features[name] * WEIGHTS[name] for name in WEIGHTS), Decimal("0.00"))
    return {
        "score": total.quantize(Decimal("0.01")),
        "features": {key: str(value) for key, value in features.items()},
        "candidate_id": right.get("record_id"),
    }
