"""Manual walkthrough of the Copilot status/semantic scenarios on a throwaway database."""

from __future__ import annotations

import io
import sys
import tempfile
import uuid
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from app.copilot import service
from app.repositories.reconciliation_repository import ReconciliationRepository
from app.services.reconciliation_service import run_reconciliation

# Throwaway file per run, so the check never touches project or dev data.
_DB_PATH = Path(tempfile.gettempdir()) / f"razorz_copilot_check_{uuid.uuid4().hex}.db"
DB = f"sqlite:///{_DB_PATH.as_posix()}"


def seed() -> list[int]:
    records = []
    for index in range(1, 5):
        tag = f"MC-{index}"
        records += [
            {
                "source": "synthetic",
                "record_type": "order",
                "record_id": f"OR-{tag}",
                "reference": f"REF-{tag}",
                "amount": "1000.00",
                "date": "2026-06-01",
                "customer": f"CUST-{tag}",
            },
            {
                "source": "synthetic",
                "record_type": "payment",
                "record_id": f"PM-{tag}",
                "reference": f"REF-{tag}",
                "amount": f"{700 + index * 10}.00",
                "date": "2026-06-01",
                "customer": f"CUST-{tag}",
            },
        ]
    run_reconciliation(records, database_url=DB)
    repo = ReconciliationRepository(DB)
    ids = sorted(row.id for row in repo.list_exceptions(limit=500))
    repo.review_exception(ids[0], action="reject", actor="ops@razorz.test", note="Duplicate feed row; not a genuine break.")
    repo.review_exception(ids[1], action="resolve", actor="ops@razorz.test", note="Fee accounted for after checking the fee file.")
    return ids


def ask(question: str, history=None) -> None:
    result = service.ask(question, history=history, database_url=DB, provider_name="mock")
    print(f"\n=== {question}")
    print(f"intent={result['intent']} llm_used={result['llm_used']} sources={result['answer']['sources_used']}")
    print(result["answer"]["answer"])
    for finding in result["answer"]["key_findings"][:6]:
        print("  -", finding)


def main() -> None:
    ids = seed()
    rejected, resolved, still_open = ids[0], ids[1], ids[2]
    ask("How many exceptions are open?")
    ask(f"Why is EX-{rejected} unresolved?")
    ask(f"Why was EX-{rejected} rejected?")
    ask(f"Why is EX-{resolved} unresolved?")
    ask(f"Why is EX-{still_open} unresolved?")
    ask("What are the biggest causes of exceptions?")
    ask("How much financial exposure is currently unresolved?")
    ask(
        "What did the reviewer do?",
        history=[
            {"role": "user", "content": f"Why is EX-{rejected} unresolved?"},
            {"role": "assistant", "content": f"EX-{rejected} is currently REJECTED."},
        ],
    )
    ask(f"Resolve EX-{still_open} for me")
    ask("What is the bank balance?")


if __name__ == "__main__":
    main()
