"""
Mal reconciliation engine.

Demonstrates the tiered matching strategy from the Technical Design Document,
Section 3:

    Tier 1  shared-key (exact) match      -> here, the card authorisation code
    Tier 2  composite / fuzzy match       -> amount + account + date-within-window
    Tier 3  unmatched                     -> route to human exception queue

Every result is then categorised as:
    MATCHED          agrees across systems within tolerance
    TIMING           same transaction, dates differ within the grace window
    BREAK_AMOUNT     matched identity, but amounts differ beyond materiality
    BREAK_UNMATCHED  no counterpart found in the other system

This is Checkpoint 2 (matching) in the architecture; it runs Silver -> Gold.
"""
from __future__ import annotations
import csv
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Tunable thresholds (agreed with finance; see TDD Section 5).
MATERIALITY_ABS = Decimal("0.01")   # ignore sub-cent rounding
MATERIALITY_PCT = Decimal("0.001")  # 0.1%
TIMING_WINDOW_DAYS = 1              # card swipe vs GL posting may differ by 1 day


@dataclass
class ReconResult:
    card_id: str | None
    gl_id: str | None
    auth_code: str | None
    card_amount: str | None
    gl_amount: str | None
    status: str
    reason: str


def _load(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _day(value: str) -> datetime:
    # accept both 'YYYY-MM-DD' and full ISO timestamps
    return datetime.fromisoformat(value).replace(hour=0, minute=0, second=0, microsecond=0)


def _within_materiality(a: Decimal, b: Decimal) -> bool:
    diff = abs(a - b)
    if diff <= MATERIALITY_ABS:
        return True
    base = max(abs(a), abs(b)) or Decimal("1")
    return (diff / base) <= MATERIALITY_PCT


def reconcile(card_rows: list[dict], gl_rows: list[dict]) -> list[ReconResult]:
    results: list[ReconResult] = []
    gl_by_auth = {r["auth_code"]: r for r in gl_rows}
    matched_gl: set[str] = set()

    for c in card_rows:
        gl = gl_by_auth.get(c["auth_code"])  # Tier 1: shared-key (auth code)

        if gl is None:
            # Tier 2 would attempt composite/fuzzy here; in this dataset the auth
            # code is the shared key, so absence means no counterpart -> Tier 3.
            results.append(ReconResult(
                card_id=c["source_id"], gl_id=None, auth_code=c["auth_code"],
                card_amount=c["amount"], gl_amount=None,
                status="BREAK_UNMATCHED",
                reason="Card transaction has no GL counterpart — investigate (missing posting)."))
            continue

        matched_gl.add(gl["auth_code"])
        c_amt, g_amt = Decimal(c["amount"]), Decimal(gl["amount"])
        day_gap = abs((_day(gl["posting_date"]) - _day(c["txn_timestamp"])).days)

        if not _within_materiality(c_amt, g_amt):
            results.append(ReconResult(
                card_id=c["source_id"], gl_id=gl["journal_id"], auth_code=c["auth_code"],
                card_amount=c["amount"], gl_amount=gl["amount"],
                status="BREAK_AMOUNT",
                reason=f"Amount mismatch beyond materiality (diff {abs(c_amt - g_amt)} AED) — investigate."))
        elif day_gap > TIMING_WINDOW_DAYS:
            results.append(ReconResult(
                card_id=c["source_id"], gl_id=gl["journal_id"], auth_code=c["auth_code"],
                card_amount=c["amount"], gl_amount=gl["amount"],
                status="BREAK_UNMATCHED",
                reason=f"Dates {day_gap} days apart, beyond grace window — investigate."))
        elif day_gap >= 1:
            results.append(ReconResult(
                card_id=c["source_id"], gl_id=gl["journal_id"], auth_code=c["auth_code"],
                card_amount=c["amount"], gl_amount=gl["amount"],
                status="TIMING",
                reason=f"Same transaction, posting lags swipe by {day_gap} day — recheck next run."))
        else:
            results.append(ReconResult(
                card_id=c["source_id"], gl_id=gl["journal_id"], auth_code=c["auth_code"],
                card_amount=c["amount"], gl_amount=gl["amount"],
                status="MATCHED",
                reason="Exact match on auth code; amounts agree; same day."))

    # GL entries with no card counterpart (possible duplicate / erroneous posting)
    for gl in gl_rows:
        if gl["auth_code"] not in matched_gl:
            results.append(ReconResult(
                card_id=None, gl_id=gl["journal_id"], auth_code=gl["auth_code"],
                card_amount=None, gl_amount=gl["amount"],
                status="BREAK_UNMATCHED",
                reason="GL entry has no card counterpart — possible duplicate or error."))

    return results


def main() -> None:
    card_rows = _load(DATA_DIR / "card_network.csv")
    gl_rows = _load(DATA_DIR / "general_ledger.csv")
    results = reconcile(card_rows, gl_rows)

    summary: dict[str, int] = {}
    print(f"{'STATUS':<16} {'CARD':<10} {'GL':<9} {'AUTH':<7} REASON")
    print("-" * 100)
    for r in results:
        summary[r.status] = summary.get(r.status, 0) + 1
        print(f"{r.status:<16} {str(r.card_id or '-'):<10} {str(r.gl_id or '-'):<9} "
              f"{str(r.auth_code or '-'):<7} {r.reason}")

    print("\nSUMMARY")
    for status, n in sorted(summary.items()):
        print(f"  {status:<16} {n}")
    breaks = sum(n for s, n in summary.items() if s.startswith("BREAK"))
    print(f"\n  Auto-reconciled (MATCHED+TIMING): {summary.get('MATCHED',0)+summary.get('TIMING',0)}"
          f"  |  Exceptions for review: {breaks}")


if __name__ == "__main__":
    main()
