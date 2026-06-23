-- ============================================================================
-- Mal reconciliation — core matching logic (SQL reference implementation)
--
-- This is the SQL expression of the tiered matching in src/reconcile.py.
-- It runs on the Silver layer (cleaned, conformed, amounts normalised to AED)
-- and writes the labelled result that lands in Gold (TDD Section 2 & 3).
--
-- Tables assumed (Silver):
--   silver_card(source_id, auth_code, amount_aed, txn_date, account_code)
--   silver_gl(journal_id, auth_code, amount_aed, posting_date, account_code)
--
-- Tunable thresholds (TDD Section 5): materiality 0.01 AED / 0.1%, timing 1 day.
-- ============================================================================

-- Tier 1 here is the shared-key (auth_code) match. The Python reference
-- (src/reconcile.py) additionally implements Tier 2 (composite/fuzzy match on
-- amount + account + date-window for rows lacking a shared key) and Tier 3
-- (ambiguous clusters routed to human review). In SQL the Tier-2 fallback is a
-- second LEFT JOIN on (amount within tolerance, account_code, date window),
-- applied only to rows unmatched by Tier 1.
WITH joined AS (
    -- Tier 1: shared-key (exact) match on the card authorisation code.
    -- A real implementation adds a Tier-2 composite/fuzzy LEFT JOIN fallback
    -- (amount + account + date window) for rows where auth_code is absent.
    SELECT
        c.source_id        AS card_id,
        g.journal_id       AS gl_id,
        COALESCE(c.auth_code, g.auth_code) AS auth_code,
        c.amount_aed       AS card_amount,
        g.amount_aed       AS gl_amount,
        c.txn_date         AS card_date,
        g.posting_date     AS gl_date
    FROM silver_card c
    FULL OUTER JOIN silver_gl g
        ON c.auth_code = g.auth_code
)
SELECT
    card_id,
    gl_id,
    auth_code,
    card_amount,
    gl_amount,
    CASE
        WHEN card_id IS NULL OR gl_id IS NULL
            THEN 'BREAK_UNMATCHED'
        WHEN ABS(card_amount - gl_amount) > GREATEST(0.01, 0.001 * GREATEST(ABS(card_amount), ABS(gl_amount)))
            THEN 'BREAK_AMOUNT'
        WHEN ABS(DATE_PART('day', gl_date::timestamp - card_date::timestamp)) > 1
            THEN 'BREAK_UNMATCHED'
        WHEN ABS(DATE_PART('day', gl_date::timestamp - card_date::timestamp)) >= 1
            THEN 'TIMING'
        ELSE 'MATCHED'
    END AS status,
    CASE
        WHEN card_id IS NULL
            THEN 'GL entry has no card counterpart — possible duplicate or error.'
        WHEN gl_id IS NULL
            THEN 'Card transaction has no GL counterpart — investigate.'
        WHEN ABS(card_amount - gl_amount) > GREATEST(0.01, 0.001 * GREATEST(ABS(card_amount), ABS(gl_amount)))
            THEN 'Amount mismatch beyond materiality — investigate.'
        WHEN ABS(DATE_PART('day', gl_date::timestamp - card_date::timestamp)) >= 1
            THEN 'Posting lags swipe within grace window — recheck next run.'
        ELSE 'Exact match; amounts agree; same day.'
    END AS reason
FROM joined
ORDER BY status, card_id;
