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


def _fuzzy_candidates(card: dict, gl_rows: list[dict], used_gl_ids: set[str]) -> list[dict]:
    """Tier 2: find GL rows that match a card row on the composite fingerprint
    (amount within materiality + same account + date within the timing window),
    excluding GL rows already consumed by an earlier match."""
    c_amt = Decimal(card["amount"])
    c_day = _day(card["txn_timestamp"])
    out = []
    for g in gl_rows:
        if g["journal_id"] in used_gl_ids:
            continue
        if g["account_code"] != card["account_code"]:
            continue
        if not _within_materiality(c_amt, Decimal(g["amount"])):
            continue
        if abs((_day(g["posting_date"]) - c_day).days) > TIMING_WINDOW_DAYS:
            continue
        out.append(g)
    return out


def reconcile(card_rows: list[dict], gl_rows: list[dict]) -> list[ReconResult]:
    results: list[ReconResult] = []
    gl_by_auth = {r["auth_code"]: r for r in gl_rows if r["auth_code"]}
    matched_gl_ids: set[str] = set()

    for c in card_rows:
        # Tier 1: shared-key (auth code) — only when the card row carries one.
        gl = gl_by_auth.get(c["auth_code"]) if c["auth_code"] else None

        if gl is None:
            # Tier 2: composite / fuzzy match when no shared key is available.
            candidates = _fuzzy_candidates(c, gl_rows, matched_gl_ids)
            if len(candidates) == 1:
                gl = candidates[0]
                matched_gl_ids.add(gl["journal_id"])
                results.append(ReconResult(
                    card_id=c["source_id"], gl_id=gl["journal_id"], auth_code="(none)",
                    card_amount=c["amount"], gl_amount=gl["amount"],
                    status="MATCHED_FUZZY",
                    reason="No shared key; matched on amount + account + date window (composite key)."))
                continue
            elif len(candidates) > 1:
                # Tier 3: genuinely ambiguous — multiple equally-valid counterparts.
                results.append(ReconResult(
                    card_id=c["source_id"], gl_id=None, auth_code="(none)",
                    card_amount=c["amount"], gl_amount=None,
                    status="AMBIGUOUS",
                    reason=f"{len(candidates)} possible GL matches on composite key — route to human review."))
                continue
            else:
                results.append(ReconResult(
                    card_id=c["source_id"], gl_id=None, auth_code=c["auth_code"] or "(none)",
                    card_amount=c["amount"], gl_amount=None,
                    status="BREAK_UNMATCHED",
                    reason="Card transaction has no GL counterpart — investigate (missing posting)."))
                continue

        matched_gl_ids.add(gl["journal_id"])
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

    # GL entries never matched and not part of an ambiguous cluster
    ambiguous_amts = {(r.card_amount) for r in results if r.status == "AMBIGUOUS"}
    for gl in gl_rows:
        if gl["journal_id"] not in matched_gl_ids:
            if not gl["auth_code"] and gl["amount"] in ambiguous_amts:
                # part of an ambiguous composite cluster already flagged for review
                continue
            results.append(ReconResult(
                card_id=None, gl_id=gl["journal_id"], auth_code=gl["auth_code"] or "(none)",
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
    auto = summary.get('MATCHED', 0) + summary.get('MATCHED_FUZZY', 0) + summary.get('TIMING', 0)
    exceptions = sum(n for s, n in summary.items() if s.startswith("BREAK") or s == "AMBIGUOUS")
    print(f"\n  Auto-reconciled (MATCHED + MATCHED_FUZZY + TIMING): {auto}"
          f"  |  Exceptions for review: {exceptions}")


if __name__ == "__main__":
    main()
