from __future__ import annotations

import json

import pytest

from quant.automation import execution


def _snap(tmp_path, weights, market="us", next_reb="2099-01-01"):
    p = tmp_path / "snap.json"
    p.write_text(json.dumps({"sleeves": {market: {
        "weights": weights, "rebalance_date": "2026-11-01",
        "next_rebalance_date": next_reb}}}), encoding="utf-8")
    return p


def test_top_up_existing_position(monkeypatch, tmp_path):
    """已有持仓低于目标 → 生成加仓单。"""
    monkeypatch.setattr(execution, "SNAPSHOT", _snap(tmp_path, {"A.US": 0.5}))
    monkeypatch.setattr(execution, "_quotes", lambda s: {x: {"last": 100.0} for x in s})
    monkeypatch.setattr(execution, "_positions", lambda: {"A.US": 10.0})   # 现值 1000
    monkeypatch.setattr(execution, "_pending_symbols", lambda: set())
    monkeypatch.setattr(execution, "_cash", lambda ccy: 100_000.0)
    plan = execution.build_plan("us", execution.Policy(min_order_notional_usd=100.0))
    buys = [o for o in plan["orders"] if o["side"] == "buy"]
    assert buys and buys[0]["symbol"] == "A.US"
    assert buys[0]["qty"] > 0


def test_no_order_when_position_at_target(monkeypatch, tmp_path):
    """持仓市值已达到/超过目标（预算按市场权益计算）→ 不加仓。"""
    monkeypatch.setattr(execution, "SNAPSHOT", _snap(tmp_path, {"A.US": 0.5}))
    monkeypatch.setattr(execution, "_quotes", lambda s: {x: {"last": 100.0} for x in s})
    monkeypatch.setattr(execution, "_positions", lambda: {"A.US": 490.0})  # 市值 49,000
    monkeypatch.setattr(execution, "_pending_symbols", lambda: set())
    monkeypatch.setattr(execution, "_cash", lambda ccy: 0.0)               # 权益=持仓市值
    plan = execution.build_plan("us", execution.Policy(min_order_notional_usd=500.0))
    # 权益 49,000 → 预算 49,000*0.75*0.98≈36,015 → 目标 18,007 < 当前 49,000
    assert [o for o in plan["orders"] if o["side"] == "buy"] == []


def test_orphan_position_is_sold(monkeypatch, tmp_path):
    """目标外持仓 → 全额卖出（清理旧仓位）。"""
    monkeypatch.setattr(execution, "SNAPSHOT", _snap(tmp_path, {"A.US": 0.5}))
    monkeypatch.setattr(execution, "_quotes",
                        lambda s: {x: {"last": 100.0} for x in s})
    monkeypatch.setattr(execution, "_positions", lambda: {"OLD.US": 50.0})
    monkeypatch.setattr(execution, "_pending_symbols", lambda: set())
    monkeypatch.setattr(execution, "_cash", lambda ccy: 0.0)
    plan = execution.build_plan("us", execution.Policy(cleanup_orphans=True))
    sells = [o for o in plan["orders"] if o["side"] == "sell"]
    assert sells and sells[0]["symbol"] == "OLD.US" and sells[0]["qty"] == 50


def test_orphan_kept_by_default(monkeypatch, tmp_path):
    """默认不清理目标外持仓（避免一次性清仓）。"""
    monkeypatch.setattr(execution, "SNAPSHOT", _snap(tmp_path, {"A.US": 0.5}))
    monkeypatch.setattr(execution, "_quotes", lambda s: {x: {"last": 100.0} for x in s})
    monkeypatch.setattr(execution, "_positions", lambda: {"OLD.US": 50.0})
    monkeypatch.setattr(execution, "_pending_symbols", lambda: set())
    monkeypatch.setattr(execution, "_cash", lambda ccy: 100_000.0)
    plan = execution.build_plan("us", execution.Policy())
    assert [o for o in plan["orders"] if o["side"] == "sell"] == []


def test_expired_snapshot_blocks_plan(monkeypatch, tmp_path):
    monkeypatch.setattr(execution, "SNAPSHOT", _snap(tmp_path, {"A.US": 1.0}, next_reb="2020-01-01"))
    plan = execution.build_plan("us", execution.Policy())
    assert plan["ok"] is False and "过期" in plan["reason"]
