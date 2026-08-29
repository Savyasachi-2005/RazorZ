export function pct(rate: number, digits = 1): string {
  return `${(rate * 100).toFixed(digits)}%`;
}

export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (Number.isFinite(n)) {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 2,
    }).format(n);
  }
  return String(value);
}

export function conf(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (n <= 1) return pct(n, 0);
  return `${n.toFixed(0)}%`;
}

export function labelize(value: string): string {
  return value.replaceAll("_", " ");
}

export type PairSlots = {
  orderId: string;
  paymentId: string;
  settlementId: string;
  refundId: string;
  feeId: string;
};

/** Map ledger ids into typed columns without inventing data. */
export function splitPair(
  recordId: string,
  matchedWith: string | null,
  extras?: {
    pair_type?: string | null;
    order_id?: string | null;
    payment_id?: string | null;
    settlement_id?: string | null;
    refund_id?: string | null;
    fee_id?: string | null;
  },
): PairSlots {
  if (extras) {
    const hasTyped =
      extras.order_id ||
      extras.payment_id ||
      extras.settlement_id ||
      extras.refund_id ||
      extras.fee_id;
    if (hasTyped) {
      return {
        orderId: extras.order_id ?? "—",
        paymentId: extras.payment_id ?? "—",
        settlementId: extras.settlement_id ?? "—",
        refundId: extras.refund_id ?? "—",
        feeId: extras.fee_id ?? "—",
      };
    }
  }

  const orderLike = (id: string) => /^(or|ord|order)/i.test(id) || id.toLowerCase().includes("order");
  const payLike = (id: string) => /^(pm|pmt|pay)/i.test(id) || id.toLowerCase().includes("pay");
  const settleLike = (id: string) => /^(st|set)/i.test(id) || id.toLowerCase().includes("settle");
  const refundLike = (id: string) => /^(rf|ref)/i.test(id) || id.toLowerCase().includes("refund");
  const feeLike = (id: string) => /^(fe|fee)/i.test(id);

  const slots: PairSlots = {
    orderId: "—",
    paymentId: "—",
    settlementId: "—",
    refundId: "—",
    feeId: "—",
  };

  const place = (id: string) => {
    if (orderLike(id)) slots.orderId = id;
    else if (payLike(id)) slots.paymentId = id;
    else if (settleLike(id)) slots.settlementId = id;
    else if (refundLike(id)) slots.refundId = id;
    else if (feeLike(id)) slots.feeId = id;
  };

  place(recordId);
  if (matchedWith) place(matchedWith);

  // Legacy order/payment inference when prefixes are ambiguous
  if (slots.orderId === "—" && slots.paymentId === "—" && matchedWith) {
    if (orderLike(recordId) || payLike(matchedWith)) {
      slots.orderId = recordId;
      slots.paymentId = matchedWith;
    } else if (payLike(recordId) || orderLike(matchedWith)) {
      slots.orderId = matchedWith;
      slots.paymentId = recordId;
    } else {
      slots.orderId = recordId;
      slots.paymentId = matchedWith;
    }
  }

  return slots;
}

export function pairTypeLabel(pairType: string | null | undefined): string {
  switch (pairType) {
    case "payment_settlement":
      return "Payment ↔ Settlement";
    case "payment_refund":
      return "Payment ↔ Refund";
    case "payment_fee":
      return "Payment ↔ Fee";
    case "order_payment":
    default:
      return "Order ↔ Payment";
  }
}
