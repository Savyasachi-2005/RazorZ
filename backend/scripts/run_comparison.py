"""Baseline (deterministic-only) vs hybrid (deterministic + AI) on the held-out batch.

    python -m scripts.run_comparison
    python -m scripts.run_comparison --cycles 34 --provider mock
"""

from __future__ import annotations

import argparse

from app.evaluation.comparison import format_comparison, run_comparison
from app.evaluation.dataset import HELD_OUT_SEED


def main() -> None:
    parser = argparse.ArgumentParser(description="Baseline vs hybrid reconciliation comparison")
    parser.add_argument("--seed", type=int, default=HELD_OUT_SEED)
    parser.add_argument("--cycles", type=int, default=2, help="test-case cycles (~31 records each)")
    parser.add_argument("--provider", default="mock", help="AI provider for the advisory layer")
    args = parser.parse_args()
    report = run_comparison(seed=args.seed, cycles=args.cycles, provider_name=args.provider)
    print(format_comparison(report))


if __name__ == "__main__":
    main()
