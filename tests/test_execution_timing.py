"""卖出→等待→买入 的顺序与等待语义（ADR：先卖后买之间要有上限等待）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation import execution  # noqa: E402


def test_wait_sells_returns_filled_status(monkeypatch):
    rows = [{"symbol": "AAA.US", "status": "Filled", "quantity": "100", "executed_quantity": "100"}]
    monkeypatch.setattr(execution, "_cli", lambda args, timeout=40: rows)
    out = execution._wait_sells({"AAA.US"}, timeout=1, interval=0.01)
    assert out["AAA.US"]["status"] == "Filled"
    assert out["AAA.US"]["filled"] == 100.0


def test_wait_sells_disabled_by_env(monkeypatch):
    monkeypatch.setenv("EXEC_WAIT_SELLS", "0")
    monkeypatch.setattr(execution, "_cli", lambda args, timeout=40: pytest.fail("不该查询"))
    assert execution._wait_sells({"AAA.US"}, timeout=1) == {}


def test_wait_sells_times_out_on_working_orders(monkeypatch):
    rows = [{"symbol": "AAA.US", "status": "Submitted", "quantity": "100", "executed_quantity": "0"}]
    monkeypatch.setattr(execution, "_cli", lambda args, timeout=40: rows)
    out = execution._wait_sells({"AAA.US"}, timeout=0.05, interval=0.01)
    assert out["AAA.US"]["status"] == "Submitted"


def test_wait_sells_matches_broker_symbol_format(monkeypatch):
    """模型写 BRK-B.US，券商返回 BRK.B.US，等待时也要能对上。"""
    rows = [{"symbol": "BRK.B.US", "status": "Filled", "quantity": "10", "executed_quantity": "10"}]
    monkeypatch.setattr(execution, "_cli", lambda args, timeout=40: rows)
    out = execution._wait_sells({"BRK-B.US"}, timeout=1, interval=0.01)
    assert out["BRK-B.US"]["status"] == "Filled"


def test_buy_power_helper_reads_assets(monkeypatch):
    monkeypatch.setattr(execution, "_cli",
                        lambda args, timeout=40: [{"currency": "USD", "buy_power": "1606194.45"}])
    assert execution._buy_power("USD") == 1606194.45


def test_buy_power_helper_handles_failure(monkeypatch):
    def boom(args, timeout=40):
        raise RuntimeError("network")
    monkeypatch.setattr(execution, "_cli", boom)
    assert execution._buy_power("USD") is None


def test_place_order_maps_dashed_symbol(monkeypatch):
    """下单必须把 BRK-B.US 换成券商要的 BRK.B.US。"""
    seen = []

    def fake_cli(args, timeout=40):
        seen.append(args[:4])
        if "--execute" in args:
            return {"order_id": "123"}
        return {"confirmation_code": "42"}

    monkeypatch.setattr(execution, "_cli", fake_cli)
    out = execution.place_order("buy", "BRK-B.US", 10, 500.0)
    assert out["status"] == "submitted" and out["order_id"] == "123"
    assert all("BRK.B.US" in s for s in seen)


def test_place_order_without_confirmation_code(monkeypatch):
    monkeypatch.setattr(execution, "_cli", lambda args, timeout=40: {})
    out = execution.place_order("sell", "AAPL.US", 5, 190.0)
    assert out["status"] == "no-confirmation-code"


def test_risk_uses_shared_place_order(monkeypatch):
    """风控下单也必须走统一实现（否则又会漏掉符号映射）。"""
    from quant.automation import risk_execute as rx

    called = []

    def fake_place(side, symbol, qty, price):
        called.append((side, symbol, qty, price))
        return {"symbol": symbol, "side": side, "status": "submitted", "order_id": "1"}

    monkeypatch.setattr(execution, "place_order", fake_place)
    out = rx._lb_submit(rx.OrderPlan("BRK-B.US", "buy", 10, 500.0, "replace", ""))
    assert called == [("buy", "BRK-B.US", 10, 500.0)]
    assert out["status"] == "submitted"
