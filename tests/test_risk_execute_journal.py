"""风险动作执行的幂等台账（同一交易日同一标的同一方向只发一次）。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation import risk_execute as rx  # noqa: E402


def _order(symbol: str, side: str, qty: float = 100.0, price: float = 10.0):
    return rx.OrderPlan(symbol=symbol, side=side, qty=qty, price=price, reason="test")


def test_journal_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rx, "ROOT", tmp_path)
    day = date(2026, 11, 27)
    rx.append_journal({"symbol": "NVDA.US", "side": "sell", "status": "submitted"}, day)
    rx.append_journal({"symbol": "HD.US", "side": "buy", "status": "error: 拒单"}, day)
    assert rx.load_sent(day) == {("NVDA.US", "sell")}


def _trading(monkeypatch):
    """把时段查询固定为开市，避免测试依赖真实交易时段。"""
    import quant.automation.execution as ex
    monkeypatch.setattr(ex, "market_sessions", lambda: {"US": "Trading", "HK": "Trading", "CN": "Trading"})


def test_execute_skips_already_sent(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rx, "ROOT", tmp_path)
    monkeypatch.setenv("RISK_ALLOW_EXECUTION", "1")
    _trading(monkeypatch)
    calls: list[str] = []

    def fake_submit(order):
        calls.append(order.symbol)
        return {"symbol": order.symbol, "side": order.side, "status": "submitted", "order_id": "x"}

    monkeypatch.setattr(rx, "_lb_submit", fake_submit)
    monkeypatch.setattr(rx, "load_sent", lambda day=None: {("NVDA.US", "sell")})

    out = rx.execute([], market="us", dry_run=False, positions={}, quotes={}, limits=rx.Limits())
    assert out["ok"] is True
    assert calls == []  # 没有真正发单


def test_execute_respects_switch(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rx, "ROOT", tmp_path)
    monkeypatch.delenv("RISK_ALLOW_EXECUTION", raising=False)
    # dry_run=False 但没开关 → 只算不发，且不写台账
    out = rx.execute([], market="us", dry_run=False, positions={}, quotes={}, limits=rx.Limits())
    assert out.get("ok") is False
    assert "RISK_ALLOW_EXECUTION" in str(out.get("reason"))


def test_to_orders_dry_run_has_no_journal_side_effect(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(rx, "ROOT", tmp_path)
    out = rx.execute([], market="us", dry_run=True, positions={}, quotes={}, limits=rx.Limits())
    assert out["dry_run"] is True
    assert not (tmp_path / "state").exists()


def test_execute_blocked_outside_session(tmp_path: Path, monkeypatch):
    """休市不该发风控单：否则全被拒，对账还会出现假 missing。"""
    import quant.automation.execution as ex

    monkeypatch.setattr(rx, "ROOT", tmp_path)
    monkeypatch.setenv("RISK_ALLOW_EXECUTION", "1")
    monkeypatch.setattr(ex, "market_sessions", lambda: {"US": "Closed", "HK": "Closed", "CN": "Closed"})
    out = rx.execute([], market="us", dry_run=False, positions={}, quotes={}, limits=rx.Limits())
    assert out.get("blocked") is True
    assert "非交易时段" in str(out.get("reason"))


def test_risk_orders_skip_symbols_already_planned(tmp_path, monkeypatch):
    """再平衡当天已经安排过的标的，风控不能重复下单（否则同一标的被卖两次）。"""
    from quant.automation import reconcile as rc

    monkeypatch.setattr(rx, "ROOT", tmp_path)
    monkeypatch.setattr(rc, "EXPECTED_DIR", tmp_path)
    monkeypatch.setenv("RISK_ALLOW_EXECUTION", "1")
    _trading(monkeypatch)
    rc.record_expected("us", [{"symbol": "TSLA.US", "side": "sell", "qty": 196}])

    calls = []

    def fake_submit(order):
        calls.append(order.symbol)
        return {"symbol": order.symbol, "side": order.side, "status": "submitted", "order_id": "x"}

    monkeypatch.setattr(rx, "_lb_submit", fake_submit)

    import quant.automation.risk_guard as rg

    actions = [rg.Action(action="exit", symbol="TSLA.US", reason="止损")]
    assert actions  # 确保真的走了「有动作但被去重」的路径
    positions = {"TSLA.US": {"qty": 196, "last": 378.0}}
    quotes = {"TSLA.US": {"last": 378.0}}
    out = rx.execute(actions, market="us", dry_run=False, positions=positions, quotes=quotes,
                     limits=rx.Limits())
    assert "TSLA.US" not in calls
    assert out["ok"] is True


def test_limit_price_has_buffer():
    import quant.automation.risk_guard as rg

    pos = {"NVDA.US": {"qty": 100, "last": 200.0}}
    quotes = {"NVDA.US": {"last": 200.0}}
    orders = rx.to_orders([rg.Action(action="exit", symbol="NVDA.US", reason="止损")],
                          pos, quotes, rx.Limits())
    sell = [o for o in orders if o.side == "sell"][0]
    assert sell.price < 200.0   # 卖单必须低于最新价，否则挂不上
