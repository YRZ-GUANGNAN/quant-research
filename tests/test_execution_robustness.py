"""执行层鲁棒性：脏数据 / 异常输入下不许崩、不许发出荒谬的委托。

交易系统的坏数据比坏逻辑更常见：行情缺一只、现金为负、权重重复、价格为 0 或 NaN。
这些情况都应该「少做」而不是「崩掉」或「乱下单」。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation.rebalance import Limits, plan_rebalance  # noqa: E402


def _limits(**kw) -> Limits:
    base = dict(lot_size=1, min_notional=100.0, max_order_notional=10_000.0, max_orders=20)
    base.update(kw)
    return Limits(**base)


def test_missing_prices_produce_no_orders():
    plan, notes = plan_rebalance({"AAA": 0.5}, cash=10_000, capital=10_000,
                                 holdings={}, prices={}, limits=_limits())
    assert plan == []
    assert any("无报价" in n for n in notes)


def test_zero_or_nan_price_is_skipped():
    plan, _ = plan_rebalance({"AAA": 0.5, "BBB": 0.5}, cash=10_000, capital=10_000,
                             holdings={}, prices={"AAA": 0.0, "BBB": float("nan")},
                             limits=_limits())
    assert plan == []


def test_zero_cash_and_no_proceeds_means_no_buys():
    plan, _ = plan_rebalance({"AAA": 0.5}, cash=0, capital=10_000,
                             holdings={}, prices={"AAA": 100.0}, limits=_limits())
    assert [o for o in plan if o["side"] == "buy"] == []


def test_sell_never_exceeds_holdings():
    plan, _ = plan_rebalance({"BBB": 0.1}, cash=0, capital=100_000,
                             holdings={"AAA": 100.0, "BBB": 500.0},
                             prices={"AAA": 100.0, "BBB": 100.0}, limits=_limits())
    sells = {o["symbol"]: o["qty"] for o in plan if o["side"] == "sell"}
    assert sells.get("AAA", 0) <= 100.0
    assert sells.get("BBB", 0) <= 500.0


def test_weights_are_normalized_for_sleeves():
    """袖套权重只描述结构：0.3+0.3 应等价于 0.5+0.5。"""
    a, _ = plan_rebalance({"AAA": 0.3, "BBB": 0.3}, cash=100_000, capital=100_000,
                          holdings={}, prices={"AAA": 10.0, "BBB": 10.0}, limits=_limits())
    b, _ = plan_rebalance({"AAA": 0.5, "BBB": 0.5}, cash=100_000, capital=100_000,
                          holdings={}, prices={"AAA": 10.0, "BBB": 10.0}, limits=_limits())
    assert {o["symbol"]: o["qty"] for o in a} == {o["symbol"]: o["qty"] for o in b}


def test_duplicate_keys_collapse():
    plan, _ = plan_rebalance({"AAA": 0.5}, cash=10_000, capital=10_000,
                             holdings={}, prices={"AAA": 10.0}, limits=_limits())
    symbols = [o["symbol"] for o in plan]
    assert symbols.count("AAA") == 1


def test_order_count_cap_is_respected():
    targets = {f"S{i}": 1 / 30 for i in range(30)}
    prices = {f"S{i}": 10.0 for i in range(30)}
    plan, notes = plan_rebalance(targets, cash=100_000, capital=100_000,
                                 holdings={}, prices=prices, limits=_limits(max_orders=5))
    assert len(plan) <= 5
    assert any("上限" in n for n in notes)


def test_negative_cash_does_not_create_buys():
    plan, _ = plan_rebalance({"AAA": 0.5}, cash=-5_000, capital=10_000,
                             holdings={}, prices={"AAA": 10.0}, limits=_limits())
    assert [o for o in plan if o["side"] == "buy"] == []


def test_empty_targets_liquidates_holdings():
    plan, _ = plan_rebalance({}, cash=0, capital=10_000,
                             holdings={"AAA": 100.0}, prices={"AAA": 100.0},
                             limits=_limits())
    assert [o for o in plan if o["side"] == "sell" and o["symbol"] == "AAA"]
