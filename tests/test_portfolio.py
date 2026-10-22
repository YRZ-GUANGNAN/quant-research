"""跨市场组合模块测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from portfolio import combine, granular_report  # noqa: E402


def test_combine_is_weighted_sum_on_union() -> None:
    a = pd.Series([0.01, 0.02, -0.01], index=pd.date_range("2020-01-01", periods=3))
    b = pd.Series([0.00, -0.01, 0.03], index=pd.date_range("2020-01-02", periods=3))
    port = combine({"A": a, "B": b}, {"A": 0.6, "B": 0.4})
    # 2020-01-01 只有 A 有数据 → B 视为 0 收益
    assert port.iloc[0] == np.float64(0.6 * 0.01)
    # 2020-01-04 只有 B 有数据 → A 视为 0 收益
    assert port.iloc[-1] == np.float64(0.4 * 0.03)


def test_granular_report_ranges() -> None:
    rng = np.random.default_rng(1)
    rets = pd.Series(
        rng.normal(0.0005, 0.01, 800),
        index=pd.bdate_range("2020-01-01", periods=800),
    )
    rep = granular_report(rets, "synthetic")
    for key in ("annual_return", "sharpe", "max_drawdown",
                "month_positive", "quarter_positive", "r6_positive", "r12_positive"):
        assert key in rep
    assert 0.0 <= rep["month_positive"] <= 1.0
    assert 0.0 <= rep["quarter_positive"] <= 1.0
    assert rep["max_drawdown"] <= 0.0
    assert rep["n_months"] > 0
