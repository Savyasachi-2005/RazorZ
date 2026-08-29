from __future__ import annotations

import csv
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List


def expected_fee_amount(payment_amount: Decimal) -> Decimal:
    """Deterministic processing fee used by generator and reconciliation."""
    return (payment_amount * Decimal("0.02")).quantize(Decimal("0.01"))


@dataclass
class GeneratedRecord:
    source: str
    record_type: str
    record_id: str
    reference: str
    amount: Decimal
    date: str
    customer: str
    payment_reference: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


class SyntheticDataGenerator:
    def __init__(self, seed: int = 42, records: int = 50):
        self.seed = seed
        self.records = records
        self.rng = random.Random(seed)

    def _amount(self) -> Decimal:
        base = self.rng.randint(50, 3000)
        cents = self.rng.randint(0, 99)
        return Decimal(f"{base}.{cents:02d}")

    def _customer(self, index: int) -> str:
        return f"CUST-{(index % 250) + 1:04d}"

    def _date(self, offset_days: int) -> str:
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=offset_days)
        return dt.date().isoformat()

    def build_dataset(self) -> List[GeneratedRecord]:
        records: List[GeneratedRecord] = []
        for i in range(self.records):
            order_ref = f"ORD-{i+1:05d}"
            amount = self._amount()
            customer = self._customer(i)
            order_date = self._date(i % 90)
            records.append(
                GeneratedRecord(
                    source="synthetic",
                    record_type="order",
                    record_id=f"OR-{i+1:05d}",
                    reference=order_ref,
                    amount=amount,
                    date=order_date,
                    customer=customer,
                    metadata={"status": "created"},
                )
            )
            payment_amount = amount
            if self.rng.random() < 0.08:
                payment_amount = payment_amount + Decimal("25.00")
            records.append(
                GeneratedRecord(
                    source="synthetic",
                    record_type="payment",
                    record_id=f"PM-{i+1:05d}",
                    reference=order_ref,
                    amount=payment_amount,
                    date=self._date((i % 90) + 1),
                    customer=customer,
                    metadata={
                        "method": "UPI",
                        "expects_settlement": True,
                        "expects_fee": True,
                        "expects_refund": False,
                        "expected_fee_amount": str(expected_fee_amount(payment_amount)),
                    },
                )
            )
        self._inject_anomalies(records)
        self._extend_settlements_refunds_fees(records)
        return records

    def _inject_anomalies(self, records: List[GeneratedRecord]) -> None:
        """Order/payment anomalies — preserve historical seed behavior."""
        for idx in range(0, len(records), 2):
            if idx + 1 >= len(records):
                break
            if idx % 17 == 0:
                records[idx].reference = f"ORD-{(idx // 2) + 101:05d}"
            if idx % 23 == 0:
                records[idx + 1].amount = records[idx + 1].amount + Decimal("75.00")
                records[idx + 1].metadata["expected_fee_amount"] = str(
                    expected_fee_amount(records[idx + 1].amount)
                )
            if idx % 31 == 0:
                records[idx].customer = "CUSTOMER-UNKNOWN"
            if idx % 41 == 0:
                records[idx + 1].reference = ""

    def _payments(self, records: List[GeneratedRecord]) -> List[GeneratedRecord]:
        return [r for r in records if r.record_type == "payment"]

    def _extend_settlements_refunds_fees(self, records: List[GeneratedRecord]) -> None:
        """Deterministic multi-record layer (index moduli — no extra RNG)."""
        payments = self._payments(records)
        settlement_seq = 0
        refund_seq = 0
        fee_seq = 0

        for i, payment in enumerate(payments):
            pay_idx = i + 1
            day_offset = (i % 90) + 2

            # --- Settlements ---
            # Missing settlement
            if pay_idx % 19 == 0:
                payment.metadata["expects_settlement"] = True
            else:
                settlement_seq += 1
                settle_amount = payment.amount
                # Settlement amount mismatch
                if pay_idx % 29 == 0:
                    settle_amount = settle_amount - Decimal("10.00")
                records.append(
                    GeneratedRecord(
                        source="synthetic",
                        record_type="settlement",
                        record_id=f"ST-{settlement_seq:05d}",
                        reference=payment.record_id,
                        payment_reference=payment.record_id,
                        amount=settle_amount,
                        date=self._date(day_offset),
                        customer=payment.customer,
                        metadata={"status": "processed"},
                    )
                )
                # Duplicate settlement
                if pay_idx % 37 == 0:
                    settlement_seq += 1
                    records.append(
                        GeneratedRecord(
                            source="synthetic",
                            record_type="settlement",
                            record_id=f"ST-{settlement_seq:05d}",
                            reference=payment.record_id,
                            payment_reference=payment.record_id,
                            amount=payment.amount,
                            date=self._date(day_offset + 1),
                            customer=payment.customer,
                            metadata={"status": "processed", "anomaly": "duplicate"},
                        )
                    )

            # --- Refunds ---
            # Valid expected refund on every 11th payment; dedicated indices for anomalies.
            if pay_idx % 11 == 0:
                payment.metadata["expects_refund"] = True
                refund_amount = (payment.amount * Decimal("0.25")).quantize(Decimal("0.01"))
                payment.metadata["expected_refund_amount"] = str(refund_amount)
                # Missing refund (expected but not created) — pay_idx 11 in a 50-record batch
                if pay_idx == 11:
                    pass
                else:
                    refund_seq += 1
                    # Excessive refund — pay_idx 22
                    if pay_idx == 22:
                        refund_amount = payment.amount + Decimal("50.00")
                    records.append(
                        GeneratedRecord(
                            source="synthetic",
                            record_type="refund",
                            record_id=f"RF-{refund_seq:05d}",
                            reference=f"REF-{pay_idx:05d}",
                            payment_reference=payment.record_id,
                            amount=refund_amount,
                            date=self._date(day_offset + 3),
                            customer=payment.customer,
                            metadata={"status": "processed"},
                        )
                    )
                    # Multiple refunds — pay_idx 33
                    if pay_idx == 33:
                        refund_seq += 1
                        second = (payment.amount * Decimal("0.10")).quantize(Decimal("0.01"))
                        records.append(
                            GeneratedRecord(
                                source="synthetic",
                                record_type="refund",
                                record_id=f"RF-{refund_seq:05d}",
                                reference=f"REF-{pay_idx:05d}-B",
                                payment_reference=payment.record_id,
                                amount=second,
                                date=self._date(day_offset + 4),
                                customer=payment.customer,
                                metadata={"status": "processed", "anomaly": "multiple"},
                            )
                        )

            # --- Fees ---
            expected = expected_fee_amount(payment.amount)
            payment.metadata["expected_fee_amount"] = str(expected)
            # Missing fee
            if pay_idx % 13 == 0:
                payment.metadata["expects_fee"] = True
            else:
                fee_amount = expected
                # Incorrect fee
                if pay_idx % 33 == 0:
                    fee_amount = expected + Decimal("5.00")
                fee_seq += 1
                records.append(
                    GeneratedRecord(
                        source="synthetic",
                        record_type="fee",
                        record_id=f"FE-{fee_seq:05d}",
                        reference=payment.record_id,
                        payment_reference=payment.record_id,
                        amount=fee_amount,
                        date=self._date(day_offset),
                        customer=payment.customer,
                        metadata={"fee_type": "processing", "expected_amount": str(expected)},
                    )
                )

        # Orphan settlement (unknown payment)
        settlement_seq += 1
        records.append(
            GeneratedRecord(
                source="synthetic",
                record_type="settlement",
                record_id=f"ST-{settlement_seq:05d}",
                reference="PM-UNKNOWN-01",
                payment_reference="PM-UNKNOWN-01",
                amount=Decimal("999.99"),
                date=self._date(10),
                customer="CUST-0000",
                metadata={"anomaly": "orphan"},
            )
        )

        # Orphan refund
        refund_seq += 1
        records.append(
            GeneratedRecord(
                source="synthetic",
                record_type="refund",
                record_id=f"RF-{refund_seq:05d}",
                reference="REF-ORPHAN",
                payment_reference="PM-UNKNOWN-02",
                amount=Decimal("120.00"),
                date=self._date(12),
                customer="CUST-0000",
                metadata={"anomaly": "orphan"},
            )
        )

        # Unexpected fee (no corresponding payment / not expected)
        fee_seq += 1
        records.append(
            GeneratedRecord(
                source="synthetic",
                record_type="fee",
                record_id=f"FE-{fee_seq:05d}",
                reference="PM-UNKNOWN-03",
                payment_reference="PM-UNKNOWN-03",
                amount=Decimal("15.00"),
                date=self._date(14),
                customer="CUST-0000",
                metadata={"anomaly": "unexpected", "fee_type": "processing"},
            )
        )

    def write_csv(self, path: str | Path, records: List[GeneratedRecord]) -> None:
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "source",
                    "record_type",
                    "record_id",
                    "reference",
                    "payment_reference",
                    "amount",
                    "date",
                    "customer",
                ],
            )
            writer.writeheader()
            for item in records:
                writer.writerow(
                    {
                        "source": item.source,
                        "record_type": item.record_type,
                        "record_id": item.record_id,
                        "reference": item.reference,
                        "payment_reference": item.payment_reference,
                        "amount": str(item.amount),
                        "date": item.date,
                        "customer": item.customer,
                    }
                )


def generate_dataset(records: int = 50, seed: int = 42, output_path: str | None = None) -> List[GeneratedRecord]:
    generator = SyntheticDataGenerator(seed=seed, records=records)
    dataset = generator.build_dataset()
    if output_path:
        generator.write_csv(output_path, dataset)
    return dataset
