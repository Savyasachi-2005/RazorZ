from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.ai.schemas import EvidencePacket
from app.exceptions.intelligence import classify
from app.models import ExceptionRecord


def _as_money_str(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        return str(Decimal(str(value)))
    except Exception:
        return None


def _confidence_float(value: Any) -> float:
    try:
        n = float(Decimal(str(value)))
    except Exception:
        return 0.0
    if n > 1.0:
        n = n / 100.0
    return max(0.0, min(1.0, n))


def _classify_id(token: str) -> Optional[str]:
    lower = token.lower()
    if re.search(r"^(ord|or|order)[-_]?", lower) or "order" in lower:
        return "order_id"
    if re.search(r"^(pay|pm|pmt|payment)[-_]?", lower) or "payment" in lower:
        return "payment_id"
    if re.search(r"^(st|set|settlement)[-_]?", lower) or "settlement" in lower:
        return "settlement_id"
    if re.search(r"^(rf|ref|refund)[-_]?", lower) or "refund" in lower:
        return "refund_id"
    if re.search(r"^(fe|fee)[-_]?", lower) or lower.startswith("fee"):
        return "fee_id"
    return None


def _parse_record_ids(description: str, evidence_raw: Optional[str]) -> Dict[str, Optional[str]]:
    ids: Dict[str, Optional[str]] = {
        "order_id": None,
        "payment_id": None,
        "settlement_id": None,
        "refund_id": None,
        "fee_id": None,
    }

    def _set(key: str, value: str) -> None:
        if key in ids and ids[key] is None:
            ids[key] = value

    for match in re.finditer(r"\b([A-Za-z]{2,}[-_][\w-]+)\b", description):
        token = match.group(1)
        kind = _classify_id(token)
        if kind:
            _set(kind, token)

    # description pattern: "STATUS for RECORD_ID"
    if " for " in description:
        tail = description.split(" for ", 1)[1].strip()
        if tail:
            token = tail.split()[0]
            kind = _classify_id(token)
            if kind:
                _set(kind, token)
            elif re.match(r"^[\w\-]+$", token):
                # Unknown token — do not invent type; leave unset.
                pass

    evidence_obj = _load_evidence_object(evidence_raw)
    for key in ("matched_with",):
        value = evidence_obj.get(key)
        if isinstance(value, str) and value:
            kind = _classify_id(value)
            if kind:
                _set(kind, value)

    source_type = evidence_obj.get("source_record_type")
    related_type = evidence_obj.get("related_record_type")
    # Prefer typed slots from evidence when description token was the primary record.
    if " for " in description:
        token = description.split(" for ", 1)[1].strip().split()[0]
        if source_type and token:
            _set(f"{source_type}_id", token)

    candidates = _load_candidates(evidence_raw)
    for candidate in candidates:
        rid = str(candidate.get("record_id") or "")
        if not rid:
            continue
        kind = _classify_id(rid)
        if kind:
            _set(kind, rid)

    return ids


def _load_evidence_object(evidence_raw: Optional[str]) -> Dict[str, Any]:
    if not evidence_raw:
        return {}
    try:
        parsed = json.loads(evidence_raw)
    except Exception:
        return {}
    if isinstance(parsed, dict):
        return parsed
    return {}


def _load_candidates(evidence_raw: Optional[str]) -> List[Dict[str, Any]]:
    if not evidence_raw:
        return []
    try:
        parsed = json.loads(evidence_raw)
    except Exception:
        return []
    if isinstance(parsed, list):
        return [row for row in parsed if isinstance(row, dict)]
    if isinstance(parsed, dict):
        candidates = parsed.get("candidates")
        if isinstance(candidates, list):
            return [row for row in candidates if isinstance(row, dict)]
        return [parsed]
    return []


def _evidence_tags(candidates: List[Dict[str, Any]], exception_type: str, evidence_obj: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    pair_type = evidence_obj.get("pair_type")
    if pair_type:
        tags.append(f"pair_type:{pair_type}")
    for candidate in candidates:
        features = candidate.get("features") or {}
        if isinstance(features, dict):
            for key, value in features.items():
                if value in (True, 1, "1", "true", "True") or (isinstance(value, (int, float)) and float(value) > 0):
                    tags.append(str(key))
                elif isinstance(value, str) and value and key.endswith("_match"):
                    tags.append(key)
        score = candidate.get("score")
        if score is not None:
            tags.append(f"candidate_score:{score}")
    if not tags:
        tags.append(exception_type.lower())
    seen = set()
    out: List[str] = []
    for tag in tags:
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out[:12]


def build_evidence_packet(record: ExceptionRecord) -> EvidencePacket:
    profile = classify(record.exception_type)
    evidence_obj = _load_evidence_object(record.evidence)
    ids = _parse_record_ids(record.description or "", record.evidence)
    candidates = _load_candidates(record.evidence)

    # Never invent monetary amounts. Only difference is persisted today as `amount`.
    difference = _as_money_str(record.amount)

    return EvidencePacket(
        exception_type=record.exception_type or "UNKNOWN_EXCEPTION",
        exception_id=f"EX-{record.id}",
        status=record.status,
        deterministic_reason=record.exception_type or "UNKNOWN_EXCEPTION",
        confidence=_confidence_float(record.confidence),
        difference=difference,
        pair_type=str(evidence_obj["pair_type"]) if evidence_obj.get("pair_type") else None,
        order_id=ids.get("order_id"),
        payment_id=ids.get("payment_id"),
        settlement_id=ids.get("settlement_id"),
        refund_id=ids.get("refund_id"),
        fee_id=ids.get("fee_id"),
        matched_with=str(evidence_obj["matched_with"]) if evidence_obj.get("matched_with") else None,
        order_amount=None,
        payment_amount=None,
        evidence=_evidence_tags(candidates, record.exception_type or "UNKNOWN_EXCEPTION", evidence_obj),
        description=(record.description or "")[:240],
        possible_root_causes=list(profile.get("root_causes") or []),
        recommended_action=record.recommended_action or str(profile.get("recommended_action") or ""),
    )
