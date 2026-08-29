"""Baseline vs hybrid comparison on the held-out batch (docs/EVALUATION.md).

Baseline = the deterministic engine alone.
Hybrid   = the same deterministic decisions plus the existing advisory AI layer
           (`app.ai.providers` + `app.exceptions.intelligence`).

The AI layer is structurally advisory: `AIAssistResult` carries no decision,
status, amount or counterpart field, so it cannot alter reconciliation
arithmetic. `assert_decisions_unchanged` re-checks that at runtime and raises
rather than reporting a hybrid number produced by a mutated decision.
"""

from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.ai.evidence import build_evidence_packet
from app.ai.providers.factory import get_provider
from app.ai.schemas import AIAssistError, AssistMode
from app.evaluation.dataset import HELD_OUT_SEED, EvaluationDataset, build_held_out_dataset
from app.evaluation.metrics import Counters, EvaluationReport, score_results
from app.exceptions.intelligence import enrich_exception
from app.models import ExceptionRecord
from app.reconciliation.engine import reconcile_records

BASELINE_MODE = "deterministic_only"
HYBRID_MODE = "deterministic_plus_ai"

EXCEPTION_STATUSES = frozenset({"EXCEPTION", "REVIEW_REQUIRED", "UNRESOLVED"})

# Fields the AI must never influence.
DECISION_FIELDS: Tuple[str, ...] = (
    "record_id",
    "pair_type",
    "status",
    "matched_with",
    "exception_type",
    "amount_diff",
    "confidence",
)

HIGHER_IS_BETTER = ("precision", "recall", "f1", "auto_resolve_precision", "exception_type_accuracy")
LOWER_IS_BETTER = ("false_positives", "false_negatives")
# A lower review rate is not automatically better: review is the safe outcome.
NEUTRAL = ("review_required", "review_required_rate")

COMPARED_METRICS: Tuple[str, ...] = (
    "precision",
    "recall",
    "auto_resolve_precision",
    "false_positives",
    "false_negatives",
    "review_required",
    "review_required_rate",
    "exception_type_accuracy",
)

PAIR_METRICS: Tuple[str, ...] = (
    "precision",
    "recall",
    "auto_resolve_precision",
    "false_positives",
    "false_negatives",
    "review_required_rate",
)


class AdvisoryIntegrityError(RuntimeError):
    """Raised when the advisory layer changed a reconciliation decision."""


@dataclass
class AdvisoryStats:
    provider: str
    exceptions_seen: int = 0
    advisory_produced: int = 0
    ai_failures: int = 0
    decisions_changed: int = 0
    elapsed_seconds: Decimal = Decimal("0")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "exceptions_seen": self.exceptions_seen,
            "advisory_produced": self.advisory_produced,
            "ai_failures": self.ai_failures,
            "decisions_changed": self.decisions_changed,
            "elapsed_seconds": self.elapsed_seconds,
        }


@dataclass
class ComparisonRow:
    metric: str
    baseline: Any
    hybrid: Any
    delta: Optional[Decimal]
    verdict: str  # improved | regressed | unchanged | not_measurable

    def as_dict(self) -> Dict[str, Any]:
        return {
            "metric": self.metric,
            "baseline": self.baseline,
            "hybrid": self.hybrid,
            "delta": self.delta,
            "verdict": self.verdict,
        }


@dataclass
class ComparisonReport:
    seed: int
    record_count: int
    label_count: int
    baseline: EvaluationReport
    hybrid: EvaluationReport
    advisory: AdvisoryStats
    rows: List[ComparisonRow] = field(default_factory=list)
    by_pair_type: Dict[str, List[ComparisonRow]] = field(default_factory=dict)

    @property
    def ai_improved_metrics(self) -> List[str]:
        return [row.metric for row in self.rows if row.verdict == "improved"]

    @property
    def ai_regressed_metrics(self) -> List[str]:
        return [row.metric for row in self.rows if row.verdict == "regressed"]

    @property
    def ai_changed_any_decision_metric(self) -> bool:
        return bool(self.ai_improved_metrics or self.ai_regressed_metrics)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "seed": self.seed,
            "record_count": self.record_count,
            "label_count": self.label_count,
            "baseline": self.baseline.as_dict(),
            "hybrid": self.hybrid.as_dict(),
            "advisory": self.advisory.as_dict(),
            "rows": [row.as_dict() for row in self.rows],
            "by_pair_type": {
                name: [row.as_dict() for row in rows] for name, rows in self.by_pair_type.items()
            },
            "ai_improved_metrics": self.ai_improved_metrics,
            "ai_regressed_metrics": self.ai_regressed_metrics,
        }


def _decision_signature(results: List[Dict[str, Any]]) -> List[Tuple[Any, ...]]:
    return sorted(tuple(str(result.get(name)) for name in DECISION_FIELDS) for result in results)


def assert_decisions_unchanged(
    baseline: List[Dict[str, Any]],
    hybrid: List[Dict[str, Any]],
) -> None:
    """Fail loudly if the advisory layer touched any reconciliation decision."""
    if len(baseline) != len(hybrid):
        raise AdvisoryIntegrityError(
            f"advisory layer changed the decision count: {len(baseline)} -> {len(hybrid)}"
        )
    if _decision_signature(baseline) != _decision_signature(hybrid):
        raise AdvisoryIntegrityError("advisory layer changed a reconciliation decision")


def _exception_view(result: Dict[str, Any], index: int) -> ExceptionRecord:
    """In-memory exception in the same shape the repository persists (never saved)."""
    amount = Decimal(str(result.get("amount_diff") or "0"))
    return ExceptionRecord(
        id=index,
        exception_type=str(result.get("exception_type") or "UNKNOWN_EXCEPTION"),
        severity="high" if amount >= Decimal("500") else "medium",
        status="OPEN",
        confidence=Decimal(str(result.get("confidence") or "0")),
        amount=amount,
        description=f"{result.get('status')} for {result.get('record_id')}",
        evidence=json.dumps(
            {
                "pair_type": result.get("pair_type") or "order_payment",
                "matched_with": result.get("matched_with"),
                "source_record_type": result.get("source_record_type"),
                "related_record_type": result.get("related_record_type"),
                "candidates": result.get("candidates") or [],
            }
        ),
    )


def apply_advisory_layer(
    results: List[Dict[str, Any]],
    *,
    provider_name: str = "mock",
    assist_mode: AssistMode = "full_analysis",
) -> Tuple[List[Dict[str, Any]], AdvisoryStats]:
    """Attach advisory AI analysis + triage to every non-auto-accepted decision.

    Returns new decision dicts. Financial fields are copied verbatim; the AI
    output lands under `advisory`, which no metric reads as a decision.
    """
    provider = get_provider(provider_name)
    stats = AdvisoryStats(provider=str(getattr(provider, "name", provider_name)))
    started = time.perf_counter()
    enriched: List[Dict[str, Any]] = []

    for index, result in enumerate(results, start=1):
        decision = copy.deepcopy(result)
        if str(result.get("status")) not in EXCEPTION_STATUSES:
            enriched.append(decision)
            continue

        stats.exceptions_seen += 1
        record = _exception_view(result, index)
        triage = enrich_exception(record)
        try:
            assistance = provider.assist(build_evidence_packet(record), mode=assist_mode)
        except AIAssistError:
            stats.ai_failures += 1
            enriched.append(decision)
            continue
        except Exception:
            stats.ai_failures += 1
            enriched.append(decision)
            continue

        stats.advisory_produced += 1
        decision["advisory"] = {
            "provider": stats.provider,
            "priority": triage["priority"],
            "certainty": triage["certainty"],
            "likely_cause": assistance.likely_cause,
            "suggested_action": assistance.suggested_action,
            "investigation_steps": assistance.investigation_steps,
            "ai_confidence": assistance.ai_confidence,
            "advisory_only": True,
        }
        enriched.append(decision)

    stats.elapsed_seconds = Decimal(str(round(time.perf_counter() - started, 6)))
    return enriched, stats


def _as_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    return None


def _verdict(metric: str, delta: Optional[Decimal]) -> str:
    if delta is None:
        return "not_measurable"
    if delta == 0:
        return "unchanged"
    if metric in NEUTRAL:
        return "changed"
    if metric in HIGHER_IS_BETTER:
        return "improved" if delta > 0 else "regressed"
    if metric in LOWER_IS_BETTER:
        return "improved" if delta < 0 else "regressed"
    return "changed"


def _rows(baseline: Counters, hybrid: Counters, metrics: Tuple[str, ...]) -> List[ComparisonRow]:
    rows: List[ComparisonRow] = []
    for metric in metrics:
        base_value = getattr(baseline, metric)
        hybrid_value = getattr(hybrid, metric)
        base_dec = _as_decimal(base_value)
        hybrid_dec = _as_decimal(hybrid_value)
        delta = hybrid_dec - base_dec if base_dec is not None and hybrid_dec is not None else None
        rows.append(ComparisonRow(metric, base_value, hybrid_value, delta, _verdict(metric, delta)))
    return rows


def compare_modes(
    dataset: EvaluationDataset,
    *,
    provider_name: str = "mock",
    assist_mode: AssistMode = "full_analysis",
) -> ComparisonReport:
    """Score deterministic-only and deterministic+AI on the identical batch."""
    records = dataset.engine_records()

    started = time.perf_counter()
    baseline_results = reconcile_records(records)
    engine_elapsed = Decimal(str(round(time.perf_counter() - started, 6)))

    baseline_report = score_results(
        dataset,
        baseline_results,
        elapsed_seconds=engine_elapsed,
        mode=BASELINE_MODE,
        record_count=len(records),
    )

    hybrid_results, advisory = apply_advisory_layer(
        baseline_results, provider_name=provider_name, assist_mode=assist_mode
    )
    assert_decisions_unchanged(baseline_results, hybrid_results)

    hybrid_report = score_results(
        dataset,
        hybrid_results,
        elapsed_seconds=engine_elapsed + advisory.elapsed_seconds,
        mode=HYBRID_MODE,
        record_count=len(records),
    )

    by_pair_type = {
        name: _rows(counters, hybrid_report.by_pair_type[name], PAIR_METRICS)
        for name, counters in baseline_report.by_pair_type.items()
        if name in hybrid_report.by_pair_type
    }

    return ComparisonReport(
        seed=dataset.seed,
        record_count=len(records),
        label_count=len(dataset.labels),
        baseline=baseline_report,
        hybrid=hybrid_report,
        advisory=advisory,
        rows=_rows(baseline_report.overall, hybrid_report.overall, COMPARED_METRICS),
        by_pair_type=by_pair_type,
    )


def run_comparison(
    seed: int | None = None,
    cycles: int = 2,
    *,
    provider_name: str = "mock",
) -> ComparisonReport:
    dataset = build_held_out_dataset(seed=HELD_OUT_SEED if seed is None else seed, cycles=cycles)
    return compare_modes(dataset, provider_name=provider_name)


def format_comparison(report: ComparisonReport) -> str:
    lines: List[str] = []
    lines.append("Baseline vs hybrid on the held-out batch")
    lines.append(f"  seed / records / labels : {report.seed} / {report.record_count} / {report.label_count}")
    lines.append(f"  baseline                : {report.baseline.mode}")
    lines.append(f"  hybrid                  : {report.hybrid.mode} (provider: {report.advisory.provider})")
    lines.append(
        f"  advisory coverage       : {report.advisory.advisory_produced}/{report.advisory.exceptions_seen} "
        f"exceptions, {report.advisory.ai_failures} AI failures"
    )
    lines.append(f"  decisions changed by AI : {report.advisory.decisions_changed}")
    lines.append(
        f"  engine time / +AI time  : {report.baseline.elapsed_seconds}s / {report.hybrid.elapsed_seconds}s"
    )
    lines.append("")
    lines.append(f"  {'metric':<24}{'baseline':>14}{'hybrid':>14}{'delta':>12}   verdict")
    for row in report.rows:
        lines.append(
            f"  {row.metric:<24}{str(row.baseline):>14}{str(row.hybrid):>14}"
            f"{str(row.delta):>12}   {row.verdict}"
        )
    lines.append("")
    lines.append("  by relationship type (delta, verdict):")
    for name in sorted(report.by_pair_type):
        deltas = ", ".join(f"{row.metric}={row.delta}" for row in report.by_pair_type[name])
        lines.append(f"    {name:<20} {deltas}")
    lines.append("")
    improved = report.ai_improved_metrics
    regressed = report.ai_regressed_metrics
    if improved:
        lines.append(f"  AI improved  : {', '.join(improved)}")
    else:
        lines.append("  AI improved  : none - no measured metric improved")
    if regressed:
        lines.append(f"  AI regressed : {', '.join(regressed)}")
    else:
        lines.append("  AI regressed : none")
    lines.append(
        "  Interpretation: the AI layer is advisory only (no decision/status/amount field exists "
        "in its output), so it adds analysis and triage without moving accuracy metrics."
    )
    return "\n".join(lines)
