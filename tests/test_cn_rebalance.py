"""A 股双侧再平衡的单测：卖单、T+1 可卖、现金约束、集中度、幂等。

全部走纯函数 plan_orders 与打桩后的 execute_target(dry_run=True)，不碰真实账户。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from quant.broker import cn_broker as cb  # noqa: E402


def _hold(qty: int, price: float, sellable: int | None = None) -> dict:
    return {"qty": qty, "price": price, "sellable": qty if sellable is None else sellable}


def test_exit_sells_position_not_in_target():
    plan, notes = cb.plan_orders(
        {"600000": 0.2}, cash=0, equity=100_000,
        holdings={"002151": _hold(1000, 20.0), "600000": _hold(0, 10.0)},
        prices={"600000": 10.0},
    )
    sells = [o for o in plan if o["side"] == "sell"]
    assert sells and sells[0]["code"] == "002151"
    assert sells[0]["qty"] == 1000
    assert sells[0]["reason"] == "exit"


def test_t_plus_one_blocks_sell_of_today_bought():
    plan, notes = cb.plan_orders(
        {}, cash=10_000, equity=100_000,
        holdings={"002151": _hold(1000, 20.0, sellable=0)},
    )
    assert plan == []
    assert any("T+1" in n for n in notes)


def test_trim_reduces_to_target_weight():
    # 目标 10% / 20 万 = 2 万，当前 1000 股 × 20 = 2 万 → 不需要动；改成 500 股目标则减 500
    tgt = 500 * 20 / 200_000
    plan, _ = cb.plan_orders({"002151": tgt}, cash=100_000, equity=200_000,
                             holdings={"002151": _hold(1000, 20.0)})
    assert len(plan) == 1
    assert plan[0]["side"] == "sell" and plan[0]["qty"] == 500
    assert plan[0]["reason"] == "trim"


def test_buys_respect_cash_and_buffer():
    # 现金 10 万、3% 留底 → 可买 9.7 万；目标权重合计 100% 也只能买这么多
    plan, _ = cb.plan_orders({"600000": 0.3, "600036": 0.3, "601318": 0.3},
                             cash=100_000, equity=100_000, holdings={},
                             prices={"600000": 10.0, "600036": 30.0, "601318": 40.0})
    total = sum(o["notional"] for o in plan)
    assert plan and total <= 100_000 * 0.97 + 1


def test_sell_proceeds_are_reusable_same_day():
    # 清仓腾出的钱可以当天买入新标的（A 股卖出资金当日可用）
    plan, _ = cb.plan_orders(
        {"600000": 0.5}, cash=0, equity=100_000,
        holdings={"002151": _hold(5000, 20.0)},
        prices={"600000": 10.0},
    )
    sides = [o["side"] for o in plan]
    assert sides == ["sell", "buy"]
    assert plan[1]["notional"] > 0


def test_position_weight_is_clamped():
    plan, _ = cb.plan_orders({"600000": 0.8}, cash=1_000_000, equity=1_000_000,
                             holdings={}, prices={"600000": 10.0})
    assert plan[0]["notional"] <= 1_000_000 * 0.30 + 1e-6


def test_sent_orders_are_skipped_for_idempotency():
    plan, notes = cb.plan_orders({"600000": 0.2}, cash=100_000, equity=100_000,
                                 holdings={}, prices={"600000": 10.0},
                                 sent={("600000", "buy")})
    assert plan == []
    assert any("已提交" in n for n in notes)


def test_tiny_orders_are_ignored():
    plan, _ = cb.plan_orders({"600000": 0.005}, cash=100_000, equity=100_000,
                             holdings={}, prices={"600000": 10.0})
    assert plan == []  # 500 元 < 3000 元最小买单


def test_lot_size_rounding():
    plan, _ = cb.plan_orders({"600000": 0.2}, cash=100_000, equity=100_000,
                             holdings={}, prices={"600000": 33.0})
    assert plan[0]["qty"] % 100 == 0


def test_execute_target_dry_run_reads_real_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("CN_ALLOW_EXECUTION", "0")
    monkeypatch.setattr(cb, "detect", lambda: {"ths": {"available": True, "blockers": []}})
    monkeypatch.setattr(cb, "_focus_trade_window", lambda: True)
    monkeypatch.setattr(cb, "_ths_account", lambda: object())

    def fake_retry(getter, attempts=2, delay=0.0):
        return getter()

    monkeypatch.setattr(cb, "_read_with_retry", fake_retry)
    monkeypatch.setattr(cb, "_read_balance_via_gui", lambda: {"可用金额": 50_000.0, "总资产": 200_000.0})
    monkeypatch.setattr(cb, "_read_positions_via_clipboard", lambda: [
        {"证券代码": "002151", "股票余额": "4200", "可用余额": "4200", "市价": "23.81"},
    ])
    monkeypatch.setattr(cb, "_ref_prices", lambda codes: {c: 10.0 for c in codes})
    monkeypatch.setattr(cb, "ROOT", tmp_path)          # 台账隔离，别读真实 journal
    monkeypatch.setattr(cb, "load_sent", lambda day=None: set())

    out = cb.execute_target({"600000": 0.2}, dry_run=True)
    assert out["dry_run"] is True
    assert out["account"]["equity"] == 200_000.0
    sides = {o["code"]: o["side"] for o in out["orders"]}
    assert sides["002151"] == "sell"   # 不在目标里 → 清仓
    assert sides["600000"] == "buy"    # 新开仓
    sides_seq = [o["side"] for o in out["orders"]]
    assert sides_seq[0] == "sell" and sides_seq[-1] == "buy"
    assert "sell" not in sides_seq[len([x for x in sides_seq if x == "sell"]):]


def test_execute_target_requires_switch(monkeypatch):
    monkeypatch.delenv("CN_ALLOW_EXECUTION", raising=False)
    monkeypatch.setattr(cb, "detect", lambda: {"ths": {"available": True, "blockers": []}})
    out = cb.execute_target({"600000": 0.2}, dry_run=False)
    assert out["ok"] is False and "CN_ALLOW_EXECUTION" in out["reason"]


def test_journal_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(cb, "ROOT", tmp_path)
    day = cb.datetime.date(2026, 11, 19)
    cb.append_journal({"code": "600000", "side": "buy", "status": "confirmed"}, day)
    cb.append_journal({"code": "600036", "side": "buy", "status": "unconfirmed"}, day)
    cb.append_journal({"code": "601318", "side": "sell", "status": "error: x"}, day)
    # 只有券商端确认过的才算「今日已提交」，未确认/报错的必须允许重试
    assert cb.load_sent(day) == {("600000", "buy")}


def test_clipboard_disabled_by_default(monkeypatch):
    """默认不读剪贴板：真实券商的防拷贝风控一复制就弹验证码并卡住界面。"""
    monkeypatch.delenv("CN_ALLOW_CLIPBOARD", raising=False)
    assert cb._clipboard_allowed() is False
    with pytest.raises(cb.BrokerGuard):
        cb._read_grid_via_clipboard("资金股票")


def test_clipboard_can_be_enabled_explicitly(monkeypatch):
    monkeypatch.setenv("CN_ALLOW_CLIPBOARD", "1")
    assert cb._clipboard_allowed() is True


def test_guard_error_is_not_retried(monkeypatch):
    """风控弹窗必须立刻上抛：重试只会继续弹验证码。"""
    calls = {"n": 0}

    def boom(tab=None):
        calls["n"] += 1
        raise cb.BrokerGuard("正在拷贝数据")

    monkeypatch.setenv("CN_ALLOW_CLIPBOARD", "1")
    monkeypatch.setattr(cb, "_read_grid_via_clipboard", boom)
    with pytest.raises(cb.BrokerGuard):
        cb._read_positions_via_clipboard()
    assert calls["n"] == 1


def test_execute_target_returns_blocked_on_guard(monkeypatch):
    monkeypatch.setenv("CN_ALLOW_CLIPBOARD", "1")
    monkeypatch.setenv("CN_ALLOW_EXECUTION", "1")
    monkeypatch.setattr(cb, "detect", lambda: {"ths": {"available": True, "blockers": []}})
    monkeypatch.setattr(cb, "account_guard", lambda: (True, ""))
    monkeypatch.setattr(cb, "_focus_trade_window", lambda: True)
    monkeypatch.setattr(cb, "_ths_account", lambda: object())
    monkeypatch.setattr(cb, "_read_with_retry", lambda getter, attempts=2, delay=0.0: getter())
    monkeypatch.setattr(cb, "_read_balance_via_gui", lambda: {"可用金额": 1.0, "总资产": 1.0})

    def boom():
        raise cb.BrokerGuard("正在拷贝数据")

    monkeypatch.setattr(cb, "_read_positions_via_clipboard", boom)
    out = cb.execute_target({"600000": 0.2}, dry_run=False)
    assert out["ok"] is False and out.get("blocked") is True
    assert "风控" in out["reason"]


def test_submit_order_enforces_lot_and_journals(tmp_path, monkeypatch):
    """单笔提交：不足一手拒绝、成功记账、失败如实记账。"""
    monkeypatch.setattr(cb, "ROOT", tmp_path)
    calls = []

    class _Acct:
        def buy(self, code, price=None, amount=None):
            calls.append(("buy", code, amount))

        def sell(self, code, price=None, amount=None):
            calls.append(("sell", code, amount))

    rec = cb.submit_order("600000", "buy", 250, 10.05, acct=_Acct())
    assert rec["status"] == "sent" and rec["qty"] == 200
    assert calls == [("buy", "600000", 200)]

    bad = cb.submit_order("600000", "buy", 50, 10.0, acct=_Acct())
    assert "error" in bad["status"]

    class _Boom:
        def buy(self, *a, **kw):
            raise RuntimeError("券商拒单")

    err = cb.submit_order("600000", "buy", 100, 10.0, acct=_Boom())
    assert err["status"].startswith("error:") and "券商拒单" in err["status"]

    sent = cb.load_sent()
    assert ("600000", "buy") not in sent   # sent 状态不算已确认，允许重试


def test_risk_cn_submit_uses_shared_implementation(monkeypatch):
    from quant.automation import risk_execute as rx

    called = []
    monkeypatch.setattr(cb, "submit_order",
                        lambda code, side, qty, price, **kw: called.append((code, side, qty, price))
                        or {"status": "sent"})
    out = rx._cn_submit(rx.OrderPlan("600000", "buy", 200, 10.0, "test", ""))
    assert called == [("600000", "buy", 200, 10.0)]
    assert out["status"] == "submitted"
