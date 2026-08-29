from decimal import Decimal

from app.reconciliation.scoring import amount_similarity, score_pair


def test_amount_similarity_is_decimal_and_exact_for_equal_values():
    score = amount_similarity(Decimal("100.00"), Decimal("100.00"))
    assert score == Decimal("1.00")


def test_score_pair_prefers_identical_identity_fields():
    left = {
        "record_id": "ORD-1",
        "reference": "ORD-1",
        "amount": Decimal("80.00"),
        "date": "2026-01-01",
        "customer": "CUST-1",
    }
    right = {
        "record_id": "PMT-1",
        "reference": "ORD-1",
        "amount": Decimal("80.00"),
        "date": "2026-01-01",
        "customer": "CUST-1",
    }
    result = score_pair(left, right)
    assert result["score"] >= Decimal("0.99")
