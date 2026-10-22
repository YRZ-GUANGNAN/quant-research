"""绩效指标测试：用可手算的序列验证公式。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.engine.metrics import (
    annualized_return,
    max_drawdown,
    performance,
    sharpe,
    to_returns,
)


def test_max_drawdown_hand_computed() -> None:
    nav = pd.Series([1.0, 1.1, 0.9, 1.2])
    assert max_drawdown(nav) == pytest.approx(0.9 / 1.1 - 1.0)


def test_annualized_return_two_years() -> None:
    # 504 个交易日净翻倍 → 年化约 (2)^(1/2)-1 = 41.42%
    days = 505
    nav = pd.Series(np.linspace(1.0, 2.0, days))
    got = annualized_return(nav)
    assert got == pytest.approx(2.0 ** (252 / 504) - 1.0, rel=0.02)


def test_sharpe_positive_for_rising_series() -> None:
    rng = np.random.default_rng(0)
    rets = pd.Series(rng.normal(0.001, 0.01, 500))
    assert sharpe(rets) > 0


def test_sharpe_nan_safe_for_constant() -> None:
    assert sharpe(pd.Series([0.0] * 10)) == 0.0


def test_performance_has_expected_keys() -> None:
    nav = pd.Series(np.linspace(1.0, 1.5, 300), index=pd.date_range("2020-01-01", periods=300, freq="B"))
    bench = nav * 0.9
    m = performance(nav, benchmark=bench, name="t")
    for key in ("annual_return", "sharpe", "max_drawdown", "calmar", "win_rate", "beta"):
        assert key in m
    assert m["total_return"] == pytest.approx(0.5)


def test_to_returns_drops_first() -> None:
    nav = pd.Series([1.0, 1.1, 1.21])
    rets = to_returns(nav)
    assert len(rets) == 2
    assert rets.iloc[-1] == pytest.approx(0.1)
