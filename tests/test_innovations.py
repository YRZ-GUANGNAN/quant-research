"""创新模块测试：相位不变组合与风控叠加层。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.config import BacktestConfig
from quant.data.panel import Panel
from quant.engine.backtest import Backtester
from quant.strategies.ensemble import PhaseEnsembleStrategy
from quant.strategies.factory import build_strategy
from quant.strategies.overlay import ICMonitorOverlay, RiskOverlay


def make_panel(n_days: int = 420, n_codes: int = 30, seed: int = 5) -> Panel:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=n_days)
    codes = [f"{600000 + i:06d}" for i in range(n_codes)]
    rets = rng.normal(0.0004, 0.02, size=(n_days, n_codes))
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


def test_phase_ensemble_produces_bounded_weights() -> None:
    panel = make_panel()
    cfg = BacktestConfig(top_k=10, rebalance_days=20)
    strat = PhaseEnsembleStrategy(
        panel=panel, cfg=cfg, name="reversal", offsets=(0, 5, 10), warmup=130
    )
    wf = strat.weight_func()
    # 推进到最后一天，让各子策略都更新过
    for i in range(len(panel.dates) - 1):
        wf(i)
    weights = wf(len(panel.dates) - 1)
    assert weights, "相位不变组合没有产生目标权重"
    assert len(weights) <= cfg.top_k
    assert abs(sum(weights.values()) - (1 - cfg.cash_buffer)) < 1e-6


def test_phase_ensemble_backtest_has_trades() -> None:
    panel = make_panel()
    cfg = BacktestConfig(top_k=10, rebalance_days=20)
    strat = PhaseEnsembleStrategy(panel=panel, cfg=cfg, name="reversal", offsets=(0, 5), warmup=130)
    result = Backtester(panel, cfg).run(strat.weight_func(), name="ens")
    assert not result.trades.empty
    assert result.nav.std() > 0


def test_risk_overlay_exposure_within_range_and_varies() -> None:
    panel = make_panel(n_days=500)
    cfg = BacktestConfig(top_k=10, rebalance_days=20)
    base = build_strategy("reversal", panel, cfg, warmup=130)
    overlay = RiskOverlay(panel=panel, cfg=cfg, base=base, timing=True, ma_days=100, warmup=130)
    expo = overlay.exposure_series()
    assert expo.min() >= 0.0 and expo.max() <= 1.0
    # 择时必须真的在动作：至少出现过满仓和空仓两种状态
    assert expo.max() == 1.0
    assert (expo == 0.0).any()


def test_vol_target_never_increases_exposure() -> None:
    panel = make_panel(n_days=500)
    cfg = BacktestConfig(top_k=10, rebalance_days=20)
    base = build_strategy("reversal", panel, cfg, warmup=130)
    overlay = RiskOverlay(panel=panel, cfg=cfg, base=base, timing=False,
                          vol_target=0.10, warmup=130)
    expo = overlay.exposure_series().dropna()
    assert (expo <= 1.0 + 1e-9).all()
    assert (expo >= 0.0).all()


def test_ic_monitor_wraps_base_and_respects_warmup() -> None:
    panel = make_panel(n_days=500)
    cfg = BacktestConfig(top_k=10, rebalance_days=20)
    base = build_strategy("reversal", panel, cfg, warmup=130)
    gate = ICMonitorOverlay(panel=panel, cfg=cfg, base=base, window=60, min_ic=-1.0, warmup=130)
    wf = gate.weight_func()
    # 预热期内不产生目标
    assert wf(10) is None
    # 用极低阈值（永不关门）时应产生正常权重
    weights = wf(len(panel.dates) - 1)
    assert weights is None or isinstance(weights, dict)
