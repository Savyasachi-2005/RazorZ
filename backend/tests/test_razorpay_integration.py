from __future__ import annotations

from decimal import Decimal

from app.integrations.razorpay.client import RazorpayClient
from app.integrations.razorpay.errors import RazorpayIntegrationError
from app.integrations.razorpay.mapper import (
    map_fee_from_payment,
    map_order,
    map_payment,
    map_refund,
    normalize_razorpay_payload,
)
from app.integrations.razorpay.service import sync_and_reconcile
from app.reconciliation.engine import reconcile_records


def test_map_order_and_payment_paise_to_decimal():
    order = map_order(
        {
            "id": "order_ABC",
            "amount": 12550,
            "currency": "INR",
            "receipt": "rcpt_1",
            "created_at": 1704067200,
            "status": "paid",
            "notes": {},
        }
    )
    payment = map_payment(
        {
            "id": "pay_XYZ",
            "amount": 12550,
            "currency": "INR",
            "order_id": "order_ABC",
            "created_at": 1704067300,
            "status": "captured",
            "fee": 295,
            "method": "upi",
        }
    )
    assert order["amount"] == "125.50"
    assert payment["amount"] == "125.50"
    assert payment["reference"] == "order_ABC"
    assert payment["metadata"]["expects_fee"] is True
    fee = map_fee_from_payment(
        {"id": "pay_XYZ", "fee": 295, "tax": 45, "created_at": 1704067300}
    )
    assert fee is not None
    assert fee["amount"] == "2.95"
    assert fee["payment_reference"] == "pay_XYZ"


def test_normalize_and_reconcile_razorpay_shaped_records():
    records = normalize_razorpay_payload(
        orders=[
            {
                "id": "order_1",
                "amount": 10000,
                "receipt": "order_1",
                "created_at": 1704067200,
                "status": "paid",
                "notes": {},
            }
        ],
        payments=[
            {
                "id": "pay_1",
                "amount": 10000,
                "order_id": "order_1",
                "created_at": 1704067300,
                "status": "captured",
                "fee": 200,
            }
        ],
        refunds=[],
        settlements=[],
    )
    # Force order reference match: mapper uses receipt or id
    results = reconcile_records(records)
    assert any(r["pair_type"] == "order_payment" and r["status"] == "MATCHED" for r in results)
    assert any(r["pair_type"] == "payment_fee" and r["status"] == "MATCHED" for r in results)


def test_map_refund_links_payment():
    refund = map_refund(
        {
            "id": "rfnd_1",
            "payment_id": "pay_1",
            "amount": 2500,
            "created_at": 1704067400,
            "status": "processed",
        }
    )
    assert refund["payment_reference"] == "pay_1"
    assert Decimal(refund["amount"]) == Decimal("25.00")


def test_client_blocks_live_without_flag():
    client = RazorpayClient(key_id="rzp_live_xxx", key_secret="secret", mode="live", allow_live=False)
    try:
        client.assert_ready()
        assert False, "expected live block"
    except RazorpayIntegrationError as exc:
        assert exc.code == "live_blocked"


def test_client_requires_test_key_prefix():
    client = RazorpayClient(key_id="rzp_live_xxx", key_secret="secret", mode="test")
    try:
        client.assert_ready()
        assert False, "expected key mode mismatch"
    except RazorpayIntegrationError as exc:
        assert exc.code == "key_mode_mismatch"


def test_sync_empty_account(monkeypatch):
    class EmptyClient(RazorpayClient):
        def __init__(self):
            super().__init__(key_id="rzp_test_demo", key_secret="secret", mode="test")

        def assert_ready(self) -> None:
            return None

        def fetch_collection(self, resource: str, *, count: int = 100, skip: int = 0):
            return []

    outcome = sync_and_reconcile(count=10, database_url="sqlite://", client=EmptyClient())
    assert outcome["empty"] is True
    assert outcome["persisted"] is False
    assert outcome["counts"]["normalized"] == 0


def test_sync_and_reconcile_with_stubbed_api(monkeypatch):
    class StubClient(RazorpayClient):
        def __init__(self):
            super().__init__(key_id="rzp_test_demo", key_secret="secret", mode="test")

        def assert_ready(self) -> None:
            return None

        def fetch_collection(self, resource: str, *, count: int = 100, skip: int = 0):
            if resource == "orders":
                return [
                    {
                        "id": "order_stub",
                        "amount": 50000,
                        "receipt": "order_stub",
                        "created_at": 1704067200,
                        "status": "paid",
                        "notes": {},
                    }
                ]
            if resource == "payments":
                return [
                    {
                        "id": "pay_stub",
                        "amount": 50000,
                        "order_id": "order_stub",
                        "created_at": 1704067300,
                        "status": "captured",
                        "fee": 1000,
                    }
                ]
            if resource == "refunds":
                return []
            if resource == "settlements":
                return []
            return []

    outcome = sync_and_reconcile(count=10, database_url="sqlite://", client=StubClient())
    assert outcome["empty"] is False
    assert outcome["persisted"] is True
    assert outcome["summary"]["total"] > 0
    assert any(r.get("pair_type") == "order_payment" for r in outcome["results"])


def test_live_shaped_razorpay_order_payment_matches_after_customer_normalization():
    """Regression: real Test Mode payloads had order.customer='' vs payment.email.

    Exact match requires customer equality. Email must stay in metadata only.
    Structure mirrored from a live rzp_test sync (order_TUPyuZptGZlGfm / pay_TUQ0hDxQw72raQ).
    """
    orders = [
        {
            "id": "order_TUPyuZptGZlGfm",
            "entity": "order",
            "amount": 50000,
            "amount_paid": 50000,
            "amount_due": 0,
            "currency": "INR",
            "receipt": "",
            "status": "paid",
            "attempts": 2,
            "notes": [],
            "created_at": 1787751613,
        }
    ]
    payments = [
        {
            "id": "pay_TUQ0hDxQw72raQ",
            "entity": "payment",
            "amount": 50000,
            "currency": "INR",
            "status": "captured",
            "order_id": "order_TUPyuZptGZlGfm",
            "invoice_id": None,
            "method": "netbanking",
            "email": "void@razorpay.com",
            "contact": "+918147893200",
            "fee": 1298,
            "tax": 198,
            "captured": True,
            "created_at": 1787751714,
        },
        # Non-captured attempt on same order — must not enter recon as a second candidate.
        {
            "id": "pay_TUQ0BBqiMcDfpz",
            "entity": "payment",
            "amount": 50000,
            "currency": "INR",
            "status": "created",
            "order_id": "order_TUPyuZptGZlGfm",
            "email": "void@razorpay.com",
            "contact": "+918147893200",
            "fee": None,
            "created_at": 1787751685,
        },
    ]
    records = normalize_razorpay_payload(orders=orders, payments=payments, refunds=[], settlements=[])
    assert all(r["customer"] == "" for r in records if r["record_type"] in {"order", "payment"})
    assert not any(r["record_id"] == "pay_TUQ0BBqiMcDfpz" for r in records)

    results = reconcile_records(records)
    order_payment = [r for r in results if r["pair_type"] == "order_payment"]
    assert any(
        r["status"] == "MATCHED"
        and r["record_id"] == "order_TUPyuZptGZlGfm"
        and r["matched_with"] == "pay_TUQ0hDxQw72raQ"
        for r in order_payment
    )
    assert not any(r.get("exception_type") == "ORPHAN_PAYMENT" for r in order_payment)
    assert any(r["pair_type"] == "payment_fee" and r["status"] == "MATCHED" for r in results)


def test_genuine_amount_mismatch_still_review_required():
    records = normalize_razorpay_payload(
        orders=[
            {
                "id": "order_mismatch",
                "amount": 50000,
                "currency": "INR",
                "receipt": "",
                "status": "paid",
                "notes": {},
                "created_at": 1787751613,
            }
        ],
        payments=[
            {
                "id": "pay_mismatch",
                "amount": 48000,
                "currency": "INR",
                "status": "captured",
                "order_id": "order_mismatch",
                "email": "void@razorpay.com",
                "fee": 100,
                "created_at": 1787751714,
            }
        ],
        refunds=[],
        settlements=[],
    )
    results = reconcile_records(records)
    assert any(
        r["pair_type"] == "order_payment"
        and r["exception_type"] == "AMOUNT_MISMATCH"
        and r["status"] == "REVIEW_REQUIRED"
        for r in results
    )


def test_payment_email_preserved_in_metadata_not_customer():
    payment = map_payment(
        {
            "id": "pay_meta",
            "amount": 10000,
            "order_id": "order_meta",
            "status": "captured",
            "email": "void@razorpay.com",
            "contact": "+911234567890",
            "created_at": 1704067300,
        }
    )
    assert payment["customer"] == ""
    assert payment["metadata"]["email"] == "void@razorpay.com"
    assert payment["metadata"]["contact"] == "+911234567890"
