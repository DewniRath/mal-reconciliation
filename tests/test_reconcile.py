"""
Tests for the reconciliation engine.

These lock in the four outcomes the engine must distinguish, so a reviewer can
see the logic is verified, not just asserted. Run with:  python -m pytest -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reconcile import reconcile, _load, DATA_DIR  # noqa: E402
from contracts import validate_record  # noqa: E402


def _results():
    card = _load(DATA_DIR / "card_network.csv")
    gl = _load(DATA_DIR / "general_ledger.csv")
    return {(r.card_id, r.gl_id): r for r in reconcile(card, gl)}


def test_clean_match():
    r = _results()[("CRD-88812", "GL-5567")]
    assert r.status == "MATCHED"


def test_timing_difference():
    r = _results()[("CRD-88813", "GL-5568")]
    assert r.status == "TIMING"


def test_amount_break():
    r = _results()[("CRD-88814", "GL-5569")]
    assert r.status == "BREAK_AMOUNT"


def test_card_without_gl():
    r = _results()[("CRD-88815", None)]
    assert r.status == "BREAK_UNMATCHED"


def test_gl_without_card():
    r = _results()[(None, "GL-5571")]
    assert r.status == "BREAK_UNMATCHED"


def test_contract_rejects_bad_currency():
    bad = {"source_id": "X1", "amount": "100.00", "currency": "XYZ",
           "txn_timestamp": "2026-06-22T10:00:00", "account_code": "card_settlement"}
    res = validate_record(bad, "amount", "txn_timestamp", "source_id")
    assert not res.valid


def test_contract_accepts_good_record():
    good = {"source_id": "X1", "amount": "100.00", "currency": "AED",
            "txn_timestamp": "2026-06-22T10:00:00", "account_code": "card_settlement"}
    res = validate_record(good, "amount", "txn_timestamp", "source_id")
    assert res.valid
