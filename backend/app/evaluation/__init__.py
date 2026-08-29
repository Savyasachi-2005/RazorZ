"""Held-out evaluation harness (docs/EVALUATION.md).

Nothing in `app.evaluation` is imported by the reconciliation engine, the
Copilot or the API — the evaluation dataset must never influence inference.
"""

from app.evaluation.dataset import (
    HELD_OUT_SEED,
    SCENARIOS,
    EvaluationDataset,
    GroundTruthLabel,
    build_held_out_dataset,
)
from app.evaluation.comparison import (
    BASELINE_MODE,
    HYBRID_MODE,
    AdvisoryIntegrityError,
    ComparisonReport,
    apply_advisory_layer,
    assert_decisions_unchanged,
    compare_modes,
    run_comparison,
)
from app.evaluation.metrics import (
    EvaluationReport,
    PairTypeMetrics,
    evaluate_dataset,
    run_evaluation,
    score_results,
)

__all__ = [
    "BASELINE_MODE",
    "HYBRID_MODE",
    "AdvisoryIntegrityError",
    "ComparisonReport",
    "apply_advisory_layer",
    "assert_decisions_unchanged",
    "compare_modes",
    "run_comparison",
    "score_results",
    "HELD_OUT_SEED",
    "SCENARIOS",
    "EvaluationDataset",
    "GroundTruthLabel",
    "build_held_out_dataset",
    "EvaluationReport",
    "PairTypeMetrics",
    "evaluate_dataset",
    "run_evaluation",
]
