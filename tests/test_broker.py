"""模拟盘撮合器的规则测试。

这些测试不是形式主义：每一条都对应真实交易规则，规则写错会让回测数字整体失真。
覆盖：A 股（T+1/涨跌停/印花税）与美股（T+0/无涨跌停/零佣金）。
"""

from __future__ import annotations

import pytest

from quant.config import BacktestConfig, Costs
from quant.engine.broker import PaperBroker, price_limit_pct


def make_broker(cash: float = 1_000_000.0, slippage: float = 0.0) -> PaperBroker:
    cfg = BacktestConfig(costs=Costs(slippage=slippage))
    return PaperBroker(cash, cfg)


def make_us_broker(cash: float = 1_000_000.0, slippage: float = 0.0) -> PaperBroker:
    from dataclasses import replace

    cfg = BacktestConfig.for_market("us")
    cfg = cfg.with_(costs=replace(cfg.costs, slippage=slippage))
    return PaperBroker(cash, cfg)


def test_price_limit_pct_boards() -> None:
    cn = BacktestConfig()
    assert price_limit_pct("600000", cn) == 0.10      # 主板
    assert price_limit_pct("000001", cn) == 0.10
    assert price_limit_pct("300750", cn) == 0.20      # 创业板
    assert price_limit_pct("688981", cn) == 0.20      # 科创板
    # 美股无涨跌停
    us = BacktestConfig.for_market("us")
    assert price_limit_pct("AAPL", us) == 0.0


def test_buy_rounds_down_to_lot() -> None:
    broker = make_broker()
    got = broker.buy("2020-01-02", "600000", 150, 10.0, 9.9)
    assert got == 100
    assert broker.positions["600000"].shares == 100


def test_us_lot_size_is_one_share() -> None:
    broker = make_us_broker()
    assert broker.buy("2020-01-02", "AAPL", 7, 100.0, 99.0) == 7


def test_buy_respects_cash() -> None:
    broker = make_broker(cash=1_000.0)
    got = broker.buy("2020-01-02", "600000", 1000, 10.0, 9.9)
    # 1000 元买 100 股需要 1000 + 费用 > 1000，现金不足 → 不成交
    assert got == 0
    assert broker.cash == pytest.approx(1_000.0)


def test_t_plus_one_blocks_same_day_sell() -> None:
    broker = make_broker()
    broker.buy("2020-01-02", "600000", 100, 10.0, 9.9)
    assert broker.sell("2020-01-02", "600000", 100, 10.1, 9.9) == 0
    broker.settle()
    assert broker.sell("2020-01-03", "600000", 100, 10.1, 9.9) == 100


def test_us_t_plus_zero_allows_same_day_sell() -> None:
    broker = make_us_broker()
    broker.buy("2020-01-02", "AAPL", 10, 100.0, 99.0)
    assert broker.sell("2020-01-02", "AAPL", 10, 101.0, 100.0) == 10


def test_limit_up_blocks_buy() -> None:
    broker = make_broker()
    # 开盘 11.0 = 前收 10.0 的 +10%，封涨停无法买入
    assert broker.buy("2020-01-02", "600000", 100, 11.0, 10.0) == 0
    # 创业板 +20% 才封板
    assert broker.buy("2020-01-02", "300750", 100, 11.0, 10.0) == 100


def test_limit_down_blocks_sell() -> None:
    broker = make_broker()
    broker.buy("2020-01-02", "600000", 100, 10.0, 9.9)
    broker.settle()
    assert broker.sell("2020-01-03", "600000", 100, 9.0, 10.0) == 0


def test_commission_minimum_and_fees() -> None:
    broker = make_broker()
    broker.buy("2020-01-02", "600000", 100, 1.0, 0.99)
    trade = broker.trades[-1]
    amount = 100 * 1.0
    expected_fee = 5.0 + amount * 1e-5      # 最低佣金 5 元 + 过户费
    assert trade.fee == pytest.approx(expected_fee)


def test_sell_includes_stamp_duty() -> None:
    broker = make_broker()
    broker.buy("2020-01-02", "600000", 1000, 10.0, 9.9)
    broker.settle()
    broker.sell("2020-01-03", "600000", 1000, 10.0, 10.0)
    sell_trade = [t for t in broker.trades if t.side == "sell"][-1]
    amount = 1000 * 10.0
    expected = max(amount * 2.5e-4, 5.0) + amount * (1e-5 + 5e-4)
    assert sell_trade.fee == pytest.approx(expected)


def test_us_zero_commission() -> None:
    broker = make_us_broker()
    broker.buy("2020-01-02", "AAPL", 10, 100.0, 99.0)
    assert broker.trades[-1].fee == pytest.approx(0.0)


def test_slippage_moves_price() -> None:
    broker = make_broker(slippage=5e-4)
    broker.buy("2020-01-02", "600000", 100, 10.0, 9.9)
    assert broker.trades[-1].price == pytest.approx(10.0 * 1.0005)


def test_equity_after_mark() -> None:
    broker = make_broker(cash=100_000.0)
    broker.buy("2020-01-02", "600000", 1000, 10.0, 9.9)
    broker.mark({"600000": 11.0})
    eq = broker.equity()
    assert eq > 100_000.0
    assert broker.position_weight("600000") == pytest.approx(11_000 / eq)


def test_close_all_clears_positions() -> None:
    broker = make_broker()
    broker.buy("2020-01-02", "600000", 100, 10.0, 9.9)
    broker.buy("2020-01-02", "000001", 100, 8.0, 7.9)
    broker.settle()
    broker.close_all("2020-01-03", {"600000": 10.2, "000001": 8.1}, {"600000": 10.0, "000001": 8.0})
    assert broker.positions == {}
