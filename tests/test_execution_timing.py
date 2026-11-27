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
