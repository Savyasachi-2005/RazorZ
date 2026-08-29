from __future__ import annotations

from decimal import Decimal

import pytest

from app.ai.schemas import AIAssistError
from app.evaluation import comparison as comparison_module
from app.evaluation.comparison import (
    BASELINE_MODE,
    COMPARED_METRICS,
    DECISION_FIELDS,
    HYBRID_MODE,
    AdvisoryIntegrityError,
    apply_advisory_layer,
    assert_decisions_unchanged,
    compare_modes,
    run_comparison,
)
from app.evaluation.dataset import build_held_out_dataset
from app.reconciliation.engine import reconcile_records


@pytest.fixture(scope="module")
def report():
    return run_comparison()


def _signature(results):
    return sorted(tuple(str(row.get(field)) for field in DECISION_FIELDS) for row in results)


def test_both_modes_run_on_the_same_held_out_batch(report):
    assert report.baseline.mode == BASELINE_MODE
    assert report.hybrid.mode == HYBRID_MODE
    assert report.baseline.seed == report.hybrid.seed == report.seed
    assert report.baseline.record_count == report.hybrid.record_count
    assert report.baseline.label_count == report.hybrid.label_count
    assert report.baseline.prediction_count == report.hybrid.prediction_count


def test_advisory_layer_never_changes_a_decision():
    dataset = build_held_out_dataset()
    baseline = reconcile_records(dataset.engine_records())
    hybrid, stats = apply_advisory_layer(baseline)
    assert _signature(baseline) == _signature(hybrid)
    assert stats.decisions_changed == 0
    assert_decisions_unchanged(baseline, hybrid)


def test_integrity_guard_detects_a_tampered_decision():
    dataset = build_held_out_dataset()
    baseline = reconcile_records(dataset.engine_records())
    hybrid, _ = apply_advisory_layer(baseline)
    tampered = [dict(row) for row in hybrid]
    exception_index = next(
        index for index, row in enumerate(tampered) if row["status"] in comparison_module.EXCEPTION_STATUSES
    )
    tampered[exception_index]["status"] = "MATCHED"
    with pytest.raises(AdvisoryIntegrityError):
        assert_decisions_unchanged(baseline, tampered)

    dropped = hybrid[:-1]
    with pytest.raises(AdvisoryIntegrityError):
        assert_decisions_unchanged(baseline, dropped)


def test_advisory_is_attached_only_to_exceptions_and_carries_no_decision():
    dataset = build_held_out_dataset()
    baseline = reconcile_records(dataset.engine_records())
    hybrid, stats = apply_advisory_layer(baseline)
    assert stats.exceptions_seen > 0
    assert stats.advisory_produced == stats.exceptions_seen
    assert stats.ai_failures == 0
    for row in hybrid:
        advisory = row.get("advisory")
        if row["status"] in comparison_module.EXCEPTION_STATUSES:
            assert advisory is not None
            assert advisory["advisory_only"] is True
            assert set(advisory).isdisjoint({"status", "matched_with", "amount_diff", "exception_type"})
        else:
            assert advisory is None


def test_ai_failure_is_fail_open_and_leaves_decisions_intact(monkeypatch):
    class FailingProvider:
        name = "failing"

        def assist(self, packet, mode="full_analysis"):
            raise AIAssistError("no provider", code="provider_unavailable")

    monkeypatch.setattr(comparison_module, "get_provider", lambda name=None: FailingProvider())
    dataset = build_held_out_dataset()
    baseline = reconcile_records(dataset.engine_records())
    hybrid, stats = apply_advisory_layer(baseline, provider_name="failing")
    assert stats.ai_failures == stats.exceptions_seen > 0
    assert stats.advisory_produced == 0
    assert _signature(baseline) == _signature(hybrid)
    assert all("advisory" not in row for row in hybrid)


def test_comparison_reports_every_required_metric(report):
    metrics = {row.metric for row in report.rows}
    for required in (
        "precision",
        "recall",
        "auto_resolve_precision",
        "false_positives",
        "false_negatives",
        "review_required_rate",
    ):
        assert required in metrics
    assert set(metrics) == set(COMPARED_METRICS)


def test_comparison_is_broken_down_by_relationship_type(report):
    assert {"order_payment", "payment_settlement", "payment_fee", "payment_refund"} <= set(report.by_pair_type)
    for rows in report.by_pair_type.values():
        assert {"precision", "recall", "false_positives", "false_negatives"} <= {row.metric for row in rows}


def test_deltas_are_exact_and_reproducible():
    first = run_comparison()
    second = run_comparison()
    assert [row.as_dict() for row in first.rows] == [row.as_dict() for row in second.rows]
    for row in first.rows:
        assert row.delta is None or isinstance(row.delta, Decimal)


def test_measured_outcome_is_reported_without_claiming_improvement(report):
    # The AI layer is advisory, so no accuracy metric may move in either direction.
    assert report.ai_improved_metrics == []
    assert report.ai_regressed_metrics == []
    assert all(row.verdict in {"unchanged", "not_measurable"} for row in report.rows)
    assert report.advisory.decisions_changed == 0


def test_verdict_direction_is_correct_for_each_metric_kind():
    from app.evaluation.comparison import _verdict

    assert _verdict("precision", Decimal("0.05")) == "improved"
    assert _verdict("precision", Decimal("-0.05")) == "regressed"
    assert _verdict("false_positives", Decimal("-2")) == "improved"
    assert _verdict("false_positives", Decimal("3")) == "regressed"
    assert _verdict("review_required_rate", Decimal("0.02")) == "changed"
    assert _verdict("recall", None) == "not_measurable"
    assert _verdict("recall", Decimal("0")) == "unchanged"


def test_hybrid_costs_extra_time_but_no_extra_engine_work(report):
    assert report.hybrid.elapsed_seconds >= report.baseline.elapsed_seconds
    assert report.advisory.elapsed_seconds >= 0


def test_comparison_does_not_touch_thresholds():
    from app.config import settings

    assert settings.thresholds.auto_resolve == Decimal("0.99")
    assert settings.thresholds.auto_resolve_warning == Decimal("0.90")
    assert settings.thresholds.human_review == Decimal("0.70")


def test_format_comparison_states_whether_ai_improved_anything(report):
    from app.evaluation.comparison import format_comparison

    text = format_comparison(report)
    assert "baseline" in text and "hybrid" in text
    assert "AI improved" in text
    assert "advisory" in text.lower()


def test_compare_modes_accepts_an_explicit_dataset():
    dataset = build_held_out_dataset(cycles=1)
    result = compare_modes(dataset)
    assert result.record_count == len(dataset.records)
    assert result.label_count == len(dataset.labels)
