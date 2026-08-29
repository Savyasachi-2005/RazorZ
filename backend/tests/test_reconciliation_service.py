from app.db import create_db_and_tables
from app.services.reconciliation_service import run_reconciliation


def test_reconciliation_service_persists_results():
    records = [
        {
            "source": "synthetic",
            "record_type": "order",
            "record_id": "ORD-101",
            "reference": "ORD-101",
            "amount": "200.00",
            "date": "2026-01-12",
            "customer": "CUST-77",
        },
        {
            "source": "synthetic",
            "record_type": "payment",
            "record_id": "PMT-101",
            "reference": "ORD-101",
            "amount": "200.00",
            "date": "2026-01-12",
            "customer": "CUST-77",
        },
    ]

    response = run_reconciliation(records, database_url="sqlite://")

    assert response["summary"]["matched"] >= 1
    assert response["summary"]["total"] >= 1
    assert response["persisted"] is True
