"""
Data contract validation for Mal reconciliation.

Each contract enforces the rules described in the Technical Design Document,
Section 4. A record that violates a HARD rule is rejected before it can reach
matching; soft issues are flagged for review.

This mirrors Checkpoint 1 (data quality) in the architecture.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# Minimal Chart of Accounts for referential-integrity checks (Section 4, account_code).
VALID_ACCOUNTS = {"card_settlement", "crypto_settlement", "fees", "cash"}
SUPPORTED_CURRENCIES = {"AED", "USD", "EUR"}  # ISO 4217 subset Mal supports
OPEN_PERIOD_START = date(2026, 6, 1)
OPEN_PERIOD_END = date(2026, 6, 30)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


def _is_valid_decimal_2dp(value: str) -> bool:
    try:
        d = Decimal(value)
    except (InvalidOperation, TypeError):
        return False
    if d == 0:
        return False
    # exactly two decimal places for fiat
    return -d.as_tuple().exponent == 2


def validate_record(rec: dict, amount_field: str, date_field: str, id_field: str) -> ValidationResult:
    """Validate one record against the 5 core finance data contracts."""
    errors: list[str] = []

    # transaction_id: not null
    if not rec.get(id_field):
        errors.append(f"{id_field} is null (HARD: identity required)")

    # amount: not null, decimal, 2dp, non-zero
    amount = rec.get(amount_field)
    if amount is None or amount == "":
        errors.append("amount is null (HARD)")
    elif not _is_valid_decimal_2dp(str(amount)):
        errors.append(f"amount '{amount}' invalid (HARD: decimal, 2dp, non-zero)")

    # currency: ISO 4217, supported
    ccy = rec.get("currency")
    if ccy not in SUPPORTED_CURRENCIES:
        errors.append(f"currency '{ccy}' not supported (HARD)")

    # date: valid, not future, within open period
    raw_date = rec.get(date_field)
    try:
        d = datetime.fromisoformat(str(raw_date)).date()
        if d > date(2026, 6, 24):
            errors.append(f"{date_field} '{raw_date}' is in the future (HARD)")
        elif not (OPEN_PERIOD_START <= d <= OPEN_PERIOD_END):
            errors.append(f"{date_field} '{raw_date}' outside open period (HARD)")
    except (ValueError, TypeError):
        errors.append(f"{date_field} '{raw_date}' is not a valid date (HARD)")

    # account_code: referential integrity
    acct = rec.get("account_code")
    if acct not in VALID_ACCOUNTS:
        errors.append(f"account_code '{acct}' not in Chart of Accounts (HARD)")

    return ValidationResult(valid=len(errors) == 0, errors=errors)
