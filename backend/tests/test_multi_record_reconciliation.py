from __future__ import annotations

from decimal import Decimal

from app.data_generator import expected_fee_amount, generate_dataset
from app.reconciliation.engine import reconcile_records
from app.repositories.reconciliation_repository import ReconciliationRepository
from app.services.reconciliation_service import generate_and_reconcile, run_reconciliation


def _payment(pid: str, amount: str, **meta):
    return {
        "source": "synthetic",
        "record_type": "payment",
        "record_id": pid,
        "reference": f"ORD-{pid}",
        "amount": amount,
        "date": "2026-01-05",
        "customer": "CUST-1",
        "metadata": {
            "expects_settlement": True,
            "expects_fee": True,
            "expects_refund": False,
            "expected_fee_amount": str(expected_fee_amount(Decimal(amount))),
            **meta,
        },
    }


def _settlement(sid: str, payment_id: str, amount: str):
    return {
        "source": "synthetic",
        "record_type": "settlement",
        "record_id": sid,
        "reference": payment_id,
        "payment_reference": payment_id,
        "amount": amount,
        "date": "2026-01-06",
        "customer": "CUST-1",
    }


def _refund(rid: str, payment_id: str, amount: str):
    return {
        "source": "synthetic",
        "record_type": "refund",
        "record_id": rid,
        "reference": f"REF-{rid}",
        "payment_reference": payment_id,
        "amount": amount,
        "date": "2026-01-07",
        "customer": "CUST-1",
    }


def _fee(fid: str, payment_id: str, amount: str):
    return {
        "source": "synthetic",
        "record_type": "fee",
        "record_id": fid,
        "reference": payment_id,
        "payment_reference": payment_id,
        "amount": amount,
        "date": "2026-01-06",
        "customer": "CUST-1",
    }


# --- Settlement ---


def test_valid_settlement_matches():
    records = [_payment("PM-S1", "100.00"), _settlement("ST-S1", "PM-S1", "100.00")]
    results = reconcile_records(records)
    assert any(
        r["pair_type"] == "payment_settlement" and r["status"] == "MATCHED" and r["matched_with"] == "ST-S1"
        for r in results
    )


def test_missing_settlement_exception():
    records = [_payment("PM-S2", "200.00")]
    results = reconcile_records(records)
    assert any(r["exception_type"] == "SETTLEMENT_MISSING" and r["status"] == "EXCEPTION" for r in results)


def test_orphan_settlement_exception():
    records = [_settlement("ST-ORPH", "PM-MISSING", "50.00")]
    results = reconcile_records(records)
    assert any(r["exception_type"] == "ORPHAN_SETTLEMENT" for r in results)


def test_settlement_amount_mismatch_review():
    records = [_payment("PM-S3", "100.00"), _settlement("ST-S3", "PM-S3", "90.00")]
    results = reconcile_records(records)
    assert any(
        r["exception_type"] == "SETTLEMENT_AMOUNT_MISMATCH" and r["status"] == "REVIEW_REQUIRED" for r in results
    )


def test_duplicate_settlement_exception():
    records = [
        _payment("PM-S4", "100.00"),
        _settlement("ST-S4A", "PM-S4", "100.00"),
        _settlement("ST-S4B", "PM-S4", "100.00"),
    ]
    results = reconcile_records(records)
    assert any(r["exception_type"] == "DUPLICATE_SETTLEMENT" and r["status"] == "EXCEPTION" for r in results)


# --- Refund ---


def test_valid_refund_matches():
    records = [
        _payment("PM-R1", "100.00", expects_refund=True, expected_refund_amount="25.00"),
        _refund("RF-R1", "PM-R1", "25.00"),
    ]
    results = reconcile_records(records)
    assert any(
        r["pair_type"] == "payment_refund" and r["status"] == "MATCHED" and r["matched_with"] == "RF-R1"
        for r in results
    )


def test_orphan_refund_exception():
    records = [_refund("RF-ORPH", "PM-GONE", "10.00")]
    results = reconcile_records(records)
    assert any(r["exception_type"] == "ORPHAN_REFUND" for r in results)


def test_missing_refund_exception():
    records = [_payment("PM-R2", "100.00", expects_refund=True, expected_refund_amount="25.00")]
    results = reconcile_records(records)
    assert any(r["exception_type"] == "REFUND_MISSING" and r["status"] == "EXCEPTION" for r in results)


def test_excessive_refund_exception():
    records = [
        _payment("PM-R3", "100.00", expects_refund=True),
        _refund("RF-R3", "PM-R3", "150.00"),
    ]
    results = reconcile_records(records)
    assert any(r["exception_type"] == "REFUND_EXCESSIVE" and r["status"] == "EXCEPTION" for r in results)


def test_multiple_refunds_review():
    records = [
        _payment("PM-R4", "100.00", expects_refund=True),
        _refund("RF-R4A", "PM-R4", "20.00"),
        _refund("RF-R4B", "PM-R4", "10.00"),
    ]
    results = reconcile_records(records)
    assert any(r["exception_type"] == "MULTIPLE_REFUNDS" and r["status"] == "REVIEW_REQUIRED" for r in results)


# --- Fee ---


def test_valid_fee_matches():
    amount = Decimal("100.00")
    fee = expected_fee_amount(amount)
    records = [_payment("PM-F1", "100.00"), _fee("FE-F1", "PM-F1", str(fee))]
    results = reconcile_records(records)
    assert any(r["pair_type"] == "payment_fee" and r["status"] == "MATCHED" for r in results)


def test_missing_fee_exception():
    records = [_payment("PM-F2", "100.00")]
    results = reconcile_records(records)
    assert any(r["exception_type"] == "FEE_MISSING" and r["status"] == "EXCEPTION" for r in results)


def test_unexpected_fee_exception():
    records = [_fee("FE-ORPH", "PM-GONE", "5.00")]
    results = reconcile_records(records)
    assert any(r["exception_type"] == "FEE_UNEXPECTED" for r in results)


def test_incorrect_fee_review():
    records = [_payment("PM-F3", "100.00"), _fee("FE-F3", "PM-F3", "9.99")]
    results = reconcile_records(records)
    assert any(r["exception_type"] == "FEE_DIFFERENCE" and r["status"] == "REVIEW_REQUIRED" for r in results)


# --- Integration ---


def test_order_payment_only_unchanged_when_no_multi_records():
    """Existing order/payment callers must not get settlement/fee noise."""
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
    assert len(results) == 1
    assert results[0]["status"] == "MATCHED"
    assert results[0]["pair_type"] == "order_payment"
    assert all(r["pair_type"] == "order_payment" for r in results)


def test_generated_dataset_includes_multi_record_types():
    dataset = generate_dataset(records=50, seed=42)
    types = {item.record_type for item in dataset}
    assert {"order", "payment", "settlement", "refund", "fee"} <= types
    assert generate_dataset(records=50, seed=42)[0].amount == dataset[0].amount


def test_generate_reconcile_persist_creates_exceptions_and_audit():
    outcome = generate_and_reconcile(records=50, seed=42, database_url="sqlite://")
    assert outcome["persisted"] is True
    assert outcome["summary"]["total"] > 50
    types = {item.get("exception_type") for item in outcome["results"] if item.get("exception_type")}
    for required in {
        "SETTLEMENT_MISSING",
        "ORPHAN_SETTLEMENT",
        "SETTLEMENT_AMOUNT_MISMATCH",
        "DUPLICATE_SETTLEMENT",
        "REFUND_MISSING",
        "ORPHAN_REFUND",
        "REFUND_EXCESSIVE",
        "MULTIPLE_REFUNDS",
        "FEE_MISSING",
        "FEE_UNEXPECTED",
        "FEE_DIFFERENCE",
    }:
        assert required in types, f"missing anomaly type {required}"

    repo = ReconciliationRepository("sqlite://")
    exceptions = repo.list_exceptions(limit=500)
    assert len(exceptions) > 0
    audit = repo.list_audit(limit=500)
    assert any(event.event_type == "reconciliation_decision" for event in audit)
    rows = repo.list_recent(limit=500)
    assert any(getattr(row, "pair_type", None) == "payment_settlement" for row in rows)


def test_ai_evidence_includes_settlement_ids_without_inventing_amounts():
    from app.ai.evidence import build_evidence_packet
    from app.models import ExceptionRecord

    run_reconciliation(
        [
            _payment("PM-AI-S", "100.00"),
            _settlement("ST-AI-S", "PM-AI-S", "80.00"),
        ],
        database_url="sqlite://",
    )
    repo = ReconciliationRepository("sqlite://")
    exceptions = repo.list_exceptions(limit=50)
    target = next(ex for ex in exceptions if ex.exception_type == "SETTLEMENT_AMOUNT_MISMATCH")
    packet = build_evidence_packet(target)
    assert packet.payment_id == "PM-AI-S" or packet.settlement_id == "ST-AI-S"
    assert packet.order_amount is None
    assert packet.payment_amount is None
    assert packet.difference is not None
