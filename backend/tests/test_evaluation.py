from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from app.data_generator import generate_dataset
from app.evaluation.dataset import (
    HELD_OUT_SEED,
    MATCH,
    SCENARIOS,
    GroundTruthLabel,
    build_held_out_dataset,
)
from app.evaluation.metrics import Counters, evaluate_dataset, format_report, run_evaluation, _score_label


# --- dataset ------------------------------------------------------------


def test_dataset_is_reproducible_with_a_fixed_seed():
    first = build_held_out_dataset()
    second = build_held_out_dataset()
    assert [(r.record_id, r.amount) for r in first.records] == [
        (r.record_id, r.amount) for r in second.records
    ]
    assert first.labels == second.labels
    assert first.seed == HELD_OUT_SEED


def test_held_out_seed_differs_from_development_seed():
    assert HELD_OUT_SEED != 42
    dev_ids = {record.record_id for record in generate_dataset(records=50, seed=42)}
    held_out_ids = {record.record_id for record in build_held_out_dataset().records}
    assert dev_ids.isdisjoint(held_out_ids)


def test_dataset_meets_minimum_batch_size_and_covers_every_test_case():
    dataset = build_held_out_dataset()
    assert len(dataset.records) >= 50
    counts = dataset.scenario_counts()
    assert set(counts) == set(SCENARIOS)
    assert all(count > 0 for count in counts.values())


def test_dataset_scales_past_one_thousand_records():
    dataset = build_held_out_dataset(cycles=34)
    assert len(dataset.records) > 1000


def test_engine_records_carry_no_ground_truth():
    dataset = build_held_out_dataset()
    label_fields = set(GroundTruthLabel.__dataclass_fields__)
    leaking = label_fields - {"record_id", "amount"}
    for record in dataset.engine_records():
        assert leaking.isdisjoint(record)
        assert leaking.isdisjoint(record["metadata"])


def test_amounts_are_decimal():
    dataset = build_held_out_dataset()
    assert all(isinstance(record.amount, Decimal) for record in dataset.records)
    assert all(isinstance(label.amount, Decimal) for label in dataset.labels)


def test_evaluation_package_is_not_imported_by_the_engine():
    engine_dir = Path(__file__).resolve().parents[1] / "app"
    for module in [
        engine_dir / "reconciliation" / "engine.py",
        engine_dir / "reconciliation" / "decisions.py",
        engine_dir / "reconciliation" / "scoring.py",
        engine_dir / "services" / "reconciliation_service.py",
        engine_dir / "data_generator.py",
        engine_dir / "main.py",
    ]:
        assert "app.evaluation" not in module.read_text(encoding="utf-8")


# --- harness ------------------------------------------------------------


def test_every_engine_decision_is_labelled():
    report = run_evaluation()
    assert report.unlabeled_predictions == 0
    assert report.overall.missing_predictions == 0
    assert report.prediction_count == report.label_count


def test_report_exposes_the_required_metrics():
    report = run_evaluation()
    overall = report.overall.as_dict()
    for key in (
        "precision",
        "recall",
        "auto_resolve_precision",
        "false_positives",
        "false_negatives",
        "review_required",
    ):
        assert key in overall
    assert report.by_pair_type, "metrics must be broken down by relationship type"
    assert {"order_payment", "payment_settlement", "payment_fee", "payment_refund"} <= set(report.by_pair_type)


def test_ratios_and_money_are_decimal():
    report = run_evaluation()
    overall = report.overall
    for value in (overall.precision, overall.recall, overall.f1, overall.auto_resolve_precision):
        assert value is None or isinstance(value, Decimal)
    for value in (overall.matched_value, overall.false_match_value, overall.missed_match_value):
        assert isinstance(value, Decimal)
    assert isinstance(report.elapsed_seconds, Decimal)


def test_evaluation_is_reproducible():
    first = run_evaluation().overall.as_dict()
    second = run_evaluation().overall.as_dict()
    assert first == second


def test_per_pair_counts_sum_to_the_overall_counts():
    report = run_evaluation()
    for field in ("true_positives", "false_positives", "false_negatives", "true_negatives", "review_required"):
        total = sum(getattr(counters, field) for counters in report.by_pair_type.values())
        assert total == getattr(report.overall, field)


def test_a_false_match_is_counted_as_a_false_positive():
    counters = Counters()
    label = GroundTruthLabel(
        record_id="EOR-00001",
        pair_type="order_payment",
        scenario="clean_match",
        expected_outcome=MATCH,
        counterpart_id="EPM-00001",
        amount=Decimal("100.00"),
    )


    error = _score_label(
        label,
        {"status": "MATCHED", "matched_with": "EPM-09999", "exception_type": None},
        [counters],
    )
    assert error is not None and error.kind == "false_match"
    assert counters.false_positives == 1
    assert counters.false_negatives == 1
    assert counters.true_positives == 0
    assert counters.false_match_value == Decimal("100.00")


def test_review_required_is_not_counted_as_a_false_match():
    counters = Counters()
    label = GroundTruthLabel(
        record_id="EOR-00002",
        pair_type="order_payment",
        scenario="amount_mismatch",
        expected_outcome="EXCEPTION",
        expected_exception_type="AMOUNT_MISMATCH",
        amount=Decimal("40.00"),
    )


    error = _score_label(
        label,
        {"status": "REVIEW_REQUIRED", "matched_with": "EPM-00002", "exception_type": "AMOUNT_MISMATCH"},
        [counters],
    )
    assert error is None
    assert counters.false_positives == 0
    assert counters.true_negatives == 1
    assert counters.review_required == 1
    assert counters.exception_type_correct == 1


def test_engine_makes_no_false_matches_on_the_held_out_batch():
    report = run_evaluation()
    assert report.overall.false_positives == 0
    assert report.overall.false_match_value == Decimal("0.00")


def test_error_analysis_reports_disagreements_by_kind():
    report = run_evaluation(cycles=34)
    assert report.overall.false_positives == 0
    kinds = {error.kind for error in report.errors}
    assert kinds <= {"false_match", "missed_match", "wrong_exception_type"}


def test_format_report_renders_headline_metrics():
    text = format_report(run_evaluation())
    for fragment in ("precision", "recall", "auto-resolve precision", "false positives", "review required"):
        assert fragment in text


def test_evaluate_dataset_does_not_mutate_the_dataset():
    dataset = build_held_out_dataset()
    before = [(r.record_id, r.amount, dict(r.metadata)) for r in dataset.records]
    labels_before = list(dataset.labels)
    evaluate_dataset(dataset)
    assert [(r.record_id, r.amount, dict(r.metadata)) for r in dataset.records] == before
    assert dataset.labels == labels_before


def test_reconciliation_thresholds_are_untouched_by_the_harness():
    from app.config import settings

    assert settings.thresholds.auto_resolve == Decimal("0.99")
    assert settings.thresholds.auto_resolve_warning == Decimal("0.90")
    assert settings.thresholds.human_review == Decimal("0.70")
