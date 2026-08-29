from app.repositories.reconciliation_repository import ReconciliationRepository


def test_repository_persists_and_lists_results():
    repo = ReconciliationRepository("sqlite://")
    repo.persist_results(
        [
            {
                "record_id": "ORD-1",
                "matched_with": "PMT-1",
                "status": "MATCHED",
                "confidence": 0.99,
                "amount_diff": "0.00",
            },
            {
                "record_id": "ORD-2",
                "matched_with": None,
                "status": "EXCEPTION",
                "confidence": 0.42,
                "exception_type": "PAYMENT_MISSING",
                "amount_diff": "50.00",
            },
        ]
    )

    rows = repo.list_recent()
    statuses = {row.status for row in rows}
    assert "MATCHED" in statuses
    assert "EXCEPTION" in statuses
    assert all(str(row.confidence) != "" for row in rows)
    summary = repo.summary()
    assert summary["total_records"] >= 2
    assert repo.list_exceptions()
    assert repo.list_audit()
