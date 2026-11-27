from __future__ import annotations

import pytest

from quant.automation import execution


def test_env_gate_blocks_execute(monkeypatch):
    monkeypatch.delenv("LB_ALLOW_EXECUTION", raising=False)
    monkeypatch.setenv("LB_ACCOUNT_KIND", "paper")
    out = execution.execute("hk", dry_run=False)
    assert out["ok"] is False and "LB_ALLOW_EXECUTION" in out["reason"]


def test_account_kind_gate(monkeypatch):
    monkeypatch.setenv("LB_ALLOW_EXECUTION", "1")
    monkeypatch.delenv("LB_ACCOUNT_KIND", raising=False)
    out = execution.execute("us", dry_run=False)
    assert out["ok"] is False and "LB_ACCOUNT_KIND" in out["reason"]


def test_session_gate_blocks_closed_market(monkeypatch):
    monkeypatch.setenv("LB_ALLOW_EXECUTION", "1")
    monkeypatch.setenv("LB_ACCOUNT_KIND", "paper")
    monkeypatch.setattr(execution, "market_sessions", lambda: {"HK": "Closed", "US": "Closed"})
    out = execution.execute("hk", dry_run=False)
    assert out["ok"] is False and "非交易时段" in out["reason"]


def test_policy_limits_order_count(monkeypatch, tmp_path):
    monkeypatch.setattr(execution, "SNAPSHOT", tmp_path / "snap.json")
    (tmp_path / "snap.json").write_text(
        '{"sleeves":{"us":{"weights":{"A.US":0.1,"B.US":0.1,"C.US":0.1},'
        '"rebalance_date":"2026-11-01","next_rebalance_date":"2026-12-01"}}}', encoding="utf-8")
    monkeypatch.setattr(execution, "_quotes", lambda s: {x: {"last": 100.0} for x in s})
    monkeypatch.setattr(execution, "_positions", lambda: {})
    monkeypatch.setattr(execution, "_pending_symbols", lambda: set())
    monkeypatch.setattr(execution, "_cash", lambda ccy: 100_000.0)
    policy = execution.Policy(max_orders_per_run=2, min_order_notional_usd=100.0)
    plan = execution.build_plan("us", policy)
    assert plan["ok"] is True
    assert len(plan["orders"]) <= 2


def test_policy_blocks_oversized_order(monkeypatch, tmp_path):
    monkeypatch.setattr(execution, "SNAPSHOT", tmp_path / "snap.json")
    (tmp_path / "snap.json").write_text(
        '{"sleeves":{"us":{"weights":{"A.US":1.0},'
        '"rebalance_date":"2026-11-01","next_rebalance_date":"2026-12-01"}}}', encoding="utf-8")
    monkeypatch.setattr(execution, "_quotes", lambda s: {x: {"last": 500.0} for x in s})
    monkeypatch.setattr(execution, "_positions", lambda: {})
    monkeypatch.setattr(execution, "_pending_symbols", lambda: set())
    monkeypatch.setattr(execution, "_cash", lambda ccy: 100_000.0)
    policy = execution.Policy(max_notional_per_order_usd=1000.0, min_order_notional_usd=100.0)
    plan = execution.build_plan("us", policy)
    assert plan["orders"] == []


def test_no_cash_means_no_orders(monkeypatch, tmp_path):
    """无现金、无持仓、无目标外仓位 → 不产生订单（且不触网）。"""
    monkeypatch.setattr(execution, "SNAPSHOT", tmp_path / "snap.json")
    (tmp_path / "snap.json").write_text(
        '{"sleeves":{"hk":{"weights":{"700.HK":0.5},'
        '"rebalance_date":"2026-11-01","next_rebalance_date":"2026-12-01"}}}', encoding="utf-8")
    monkeypatch.setattr(execution, "_cash", lambda ccy: 0.0)
    monkeypatch.setattr(execution, "_quotes", lambda syms: {s: {"last": 300.0} for s in syms})
    monkeypatch.setattr(execution, "_positions", lambda: {})
    monkeypatch.setattr(execution, "_pending_symbols", lambda: set())
    plan = execution.build_plan("hk", execution.Policy())
    assert plan["ok"] is False
    assert plan["orders"] == []


def test_broker_symbol_maps_dashed_us_tickers():
    """模型用 BRK-B.US，券商接口要 BRK.B.US；不转换这类标的永远买不进去。"""
    assert execution.broker_symbol("BRK-B.US") == "BRK.B.US"
    assert execution.broker_symbol("AAPL.US") == "AAPL.US"
    assert execution.broker_symbol("0700.HK") == "0700.HK"


def test_quotes_falls_back_to_broker_symbol(monkeypatch):
    calls = []

    def fake_cli(args, timeout=40):
        calls.append(args[:2])
        if args[1] == "BRK-B.US":
            return []
        return [{"symbol": "BRK.B.US", "last": "582.2"}]

    monkeypatch.setattr(execution, "_cli", fake_cli)
    quotes = execution._quotes(["BRK-B.US"])
    assert quotes["BRK-B.US"]["last"] == "582.2"
    assert any("BRK.B.US" in c for c in calls)
