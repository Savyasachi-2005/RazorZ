"""Run the held-out evaluation harness.

    python -m scripts.run_evaluation            # ~62 records
    python -m scripts.run_evaluation --cycles 34  # 1,000+ records
"""

from __future__ import annotations

import argparse

from app.evaluation.dataset import HELD_OUT_SEED
from app.evaluation.metrics import format_report, run_evaluation


def main() -> None:
    parser = argparse.ArgumentParser(description="Held-out reconciliation evaluation")
    parser.add_argument("--seed", type=int, default=HELD_OUT_SEED)
    parser.add_argument("--cycles", type=int, default=2, help="test-case cycles (~31 records each)")
    args = parser.parse_args()
    print(format_report(run_evaluation(seed=args.seed, cycles=args.cycles)))


if __name__ == "__main__":
    main()
