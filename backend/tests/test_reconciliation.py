from app.reconciliation.engine import reconcile_records


def test_exact_match_is_auto_resolved():
    records = [
        {
            "source": "synthetic",
            "record_type": "order",
            "record_id": "ORD-100",
            "reference": "ORD-100",
            "amount": "125.50",
            "date": "2026-01-05",
            "customer": "CUST-27",
        },
        {
            "source": "synthetic",
            "record_type": "payment",
            "record_id": "PMT-100",
            "reference": "ORD-100",
            "amount": "125.50",
            "date": "2026-01-05",
            "customer": "CUST-27",
        },
    ]

    results = reconcile_records(records)

    assert any(item["status"] == "MATCHED" for item in results)
    assert any(item["confidence"] >= 0.99 for item in results)


def test_missing_payment_is_flagged_as_exception():
    records = [
        {
            "source": "synthetic",
            "record_type": "order",
            "record_id": "ORD-200",
            "reference": "ORD-200",
            "amount": "450.00",
            "date": "2026-02-09",
            "customer": "CUST-88",
        }
    ]

    results = reconcile_records(records)

    assert any(item["exception_type"] == "PAYMENT_MISSING" for item in results)
    assert any(item["status"] == "EXCEPTION" for item in results)


def test_amount_mismatch_is_review_required():
    records = [
        {
            "source": "synthetic",
            "record_type": "order",
            "record_id": "ORD-300",
            "reference": "ORD-300",
            "amount": "1000.00",
            "date": "2026-03-10",
            "customer": "CUST-12",
        },
        {
            "source": "synthetic",
            "record_type": "payment",
            "record_id": "PMT-300",
            "reference": "ORD-300",
            "amount": "980.00",
            "date": "2026-03-10",
            "customer": "CUST-12",
        },
    ]

    results = reconcile_records(records)

    assert any(item["exception_type"] == "AMOUNT_MISMATCH" for item in results)
    assert any(item["status"] in {"REVIEW_REQUIRED", "EXCEPTION"} for item in results)
