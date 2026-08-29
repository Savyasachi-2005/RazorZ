from __future__ import annotations

from typing import Any, Dict, List

from app.ai.schemas import AIAssistError, AIAssistResult, AssistMode, EvidencePacket
from app.copilot.schemas import CopilotAnswer, CopilotContext, DataPoint


def _label(exception_type: str) -> str:
    """AMOUNT_MISMATCH -> amount mismatch, for readable prose."""
    return str(exception_type).replace("_", " ").lower()


def _count_of(count: Any, noun: str) -> str:
    """"1 payment" / "2 payments" — counts are quoted from evidence, never recomputed."""
    number = int(count or 0)
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


class MockProvider:
    """Deterministic local provider — no API key required.

    Covers order/payment and multi-record (settlement/refund/fee) exception types
    with type-specific playbooks. Never invents missing amounts.
    """

    name = "mock"

    def assist(self, packet: EvidencePacket, mode: AssistMode = "full_analysis") -> AIAssistResult:
        if mode not in {"full_analysis", "suggest_note", "investigation_steps"}:
            raise AIAssistError(f"unsupported assist mode: {mode}", code="unsupported_mode")

        result = self._for_type(packet)
        if mode == "suggest_note":
            return result
        if mode == "investigation_steps":
            return AIAssistResult(
                likely_cause=result.likely_cause,
                explanation="Investigation checklist generated from deterministic exception evidence.",
                investigation_steps=result.investigation_steps,
                suggested_action=result.suggested_action,
                suggested_review_note=result.suggested_review_note,
                ai_confidence=min(result.ai_confidence, 0.75),
            )
        return result

    def copilot(self, context: CopilotContext) -> CopilotAnswer:
        """Deterministic grounded answer built only from tool evidence."""
        evidence: Dict[str, Any] = {
            result.tool: result.data for result in context.tool_results if result.ok
        }
        if not evidence:
            return CopilotAnswer(
                answer="That information is not available in the current RAZORZ data.",
                key_findings=[],
                data_points=[],
                sources_used=[],
                confidence=0.3,
            )

        builders = {
            "financial_position": self._copilot_financial,
            "exception_analysis": self._copilot_exceptions,
            "exception_detail": self._copilot_exception_detail,
            "record_investigation": self._copilot_record,
            "audit_history": self._copilot_audit,
            "unsettled_payments": self._copilot_unsettled,
            "settlement_analysis": self._copilot_settlement,
            "cross_source_analysis": self._copilot_cross_source,
        }
        builder = builders.get(context.intent, self._copilot_reconciliation)
        answer, findings, points = builder(evidence, context.question)
        return CopilotAnswer(
            answer=answer,
            key_findings=findings,
            data_points=[DataPoint(label=label, value=value) for label, value in points],
            sources_used=list(evidence.keys()),
            confidence=0.72,
        )

    def _copilot_reconciliation(self, evidence: Dict[str, Any], question: str = ""):
        summary = evidence.get("get_reconciliation_summary", {})
        total = summary.get("total_records", 0)
        if not total:
            return (
                "No reconciliation run has been recorded yet, so there is no match rate to report.",
                [],
                [],
            )
        rate = summary.get("match_rate", "0.00%")
        findings = [
            f"{summary.get('matched', 0)} of {total} relationships matched cleanly ({rate}).",
            f"{summary.get('review_required', 0)} need human review — the engine found a candidate "
            "but not enough certainty to post it.",
            f"{summary.get('exceptions', 0)} are hard exceptions with no acceptable match.",
        ]
        top_types = summary.get("top_exception_types", [])
        for item in top_types[:3]:
            findings.append(f"{item['count']} × {_label(item['type'])} in the open queue.")

        answer = (
            f"{summary.get('matched', 0)} of {total} relationships reconciled cleanly ({rate}); "
            f"{summary.get('unresolved_count', 0)} are still open. Every unmatched record is held "
            "back deliberately rather than posted on a guess."
        )
        if top_types:
            drivers = ", ".join(_label(item["type"]) for item in top_types[:2])
            answer += f" The open queue is dominated by {drivers}."
        exposure_hint = evidence.get("search_exceptions", {}).get("total_amount")
        if exposure_hint:
            answer += f" Those breaks account for INR {exposure_hint} of difference."
        points = [
            ("Match rate", str(rate)),
            ("Total records", str(total)),
            ("Review required", str(summary.get("review_required", 0))),
            ("Exceptions", str(summary.get("exceptions", 0))),
        ]
        exposure = evidence.get("get_financial_summary", {}).get("unresolved_exposure")
        if exposure is not None:
            points.append(("Unresolved exposure", f"INR {exposure}"))
        return answer, findings, points

    def _copilot_financial(self, evidence: Dict[str, Any], question: str = ""):
        financial = evidence.get("get_financial_summary", {})
        if not financial:
            return ("That information is not available in the current RAZORZ data.", [], [])
        findings = [
            f"Payments collected: INR {financial.get('total_payments', '0.00')} across {financial.get('payment_count', 0)} payments.",
            f"Refunds: INR {financial.get('total_refunds', '0.00')}.",
            f"Fees: INR {financial.get('total_fees', '0.00')}.",
            f"Settlements: INR {financial.get('total_settlements', '0.00')}.",
        ]
        lowered = (question or "").lower()
        exposure_asked = any(
            word in lowered for word in ("exposure", "unresolved", "at risk", "tied up")
        )
        headline = (
            f"Across all ingested records, INR {financial.get('total_payments', '0.00')} was collected, "
            f"INR {financial.get('total_refunds', '0.00')} refunded, and "
            f"INR {financial.get('total_fees', '0.00')} paid in fees."
        )
        exposure_line = (
            f"INR {financial.get('unresolved_exposure', '0.00')} of recorded reconciliation "
            f"difference sits in {financial.get('open_exception_count', 0)} OPEN exceptions "
            "— resolved and rejected exceptions are excluded, and this is exposure under review "
            "rather than confirmed loss."
        )
        # Lead with the figure the question actually asked about.
        answer = f"{exposure_line} {headline}" if exposure_asked else f"{headline} {exposure_line}"
        points = [
            ("Total payments", f"INR {financial.get('total_payments', '0.00')}"),
            ("Total refunds", f"INR {financial.get('total_refunds', '0.00')}"),
            ("Total fees", f"INR {financial.get('total_fees', '0.00')}"),
            ("Total settlements", f"INR {financial.get('total_settlements', '0.00')}"),
            ("Payments − settlements", f"INR {financial.get('payments_minus_settlements', '0.00')}"),
            (
                "Unresolved exposure (OPEN only)",
                f"INR {financial.get('unresolved_exposure', '0.00')}",
            ),
        ]
        return answer, findings, points

    def _copilot_exceptions(self, evidence: Dict[str, Any], question: str = ""):
        search = evidence.get("search_exceptions", {})
        if not search:
            return self._copilot_reconciliation(evidence, question)
        count = search.get("match_count", 0)
        if not count:
            return ("No exceptions match those filters in the current RAZORZ data.", [], [])

        breakdown: List[Dict[str, Any]] = search.get("type_breakdown", [])
        total_amount = search.get("total_amount", "0.00")
        highest = search.get("highest_amount_exception")

        status_filter = (search.get("filters") or {}).get("status")
        scope = f"{count} {status_filter} exception(s)" if status_filter else f"{count} record(s)"

        # Explain WHY these records broke, driven by the dominant exception types.
        answer_parts = [
            f"{scope} could not be auto-matched, representing INR {total_amount} in recorded "
            "reconciliation difference — exposure under review, not confirmed loss."
        ]
        if breakdown:
            top = breakdown[0]
            answer_parts.append(
                f"The biggest driver is {_label(top['type'])} — {top['count']} of them "
                f"({top['share_of_count']} of the queue), because {top['meaning'][0].lower()}{top['meaning'][1:]}"
            )
            if len(breakdown) > 1:
                second = breakdown[1]
                answer_parts.append(
                    f"Next is {_label(second['type'])} ({second['count']}), where "
                    f"{second['meaning'][0].lower()}{second['meaning'][1:]}"
                )
            answer_parts.append(f"Typical causes: {', '.join(top['likely_causes'][:3])}.")
        if highest:
            answer_parts.append(f"The largest single exposure is {highest}.")

        findings: List[str] = []
        for item in breakdown:
            findings.append(
                f"{item['count']} × {_label(item['type'])} — INR {item['total_amount']} "
                f"({item['share_of_amount']} of the recorded difference, {item['queue_percentage']}% "
                f"of the queue). {item['meaning']} Next step: {item['recommended_action']}."
            )

        rows: List[Dict[str, Any]] = search.get("exceptions", [])
        if rows:
            findings.append(
                "Highest-value items to open first: "
                + ", ".join(
                    f"{row['exception_id']} (INR {row.get('amount') or '0.00'}, {_label(row['exception_type'])})"
                    for row in rows[:3]
                )
                + "."
            )

        points = [
            ("Unmatched records", str(count)),
            ("Recorded difference", f"INR {total_amount}"),
        ]
        status_mix = search.get("status_mix") or {}
        if len(status_mix) > 1:
            points.append(
                (
                    "Lifecycle mix",
                    ", ".join(f"{state} {number}" for state, number in status_mix.items()),
                )
            )
        if breakdown:
            points.append(("Top driver", f"{_label(breakdown[0]['type'])} ({breakdown[0]['count']})"))
        if highest:
            points.append(("Largest exposure", str(highest)))
        return " ".join(answer_parts), findings, points

    def _copilot_exception_detail(self, evidence: Dict[str, Any], question: str = ""):
        detail = evidence.get("get_exception", {})
        if not detail or not detail.get("found"):
            return (
                "That exception was not found in the current RAZORZ data.",
                [],
                [],
            )

        audit = evidence.get("get_audit_events") or {}
        semantics = detail.get("status_semantics") or {}
        status = semantics.get("status") or detail.get("status")
        exception_id = detail.get("exception_id")
        lowered = (question or "").lower()

        answer_parts: List[str] = []

        # The user's premise can disagree with the recorded state. Correct it first:
        # REJECTED and RESOLVED are closed states, not "unresolved".
        premise_words = ("unresolved", "still open", "not resolved", "pending", "why is it open")
        # The meaning reads "REJECTED — ..."; drop the label so it isn't repeated.
        meaning = str(semantics.get("meaning") or "")
        clause = meaning.split("— ", 1)[1] if "— " in meaning else meaning
        if semantics.get("is_unresolved") is False and any(
            word in lowered for word in premise_words
        ):
            answer_parts.append(f"{exception_id} is currently {status}, not OPEN — {clause}")
        else:
            answer_parts.append(f"{exception_id} is currently {status} — {clause}")

        # Why the system flagged it in the first place.
        origin = detail.get("origin_decision")
        origin_meaning = detail.get("origin_decision_meaning")
        flagged = (
            f"It was originally sent for review because the engine classified it as "
            f"{detail.get('exception_type')}: {detail.get('exception_meaning') or ''}".strip()
        )
        if origin and origin_meaning:
            flagged += f" The reconciliation decision was {origin} — {origin_meaning}."
        answer_parts.append(flagged)

        findings = [
            f"Current status: {status} — {semantics.get('meaning')}",
            f"Why it required review: {detail.get('exception_type')} — "
            f"{detail.get('exception_meaning') or 'classified by the deterministic engine.'}",
        ]

        points = [
            ("Exception", str(exception_id)),
            ("Current status", str(status)),
            ("Why it required review", str(detail.get("exception_type"))),
        ]

        # Financial impact only when the backend actually recorded an amount.
        amount = detail.get("amount")
        if amount is not None:
            answer_parts.append(
                f"The recorded difference is INR {amount} ({detail.get('amount_basis')})."
            )
            findings.append(f"Financial impact: INR {amount} recorded difference.")
            points.append(("Recorded difference", f"INR {amount}"))
        else:
            findings.append("Financial impact: no amount is recorded for this exception.")

        confidence = detail.get("confidence")
        if confidence is not None:
            findings.append(f"Deterministic reconciliation confidence: {confidence}")
            points.append(("Deterministic reconciliation confidence", str(confidence)))

        # Human decision, kept separate from AI assistance and from system detection.
        human_event = audit.get("latest_human_decision")
        decision = semantics.get("human_decision")
        if decision:
            actor = (human_event or {}).get("actor") or semantics.get("decided_by") or "a reviewer"
            sentence = f"A human reviewer ({actor}) {decision} this exception during manual review."
            note = (human_event or {}).get("note") or detail.get("reviewer_note")
            if note:
                sentence += f" Their recorded reason: {note}"
            answer_parts.append(sentence)
            findings.append(f"Human decision: {decision} by {actor}.")
            points.append(("Human decision", f"{decision} by {actor}"))
        else:
            answer_parts.append(
                "No human decision has been recorded yet, so it remains pending review."
            )
            findings.append("Human decision: none recorded — still pending human review.")

        if audit.get("ai_assistance_count"):
            findings.append(
                f"AI assistance was requested {audit['ai_assistance_count']} time(s). AI advises only; "
                "it never changes an exception's status."
            )

        events = audit.get("events") or []
        if events:
            findings.append(
                "Audit trail: "
                + " → ".join(
                    f"{row.get('created_at') or 'n/a'} {row.get('category')} "
                    f"({row.get('actor')} {row.get('action')})"
                    for row in reversed(events[:4])
                )
            )
        elif decision:
            findings.append("Audit trail: no matching audit event was recorded for this exception.")

        # Next step only where the current state leaves something to do.
        if semantics.get("state") == "pending":
            answer_parts.append(f"Recommended next step: {detail.get('recommended_action')}.")
        else:
            answer_parts.append(
                f"No further action is required — the {status} decision is final and RAZORZ does "
                "not support reopening a closed exception."
            )
        return " ".join(answer_parts), findings, points

    def _copilot_unsettled(self, evidence: Dict[str, Any], question: str = ""):
        data = evidence.get("get_unsettled_payments", {})
        if not data:
            return ("That information is not available in the current RAZORZ data.", [], [])
        if not data.get("payment_count"):
            return (
                "No payments are stored yet, so there is nothing to check for settlement.",
                [],
                [],
            )

        missing = data.get("unsettled_count", 0)
        rows = data.get("unsettled_payments", [])
        if not missing:
            answer = (
                f"No. All {_count_of(data.get('settled_count'), 'stored payment')} have a matching "
                f"settlement record ({data.get('settlement_coverage_percentage')}% coverage)."
            )
            return answer, [], [("Unsettled payments", "0")]

        answer = (
            f"Yes — {missing} of {data.get('payment_count', 0)} payments have no settlement record "
            f"({data.get('unsettled_share_percentage')}% of payments), covering INR "
            f"{data.get('unsettled_amount', '0.00')}. That is money captured from customers with no "
            f"payout linked to it yet; the remaining "
            f"{_count_of(data.get('settled_count'), 'payment')} "
            f"{'is' if int(data.get('settled_count') or 0) == 1 else 'are'} settled "
            f"({data.get('settlement_coverage_percentage')}% coverage)."
        )
        findings = [
            f"{row['payment_id']} — INR {row['amount']}"
            + (f" dated {row['date']}" if row.get("date") else "")
            for row in rows[:5]
        ]
        if missing > len(rows):
            findings.append(f"Showing the {len(rows)} largest of {missing} unsettled payments.")
        points = [
            ("Unsettled payments", str(missing)),
            ("Unsettled amount", f"INR {data.get('unsettled_amount', '0.00')}"),
            ("Settled payments", str(data.get("settled_count", 0))),
            ("Settlement coverage", f"{data.get('settlement_coverage_percentage')}%"),
        ]
        return answer, findings, points

    def _copilot_settlement(self, evidence: Dict[str, Any], question: str = ""):
        data = evidence.get("get_settlement_summary", {})
        if not data:
            return ("That information is not available in the current RAZORZ data.", [], [])

        missing = data.get("payments_missing_from_settlements", 0)
        orphans = data.get("orphan_settlement_count", 0)
        mismatches = data.get("amount_mismatch_count", 0)
        open_count = data.get("open_exception_count", 0)

        if not data.get("settlement_count") and not missing:
            return (
                "No settlement records are stored yet, so settlement reconciliation cannot be assessed.",
                [],
                [],
            )

        answer = (
            f"{_count_of(data.get('settlement_count'), 'settlement')} totalling INR "
            f"{data.get('settlement_amount', '0.00')} are stored, and "
            f"{data.get('settlement_coverage_percentage')}% of payments have a settlement linked."
        )
        drivers = []
        if missing:
            drivers.append(
                f"{_count_of(missing, 'payment')} worth INR "
                f"{data.get('payments_missing_amount', '0.00')} missing from settlements"
            )
        if orphans:
            drivers.append(
                f"{_count_of(orphans, 'settlement')} worth INR "
                f"{data.get('orphan_settlement_amount', '0.00')} pointing at no stored payment"
            )
        if mismatches:
            drivers.append(
                f"{_count_of(mismatches, 'settlement')} disagreeing on amount with their payment"
            )
        answer += (
            f" The settlement-side problems are: {'; '.join(drivers)}."
            if drivers
            else " No settlement-side breaks are recorded."
        )
        if open_count:
            answer += (
                f" {_count_of(open_count, 'payment↔settlement exception')} "
                f"{'is' if open_count == 1 else 'are'} still OPEN, carrying INR "
                f"{data.get('open_exception_amount', '0.00')} of recorded reconciliation difference "
                "(exposure under review, not confirmed loss)."
            )

        findings: List[str] = []
        if missing:
            findings.append(
                f"Payments missing from settlements: {missing} — INR "
                f"{data.get('payments_missing_amount', '0.00')}"
            )
        for row in data.get("orphan_settlements", [])[:3]:
            findings.append(
                f"Orphan settlement {row['settlement_id']} — INR {row['amount']} with no stored payment"
            )
        for row in data.get("amount_mismatches", [])[:3]:
            findings.append(
                f"Settlement mismatch {row['settlement_id']} vs payment {row['payment_id']}: "
                f"INR {row['settlement_amount']} settled against INR {row['payment_amount']} paid "
                f"(difference INR {row['difference']})"
            )
        for item in data.get("open_exception_types", [])[:3]:
            findings.append(f"{item['count']} × {_label(item['type'])} open on payment↔settlement.")

        points = [
            ("Settlements", str(data.get("settlement_count", 0))),
            ("Settlement amount", f"INR {data.get('settlement_amount', '0.00')}"),
            ("Payments not settled", str(missing)),
            ("Orphan settlements", str(orphans)),
            ("Amount mismatches", str(mismatches)),
            ("Settlement coverage", f"{data.get('settlement_coverage_percentage')}%"),
        ]
        return answer, findings, points

    def _copilot_cross_source(self, evidence: Dict[str, Any], question: str = ""):
        data = evidence.get("get_cross_source_summary", {})
        pairs: List[Dict[str, Any]] = data.get("pairs", []) if data else []
        if not pairs:
            return ("That information is not available in the current RAZORZ data.", [], [])

        active = [pair for pair in pairs if pair["reconciled_pairs"] or pair["open_exception_count"]]
        if not active:
            return (
                "No reconciliation relationships have been evaluated yet, so there is nothing to "
                "compare across payments, settlements, refunds, and fees.",
                [],
                [],
            )

        worst_type = data.get("worst_pair")
        worst = next((pair for pair in pairs if pair["pair_type"] == worst_type), None)

        total_open = int(data.get("total_open_exceptions") or 0)
        answer_parts = [
            f"Across the four relationships, {_count_of(total_open, 'exception')} "
            f"{'is' if total_open == 1 else 'are'} OPEN carrying INR "
            f"{data.get('total_open_amount', '0.00')} of recorded reconciliation difference."
        ]
        if worst:
            answer_parts.append(
                f"The worst area is {worst['label']}, where {worst['expectation']}: "
                f"{_count_of(worst['open_exception_count'], 'open exception')} worth INR "
                f"{worst['open_exception_amount']}"
                + (
                    f", driven mostly by {_label(worst['top_open_type'])}."
                    if worst.get("top_open_type")
                    else "."
                )
            )
        for pair in sorted(active, key=lambda item: -item["open_exception_count"])[1:3]:
            if pair["open_exception_count"] or pair["unmatched_count"]:
                answer_parts.append(
                    f"{pair['label']}: {pair['open_exception_count']} open, "
                    f"{pair['unmatched_count']} unmatched of {pair['reconciled_pairs']} evaluated."
                )

        findings: List[str] = []
        for pair in pairs:
            detail = (
                f"{pair['label']} — {pair['matched']} matched of {pair['reconciled_pairs']} evaluated"
                + (f" ({pair['match_percentage']}%)" if pair.get("match_percentage") else "")
                + f", {_count_of(pair['open_exception_count'], 'open exception')} worth INR "
                f"{pair['open_exception_amount']}"
            )
            if pair.get("missing_link_count"):
                detail += f", {pair['missing_link_count']} {pair['missing_link_label']}"
            if pair.get("orphan_record_count"):
                detail += f", {_count_of(pair['orphan_record_count'], 'orphaned record')}"
            findings.append(detail + ".")

        points = [
            (
                pair["label"],
                f"{pair['open_exception_count']} open · INR {pair['open_exception_amount']}",
            )
            for pair in pairs
        ]
        if worst:
            points.append(("Worst relationship", str(worst["label"])))
        return " ".join(answer_parts), findings, points

    def _copilot_record(self, evidence: Dict[str, Any], question: str = ""):
        rels = evidence.get("get_record_relationships", {})
        if not rels or not rels.get("found"):
            return (
                "That record is not present in the current RAZORZ data, so its relationships are unavailable.",
                [],
                [],
            )
        record = rels.get("record", {})
        related = rels.get("related", {})
        findings = [
            f"{record.get('record_type', 'record')} {record.get('record_id')} · INR {record.get('amount')} · {record.get('status', 'n/a')}"
        ]
        for label, value in related.items():
            if isinstance(value, list):
                findings.append(f"{label}: {len(value)} linked")
            elif value:
                findings.append(f"{label}: {value.get('record_id')}")
            else:
                findings.append(f"{label}: none found")
        answer = (
            f"{record.get('record_id')} is a stored {record.get('record_type')} of INR {record.get('amount')} "
            f"dated {record.get('date')}. Its linked records are listed below."
        )
        points = [
            ("Record", str(record.get("record_id"))),
            ("Type", str(record.get("record_type"))),
            ("Amount", f"INR {record.get('amount')}"),
        ]
        return answer, findings, points

    def _copilot_audit(self, evidence: Dict[str, Any], question: str = ""):
        audit = evidence.get("get_audit_events", {})
        events = audit.get("events", []) if audit else []
        if not events:
            return ("No audit events are available in the current RAZORZ data.", [], [])
        findings = [
            f"{row.get('created_at') or 'n/a'} · {row.get('category')} · {row.get('actor')} · "
            f"{row.get('action')} → {row.get('new_state')}"
            for row in events[:5]
        ]
        answer = (
            f"The audit trail holds {len(events)} recent event(s), separated into system detection, "
            "AI assistance, and human decisions; the most recent are listed below."
        )
        points = [("Events returned", str(len(events)))]
        human_event = audit.get("latest_human_decision")
        if human_event:
            points.append(
                (
                    "Latest human decision",
                    f"{human_event.get('action')} by {human_event.get('actor')}",
                )
            )
        return answer, findings, points

    def _ids(self, packet: EvidencePacket) -> dict[str, str]:
        return {
            "order": packet.order_id or "the order",
            "payment": packet.payment_id or "the payment",
            "settlement": packet.settlement_id or "the settlement",
            "refund": packet.refund_id or "the refund",
            "fee": packet.fee_id or "the fee",
            "diff": packet.difference or "unavailable",
            "pair": packet.pair_type or "unknown pair",
            "ex": packet.exception_id or "exception",
        }

    def _for_type(self, packet: EvidencePacket) -> AIAssistResult:
        et = (packet.exception_type or "UNKNOWN_EXCEPTION").upper()
        ids = self._ids(packet)
        handlers = {
            "AMOUNT_MISMATCH": self._amount_mismatch,
            "PAYMENT_MISSING": self._payment_missing,
            "ORPHAN_PAYMENT": self._orphan_payment,
            "AMBIGUOUS_MATCH": self._ambiguous_match,
            "SETTLEMENT_MISSING": self._settlement_missing,
            "SETTLEMENT_AMOUNT_MISMATCH": self._settlement_amount_mismatch,
            "ORPHAN_SETTLEMENT": self._orphan_settlement,
            "DUPLICATE_SETTLEMENT": self._duplicate_settlement,
            "REFUND_MISSING": self._refund_missing,
            "ORPHAN_REFUND": self._orphan_refund,
            "REFUND_EXCESSIVE": self._refund_excessive,
            "REFUND_MISMATCH": self._refund_mismatch,
            "MULTIPLE_REFUNDS": self._multiple_refunds,
            "FEE_MISSING": self._fee_missing,
            "FEE_DIFFERENCE": self._fee_difference,
            "FEE_UNEXPECTED": self._fee_unexpected,
            "DATE_MISMATCH": self._date_mismatch,
        }
        handler = handlers.get(et)
        if handler:
            return handler(packet, ids)
        return self._generic(packet, ids, et)

    def _amount_mismatch(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Amount difference between linked order and payment (fee, partial capture, or source total mismatch).",
            explanation=(
                f"Deterministic engine flagged AMOUNT_MISMATCH for {ids['order']} / {ids['payment']}. "
                f"Reported difference is {ids['diff']}. Identity signals may align, but amounts do not — "
                "human review is required before posting."
            ),
            investigation_steps=[
                f"Verify captured payment amount for {ids['payment']}",
                f"Confirm whether difference {ids['diff']} represents a fee or partial capture",
                "Compare source/order totals without changing books",
                "Confirm transaction status in the payment source",
            ],
            suggested_action="Keep exception open until source amounts are verified; do not auto-post.",
            suggested_review_note=(
                f"Payment linked to {ids['order']} shows a difference of {ids['diff']} versus the order amount. "
                "This may be a partial capture or fee adjustment. Verify payment capture and source "
                "totals before resolving; do not change source financial amounts from this review."
            ),
            ai_confidence=0.86,
        )

    def _payment_missing(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Order has no matching payment capture in the ingested set.",
            explanation=(
                f"Engine marked PAYMENT_MISSING for {ids['order']}. No payment pair cleared deterministic matching. "
                "This is a confirmed break until a payment is located or the order is marked unpaid."
            ),
            investigation_steps=[
                f"Search payment source for reference related to {ids['order']}",
                "Check ingestion lag / missing payment export",
                "Confirm whether the order was cancelled or unpaid",
                "Document evidence before resolve/reject",
            ],
            suggested_action="Confirm capture in the payment source or mark as unpaid after human verification.",
            suggested_review_note=(
                f"No matching payment found for {ids['order']}. Investigate capture status and ingestion lag "
                "before resolving. Review does not invent a payment or alter ledger amounts."
            ),
            ai_confidence=0.9,
        )

    def _orphan_payment(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Payment has no matching order in the ingested set.",
            explanation=(
                f"Engine marked ORPHAN_PAYMENT for {ids['payment']}. Cash appears without a paired order reference."
            ),
            investigation_steps=[
                f"Search orders for reference linked to {ids['payment']}",
                "Check for wrong reference / duplicate payment",
                "Consider unallocated cash treatment if no order exists",
                "Confirm source payment status",
            ],
            suggested_action="Search for a missing order or treat as unallocated cash after human confirmation.",
            suggested_review_note=(
                f"Payment {ids['payment']} has no matching order. Investigate missing order ingestion or wrong "
                "reference before resolution. Do not invent an order record from this review."
            ),
            ai_confidence=0.88,
        )

    def _ambiguous_match(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Multiple candidates score too closely for safe auto-resolution.",
            explanation=(
                f"Engine marked AMBIGUOUS_MATCH (deterministic confidence {packet.confidence:.2f}). "
                "Candidate scores are too close; a human must select the correct pair."
            ),
            investigation_steps=[
                "Compare candidate references and customers side by side",
                "Review amount proximity without overriding engine totals",
                "Check for duplicate invoices or shared customers",
                "Select the correct candidate explicitly before resolve",
            ],
            suggested_action="Human selects the correct candidate; do not auto-post.",
            suggested_review_note=(
                "Multiple payment/order candidates score closely. Reviewed candidates manually and "
                "documented the chosen pair. AI suggestion is advisory only; engine confidence remains "
                f"{packet.confidence:.2f}."
            ),
            ai_confidence=0.8,
        )

    def _settlement_missing(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Captured payment has no settlement record in the ingested set.",
            explanation=(
                f"Engine marked SETTLEMENT_MISSING for payment {ids['payment']} "
                f"(pair {ids['pair']}). Difference/exposure reported as {ids['diff']}."
            ),
            investigation_steps=[
                f"Search settlement batches for payment {ids['payment']}",
                "Check for delayed settlement or excluded payout",
                "Confirm payment capture status before expecting settlement",
                "Document batch id / settlement date from the source system",
            ],
            suggested_action="Confirm settlement batch inclusion or delayed payout; do not invent a settlement.",
            suggested_review_note=(
                f"No settlement found for payment {ids['payment']}. Exposure {ids['diff']}. "
                "Investigating settlement lag / batch exclusion. No settlement amount invented."
            ),
            ai_confidence=0.88,
        )

    def _settlement_amount_mismatch(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Settlement amount differs from the captured payment amount.",
            explanation=(
                f"Engine linked payment {ids['payment']} to settlement {ids['settlement']} but amounts diverge. "
                f"Reported difference is {ids['diff']}."
            ),
            investigation_steps=[
                f"Compare payment {ids['payment']} capture vs settlement {ids['settlement']}",
                f"Check whether difference {ids['diff']} is fee netting or partial settlement",
                "Confirm currency and FX if applicable",
                "Keep books unchanged until source confirmation",
            ],
            suggested_action="Compare fees/FX/partial settlement before adjusting books.",
            suggested_review_note=(
                f"Settlement {ids['settlement']} differs from payment {ids['payment']} by {ids['diff']}. "
                "Possible fee netting or partial settlement. Verify source totals before resolve."
            ),
            ai_confidence=0.87,
        )

    def _orphan_settlement(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Settlement references an unknown payment.",
            explanation=(
                f"Engine marked ORPHAN_SETTLEMENT for {ids['settlement']}. "
                f"No matching payment was found in the ingested set. Exposure {ids['diff']}."
            ),
            investigation_steps=[
                f"Search payments for the reference on settlement {ids['settlement']}",
                "Check wrong payment reference / ingestion gap",
                "Confirm whether settlement should be reversed",
                "Do not invent a payment to close the break",
            ],
            suggested_action="Locate the missing payment or reverse the orphan settlement after human confirmation.",
            suggested_review_note=(
                f"Settlement {ids['settlement']} has no matching payment (exposure {ids['diff']}). "
                "Investigating missing payment ingestion or bad reference."
            ),
            ai_confidence=0.86,
        )

    def _duplicate_settlement(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="More than one settlement is linked to the same payment.",
            explanation=(
                f"Engine marked DUPLICATE_SETTLEMENT for payment {ids['payment']} "
                f"(primary settlement {ids['settlement']}). Risk of double-posting cash."
            ),
            investigation_steps=[
                f"List all settlements referencing payment {ids['payment']}",
                "Identify which settlement is authoritative",
                "Check for batch replay or duplicate ingestion",
                "Do not post both settlements",
            ],
            suggested_action="Confirm which settlement is valid; do not double-post cash.",
            suggested_review_note=(
                f"Multiple settlements linked to payment {ids['payment']}. "
                f"Primary candidate {ids['settlement']}. Need human selection before posting."
            ),
            ai_confidence=0.9,
        )

    def _refund_missing(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="An expected refund was not found for the payment.",
            explanation=(
                f"Engine marked REFUND_MISSING for payment {ids['payment']}. "
                f"Expected refund exposure reported as {ids['diff']}."
            ),
            investigation_steps=[
                f"Confirm refund was initiated for payment {ids['payment']}",
                "Check refund status in the payment source",
                "Look for reference mismatch or ingestion lag",
                "Do not invent a refund record",
            ],
            suggested_action="Confirm refund initiation in the payment source before resolving.",
            suggested_review_note=(
                f"Expected refund missing for payment {ids['payment']} (exposure {ids['diff']}). "
                "Checking source refund status / ingestion lag."
            ),
            ai_confidence=0.87,
        )

    def _orphan_refund(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Refund references an unknown payment.",
            explanation=(
                f"Engine marked ORPHAN_REFUND for {ids['refund']}. "
                f"No matching payment in the ingested set. Exposure {ids['diff']}."
            ),
            investigation_steps=[
                f"Search payments for reference on refund {ids['refund']}",
                "Check wrong payment_reference",
                "Treat as unallocated refund outflow if payment cannot be found",
                "Do not invent a payment",
            ],
            suggested_action="Locate the payment or treat as unallocated refund outflow after confirmation.",
            suggested_review_note=(
                f"Refund {ids['refund']} has no matching payment (exposure {ids['diff']}). "
                "Investigating missing payment / bad reference."
            ),
            ai_confidence=0.86,
        )

    def _refund_excessive(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Refund total exceeds the captured payment amount.",
            explanation=(
                f"Engine marked REFUND_EXCESSIVE for payment {ids['payment']} "
                f"(refund {ids['refund']}). Excess reported as {ids['diff']}. "
                "This is financially unsafe to auto-resolve."
            ),
            investigation_steps=[
                f"Sum all refunds against payment {ids['payment']}",
                f"Confirm captured payment amount vs refund {ids['refund']}",
                f"Investigate whether excess {ids['diff']} is a duplicate refund or wrong amount",
                "Block posting until totals are corrected in the source",
            ],
            suggested_action="Block posting until refund totals are corrected; do not auto-resolve.",
            suggested_review_note=(
                f"Refund(s) on payment {ids['payment']} exceed capture by {ids['diff']} "
                f"(refund id {ids['refund']}). Likely duplicate or incorrect refund amount. "
                "Source correction required before resolve."
            ),
            ai_confidence=0.92,
        )

    def _refund_mismatch(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Refund is related to the payment but amounts or expectations conflict.",
            explanation=(
                f"Engine marked REFUND_MISMATCH for payment {ids['payment']} / refund {ids['refund']}. "
                f"Difference {ids['diff']}."
            ),
            investigation_steps=[
                "Compare expected vs actual refund amounts from source",
                "Check partial refund intent",
                "Confirm currency consistency",
                "Keep books unchanged until verified",
            ],
            suggested_action="Compare expected vs actual refund before changing books.",
            suggested_review_note=(
                f"Refund {ids['refund']} conflicts with expectations for payment {ids['payment']} "
                f"(difference {ids['diff']}). Verify partial refund intent before resolve."
            ),
            ai_confidence=0.84,
        )

    def _multiple_refunds(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Multiple refunds exist against one payment within the captured amount.",
            explanation=(
                f"Engine marked MULTIPLE_REFUNDS for payment {ids['payment']} "
                f"(primary refund {ids['refund']}). Totals appear within capture but need human confirmation."
            ),
            investigation_steps=[
                f"List each refund against payment {ids['payment']}",
                "Confirm each partial refund is intentional",
                "Check for retry-after-failure duplicates",
                "Document reviewer selection before close",
            ],
            suggested_action="Verify each partial refund is intentional before closing.",
            suggested_review_note=(
                f"Multiple refunds on payment {ids['payment']} (e.g. {ids['refund']}). "
                "Within capture but needs human confirmation that each leg is intentional."
            ),
            ai_confidence=0.82,
        )

    def _fee_missing(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Expected processing fee was not found for the payment.",
            explanation=(
                f"Engine marked FEE_MISSING for payment {ids['payment']}. "
                f"Expected fee exposure reported as {ids['diff']}."
            ),
            investigation_steps=[
                f"Confirm fee schedule for payment {ids['payment']}",
                "Check whether fee was waived or netted into settlement",
                "Look for missing fee ingestion",
                "Do not invent a fee amount beyond the reported difference",
            ],
            suggested_action="Confirm fee schedule and settlement netting before resolving.",
            suggested_review_note=(
                f"Expected fee missing for payment {ids['payment']} (exposure {ids['diff']}). "
                "Checking waiver / settlement netting / ingestion gap."
            ),
            ai_confidence=0.85,
        )

    def _fee_difference(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Fee amount differs from the configured/expected fee.",
            explanation=(
                f"Engine linked payment {ids['payment']} to fee {ids['fee']} with a fee difference of {ids['diff']}."
            ),
            investigation_steps=[
                f"Compare fee {ids['fee']} to expected MDR/schedule for {ids['payment']}",
                f"Confirm whether difference {ids['diff']} is tax or rate change",
                "Check incorrect fee posting",
                "Do not overwrite source fee totals from this review",
            ],
            suggested_action="Compare fee schedule and MDR before adjusting books.",
            suggested_review_note=(
                f"Fee {ids['fee']} on payment {ids['payment']} differs from expected by {ids['diff']}. "
                "Verify schedule/MDR before resolve."
            ),
            ai_confidence=0.86,
        )

    def _fee_unexpected(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Fee is attached to an unknown payment or was not expected.",
            explanation=(
                f"Engine marked FEE_UNEXPECTED for fee {ids['fee']}. "
                f"No matching payment in the ingested set. Exposure {ids['diff']}."
            ),
            investigation_steps=[
                f"Search payments for reference on fee {ids['fee']}",
                "Check duplicate fee posting",
                "Confirm whether fee should be reversed",
                "Do not invent a payment",
            ],
            suggested_action="Locate the payment or reverse the unexpected fee after confirmation.",
            suggested_review_note=(
                f"Unexpected fee {ids['fee']} (exposure {ids['diff']}) with no matching payment. "
                "Investigating bad reference / duplicate fee."
            ),
            ai_confidence=0.85,
        )

    def _date_mismatch(self, packet: EvidencePacket, ids: dict[str, str]) -> AIAssistResult:
        return AIAssistResult(
            likely_cause="Related records appear linked but event dates diverge.",
            explanation=(
                f"Engine marked DATE_MISMATCH involving {ids['order']} / {ids['payment']}. "
                "Identity may be related; timing needs confirmation."
            ),
            investigation_steps=[
                "Compare event dates and timezones",
                "Check delayed capture or settlement lag",
                "Confirm correct accounting period",
                "Do not auto-post across periods without review",
            ],
            suggested_action="Confirm delayed capture or settlement lag before closing.",
            suggested_review_note=(
                f"Date mismatch on {ids['ex']}. Checking timezone / delayed capture before resolve."
            ),
            ai_confidence=0.78,
        )

    def _generic(self, packet: EvidencePacket, ids: dict[str, str], et: str) -> AIAssistResult:
        return AIAssistResult(
            likely_cause=f"{et.replace('_', ' ').title()} requires human investigation using deterministic evidence.",
            explanation=(
                f"Exception type {et} on {ids['ex']} (pair {ids['pair']}). "
                f"Identifiers present: payment={packet.payment_id or 'n/a'}, "
                f"settlement={packet.settlement_id or 'n/a'}, refund={packet.refund_id or 'n/a'}, "
                f"fee={packet.fee_id or 'n/a'}. Reported difference={ids['diff']}. "
                "Do not invent missing amounts or source records."
            ),
            investigation_steps=[
                "Review deterministic exception evidence and taxonomy guidance",
                "Confirm identifiers and difference values already present",
                "Gather source-system proof before changing status",
                "Leave unresolved if evidence remains incomplete",
            ],
            suggested_action=packet.recommended_action
            or "Leave unresolved until a reviewer provides evidence.",
            suggested_review_note=(
                f"Reviewed {ids['ex']} ({et}). Used available identifiers and difference {ids['diff']}. "
                "Did not invent amounts or source records."
            ),
            ai_confidence=0.55,
        )
