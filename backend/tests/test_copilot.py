from __future__ import annotations

import itertools
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.providers.gemini import GeminiProvider
from app.ai.providers.mock import MockProvider
from app.copilot import service as copilot_service
from app.copilot.router import detect_mutation_request, route
from app.copilot.schemas import (
    READ_ONLY_TOOLS,
    CopilotAnswer,
    CopilotContext,
    CopilotError,
    DataPoint,
    ToolResult,
)
from app.copilot.tools import TOOL_REGISTRY, run_tool
from app.main import app
from app.repositories.reconciliation_repository import ReconciliationRepository
from app.repositories.record_repository import RecordRepository
from app.exceptions.intelligence import classify
from app.services.reconciliation_service import run_reconciliation

DB = "sqlite://"

client = TestClient(app)


def _seed_book() -> None:
    """One clean order/payment/fee chain plus one amount mismatch."""
    run_reconciliation(
        [
            {
                "source": "synthetic",
                "record_type": "order",
                "record_id": "OR-CP-01",
                "reference": "REF-CP-01",
                "amount": "1000.00",
                "date": "2026-06-01",
                "customer": "CUST-CP-1",
            },
            {
                "source": "synthetic",
                "record_type": "payment",
                "record_id": "PM-CP-01",
                "reference": "REF-CP-01",
                "amount": "1000.00",
                "date": "2026-06-01",
                "customer": "CUST-CP-1",
                "metadata": {"expects_fee": True, "expected_fee_amount": "20.00"},
            },
            {
                "source": "synthetic",
                "record_type": "fee",
                "record_id": "FE-CP-01",
                "reference": "PM-CP-01",
                "payment_reference": "PM-CP-01",
                "amount": "20.00",
                "date": "2026-06-02",
                "customer": "",
            },
            {
                "source": "synthetic",
                "record_type": "order",
                "record_id": "OR-CP-02",
                "reference": "REF-CP-02",
                "amount": "800.00",
                "date": "2026-06-03",
                "customer": "CUST-CP-2",
            },
            {
                "source": "synthetic",
                "record_type": "payment",
                "record_id": "PM-CP-02",
                "reference": "REF-CP-02",
                "amount": "500.00",
                "date": "2026-06-03",
                "customer": "CUST-CP-2",
            },
        ],
        database_url=DB,
    )


# ------------------------------------------------------- tool security


def test_copilot_exposes_only_read_only_tools() -> None:
    assert set(TOOL_REGISTRY) == set(READ_ONLY_TOOLS)


@pytest.mark.parametrize(
    "forbidden",
    [
        "resolve_exception",
        "reject_exception",
        "update_payment",
        "update_order",
        "create_refund",
        "create_payment",
        "modify_settlement",
        "modify_fee",
        "execute_sql",
        "run_query",
    ],
)
def test_no_mutation_or_sql_tool_exists(forbidden: str) -> None:
    assert forbidden not in TOOL_REGISTRY
    assert run_tool(forbidden, {}, database_url=DB).ok is False


def test_copilot_cannot_mutate_financial_records() -> None:
    _seed_book()
    before = RecordRepository(DB).financial_totals()
    copilot_service.ask("How much was collected?", database_url=DB)
    copilot_service.ask("Show unresolved exceptions", database_url=DB)
    assert RecordRepository(DB).financial_totals() == before


def test_copilot_refuses_to_resolve_or_reject() -> None:
    _seed_book()
    for question in ("Resolve EX-1", "Reject EX-1 please", "Refund this payment"):
        result = copilot_service.ask(question, database_url=DB)
        assert result["refused"] is True
        assert result["llm_used"] is False
        assert result["tools_used"] == []
        assert "read-only" in result["answer"]["answer"].lower()


def test_mutation_detection_patterns() -> None:
    assert detect_mutation_request("resolve EX-9") is True
    assert detect_mutation_request("create a refund for this") is True
    assert detect_mutation_request("how many exceptions are open?") is False


# ------------------------------------------------------- intent routing


@pytest.mark.parametrize(
    "question,expected_tool",
    [
        ("How many transactions were reconciled?", "get_reconciliation_summary"),
        ("What is today's match rate?", "get_reconciliation_summary"),
        ("Show me unresolved exceptions", "search_exceptions"),
        ("How much was refunded?", "get_financial_summary"),
        ("Why is EX-12 unresolved?", "get_exception"),
        ("What happened to payment PM-CP-01?", "get_record_relationships"),
        ("Show the audit history", "get_audit_events"),
    ],
)
def test_routing_selects_relevant_tool(question: str, expected_tool: str) -> None:
    plan = route(question)
    assert expected_tool in [name for name, _ in plan.tool_calls]


def test_routing_does_not_call_every_tool() -> None:
    plan = route("Why is EX-12 unresolved?")
    names = [name for name, _ in plan.tool_calls]
    assert "get_financial_summary" not in names
    assert len(names) <= 2


def test_routing_extracts_exception_id() -> None:
    plan = route("Why is EX-123 unresolved?")
    assert ("get_exception", {"exception_id": "123"}) in plan.tool_calls


def test_routing_ignores_plain_word_payment() -> None:
    # "payment" must not be mistaken for a record id.
    plan = route("how much money came in from payments?")
    assert "get_record_relationships" not in [name for name, _ in plan.tool_calls]


# ------------------------------------------------------- individual tools


def test_get_reconciliation_summary_tool() -> None:
    _seed_book()
    result = run_tool("get_reconciliation_summary", {}, database_url=DB)
    assert result.ok
    assert result.data["total_records"] > 0
    assert result.data["match_rate"].endswith("%")
    assert "top_exception_types" in result.data


def test_search_exceptions_tool_filters() -> None:
    _seed_book()
    result = run_tool("search_exceptions", {"status": "OPEN", "limit": 5}, database_url=DB)
    assert result.ok
    assert result.data["match_count"] >= 1
    assert len(result.data["exceptions"]) <= 5
    assert all(row["status"] == "OPEN" for row in result.data["exceptions"])


def test_search_exceptions_returns_explanatory_breakdown() -> None:
    _seed_book()
    data = run_tool("search_exceptions", {"limit": 10}, database_url=DB).data

    breakdown = data["type_breakdown"]
    assert breakdown, "breakdown drives the Copilot explanation"
    top = breakdown[0]
    # Each bucket must carry the deterministic math plus taxonomy context to explain it.
    assert {"type", "count", "total_amount", "share_of_count", "share_of_amount"} <= set(top)
    assert top["meaning"] and top["recommended_action"] and top["likely_causes"]
    assert top["share_of_count"].endswith("%")
    assert sum(item["count"] for item in breakdown) <= data["match_count"]

    # Rows are ordered by exposure so the highest-value break is shown first.
    amounts = [Decimal(row["amount"]) for row in data["exceptions"]]
    assert amounts == sorted(amounts, reverse=True)
    if data["exceptions"]:
        assert data["highest_amount_exception"] == data["exceptions"][0]["exception_id"]


def test_small_share_is_not_rounded_to_zero() -> None:
    from app.copilot.tools import _share

    assert _share(Decimal("0.01"), Decimal("1000.00")) == "<1%"
    assert _share(Decimal("500"), Decimal("1000")) == "50%"
    assert _share(Decimal("5"), Decimal("0")) == "0%"


def test_exception_answer_explains_cause_not_just_rows() -> None:
    _seed_book()
    result = copilot_service.ask("Why are transactions facing exceptions?", database_url=DB)
    answer = result["answer"]["answer"].lower()
    # The narrative must say what the dominant break means, not only list ids.
    assert "driver" in answer or "could not be auto-matched" in answer
    joined = " ".join(result["answer"]["key_findings"]).lower()
    assert "next step" in joined


def test_search_exceptions_min_amount_uses_decimal() -> None:
    _seed_book()
    result = run_tool("search_exceptions", {"min_amount": "100"}, database_url=DB)
    assert result.ok
    for row in result.data["exceptions"]:
        assert Decimal(row["amount"]) >= Decimal("100")


def test_get_exception_tool_and_missing_record() -> None:
    _seed_book()
    listed = run_tool("search_exceptions", {"limit": 1}, database_url=DB).data["exceptions"]
    found = run_tool("get_exception", {"exception_id": listed[0]["id"]}, database_url=DB)
    assert found.ok and found.data["found"] is True

    missing = run_tool("get_exception", {"exception_id": 987654}, database_url=DB)
    assert missing.ok and missing.data["found"] is False


def test_get_audit_events_tool() -> None:
    _seed_book()
    result = run_tool("get_audit_events", {"limit": 5}, database_url=DB)
    assert result.ok
    assert result.data["returned"] <= 5


def test_search_records_tool() -> None:
    _seed_book()
    result = run_tool("search_records", {"record_type": "payment", "limit": 5}, database_url=DB)
    assert result.ok
    assert all(row["record_type"] == "payment" for row in result.data["records"])


def test_get_record_relationships_tool() -> None:
    _seed_book()
    result = run_tool("get_record_relationships", {"record_id": "PM-CP-01"}, database_url=DB)
    assert result.ok and result.data["found"] is True
    related = result.data["related"]
    assert related["order"]["record_id"] == "OR-CP-01"
    assert [row["record_id"] for row in related["fees"]] == ["FE-CP-01"]


def test_get_record_relationships_missing_record() -> None:
    result = run_tool("get_record_relationships", {"record_id": "PM-DOES-NOT-EXIST"}, database_url=DB)
    assert result.ok and result.data["found"] is False


def test_get_financial_summary_is_decimal_exact() -> None:
    """Totals must add up in Decimal — measured as a delta, since the test DB is shared."""
    before = run_tool("get_financial_summary", {}, database_url=DB).data

    # Amounts chosen so float arithmetic would drift (0.1 + 0.2 style).
    run_reconciliation(
        [
            {
                "source": "synthetic",
                "record_type": "payment",
                "record_id": "PM-DEC-01",
                "reference": "REF-DEC-01",
                "amount": "0.10",
                "date": "2026-07-01",
                "customer": "CUST-DEC",
            },
            {
                "source": "synthetic",
                "record_type": "payment",
                "record_id": "PM-DEC-02",
                "reference": "REF-DEC-02",
                "amount": "0.20",
                "date": "2026-07-01",
                "customer": "CUST-DEC",
            },
        ],
        database_url=DB,
    )

    after = run_tool("get_financial_summary", {}, database_url=DB).data
    delta = Decimal(after["total_payments"]) - Decimal(before["total_payments"])
    assert delta == Decimal("0.30")
    assert after["total_payments"] == str(Decimal(after["total_payments"]).quantize(Decimal("0.01")))
    assert Decimal(after["unresolved_exposure"]) >= Decimal("0.00")
    assert after["currency"] == "INR"


# ------------------------------------------------- parameter validation


@pytest.mark.parametrize(
    "tool,params",
    [
        ("search_exceptions", {"status": "DROP TABLE"}),
        ("search_exceptions", {"priority": "P9"}),
        ("search_exceptions", {"pair_type": "order_bank"}),
        ("search_exceptions", {"limit": 5000}),
        ("search_records", {"record_type": "bank_account"}),
        ("get_exception", {"exception_id": "not-a-number"}),
        ("get_exception", {}),
        ("get_record_relationships", {}),
    ],
)
def test_invalid_tool_parameters_are_rejected(tool: str, params: dict) -> None:
    result = run_tool(tool, params, database_url=DB)
    assert result.ok is False
    assert result.error


# ------------------------------------------------------- copilot answers


def test_reconciliation_summary_question() -> None:
    _seed_book()
    result = copilot_service.ask("Summarize today's reconciliation", database_url=DB)
    assert "get_reconciliation_summary" in result["tools_used"]
    assert result["read_only"] is True
    assert result["answer"]["answer"]


def test_exception_search_question() -> None:
    _seed_book()
    result = copilot_service.ask("Show me unresolved exceptions", database_url=DB)
    assert "search_exceptions" in result["tools_used"]
    assert result["answer"]["data_points"]


def test_specific_exception_question() -> None:
    _seed_book()
    listed = run_tool("search_exceptions", {"limit": 1}, database_url=DB).data["exceptions"]
    result = copilot_service.ask(f"Why is {listed[0]['exception_id']} unresolved?", database_url=DB)
    assert result["intent"] == "exception_detail"
    assert result["evidence"]["get_exception"]["found"] is True


def test_financial_summary_question() -> None:
    _seed_book()
    result = copilot_service.ask("How much money is unresolved?", database_url=DB)
    assert "get_financial_summary" in result["tools_used"]
    labels = {point["label"] for point in result["answer"]["data_points"]}
    assert "Unresolved exposure (OPEN only)" in labels


def test_record_relationship_question() -> None:
    _seed_book()
    result = copilot_service.ask("What happened to payment PM-CP-01?", database_url=DB)
    assert result["intent"] == "record_investigation"
    assert result["evidence"]["get_record_relationships"]["found"] is True


def test_unknown_data_is_not_invented() -> None:
    _seed_book()
    result = copilot_service.ask("Tell me the bank balance", database_url=DB)
    assert result["llm_used"] is False
    assert result["tools_used"] == []
    answer = result["answer"]["answer"].lower()
    assert "does not contain bank balance" in answer
    assert "can't determine" in answer
    assert result["answer"]["sources_used"] == []


def test_empty_question_is_rejected() -> None:
    with pytest.raises(CopilotError) as exc:
        copilot_service.ask("   ", database_url=DB)
    assert exc.value.code == "empty_question"


def test_deterministic_count_avoids_llm_call() -> None:
    _seed_book()
    result = copilot_service.ask("How many open exceptions?", database_url=DB)
    assert result["llm_used"] is False
    assert result["provider"] == "deterministic"
    assert result["answer"]["confidence"] == 1.0


def test_sources_used_only_reports_real_tools() -> None:
    _seed_book()
    result = copilot_service.ask("Summarize today's reconciliation", database_url=DB)
    assert set(result["answer"]["sources_used"]).issubset(set(result["tools_used"]))


# ------------------------------------------------------- provider paths


def test_mock_provider_workflow_without_api_key() -> None:
    _seed_book()
    result = copilot_service.ask(
        "Why did the reconciliation rate fall?", database_url=DB, provider_name="mock"
    )
    assert result["provider"] == "mock"
    assert result["answer"]["key_findings"]


def test_mock_provider_reports_missing_evidence() -> None:
    context = CopilotContext(question="anything", intent="reconciliation_summary", tool_results=[])
    answer = MockProvider().copilot(context)
    assert "not available" in answer.answer.lower()
    assert answer.sources_used == []


def test_gemini_provider_requires_api_key() -> None:
    provider = GeminiProvider(api_key="")
    with pytest.raises(CopilotError) as exc:
        provider.copilot(CopilotContext(question="q", intent="reconciliation_summary"))
    assert exc.value.code == "provider_unavailable"


def test_provider_failure_is_contained(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_book()

    class BrokenProvider:
        name = "broken"

        def copilot(self, context: CopilotContext) -> CopilotAnswer:
            raise RuntimeError("network down")

    monkeypatch.setattr(copilot_service, "get_provider", lambda name=None: BrokenProvider())
    with pytest.raises(CopilotError) as exc:
        copilot_service.ask("Why did the match rate fall?", database_url=DB)
    assert exc.value.code == "provider_unavailable"


def test_invalid_llm_response_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_book()

    class MalformedProvider:
        name = "malformed"

        def copilot(self, context: CopilotContext) -> CopilotAnswer:
            # Mirrors a provider returning a payload that fails schema validation.
            raise CopilotError("Copilot response failed schema validation", code="invalid_response")

    monkeypatch.setattr(copilot_service, "get_provider", lambda name=None: MalformedProvider())
    with pytest.raises(CopilotError) as exc:
        copilot_service.ask("Why did the match rate fall?", database_url=DB)
    assert exc.value.code == "invalid_response"


def test_provider_without_copilot_support_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_book()

    class LegacyProvider:
        name = "legacy"

    monkeypatch.setattr(copilot_service, "get_provider", lambda name=None: LegacyProvider())
    with pytest.raises(CopilotError) as exc:
        copilot_service.ask("Why did the match rate fall?", database_url=DB)
    assert exc.value.code == "provider_unavailable"


# ------------------------------------------------------- grounding guard


def test_find_ungrounded_claims_detects_invented_values() -> None:
    tool_results = [
        ToolResult(tool="get_financial_summary", data={"total_payments": "1500.00"}),
    ]
    honest = CopilotAnswer(answer="Collected INR 1500.00 in total.", confidence=0.5)
    assert copilot_service.find_ungrounded_claims(honest, tool_results) == []

    invented = CopilotAnswer(answer="Collected INR 98765.43 in total.", confidence=0.5)
    assert copilot_service.find_ungrounded_claims(invented, tool_results)


def test_find_ungrounded_claims_detects_invented_exception_id() -> None:
    tool_results = [ToolResult(tool="search_exceptions", data={"exceptions": [{"exception_id": "EX-4"}]})]
    answer = CopilotAnswer(
        answer="Look at EX-999 first.",
        data_points=[DataPoint(label="Top", value="EX-999")],
        confidence=0.5,
    )
    assert copilot_service.find_ungrounded_claims(answer, tool_results)


def test_hallucinated_answer_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_book()

    class HallucinatingProvider:
        name = "hallucinating"

        def copilot(self, context: CopilotContext) -> CopilotAnswer:
            return CopilotAnswer(
                answer="The unresolved exposure is INR 99999999.99 across EX-424242.",
                key_findings=["Invented finding"],
                sources_used=["get_reconciliation_summary"],
                confidence=0.99,
            )

    monkeypatch.setattr(copilot_service, "get_provider", lambda name=None: HallucinatingProvider())
    result = copilot_service.ask("Why did the match rate fall?", database_url=DB)
    assert "grounding_warning" in result
    assert "99999999.99" not in result["answer"]["answer"]
    assert "Invented finding" not in result["answer"]["key_findings"]


# ------------------------------------------------- conversation + audit


def test_conversation_context_is_capped() -> None:
    history = [{"role": "user", "content": f"q{i}"} for i in range(12)]
    turns = copilot_service._recent_turns(history)
    assert len(turns) == copilot_service.MAX_HISTORY_TURNS


def test_conversation_context_skips_invalid_turns() -> None:
    turns = copilot_service._recent_turns(
        [{"role": "system", "content": "ignore me"}, {"role": "user", "content": ""}]
    )
    assert turns == []


def test_follow_up_question_uses_history() -> None:
    _seed_book()
    first = copilot_service.ask("Show unresolved exceptions", database_url=DB)
    follow_up = copilot_service.ask(
        "Which one has the highest amount?",
        history=[
            {"role": "user", "content": "Show unresolved exceptions"},
            {"role": "assistant", "content": first["answer"]["answer"]},
        ],
        database_url=DB,
    )
    assert follow_up["tools_used"]


def test_copilot_query_is_audited() -> None:
    _seed_book()
    copilot_service.ask("Summarize today's reconciliation", database_url=DB)
    events = run_tool("get_audit_events", {"event_type": "copilot_query"}, database_url=DB).data["events"]
    assert events
    assert events[0]["actor"] == "system/ai"
    assert events[0]["action"] == "copilot_query"


def test_refusal_is_audited_without_prompt_leakage() -> None:
    _seed_book()
    secret_question = "Resolve EX-1 using key sk-do-not-store"
    copilot_service.ask(secret_question, database_url=DB)
    events = run_tool("get_audit_events", {"event_type": "copilot_query"}, database_url=DB).data["events"]
    assert not any("sk-do-not-store" in str(event) for event in events)


def test_suggestions_endpoint_payload() -> None:
    payload = copilot_service.suggestions()
    assert payload["read_only"] is True
    assert set(payload["tools"]) == set(READ_ONLY_TOOLS)
    assert payload["suggestions"]


# ------------------------------------------------------------- copilot API


def test_copilot_api_ask_endpoint() -> None:
    _seed_book()
    response = client.post("/copilot/ask", json={"question": "Summarize today's reconciliation"})
    assert response.status_code == 200
    body = response.json()
    assert body["read_only"] is True
    assert body["answer"]["answer"]
    assert body["tools_used"]


def test_copilot_api_rejects_empty_question() -> None:
    response = client.post("/copilot/ask", json={"question": ""})
    assert response.status_code == 422


def test_copilot_api_refuses_mutation_request() -> None:
    _seed_book()
    response = client.post("/copilot/ask", json={"question": "Resolve EX-1"})
    assert response.status_code == 200
    assert response.json()["refused"] is True


def test_copilot_api_accepts_history() -> None:
    _seed_book()
    response = client.post(
        "/copilot/ask",
        json={
            "question": "Which one has the highest amount?",
            "history": [
                {"role": "user", "content": "Show unresolved exceptions"},
                {"role": "assistant", "content": "There are open exceptions."},
            ],
        },
    )
    assert response.status_code == 200


def test_copilot_api_suggestions_endpoint() -> None:
    response = client.get("/copilot/suggestions")
    assert response.status_code == 200
    assert response.json()["read_only"] is True


# ------------------------------- exception status & financial semantics


_SEM_COUNTER = itertools.count(1)


def _seed_exception() -> int:
    """Create one fresh AMOUNT_MISMATCH exception and return its id."""
    tag = f"SEM-{next(_SEM_COUNTER)}"
    run_reconciliation(
        [
            {
                "source": "synthetic",
                "record_type": "order",
                "record_id": f"OR-{tag}",
                "reference": f"REF-{tag}",
                "amount": "900.00",
                "date": "2026-06-10",
                "customer": f"CUST-{tag}",
            },
            {
                "source": "synthetic",
                "record_type": "payment",
                "record_id": f"PM-{tag}",
                "reference": f"REF-{tag}",
                "amount": "600.00",
                "date": "2026-06-10",
                "customer": f"CUST-{tag}",
            },
        ],
        database_url=DB,
    )
    repo = ReconciliationRepository(DB)
    for row in repo.list_exceptions(limit=500):
        if f"OR-{tag}" in (row.description or ""):
            return row.id
    raise AssertionError(f"no exception seeded for {tag}")


def _reviewed_exception(action: str, note: str) -> int:
    exception_id = _seed_exception()
    ReconciliationRepository(DB).review_exception(
        exception_id, action=action, actor="reviewer@razorz.test", note=note
    )
    return exception_id


def _ask(question: str, history: list[dict[str, str]] | None = None) -> dict:
    return copilot_service.ask(question, history=history, database_url=DB, provider_name="mock")


def test_open_exception_reports_pending_state() -> None:
    payload = run_tool("get_exception", {"exception_id": _seed_exception()}, database_url=DB).data
    semantics = payload["status_semantics"]
    assert semantics["status"] == "OPEN"
    assert semantics["state"] == "pending"
    assert semantics["is_unresolved"] is True
    assert semantics["human_decision"] is None


def test_review_required_origin_is_distinct_from_status() -> None:
    payload = run_tool("get_exception", {"exception_id": _seed_exception()}, database_url=DB).data
    # REVIEW_REQUIRED is the engine decision, not the exception lifecycle state.
    assert payload["origin_decision"] == "REVIEW_REQUIRED"
    assert payload["status"] == "OPEN"
    assert payload["origin_decision_meaning"]


def test_resolved_exception_is_final_and_credits_the_human() -> None:
    exception_id = _reviewed_exception("resolve", "Fee accounted for; books agree.")
    payload = run_tool("get_exception", {"exception_id": exception_id}, database_url=DB).data
    semantics = payload["status_semantics"]
    assert semantics["status"] == "RESOLVED"
    assert semantics["state"] == "final"
    assert semantics["is_unresolved"] is False
    assert semantics["human_decision"] == "resolved"


def test_rejected_exception_is_not_unresolved() -> None:
    exception_id = _reviewed_exception("reject", "Duplicate break, no action needed.")
    payload = run_tool("get_exception", {"exception_id": exception_id}, database_url=DB).data
    semantics = payload["status_semantics"]
    assert semantics["status"] == "REJECTED"
    assert semantics["is_unresolved"] is False
    assert semantics["human_decision"] == "rejected"
    assert "not pending" in semantics["meaning"]


def test_why_unresolved_on_rejected_exception_corrects_the_premise() -> None:
    exception_id = _reviewed_exception("reject", "Break created by a duplicate feed.")
    result = _ask(f"Why is EX-{exception_id} unresolved?")
    answer = result["answer"]["answer"]
    assert f"EX-{exception_id} is currently REJECTED, not OPEN" in answer
    assert "originally sent for review" in answer
    findings = " ".join(result["answer"]["key_findings"])
    assert "Current status: REJECTED" in findings


def test_why_rejected_uses_human_review_evidence() -> None:
    note = "Rejected because the counterparty feed was duplicated."
    exception_id = _reviewed_exception("reject", note)
    answer = _ask(f"Why was EX-{exception_id} rejected?")["answer"]["answer"]
    assert "human reviewer" in answer
    assert "rejected this exception" in answer
    assert note in answer


def test_follow_up_question_reuses_prior_exception_context() -> None:
    exception_id = _reviewed_exception("resolve", "Confirmed against the bank file.")
    history = [
        {"role": "user", "content": f"Why is EX-{exception_id} unresolved?"},
        {"role": "assistant", "content": f"EX-{exception_id} is currently RESOLVED."},
    ]
    result = _ask("What did the reviewer do?", history=history)
    assert result["intent"] == "exception_detail"
    assert "get_exception" in result["answer"]["sources_used"]
    assert "resolved this exception" in result["answer"]["answer"]


def test_follow_up_without_prior_context_does_not_invent_an_exception() -> None:
    result = _ask("What did the reviewer do?")
    assert result["intent"] != "exception_detail"


def test_how_many_open_exceptions_is_a_deterministic_count() -> None:
    _seed_exception()
    result = _ask("How many exceptions are open?")
    payload = run_tool("search_exceptions", {"status": "OPEN"}, database_url=DB).data
    assert result["llm_used"] is False
    assert f"{payload['match_count']} exceptions are currently OPEN." == result["answer"]["answer"]


def test_aggregate_exposure_uses_neutral_terminology_and_open_filter() -> None:
    _seed_exception()
    result = _ask("How much financial exposure is currently unresolved?")
    financial = run_tool("get_financial_summary", {}, database_url=DB).data
    assert "OPEN exceptions only" in financial["unresolved_exposure_basis"]
    blob = " ".join(
        [result["answer"]["answer"], *result["answer"]["key_findings"]]
    ).lower()
    for misleading in ("unexplained loss", "lost revenue", "cash missing"):
        assert misleading not in blob


def test_exception_type_meanings_stay_distinct() -> None:
    provider = MockProvider()
    meanings = {}
    for exception_type in (
        "AMOUNT_MISMATCH",
        "PAYMENT_MISSING",
        "ORPHAN_PAYMENT",
        "AMBIGUOUS_MATCH",
        "FEE_MISSING",
    ):
        evidence = {
            "get_exception": {
                "found": True,
                "exception_id": "EX-1",
                "exception_type": exception_type,
                "status": "OPEN",
                "status_semantics": {
                    "status": "OPEN",
                    "meaning": "Open — no final human decision has been recorded yet.",
                    "state": "pending",
                    "is_unresolved": True,
                    "human_decision": None,
                },
                "exception_meaning": classify(exception_type)["meaning"],
                "amount": "100.00",
                "amount_basis": "recorded reconciliation difference",
                "confidence": 0.8,
                "recommended_action": "review the pair",
            }
        }
        answer, _, _ = provider._copilot_exception_detail(evidence, "Why did this happen?")
        assert exception_type in answer
        meanings[exception_type] = classify(exception_type)["meaning"]
    # No two types collapse into the same generic explanation.
    assert len(set(meanings.values())) == len(meanings)


def test_missing_amount_is_never_invented() -> None:
    provider = MockProvider()
    evidence = {
        "get_exception": {
            "found": True,
            "exception_id": "EX-9",
            "exception_type": "PAYMENT_MISSING",
            "status": "OPEN",
            "status_semantics": {
                "status": "OPEN",
                "meaning": "Open — no final human decision has been recorded yet.",
                "state": "pending",
                "is_unresolved": True,
                "human_decision": None,
            },
            "exception_meaning": classify("PAYMENT_MISSING")["meaning"],
            "amount": None,
            "recommended_action": "chase the missing payment",
        }
    }
    answer, findings, points = provider._copilot_exception_detail(evidence, "How much was involved?")
    assert "INR" not in answer
    assert "no amount is recorded" in " ".join(findings)
    assert not [point for point in points if "difference" in point[0].lower()]


def test_missing_audit_event_is_reported_not_fabricated() -> None:
    provider = MockProvider()
    evidence = {
        "get_exception": {
            "found": True,
            "exception_id": "EX-9",
            "exception_type": "AMOUNT_MISMATCH",
            "status": "REJECTED",
            "status_semantics": {
                "status": "REJECTED",
                "meaning": "Rejected — a human reviewer rejected this exception. It is closed, not pending.",
                "state": "final",
                "is_unresolved": False,
                "human_decision": "rejected",
                "decided_by": None,
            },
            "exception_meaning": classify("AMOUNT_MISMATCH")["meaning"],
            "amount": "10.00",
            "amount_basis": "recorded reconciliation difference",
            "recommended_action": "review",
        },
        "get_audit_events": {"returned": 0, "events": [], "latest_human_decision": None},
    }
    answer, findings, _ = provider._copilot_exception_detail(evidence, "Why was it rejected?")
    assert "a reviewer" in answer
    assert "no matching audit event was recorded" in " ".join(findings)


def test_audit_events_separate_human_ai_and_system_actions() -> None:
    exception_id = _reviewed_exception("reject", "Not a genuine break.")
    payload = run_tool(
        "get_audit_events", {"entity_id": f"EX-{exception_id}"}, database_url=DB
    ).data
    assert payload["returned"] >= 1
    assert payload["latest_human_decision"]["category"] == "human_decision"
    assert payload["latest_human_decision"]["note"] == "Not a genuine break."
    assert all(event["category"] != "ai_assistance" for event in payload["events"])


def test_unsupported_financial_question_is_refused_honestly() -> None:
    result = _ask("What is the bank balance?")
    assert result["intent"] == "unsupported_data"
    assert result["llm_used"] is False
    assert "can't determine" in result["answer"]["answer"]


def test_copilot_cannot_mutate_exception_status() -> None:
    exception_id = _seed_exception()
    result = _ask(f"Resolve EX-{exception_id} for me")
    assert result["refused"] is True
    after = run_tool("get_exception", {"exception_id": exception_id}, database_url=DB).data
    assert after["status"] == "OPEN"


def test_percentages_are_backend_derived_and_exact() -> None:
    _seed_exception()
    payload = run_tool("search_exceptions", {"status": "OPEN"}, database_url=DB).data
    breakdown = payload["type_breakdown"]
    assert breakdown
    for bucket in breakdown:
        # Two decimals, computed with Decimal in the backend — the LLM never divides.
        assert Decimal(bucket["queue_percentage"]) == Decimal(bucket["queue_percentage"]).quantize(
            Decimal("0.01")
        )
        assert Decimal("0") <= Decimal(bucket["queue_percentage"]) <= Decimal("100")
    total = sum(Decimal(bucket["queue_percentage"]) for bucket in breakdown)
    assert total <= Decimal("100.05")


# ------------------------------- source-specific tools (settlement / cross-source)


# `sqlite://` is one shared in-memory database for the whole module, so these tests use
# their own files: exact counts here must not depend on what other tests seeded.
_SRC_DIR = Path(tempfile.mkdtemp(prefix="razorz_copilot_src_"))
SRC_DB = f"sqlite:///{(_SRC_DIR / 'sources.db').as_posix()}"
EMPTY_DB = f"sqlite:///{(_SRC_DIR / 'empty.db').as_posix()}"

_SOURCES_SEEDED = False


def _seed_sources() -> None:
    """Clean order/payment/settlement chain, an unsettled payment, and an orphan settlement.

    Seeded once, so counts stay exact across the tests in this section.
    """
    global _SOURCES_SEEDED
    if _SOURCES_SEEDED:
        return
    _SOURCES_SEEDED = True
    run_reconciliation(
        [
            {
                "source": "synthetic",
                "record_type": "order",
                "record_id": "OR-SRC-1",
                "reference": "REF-SRC-1",
                "amount": "1000.00",
                "date": "2026-07-01",
                "customer": "CUST-SRC-1",
            },
            {
                "source": "synthetic",
                "record_type": "payment",
                "record_id": "PM-SRC-1",
                "reference": "REF-SRC-1",
                "amount": "1000.00",
                "date": "2026-07-01",
                "customer": "CUST-SRC-1",
            },
            {
                "source": "synthetic",
                "record_type": "settlement",
                "record_id": "ST-SRC-1",
                "reference": "PM-SRC-1",
                "payment_reference": "PM-SRC-1",
                "amount": "980.00",
                "date": "2026-07-02",
                "customer": "",
            },
            {
                "source": "synthetic",
                "record_type": "order",
                "record_id": "OR-SRC-2",
                "reference": "REF-SRC-2",
                "amount": "500.00",
                "date": "2026-07-03",
                "customer": "CUST-SRC-2",
            },
            {
                "source": "synthetic",
                "record_type": "payment",
                "record_id": "PM-SRC-2",
                "reference": "REF-SRC-2",
                "amount": "500.00",
                "date": "2026-07-03",
                "customer": "CUST-SRC-2",
            },
            {
                "source": "synthetic",
                "record_type": "settlement",
                "record_id": "ST-SRC-ORPHAN",
                "reference": "PM-SRC-NONE",
                "payment_reference": "PM-SRC-NONE",
                "amount": "77.00",
                "date": "2026-07-05",
                "customer": "",
            },
            {
                "source": "synthetic",
                "record_type": "refund",
                "record_id": "RF-SRC-ORPHAN",
                "reference": "PM-SRC-NONE",
                "payment_reference": "PM-SRC-NONE",
                "amount": "50.00",
                "date": "2026-07-06",
                "customer": "",
            },
        ],
        database_url=SRC_DB,
    )


def _src_ask(question: str) -> dict:
    return copilot_service.ask(question, database_url=SRC_DB, provider_name="mock")


def test_new_source_tools_are_registered_and_read_only() -> None:
    for name in ("get_unsettled_payments", "get_settlement_summary", "get_cross_source_summary"):
        assert name in TOOL_REGISTRY
        assert name in READ_ONLY_TOOLS


def test_get_unsettled_payments_returns_actual_payment_ids() -> None:
    _seed_sources()
    payload = run_tool("get_unsettled_payments", {}, database_url=SRC_DB).data
    ids = {row["payment_id"] for row in payload["unsettled_payments"]}
    # PM-SRC-2 has no settlement; PM-SRC-1 does.
    assert ids == {"PM-SRC-2"}
    assert payload["unsettled_count"] == 1
    assert payload["unsettled_amount"] == "500.00"
    assert payload["settled_count"] == 1
    assert payload["settled_amount"] == "1000.00"


def test_get_unsettled_payments_totals_are_exact_and_percentages_backend_derived() -> None:
    _seed_sources()
    payload = run_tool("get_unsettled_payments", {"limit": 1}, database_url=SRC_DB).data
    # Row cap must not change the aggregates.
    assert len(payload["unsettled_payments"]) == 1
    assert payload["settled_count"] + payload["unsettled_count"] == payload["payment_count"]
    coverage = Decimal(payload["settlement_coverage_percentage"])
    share = Decimal(payload["unsettled_share_percentage"])
    assert coverage + share == Decimal("100.00")


def test_get_unsettled_payments_handles_empty_database() -> None:
    payload = run_tool("get_unsettled_payments", {}, database_url=EMPTY_DB).data
    assert payload["payment_count"] == 0
    assert payload["unsettled_count"] == 0
    assert payload["unsettled_payments"] == []
    assert "note" in payload


def test_get_settlement_summary_covers_orphans_and_mismatches() -> None:
    _seed_sources()
    payload = run_tool("get_settlement_summary", {}, database_url=SRC_DB).data
    assert payload["settlement_count"] == 2
    assert payload["payments_missing_from_settlements"] == 1
    assert payload["orphan_settlement_count"] == 1
    assert payload["settlement_coverage_percentage"] == "50.00"
    orphan_ids = {row["settlement_id"] for row in payload["orphan_settlements"]}
    assert "ST-SRC-ORPHAN" in orphan_ids
    mismatches = {row["settlement_id"]: row for row in payload["amount_mismatches"]}
    # ST-SRC-1 settled 980.00 against a 1000.00 payment.
    assert mismatches["ST-SRC-1"]["difference"] == "20.00"
    assert payload["amount_mismatch_count"] >= 1
    assert "OPEN payment" in payload["unresolved_basis"]


def test_get_settlement_summary_handles_empty_database() -> None:
    payload = run_tool("get_settlement_summary", {}, database_url=EMPTY_DB).data
    assert payload["settlement_count"] == 0
    assert payload["orphan_settlements"] == []
    assert payload["amount_mismatches"] == []
    assert "note" in payload


def test_get_cross_source_summary_compares_all_four_relationships() -> None:
    _seed_sources()
    payload = run_tool("get_cross_source_summary", {}, database_url=SRC_DB).data
    pair_types = [pair["pair_type"] for pair in payload["pairs"]]
    assert pair_types == [
        "order_payment",
        "payment_settlement",
        "payment_refund",
        "payment_fee",
    ]
    by_type = {pair["pair_type"]: pair for pair in payload["pairs"]}
    # Order↔Payment reconciles cleanly here; the settlement side does not.
    assert by_type["order_payment"]["matched"] >= 2
    assert by_type["payment_settlement"]["open_exception_count"] >= 1
    assert by_type["payment_refund"]["orphan_record_count"] >= 1
    assert payload["worst_pair"] == "payment_settlement"


def test_get_cross_source_summary_handles_empty_database() -> None:
    payload = run_tool("get_cross_source_summary", {}, database_url=EMPTY_DB).data
    assert payload["run_id"] is None
    assert len(payload["pairs"]) == 4
    assert payload["total_open_exceptions"] == 0
    assert payload["note"]


@pytest.mark.parametrize(
    "question,expected_tool,expected_intent",
    [
        (
            "Are there any payments that have not been settled?",
            "get_unsettled_payments",
            "unsettled_payments",
        ),
        (
            "Which payments are unsettled?",
            "get_unsettled_payments",
            "unsettled_payments",
        ),
        (
            "How is settlement reconciliation looking?",
            "get_settlement_summary",
            "settlement_analysis",
        ),
        (
            "Are there any settlement mismatches?",
            "get_settlement_summary",
            "settlement_analysis",
        ),
        (
            "What are the biggest reconciliation problems across payments, settlements, refunds, and fees?",
            "get_cross_source_summary",
            "cross_source_analysis",
        ),
    ],
)
def test_source_questions_route_to_source_tools(
    question: str, expected_tool: str, expected_intent: str
) -> None:
    plan = route(question)
    assert plan.intent == expected_intent
    names = [name for name, _ in plan.tool_calls]
    assert names == [expected_tool]


def test_unsettled_question_answers_with_real_payment_ids() -> None:
    _seed_sources()
    result = _src_ask("Are there any payments that have not been settled?")
    assert result["answer"]["sources_used"] == ["get_unsettled_payments"]
    blob = " ".join([result["answer"]["answer"], *result["answer"]["key_findings"]])
    assert "PM-SRC-2" in blob
    # Not a generic reconciliation statistic.
    assert "match rate" not in blob.lower()


def test_settlement_question_explains_settlement_specific_breaks() -> None:
    _seed_sources()
    result = _src_ask("Are there any settlement mismatches?")
    blob = " ".join([result["answer"]["answer"], *result["answer"]["key_findings"]])
    assert "ST-SRC-ORPHAN" in blob or "orphan" in blob.lower()
    assert "ST-SRC-1" in blob
    assert "confirmed loss" in blob or "under review" in blob


def test_cross_source_question_compares_sources() -> None:
    _seed_sources()
    result = _src_ask(
        "What are the biggest reconciliation problems across payments, settlements, refunds, and fees?"
    )
    assert result["answer"]["sources_used"] == ["get_cross_source_summary"]
    blob = " ".join([result["answer"]["answer"], *result["answer"]["key_findings"]])
    for label in ("Order ↔ Payment", "Payment ↔ Settlement", "Payment ↔ Refund", "Payment ↔ Fee"):
        assert label in blob


def test_source_answers_stay_grounded() -> None:
    _seed_sources()
    for question in (
        "Are there any payments that have not been settled?",
        "How is settlement reconciliation looking?",
        "What are the biggest reconciliation problems across payments, settlements, refunds, and fees?",
    ):
        result = _src_ask(question)
        assert result["read_only"] is True
        assert result.get("grounding_warning") is None


def test_source_tools_do_not_mutate_records() -> None:
    _seed_sources()
    before = run_tool("get_financial_summary", {}, database_url=SRC_DB).data
    for name in ("get_unsettled_payments", "get_settlement_summary", "get_cross_source_summary"):
        assert run_tool(name, {}, database_url=SRC_DB).ok
    after = run_tool("get_financial_summary", {}, database_url=SRC_DB).data
    assert before == after


def test_copilot_api_has_no_mutation_routes() -> None:
    copilot_routes = {
        (route.path, method)
        for route in app.routes
        if getattr(route, "path", "").startswith("/copilot")
        for method in route.methods
    }
    # Only one POST exists, and it answers questions — nothing mutates.
    assert {path for path, _ in copilot_routes} == {"/copilot/suggestions", "/copilot/ask"}
    assert {(path, method) for path, method in copilot_routes if method not in {"GET", "HEAD"}} == {
        ("/copilot/ask", "POST")
    }
