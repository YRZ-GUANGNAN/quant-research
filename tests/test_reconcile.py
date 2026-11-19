from __future__ import annotations

from quant.automation.reconcile import reconcile


EXP = [{"symbol": "AAPL.US", "side": "buy", "qty": 10},
       {"symbol": "MSFT.US", "side": "sell", "qty": 5}]


def test_all_filled():
    orders = [
        {"symbol": "AAPL.US", "side": "Buy", "status": "Filled", "executed_quantity": "10", "order_id": "1"},
        {"symbol": "MSFT.US", "side": "Sell", "status": "Filled", "executed_quantity": "5", "order_id": "2"},
    ]
    r = reconcile(EXP, orders)
    assert r["ok"] is True
    assert all(l["status"] == "filled" for l in r["lines"])


def test_partial_detected():
    orders = [
        {"symbol": "AAPL.US", "side": "Buy", "status": "PartialFilled", "executed_quantity": "4"},
        {"symbol": "MSFT.US", "side": "Sell", "status": "Filled", "executed_quantity": "5"},
    ]
    r = reconcile(EXP, orders)
    assert len(r["partial"]) == 1 and r["partial"][0]["symbol"] == "AAPL.US"
    assert r["ok"] is True          # 部分成交不算异常，但会列出


def test_missing_order_detected():
    orders = [{"symbol": "AAPL.US", "side": "Buy", "status": "Filled", "executed_quantity": "10"}]
    r = reconcile(EXP, orders)
    assert [m["symbol"] for m in r["mismatched"]] == ["MSFT.US"]
    assert r["ok"] is False


def test_rejected_detected():
    orders = [
        {"symbol": "AAPL.US", "side": "Buy", "status": "Rejected", "executed_quantity": "0"},
        {"symbol": "MSFT.US", "side": "Sell", "status": "Filled", "executed_quantity": "5"},
    ]
    r = reconcile(EXP, orders)
    assert r["ok"] is False
    assert r["mismatched"][0]["status"] == "rejected"


def test_unexpected_order_detected():
    orders = [{"symbol": "TSLA.US", "side": "Buy", "status": "Filled", "executed_quantity": "1"}]
    r = reconcile(EXP, orders)
    assert r["unexpected"] and r["unexpected"][0]["symbol"] == "TSLA.US"
    assert r["ok"] is False


def test_pending_not_counted_as_failure():
    orders = [{"symbol": "AAPL.US", "side": "Buy", "status": "NotReported", "executed_quantity": "0"},
              {"symbol": "MSFT.US", "side": "Sell", "status": "NotReported", "executed_quantity": "0"}]
    r = reconcile(EXP, orders)
    assert r["ok"] is True
    assert all(l["status"] == "pending" for l in r["lines"])
