import type { ExceptionItem, ReconciliationRecord } from "../api";

/** Map a ledger row to its exception using description/evidence (engine stores "STATUS for record_id"). */
export function findExceptionForRecord(
  row: ReconciliationRecord,
  exceptions: ExceptionItem[],
): ExceptionItem | null {
  const needles = [row.record_id, row.matched_with].filter(Boolean).map((v) => String(v).toLowerCase());
  if (!needles.length) return null;

  const matches = exceptions.filter((ex) => {
    const hay = `${ex.description} ${ex.evidence ?? ""}`.toLowerCase();
    return needles.some((n) => hay.includes(n));
  });
  if (!matches.length) return null;

  const typed = row.exception_type
    ? matches.filter((ex) => ex.exception_type === row.exception_type)
    : matches;
  const pool = typed.length ? typed : matches;
  return pool.find((ex) => ex.status === "OPEN") ?? pool[0] ?? null;
}

export function reviewPathForRecord(
  row: ReconciliationRecord,
  exceptions: ExceptionItem[],
): string {
  const match = findExceptionForRecord(row, exceptions);
  if (match) return `/exceptions/${match.id}`;
  return `/exceptions?record=${encodeURIComponent(row.record_id)}`;
}
