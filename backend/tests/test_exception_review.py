from app.exceptions.intelligence import classify
from app.services.reconciliation_service import review_exception, run_reconciliation


def test_missing_payment_is_classified_and_human_resolvable():
    response = run_reconciliation(
        [
            {
                "source": "synthetic",
                "record_type": "order",
                "record_id": "ORD-EX-1",
                "reference": "ORD-EX-1",
                "amount": "75.00",
                "date": "2026-05-01",
                "customer": "CUST-1",
            }
        ],
        database_url="sqlite://",
    )
    assert response["summary"]["exceptions"] >= 1
    profile = classify("PAYMENT_MISSING")
    assert profile["human_required"] is True
    assert profile["certainty"] == "CONFIRMED"

    from app.repositories.reconciliation_repository import ReconciliationRepository

    repo = ReconciliationRepository("sqlite://")
    exception = repo.list_exceptions()[0]
    reviewed = review_exception(exception.id, action="reject", actor="ops", note="Need more evidence", database_url="sqlite://")
    assert reviewed["status"] == "REJECTED"
    assert reviewed["resolved_by"] == "ops"
