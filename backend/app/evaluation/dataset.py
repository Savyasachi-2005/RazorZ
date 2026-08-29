"""Held-out labelled dataset for evaluating the deterministic engine.

The batch is generated separately from the development dataset
(`app.data_generator.generate_dataset`, dev seed 42) and uses its own seed so
that no record the engine was tuned against appears here. Ground truth is
recorded at construction time — it is never derived from engine output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from app.data_generator import GeneratedRecord, SyntheticDataGenerator, expected_fee_amount

HELD_OUT_SEED = 90210
"""Fixed seed for the held-out batch. Must differ from the development seed."""

SCENARIOS: Tuple[str, ...] = (
    "clean_match",
    "missing_payment",
    "duplicate_settlement",
    "amount_mismatch",
    "fee_discrepancy",
    "refund",
    "partial_settlement",
    "ambiguous_match",
    "orphan_payment",
)

MATCH = "MATCH"
EXCEPTION = "EXCEPTION"


@dataclass(frozen=True)
class GroundTruthLabel:
    """Known correct outcome for one record within one relationship type."""

    record_id: str
    pair_type: str
    scenario: str
    expected_outcome: str  # MATCH | EXCEPTION
    counterpart_id: Optional[str] = None
    expected_exception_type: Optional[str] = None
    amount: Decimal = Decimal("0.00")

    @property
    def expects_match(self) -> bool:
        return self.expected_outcome == MATCH


@dataclass
class EvaluationDataset:
    seed: int
    records: List[GeneratedRecord] = field(default_factory=list)
    labels: List[GroundTruthLabel] = field(default_factory=list)

    def engine_records(self) -> List[Dict[str, Any]]:
        """Records as the engine consumes them — carries no ground truth."""
        return [
            {
                "source": record.source,
                "record_type": record.record_type,
                "record_id": record.record_id,
                "reference": record.reference,
                "payment_reference": record.payment_reference,
                "amount": record.amount,
                "date": record.date,
                "customer": record.customer,
                "metadata": dict(record.metadata),
            }
            for record in self.records
        ]

    def scenario_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {name: 0 for name in SCENARIOS}
        for label in self.labels:
            counts[label.scenario] = counts.get(label.scenario, 0) + 1
        return counts


class HeldOutDatasetBuilder:
    """Builds a labelled batch by cycling through the documented test cases."""

    def __init__(self, seed: int = HELD_OUT_SEED, cycles: int = 2):
        if cycles < 1:
            raise ValueError("cycles must be >= 1")
        self.seed = seed
        self.cycles = cycles
        # Amount/customer/date helpers are reused from the existing generator.
        self._gen = SyntheticDataGenerator(seed=seed, records=0)
        self._records: List[GeneratedRecord] = []
        self._labels: List[GroundTruthLabel] = []

    def build(self) -> EvaluationDataset:
        self._records = []
        self._labels = []
        case = 0
        for _ in range(self.cycles):
            for scenario in SCENARIOS:
                case += 1
                getattr(self, f"_case_{scenario}")(case)
        return EvaluationDataset(seed=self.seed, records=list(self._records), labels=list(self._labels))

    # --- record helpers -------------------------------------------------

    def _amount(self) -> Decimal:
        return self._gen._amount()

    def _order(self, case: int, amount: Decimal) -> GeneratedRecord:
        record = GeneratedRecord(
            source="held_out",
            record_type="order",
            record_id=f"EOR-{case:05d}",
            reference=f"EORD-{case:05d}",
            amount=amount,
            date=self._gen._date(case % 90),
            customer=self._gen._customer(case),
            metadata={"status": "created"},
        )
        self._records.append(record)
        return record

    def _payment(
        self,
        case: int,
        order: GeneratedRecord,
        amount: Decimal,
        *,
        suffix: str = "",
        expects_settlement: bool = True,
        expects_fee: bool = True,
        expects_refund: bool = False,
        expected_refund_amount: Optional[Decimal] = None,
    ) -> GeneratedRecord:
        metadata: Dict[str, Any] = {
            "method": "UPI",
            "expects_settlement": expects_settlement,
            "expects_fee": expects_fee,
            "expects_refund": expects_refund,
            "expected_fee_amount": str(expected_fee_amount(amount)),
        }
        if expected_refund_amount is not None:
            metadata["expected_refund_amount"] = str(expected_refund_amount)
        record = GeneratedRecord(
            source="held_out",
            record_type="payment",
            record_id=f"EPM-{case:05d}{suffix}",
            reference=order.reference,
            amount=amount,
            date=self._gen._date((case % 90) + 1),
            customer=order.customer,
            metadata=metadata,
        )
        self._records.append(record)
        return record

    def _child(
        self,
        record_type: str,
        prefix: str,
        case: int,
        payment: GeneratedRecord,
        amount: Decimal,
        *,
        suffix: str = "",
        day_shift: int = 2,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> GeneratedRecord:
        record = GeneratedRecord(
            source="held_out",
            record_type=record_type,
            record_id=f"{prefix}-{case:05d}{suffix}",
            reference=payment.record_id,
            payment_reference=payment.record_id,
            amount=amount,
            date=self._gen._date((case % 90) + day_shift),
            customer=payment.customer,
            metadata=metadata or {},
        )
        self._records.append(record)
        return record

    def _label(
        self,
        record: GeneratedRecord,
        pair_type: str,
        scenario: str,
        outcome: str,
        *,
        counterpart: Optional[GeneratedRecord] = None,
        exception_type: Optional[str] = None,
        amount: Optional[Decimal] = None,
    ) -> None:
        self._labels.append(
            GroundTruthLabel(
                record_id=record.record_id,
                pair_type=pair_type,
                scenario=scenario,
                expected_outcome=outcome,
                counterpart_id=counterpart.record_id if counterpart else None,
                expected_exception_type=exception_type,
                amount=amount if amount is not None else record.amount,
            )
        )

    def _clean_settlement_and_fee(self, case: int, scenario: str, payment: GeneratedRecord) -> None:
        settlement = self._child("settlement", "EST", case, payment, payment.amount)
        self._label(payment, "payment_settlement", scenario, MATCH, counterpart=settlement)
        fee = self._child("fee", "EFE", case, payment, expected_fee_amount(payment.amount))
        self._label(payment, "payment_fee", scenario, MATCH, counterpart=fee)

    # --- documented test cases ------------------------------------------

    def _case_clean_match(self, case: int) -> None:
        scenario = "clean_match"
        amount = self._amount()
        order = self._order(case, amount)
        payment = self._payment(case, order, amount)
        self._label(order, "order_payment", scenario, MATCH, counterpart=payment)
        self._clean_settlement_and_fee(case, scenario, payment)

    def _case_missing_payment(self, case: int) -> None:
        scenario = "missing_payment"
        order = self._order(case, self._amount())
        self._label(order, "order_payment", scenario, EXCEPTION, exception_type="PAYMENT_MISSING")

    def _case_duplicate_settlement(self, case: int) -> None:
        scenario = "duplicate_settlement"
        amount = self._amount()
        order = self._order(case, amount)
        payment = self._payment(case, order, amount)
        self._label(order, "order_payment", scenario, MATCH, counterpart=payment)
        self._child("settlement", "EST", case, payment, amount)
        self._child(
            "settlement",
            "EST",
            case,
            payment,
            amount,
            suffix="-B",
            day_shift=3,
            metadata={"anomaly": "duplicate"},
        )
        self._label(payment, "payment_settlement", scenario, EXCEPTION, exception_type="DUPLICATE_SETTLEMENT")
        fee = self._child("fee", "EFE", case, payment, expected_fee_amount(amount))
        self._label(payment, "payment_fee", scenario, MATCH, counterpart=fee)

    def _case_amount_mismatch(self, case: int) -> None:
        scenario = "amount_mismatch"
        amount = self._amount()
        order = self._order(case, amount)
        paid = amount + Decimal("40.00")
        payment = self._payment(case, order, paid)
        self._label(
            order,
            "order_payment",
            scenario,
            EXCEPTION,
            exception_type="AMOUNT_MISMATCH",
            amount=Decimal("40.00"),
        )
        self._clean_settlement_and_fee(case, scenario, payment)

    def _case_fee_discrepancy(self, case: int) -> None:
        scenario = "fee_discrepancy"
        amount = self._amount()
        order = self._order(case, amount)
        payment = self._payment(case, order, amount)
        self._label(order, "order_payment", scenario, MATCH, counterpart=payment)
        settlement = self._child("settlement", "EST", case, payment, amount)
        self._label(payment, "payment_settlement", scenario, MATCH, counterpart=settlement)
        self._child("fee", "EFE", case, payment, expected_fee_amount(amount) + Decimal("5.00"))
        self._label(
            payment,
            "payment_fee",
            scenario,
            EXCEPTION,
            exception_type="FEE_DIFFERENCE",
            amount=Decimal("5.00"),
        )

    def _case_refund(self, case: int) -> None:
        scenario = "refund"
        amount = self._amount()
        order = self._order(case, amount)
        refund_amount = (amount * Decimal("0.25")).quantize(Decimal("0.01"))
        payment = self._payment(
            case,
            order,
            amount,
            expects_refund=True,
            expected_refund_amount=refund_amount,
        )
        self._label(order, "order_payment", scenario, MATCH, counterpart=payment)
        self._clean_settlement_and_fee(case, scenario, payment)
        refund = self._child("refund", "ERF", case, payment, refund_amount, day_shift=5)
        refund.reference = f"EREF-{case:05d}"
        self._label(payment, "payment_refund", scenario, MATCH, counterpart=refund, amount=refund_amount)

    def _case_partial_settlement(self, case: int) -> None:
        scenario = "partial_settlement"
        amount = self._amount()
        order = self._order(case, amount)
        payment = self._payment(case, order, amount)
        self._label(order, "order_payment", scenario, MATCH, counterpart=payment)
        self._child("settlement", "EST", case, payment, amount - Decimal("30.00"))
        self._label(
            payment,
            "payment_settlement",
            scenario,
            EXCEPTION,
            exception_type="SETTLEMENT_AMOUNT_MISMATCH",
            amount=Decimal("30.00"),
        )
        fee = self._child("fee", "EFE", case, payment, expected_fee_amount(amount))
        self._label(payment, "payment_fee", scenario, MATCH, counterpart=fee)

    def _case_ambiguous_match(self, case: int) -> None:
        scenario = "ambiguous_match"
        amount = self._amount()
        order = self._order(case, amount)
        first = self._payment(
            case,
            order,
            amount + Decimal("15.00"),
            suffix="-A",
            expects_settlement=False,
            expects_fee=False,
        )
        second = self._payment(
            case,
            order,
            amount + Decimal("16.00"),
            suffix="-B",
            expects_settlement=False,
            expects_fee=False,
        )
        self._label(order, "order_payment", scenario, EXCEPTION, exception_type="AMBIGUOUS_MATCH")
        # Neither candidate may be auto-accepted; the exception type is not asserted.
        for candidate in (first, second):
            self._label(candidate, "order_payment", scenario, EXCEPTION)

    def _case_orphan_payment(self, case: int) -> None:
        scenario = "orphan_payment"
        amount = self._amount()
        payment = GeneratedRecord(
            source="held_out",
            record_type="payment",
            record_id=f"EPM-{case:05d}-ORPHAN",
            reference=f"EORPHAN-{case:05d}",
            amount=amount,
            date=self._gen._date((case % 90) + 1),
            customer=f"ECUST-{case:05d}",
            metadata={
                "method": "UPI",
                "expects_settlement": False,
                "expects_fee": False,
                "expects_refund": False,
                "anomaly": "orphan",
            },
        )
        self._records.append(payment)
        self._label(payment, "order_payment", scenario, EXCEPTION, exception_type="ORPHAN_PAYMENT")


def build_held_out_dataset(seed: int = HELD_OUT_SEED, cycles: int = 2) -> EvaluationDataset:
    """Generate the reproducible held-out batch (default ~62 records)."""
    return HeldOutDatasetBuilder(seed=seed, cycles=cycles).build()
