from __future__ import annotations

import pandas as pd

from quant.config import BacktestConfig
from quant.engine.backtest import execute_target
from quant.engine.broker import PaperBroker


def _cfg() -> BacktestConfig:
    return BacktestConfig(top_k=10, rebalance_days=20)


def test_a_share_rebalance_buys_when_underweight():
    """A 股本地引擎：明显低配时会真实买入（证明交易链路可用）。"""
    cfg = _cfg()
    broker = PaperBroker(cash=1_000_000.0, cfg=cfg)
    prices = pd.Series({"600519": 1400.0, "000001": 11.0})
    target = {"600519": 0.15, "000001": 0.10}     # 100 万里要买 15 万茅台、10 万平安
    execute_target(broker, cfg, "2026-11-19", prices, prices, target)
    assert broker.trades, "应产生成交"
    bought = [t for t in broker.trades if t.side == "buy"]
    assert bought, "应有买入"
    assert "600519" in broker.positions


def test_a_share_rebalance_sells_orphan_position():
    """目标外持仓（偏离 100%）会先被卖出。"""
    cfg = _cfg()
    broker = PaperBroker(cash=1_000_000.0, cfg=cfg)   # 茅台 100 股需 14 万，现金要够
    prices = pd.Series({"600519": 1400.0, "000001": 11.0})
    execute_target(broker, cfg, "2026-11-18", prices, prices, {"600519": 0.5})
    assert "600519" in broker.positions
    broker.settle()   # T+1：次日结算后才可卖
    execute_target(broker, cfg, "2026-11-19", prices, prices, {"000001": 0.5})
    assert "600519" not in broker.positions
    assert any(t.side == "sell" for t in broker.trades)


def test_a_share_no_trade_inside_band():
    """±20% 带内不动手（避免过度交易）。"""
    cfg = _cfg()
    broker = PaperBroker(cash=1_000_000.0, cfg=cfg)
    prices = pd.Series({"600519": 1400.0})
    execute_target(broker, cfg, "init", prices, prices, {"600519": 0.10})
    n = len(broker.trades)
    execute_target(broker, cfg, "2026-11-19", prices, prices, {"600519": 0.11})
    assert len(broker.trades) == n
