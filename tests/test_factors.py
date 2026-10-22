"""因子层测试：重点验证「没有未来函数」。

方法：用完整数据构造因子，再截断到 t 日重新构造，两者在 t 日的值必须完全一致。
如果某个因子偷看了未来（例如用了 center=True 的滚动窗口、或全样本标准化），
这个测试会直接失败。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.data.panel import Panel
from quant.factors.basic import build_factors
from quant.factors.transform import composite_score, forward_returns, zscore


def make_panel(n_days: int = 260, n_codes: int = 40, seed: int = 7) -> Panel:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    codes = [f"{600000 + i:06d}" for i in range(n_codes)]
    rets = rng.normal(0.0005, 0.02, size=(n_days, n_codes))
    close = pd.DataFrame(100 * np.cumprod(1 + rets, axis=0), index=dates, columns=codes)
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.DataFrame(rng.integers(1e5, 1e6, size=(n_days, n_codes)).astype(float), index=dates, columns=codes)
    amount = volume * close
    benchmark = pd.Series(3000 * np.cumprod(1 + rets.mean(axis=1)), index=dates)
    frames = {
        "open": open_,
        "close": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "volume": volume,
        "amount": amount,
        "pct_chg": close.pct_change() * 100,
        "turnover": volume / 1e6,
    }
    return Panel(frames=frames, benchmark=benchmark, names={c: c for c in codes}, index_code="000300")


def test_no_lookahead_in_factors() -> None:
    panel = make_panel()
    t = panel.dates[200]
    full = build_factors(panel)

    truncated = Panel(
        frames={k: v.loc[:t] for k, v in panel.frames.items()},
        benchmark=panel.benchmark.loc[:t],
        names=panel.names,
        index_code=panel.index_code,
    )
    partial = build_factors(truncated)

    for name, mat in full.items():
        a = mat.loc[t]
        b = partial[name].loc[t]
        pd.testing.assert_series_equal(
            a.dropna(), b.dropna(), check_names=False, rtol=1e-9,
            obj=f"因子 {name} 在 {t.date()} 使用了未来数据",
        )


def test_zscore_is_cross_sectional() -> None:
    panel = make_panel(n_days=10)
    fac = panel.close.pct_change(1)
    z = zscore(fac)
    # 每一行的均值应接近 0
    assert z.mean(axis=1).abs().max() < 1e-9


def test_forward_returns_direction() -> None:
    panel = make_panel(n_days=10)
    fwd = forward_returns(panel.close, horizon=1)
    expected = panel.close.iloc[1] / panel.close.iloc[0] - 1
    pd.testing.assert_series_equal(fwd.iloc[0], expected, check_names=False)


def test_composite_score_unknown_factor_raises() -> None:
    panel = make_panel(n_days=30)
    factors = build_factors(panel)
    with pytest.raises(KeyError):
        composite_score(factors, {"not_a_factor": 1.0})
