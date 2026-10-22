"""IC 加权因子合成（自适应权重）。

和固定权重的区别：每个调仓日，用「历史上各因子的滚动 IC」作为权重，
让模型自己决定哪个因子当前有效。

防未来函数的关键一步：t 日能用的 IC 只能来自「标签期已结束」的样本。
即用 h 日前瞻收益算出的 IC，最早只能在 t+h 日使用。代码里通过
``shift(h)`` 实现——这是本模块最容易写错、也最值得单独说明的地方。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import BacktestConfig
from ..data.panel import Panel
from ..factors.basic import build_factors
from ..factors.transform import forward_returns


@dataclass
class ICWeightedStrategy:
    """滚动 IC 加权合成因子，Top-K 等权。"""

    panel: Panel
    cfg: BacktestConfig
    factors: list[str]
    horizon: int = 5
    ic_window: int = 120
    warmup: int = 150
    min_ic_abs: float = 0.0          # 过滤 |IC| 过低的因子

    _score: pd.DataFrame = field(init=False, repr=False)
    _ic_diag: pd.DataFrame = field(init=False, repr=False)

    def __post_init__(self) -> None:
        factors = build_factors(self.panel)
        missing = [f for f in self.factors if f not in factors]
        if missing:
            raise KeyError(f"未知因子: {missing}")

        fwd = forward_returns(self.panel.close, self.horizon)

        # 1) 每个因子的逐期 IC（使用 t 时刻的因子 与 t→t+h 的收益）
        ic = {}
        for name in self.factors:
            ic[name] = self._cross_sectional_ic(factors[name], fwd)
        ic_df = pd.DataFrame(ic)                       # date x factor

        # 2) 标签期结束才可用：t 日的权重只能用到 t-h 及更早的 IC
        usable_ic = ic_df.shift(self.horizon)

        # 3) 滚动均值作为权重
        weights = usable_ic.rolling(self.ic_window).mean()
        if self.min_ic_abs > 0:
            weights = weights.where(weights.abs() >= self.min_ic_abs)

        # 4) 截面 z-score 后加权求和
        z = {}
        for name in self.factors:
            mat = factors[name]
            mu = mat.mean(axis=1)
            sd = mat.std(axis=1, ddof=1).replace(0, np.nan)
            z[name] = mat.sub(mu, axis=0).div(sd, axis=0)

        score = pd.DataFrame(0.0, index=self.panel.dates, columns=self.panel.codes)
        for name in self.factors:
            score = score.add(z[name] * weights[name].reindex(score.index), axis=0)
        self._score = score.replace([np.inf, -np.inf], np.nan)
        self._ic_diag = ic_df

    @staticmethod
    def _cross_sectional_ic(factor: pd.DataFrame, fwd: pd.DataFrame) -> pd.Series:
        out = []
        for date in factor.index:
            if date not in fwd.index:
                out.append(np.nan)
                continue
            f = factor.loc[date].dropna()
            r = fwd.loc[date].reindex(f.index).dropna()
            common = f.index.intersection(r.index)
            if len(common) < 30:
                out.append(np.nan)
                continue
            out.append(f.loc[common].corr(r.loc[common], method="spearman"))
        return pd.Series(out, index=factor.index)

    def target_weights(self, date: pd.Timestamp) -> dict[str, float]:
        if date not in self._score.index:
            return {}
        score = self._score.loc[date].dropna()
        universe = self.panel.universe_at(date)
        score = score[score.index.isin(universe)]
        amount = self.panel.get("amount")
        if amount is not None:
            amt = amount.loc[date].reindex(score.index)
            score = score[amt.fillna(0) >= self.cfg.min_amount]
        if score.empty:
            return {}
        picks = list(score.sort_values(ascending=False).head(self.cfg.top_k).index)
        gross = 1.0 - self.cfg.cash_buffer
        w = min(gross / len(picks), self.cfg.max_weight)
        return {c: w for c in picks}

    def weight_func(self):
        dates = self.panel.dates
        reb = self.cfg.rebalance_days

        def func(i: int) -> dict[str, float] | None:
            if i < self.warmup:
                return None
            if (i - self.warmup) % reb != 0:
                return None
            return self.target_weights(dates[i])

        return func

    def ic_diagnostics(self) -> pd.DataFrame:
        """各因子 IC 的均值/IR，用于人工检查权重是否合理。"""
        ic = self._ic_diag
        return pd.DataFrame(
            {
                "ic_mean": ic.mean(),
                "ic_std": ic.std(),
                "ic_ir": ic.mean() / ic.std(),
                "ic_win": (ic > 0).mean(),
            }
        ).sort_values("ic_ir", ascending=False)
