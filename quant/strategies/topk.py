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
    # 持仓缓冲：>1 时，已持有且仍排在前 top_k*buffer 名的股票优先保留，
    # 只为「新进入前列」的股票换仓，从而显著降低换手（代价：略微钝化信号）。
    buffer_ratio: float = 1.0
    # 波动率上限过滤：排除当日 20 日波动率处于截面高分位的股票。
    # 例如 0.8 = 只在波动率低于 80% 分位的股票里选，直接削掉尾部风险。
    vol_cap_quantile: float | None = None
    # 个股 beta 上限：排除高 beta 股票，用于压低组合的方向性暴露（亏损主因）
    beta_cap: float | None = None

    _score: pd.DataFrame = field(init=False, repr=False)
    _amount: pd.DataFrame = field(init=False, repr=False)
    _vol20: pd.DataFrame = field(init=False, repr=False)
    _beta60: pd.DataFrame = field(init=False, repr=False)
    _held: list[str] = field(init=False, repr=False, default_factory=list)

    def __post_init__(self) -> None:
        factors = build_factors(self.panel)
        self._score = composite_score(factors, self.factor_weights, method=self.method)
        amount = self.panel.get("amount")
        self._amount = amount.rolling(20).mean()
        self._vol20 = self.panel.close.pct_change().rolling(20).std()
        ret = self.panel.close.pct_change()
        bench_ret = self.panel.benchmark.pct_change()
        self._beta60 = ret.rolling(60).cov(bench_ret).div(
            bench_ret.rolling(60).var(), axis=0)

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

        # 波动率上限：剔除当日波动率最高的那一部分股票
        if self.vol_cap_quantile is not None and date in self._vol20.index:
            vol = self._vol20.loc[date].reindex(score.index)
            thr = float(vol.quantile(self.vol_cap_quantile))
            score = score[vol.fillna(float("inf")) <= thr]
            if score.empty:
                return []

        if self.beta_cap is not None and date in self._beta60.index:
            beta = self._beta60.loc[date].reindex(score.index)
            score = score[beta.fillna(99.0) <= self.beta_cap]
            if score.empty:
                return []

        ranked = score.sort_values(ascending=False)

        # 持仓缓冲：先保留仍在前 top_k*buffer_ratio 名的旧持仓，再用新面孔补齐
        if self.buffer_ratio > 1.0 and self._held:
            keep_n = max(self.cfg.top_k, int(round(self.cfg.top_k * self.buffer_ratio)))
            keep_set = set(ranked.index[:keep_n])
            picks = [c for c in self._held if c in keep_set][: self.cfg.top_k]
            for code in ranked.index:
                if len(picks) >= self.cfg.top_k:
                    break
                if code not in picks:
                    picks.append(code)
        else:
            picks = list(ranked.index[: self.cfg.top_k])

        self._held = picks
        return picks

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
