"""Scoring of engine output against held-out ground truth.

All ratios and money are computed with `Decimal`. The engine is run exactly as
production runs it: ground truth is passed only to this module, never to
`reconcile_records`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.evaluation.dataset import EvaluationDataset, GroundTruthLabel, build_held_out_dataset
from app.reconciliation.engine import reconcile_records

AUTO_ACCEPT_STATUSES = frozenset({"MATCHED", "AUTO_RESOLVED"})
FUZZY_AUTO_STATUS = "AUTO_RESOLVED"
RATIO = Decimal("0.0001")
MONEY = Decimal("0.01")


def _ratio(numerator: int, denominator: int) -> Optional[Decimal]:
    if denominator == 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(RATIO)


def _f1(precision: Optional[Decimal], recall: Optional[Decimal]) -> Optional[Decimal]:
    if precision is None or recall is None or (precision + recall) == 0:
        return None
    return (Decimal("2") * precision * recall / (precision + recall)).quantize(RATIO)


@dataclass
class Counters:
    labels: int = 0
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    review_required: int = 0
    auto_resolved_correct: int = 0
    auto_resolved_total: int = 0
    score_based_auto_resolves: int = 0
    exception_type_correct: int = 0
    exception_type_total: int = 0
    missing_predictions: int = 0
    matched_value: Decimal = Decimal("0.00")
    false_match_value: Decimal = Decimal("0.00")
    missed_match_value: Decimal = Decimal("0.00")
    exception_value: Decimal = Decimal("0.00")

    @property
    def precision(self) -> Optional[Decimal]:
        return _ratio(self.true_positives, self.true_positives + self.false_positives)

    @property
    def recall(self) -> Optional[Decimal]:
        return _ratio(self.true_positives, self.true_positives + self.false_negatives)

    @property
    def f1(self) -> Optional[Decimal]:
        return _f1(self.precision, self.recall)

    @property
    def auto_resolve_precision(self) -> Optional[Decimal]:
        """Share of automatically accepted links (no human review) that are correct."""
        return _ratio(self.auto_resolved_correct, self.auto_resolved_total)

    @property
    def exception_type_accuracy(self) -> Optional[Decimal]:
        return _ratio(self.exception_type_correct, self.exception_type_total)

    @property
    def review_required_rate(self) -> Optional[Decimal]:
        return _ratio(self.review_required, self.labels)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "labels": self.labels,
            "true_positives": self.true_positives,
            "false_positives": self.false_positives,
            "false_negatives": self.false_negatives,
            "true_negatives": self.true_negatives,
            "review_required": self.review_required,
            "review_required_rate": self.review_required_rate,
            "missing_predictions": self.missing_predictions,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "auto_resolve_precision": self.auto_resolve_precision,
            "auto_resolve_decisions": self.auto_resolved_total,
            "score_based_auto_resolves": self.score_based_auto_resolves,
            "exception_type_accuracy": self.exception_type_accuracy,
            "matched_value": self.matched_value,
            "false_match_value": self.false_match_value,
            "missed_match_value": self.missed_match_value,
            "exception_value": self.exception_value,
        }


PairTypeMetrics = Counters


@dataclass
class ErrorCase:
    record_id: str
    pair_type: str
    scenario: str
    kind: str  # false_match | missed_match | wrong_exception_type
    expected: str
    predicted: str


@dataclass
class EvaluationReport:
    seed: int
    record_count: int
    label_count: int
    prediction_count: int
    unlabeled_predictions: int
    elapsed_seconds: Decimal
    overall: Counters
    by_pair_type: Dict[str, Counters] = field(default_factory=dict)
    by_scenario: Dict[str, Counters] = field(default_factory=dict)
    errors: List[ErrorCase] = field(default_factory=list)
    mode: str = "deterministic_only"

    @property
    def throughput_records_per_second(self) -> Optional[Decimal]:
        if self.elapsed_seconds <= 0:
            return None
        return (Decimal(self.record_count) / self.elapsed_seconds).quantize(MONEY)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "seed": self.seed,
            "record_count": self.record_count,
            "label_count": self.label_count,
            "prediction_count": self.prediction_count,
            "unlabeled_predictions": self.unlabeled_predictions,
            "elapsed_seconds": self.elapsed_seconds,
            "throughput_records_per_second": self.throughput_records_per_second,
            "overall": self.overall.as_dict(),
            "by_pair_type": {name: counters.as_dict() for name, counters in self.by_pair_type.items()},
            "by_scenario": {name: counters.as_dict() for name, counters in self.by_scenario.items()},
            "errors": [error.__dict__ for error in self.errors],
        }


def _index_predictions(results: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for result in results:
        index[(str(result["record_id"]), str(result["pair_type"]))] = result
    return index


def _amount(label: GroundTruthLabel) -> Decimal:
    return Decimal(label.amount).quantize(MONEY)


def _score_label(
    label: GroundTruthLabel,
    prediction: Optional[Dict[str, Any]],
    buckets: List[Counters],
) -> Optional[ErrorCase]:
    status = str(prediction["status"]) if prediction else "NO_PREDICTION"
    matched_with = prediction.get("matched_with") if prediction else None
    predicted_type = prediction.get("exception_type") if prediction else None
    auto_accepted = bool(prediction) and status in AUTO_ACCEPT_STATUSES and matched_with is not None
    value = _amount(label)

    for bucket in buckets:
        bucket.labels += 1
        if prediction is None:
            bucket.missing_predictions += 1
        if status == "REVIEW_REQUIRED":
            bucket.review_required += 1
        if auto_accepted:
            bucket.auto_resolved_total += 1
        if status == FUZZY_AUTO_STATUS:
            bucket.score_based_auto_resolves += 1

    correct_auto = auto_accepted and label.expects_match and matched_with == label.counterpart_id
    if correct_auto:
        for bucket in buckets:
            bucket.auto_resolved_correct += 1

    if label.expects_match:
        if correct_auto:
            for bucket in buckets:
                bucket.true_positives += 1
                bucket.matched_value += value
            return None
        if auto_accepted:
            # A link was asserted against the wrong counterpart: both a false
            # match and a missed true match.
            for bucket in buckets:
                bucket.false_positives += 1
                bucket.false_negatives += 1
                bucket.false_match_value += value
                bucket.missed_match_value += value
            return ErrorCase(
                label.record_id,
                label.pair_type,
                label.scenario,
                "false_match",
                f"MATCH:{label.counterpart_id}",
                f"{status}:{matched_with}",
            )
        for bucket in buckets:
            bucket.false_negatives += 1
            bucket.missed_match_value += value
        return ErrorCase(
            label.record_id,
            label.pair_type,
            label.scenario,
            "missed_match",
            f"MATCH:{label.counterpart_id}",
            f"{status}:{predicted_type}",
        )

    # Ground truth says this record must not be auto-matched.
    for bucket in buckets:
        bucket.exception_value += value
    if auto_accepted:
        for bucket in buckets:
            bucket.false_positives += 1
            bucket.false_match_value += value
        return ErrorCase(
            label.record_id,
            label.pair_type,
            label.scenario,
            "false_match",
            f"EXCEPTION:{label.expected_exception_type or 'ANY'}",
            f"{status}:{matched_with}",
        )

    for bucket in buckets:
        bucket.true_negatives += 1
    if label.expected_exception_type is not None:
        for bucket in buckets:
            bucket.exception_type_total += 1
        if predicted_type == label.expected_exception_type:
            for bucket in buckets:
                bucket.exception_type_correct += 1
            return None
        return ErrorCase(
            label.record_id,
            label.pair_type,
            label.scenario,
            "wrong_exception_type",
            label.expected_exception_type,
            str(predicted_type),
        )
    return None


def score_results(
    dataset: EvaluationDataset,
    results: List[Dict[str, Any]],
    *,
    elapsed_seconds: Decimal = Decimal("0"),
    mode: str = "deterministic_only",
    record_count: Optional[int] = None,
) -> EvaluationReport:
    """Score any set of engine decisions against the held-out ground truth."""
    predictions = _index_predictions(results)
    overall = Counters()
    by_pair_type: Dict[str, Counters] = {}
    by_scenario: Dict[str, Counters] = {}
    errors: List[ErrorCase] = []
    seen_keys = set()

    for label in dataset.labels:
        key = (label.record_id, label.pair_type)
        seen_keys.add(key)
        buckets = [
            overall,
            by_pair_type.setdefault(label.pair_type, Counters()),
            by_scenario.setdefault(label.scenario, Counters()),
        ]
        error = _score_label(label, predictions.get(key), buckets)
        if error is not None:
            errors.append(error)

    return EvaluationReport(
        seed=dataset.seed,
        record_count=len(dataset.records) if record_count is None else record_count,
        label_count=len(dataset.labels),
        prediction_count=len(results),
        unlabeled_predictions=len([key for key in predictions if key not in seen_keys]),
        elapsed_seconds=elapsed_seconds,
        overall=overall,
        by_pair_type=by_pair_type,
        by_scenario=by_scenario,
        errors=errors,
        mode=mode,
    )


def evaluate_dataset(dataset: EvaluationDataset) -> EvaluationReport:
    """Run the deterministic engine on a held-out batch and score it."""
    records = dataset.engine_records()
    started = time.perf_counter()
    results = reconcile_records(records)
    elapsed = Decimal(str(round(time.perf_counter() - started, 6)))
    return score_results(dataset, results, elapsed_seconds=elapsed, record_count=len(records))


def run_evaluation(seed: int | None = None, cycles: int = 2) -> EvaluationReport:
    from app.evaluation.dataset import HELD_OUT_SEED

    dataset = build_held_out_dataset(seed=HELD_OUT_SEED if seed is None else seed, cycles=cycles)
    return evaluate_dataset(dataset)


def format_report(report: EvaluationReport) -> str:
    lines: List[str] = []
    overall = report.overall
    lines.append("Held-out evaluation (deterministic engine)")
    lines.append(f"  seed                  : {report.seed}")
    lines.append(f"  records / labels      : {report.record_count} / {report.label_count}")
    lines.append(f"  engine decisions      : {report.prediction_count} ({report.unlabeled_predictions} unlabelled)")
    lines.append(f"  processing time (s)   : {report.elapsed_seconds}")
    lines.append(f"  throughput (rec/s)    : {report.throughput_records_per_second}")
    lines.append(f"  precision             : {overall.precision}")
    lines.append(f"  recall                : {overall.recall}")
    lines.append(f"  f1                    : {overall.f1}")
    lines.append(
        f"  auto-resolve precision: {overall.auto_resolve_precision} "
        f"({overall.auto_resolved_total} auto-accepted, {overall.score_based_auto_resolves} score-based)"
    )
    lines.append(f"  false positives       : {overall.false_positives}")
    lines.append(f"  false negatives       : {overall.false_negatives}")
    lines.append(f"  review required       : {overall.review_required}")
    lines.append(f"  exception type acc.   : {overall.exception_type_accuracy}")
    lines.append(
        f"  value matched/false/missed: {overall.matched_value} / "
        f"{overall.false_match_value} / {overall.missed_match_value}"
    )
    lines.append("  by relationship type:")
    for name in sorted(report.by_pair_type):
        counters = report.by_pair_type[name]
        lines.append(
            f"    {name:<20} P={counters.precision} R={counters.recall} "
            f"FP={counters.false_positives} FN={counters.false_negatives} "
            f"review={counters.review_required}"
        )
    if report.errors:
        lines.append(f"  error cases ({len(report.errors)}):")
        for error in report.errors[:10]:
            lines.append(
                f"    {error.kind:<20} {error.record_id} [{error.pair_type}/{error.scenario}] "
                f"expected={error.expected} predicted={error.predicted}"
            )
        if len(report.errors) > 10:
            lines.append(f"    … {len(report.errors) - 10} more")
    return "\n".join(lines)
