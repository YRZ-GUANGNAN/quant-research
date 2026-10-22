"""Top-K 等权选股策略。

流程（每个调仓日 t 收盘后执行）：
1. 取当日截面因子，做去极值 / 标准化，加权合成得分；
2. 过滤：流动性不足、停牌、ST（无数据则只做前两项）；
3. 取得分最高的 K 只，等权，总仓位 1 - cash_buffer；
4. 单票权重上限截断后重新归一。

关键约束：调仓日之间不交易，因此换手率天然受 rebalance_days 控制。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import BacktestConfig
from ..data.panel import Panel
from ..factors.basic import build_factors
from ..factors.transform import composite_score


@dataclass
class TopKStrategy:
    """按因子合成分选股。"""

    panel: Panel
    cfg: BacktestConfig
    factor_weights: dict[str, float]
    method: str = "zscore"                 # zscore | rank
    warmup: int = 130                      # 因子最长窗口 120 + 缓冲
    min_amount: float | None = None         # None 则用 cfg.min_amount

    _score: pd.DataFrame = field(init=False, repr=False)
    _amount: pd.DataFrame = field(init=False, repr=False)

    def __post_init__(self) -> None:
        factors = build_factors(self.panel)
        self._score = composite_score(factors, self.factor_weights, method=self.method)
        amount = self.panel.get("amount")
        self._amount = amount.rolling(20).mean()

    # ---------- 选股 ----------

    def select(self, date: pd.Timestamp) -> list[str]:
        """返回该日目标持仓代码列表（限定在当日时点成分股内）。"""
        if date not in self._score.index:
            return []
        score = self._score.loc[date].dropna()
        if score.empty:
            return []

        # 只在「当日确实属于沪深300」的股票里选，避免幸存者偏差
        universe = self.panel.universe_at(date)
        score = score[score.index.isin(universe)]
        if score.empty:
            return []

        min_amt = self.min_amount if self.min_amount is not None else self.cfg.min_amount
        amt = self._amount.loc[date].reindex(score.index)
        score = score[amt.fillna(0) >= min_amt]
        if score.empty:
            return []
        return list(score.sort_values(ascending=False).head(self.cfg.top_k).index)

    def target_weights(self, date: pd.Timestamp) -> dict[str, float]:
        """等权目标权重，含单票上限与现金缓冲。"""
        picks = self.select(date)
        if not picks:
            return {}
        n = len(picks)
        gross = 1.0 - self.cfg.cash_buffer
        w = min(gross / n, self.cfg.max_weight)
        weights = {code: w for code in picks}
        # 上限截断后重新分配剩余权重
        for _ in range(5):
            used = sum(weights.values())
            if used >= gross - 1e-9:
                break
            free = [c for c, v in weights.items() if v < self.cfg.max_weight - 1e-12]
            if not free:
                break
            add = (gross - used) / len(free)
            for c in free:
                weights[c] = min(weights[c] + add, self.cfg.max_weight)
        return weights

    # ---------- 回测接口 ----------

    def weight_func(self):
        """返回 Backtester 需要的权重函数。"""
        dates = self.panel.dates
        reb = self.cfg.rebalance_days

        def func(i: int) -> dict[str, float] | None:
            if i < self.warmup:
                return None
            if (i - self.warmup) % reb != 0:
                return None
            return self.target_weights(dates[i])

        return func


def make_weight_func(
    panel: Panel,
    cfg: BacktestConfig,
    factor_weights: dict[str, float],
    method: str = "zscore",
) -> tuple[TopKStrategy, object]:
    """便捷构造：返回 (策略对象, 权重函数)。"""
    strat = TopKStrategy(panel=panel, cfg=cfg, factor_weights=factor_weights, method=method)
    return strat, strat.weight_func()
