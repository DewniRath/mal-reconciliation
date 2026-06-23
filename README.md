# Mal — Finance Reconciliation (Sample Implementation)

Sample reconciliation logic and data contracts for the Mal pre-launch
reconciliation programme. This repository accompanies the Part 1 Technical
Design Document and demonstrates the core engine on a small synthetic dataset.

It is intentionally self-contained (standard library only) so it runs anywhere
with `python src/reconcile.py`.

## What this demonstrates

The reconciliation engine compares two independent records of the same money —
the **card network** (what was charged) and the **General Ledger** (what the
books posted) — and explains every difference. It implements the tiered
matching strategy and exception categorisation from the design document.

### Tiered matching (Section 3 of the TDD)

1. **Shared-key (exact) match** — on the card authorisation code, which both the
   network and the GL retain. Most reliable; preferred where a shared key exists.
2. **Composite / fuzzy match** — fallback on `amount + account + date-within-window`
   when no shared key is present. The date window absorbs timing differences.
3. **Human exception queue** — whatever remains unmatched or ambiguous.

### Outcome categories

| Status            | Meaning                                                        | Action            |
|-------------------|----------------------------------------------------------------|-------------------|
| `MATCHED`         | Agrees across systems within tolerance                         | None              |
| `TIMING`          | Same transaction; posting lags swipe within the grace window   | Recheck next run  |
| `BREAK_AMOUNT`    | Identity matches but amounts differ beyond materiality         | Investigate       |
| `BREAK_UNMATCHED` | No counterpart in the other system                             | Investigate       |

Distinguishing a **timing difference** (money in transit, resolves itself) from a
**real break** (genuine discrepancy) is the central job of the engine.

## The sample data tells a story

The synthetic dataset (`data/`) is built so each outcome appears exactly once:

| Card        | GL        | What it shows                                            |
|-------------|-----------|---------------------------------------------------------|
| CRD-88812   | GL-5567   | Clean match (1,000 AED, same day)                       |
| CRD-88813   | GL-5568   | Timing difference (card 22nd, GL posts 23rd)            |
| CRD-88814   | GL-5569   | Amount break (card 750, GL 745 — 5 AED off)             |
| CRD-88815/16| —         | In card network, missing from GL                        |
| —           | GL-5571   | In GL, no card counterpart (possible duplicate/error)   |

## Run it

```bash
python src/reconcile.py        # run the engine, print the labelled results
python -m pytest -q            # verify the four outcomes are correctly produced
```

Expected: 1 matched, 1 timing, and 4 exceptions routed for review.

## Data contracts (Section 4 of the TDD)

`src/contracts.py` enforces the five core finance data contracts at ingestion
(Checkpoint 1): `transaction_id`, `amount`, `posting_date`, `account_code`,
`currency` — each with type, not-null, validity, and a finance-specific rule
(e.g. amount is decimal not float; account_code must exist in the Chart of
Accounts; currency must be a supported ISO 4217 code).

## SQL reference

`sql/reconcile.sql` expresses the same matching logic as a single query over the
Silver tables, for execution in the warehouse. The Python version is the runnable
demonstration; the SQL version is how it would run in production at scale.

## Layout

```
data/      synthetic card + GL records (the story above)
src/       reconcile.py (engine) · contracts.py (data contracts)
sql/       reconcile.sql (warehouse matching logic)
tests/     verifies the four outcome categories + contract checks
```

## Notes & simplifications

- Thresholds (materiality 0.01 AED / 0.1%, 1-day timing window) are tunable and
  would be agreed with finance.
- For clarity the demo uses the auth code as a present shared key; production
  adds the Tier-2 composite/fuzzy fallback and a correlation ID minted at source
  to make future matching exact (TDD Section 3.3).
- Amounts are assumed already normalised to AED in Silver; FX/crypto conversion
  happens upstream.
