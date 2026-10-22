"""模型层测试：重点防「零交易伪装成零波动」。

背景 bug：IC 加权曾因 pandas 按列对齐（DataFrame * Series）导致权重全 NaN，
策略八年零交易，净值是一条平滑直线，看起来像「极其稳健」。
这里用回归测试锁死这个行为。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.config import BacktestConfig
from quant.data.panel import Panel
from quant.engine.backtest import Backtester
from quant.models.linear import ICWeightedStrategy


def make_panel(n_days: int = 480, n_codes: int = 40, seed: int = 11) -> Panel:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=n_days)
    codes = [f"{600000 + i:06d}" for i in range(n_codes)]
    # 制造一个真实可预测的结构：每只股票有独立的漂移项
    drift = rng.normal(0.0005, 0.0004, n_codes)
    rets = rng.normal(drift, 0.02, size=(n_days, n_codes))
    close = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=dates, columns=codes)
    volume = pd.DataFrame(rng.integers(1e6, 5e6, size=(n_days, n_codes)).astype(float), index=dates, columns=codes)
    frames = {
        "open": close.shift(1).fillna(close.iloc[0]),
        "close": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "volume": volume,
        "amount": volume * close,
        "pct_chg": close.pct_change() * 100,
        "turnover": volume / 1e7,
    }
    bench = pd.Series(3000 * np.cumprod(1 + rets.mean(axis=1)), index=dates)
    return Panel(frames=frames, benchmark=bench, names={c: c for c in codes}, index_code="000300")


def test_ic_weighted_strategy_actually_trades() -> None:
    """回归测试：IC 加权策略必须产生非空目标组合（防止权重 NaN 导致零交易）。"""
    panel = make_panel()
    cfg = BacktestConfig(top_k=10, rebalance_days=20)
    strat = ICWeightedStrategy(
        panel=panel,
        cfg=cfg,
        factors=["rev_5", "rev_20", "vol_20", "ma_gap_20"],
        horizon=5,
        ic_window=60,
        warmup=200,
    )
    weights = strat.target_weights(panel.dates[-1])
    assert weights, "IC 加权策略没有产生任何持仓——检查 pandas 对齐是否又出现 NaN"
    assert len(weights) <= cfg.top_k
    assert all(0 < w <= cfg.max_weight + 1e-9 for w in weights.values())


def test_ic_weighted_backtest_has_trades() -> None:
    panel = make_panel()
    cfg = BacktestConfig(top_k=10, rebalance_days=20)
    strat = ICWeightedStrategy(
        panel=panel, cfg=cfg, factors=["rev_5", "rev_20", "vol_20"],
        horizon=5, ic_window=60, warmup=200,
    )
    result = Backtester(panel, cfg).run(strat.weight_func(), name="icw")
    assert not result.trades.empty, "回测没有任何成交，净值曲线会是假平滑"
    assert result.nav.std() > 0, "净值序列无波动，说明策略完全没建仓"


def test_forward_rank_label_shape() -> None:
    """LightGBM 标签必须是 (date, code) 的 MultiIndex，与特征索引一致。"""
    panel = make_panel(n_days=60, n_codes=10)
    from quant.factors.transform import forward_returns

    fwd = forward_returns(panel.close, 5)
    y = fwd.rank(axis=1, pct=True).stack(future_stack=True)
    assert y.index.nlevels == 2
    assert y.notna().sum() > 0
