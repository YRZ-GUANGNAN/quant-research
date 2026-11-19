from __future__ import annotations

from quant.automation.action_log import evaluate_actions
from quant.automation.risk_guard import Action


def test_evaluate_good_when_sold_symbol_fell():
    recs = [{"date": "2026-11-01", "market": "us", "symbol": "CRM.US", "action": "exit",
             "price": 200.0, "replace_with": "WMT.US", "replace_price": 100.0, "reason": "止损"}]
    got = evaluate_actions(recs, {"CRM.US": 180.0, "WMT.US": 105.0})
    assert got["rows"][0]["verdict"] == "good"
    assert got["rows"][0]["change"] == -0.1
    assert got["rows"][0]["replace_change"] == 0.05


def test_evaluate_bad_when_sold_symbol_rose():
    recs = [{"date": "2026-11-01", "market": "us", "symbol": "A", "action": "exit",
             "price": 10.0, "replace_with": "", "replace_price": 0, "reason": "x"}]
    got = evaluate_actions(recs, {"A": 12.0})
    assert got["rows"][0]["verdict"] == "bad"


def test_skip_when_price_missing():
    recs = [{"date": "2026-11-01", "market": "us", "symbol": "A", "action": "exit", "price": 0}]
    assert evaluate_actions(recs, {"A": 10.0})["total"] == 0


def test_hit_rate():
    recs = [
        {"date": "d", "market": "us", "symbol": "A", "action": "exit", "price": 10.0, "replace_with": ""},
        {"date": "d", "market": "us", "symbol": "B", "action": "exit", "price": 10.0, "replace_with": ""},
    ]
    got = evaluate_actions(recs, {"A": 9.0, "B": 11.0})
    assert got["good"] == 1 and got["bad"] == 1 and got["hit_rate"] == 0.5
