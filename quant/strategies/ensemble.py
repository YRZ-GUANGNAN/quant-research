"""相位不变组合（Phase-Invariant Ensemble）——本项目的核心创新。

## 要解决的问题

单相位回测的结论是**路径噪声**：同一策略只把调仓日错开 5 天，
验证期年化就在 -0.29% ~ +13.24% 之间跳（见 docs/journal/0002）。
传统做法是「选一个好看的相位」或者「平均多次回测的净值」——前者是自欺，后者只是事后统计。

## 做法

同时维护 K 个**错开交易日**的虚拟子策略（例如 off = 0/5/10/15 天），
每个子策略在自己的调仓日更新持仓；主组合在固定节奏上执行，
目标权重由 K 个子策略的**共识**决定：

1. 把所有子策略的目标权重按代码加总；
2. 只保留总权重最高的 ``top_k`` 只（→ 被多个相位同时选中的股票权重最高）；
3. 重新归一化到 ``1 - cash_buffer``。

这样：
- 任何单一相位的「运气」只占 1/K 权重，无法主导组合；
- 被多数相位同时选中的股票 = 信号更稳健（相当于用相位做了一次隐式 bagging）；
- 持仓数不变（不是把 30 只变 120 只），避免小仓位被最低佣金吃掉。

## 可验证的预期

- 多相位评估的**标准差显著下降**（组合对相位不再敏感）；
- 均值的绝对值可能略降（牺牲一点运气收益），但信噪比提升；
- 换手率下降（共识名单更稳定）。

如果实测标准差没降，说明这个创新不成立，应当如实记录并放弃。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..config import BacktestConfig
from ..data.panel import Panel
from .factory import build_strategy


@dataclass
class PhaseEnsembleStrategy:
    """多相位共识组合。"""

    panel: Panel
    cfg: BacktestConfig
    name: str = "ic_tilt"
    offsets: tuple[int, ...] = (0, 5, 10, 15)
    warmup: int = 130
    buffer_ratio: float = 1.0
    timing: bool = False
    vol_target: float | None = None
    min_exposure: float = 0.0
    vol_cap: float | None = None
    # 共识变化阈值：新旧目标权重的单边换手率低于该值就不下发新目标（不交易）。
    # 0 = 每次校准都执行；0.08 约等于「目标权重有 8% 以上变化才动手」。
    min_change: float = 0.0
    # 主组合执行节奏：True = 每个交易日都用共识权重校准（相位无关），
    # 靠撮合层的无交易带（偏离 15%/20% 才动手）抑制换手。
    master_daily: bool = True

    _members: list = field(init=False, repr=False, default_factory=list)
    _current: list[dict[str, float]] = field(init=False, repr=False, default_factory=list)
    _last_target: dict[str, float] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self) -> None:
        self._members = [
            build_strategy(
                self.name, self.panel, self.cfg,
                warmup=self.warmup + off,
                buffer_ratio=self.buffer_ratio,
                timing=self.timing,
                vol_target=self.vol_target,
                min_exposure=self.min_exposure,
                vol_cap=self.vol_cap,
            )
            for off in self.offsets
        ]
        self._current = [dict() for _ in self._members]

    @property
    def master_warmup(self) -> int:
        return self.warmup + max(self.offsets)

    def _update_members(self, i: int, date: pd.Timestamp) -> None:
        reb = self.cfg.rebalance_days
        for k, off in enumerate(self.offsets):
            w_k = self.warmup + off
            if i >= w_k and (i - w_k) % reb == 0:
                try:
                    self._current[k] = dict(self._members[k].target_weights(date))
                except Exception:  # noqa: BLE001 - 单个子策略失败不应拖垮整体
                    self._current[k] = {}

    def consensus(self, date: pd.Timestamp) -> dict[str, float]:
        """把各相位权重加总，取总权重最高的 top_k 只，再归一化。"""
        total: dict[str, float] = {}
        for weights in self._current:
            for code, w in weights.items():
                total[code] = total.get(code, 0.0) + float(w)
        if not total:
            return {}
        ranked = sorted(total.items(), key=lambda kv: -kv[1])[: self.cfg.top_k]
        s = sum(v for _, v in ranked)
        if s <= 0:
            return {}
        gross = 1.0 - self.cfg.cash_buffer
        return {code: v / s * gross for code, v in ranked}

    def target_weights(self, date: pd.Timestamp) -> dict[str, float]:
        return self.consensus(date)

    def weight_func(self):
        dates = self.panel.dates
        reb = self.cfg.rebalance_days

        def _maybe_emit(cons: dict[str, float]) -> dict[str, float] | None:
            """变化不足阈值就不交易，避免为小幅漂移付成本。"""
            if self._last_target and self.min_change > 0:
                keys = set(cons) | set(self._last_target)
                diff = sum(abs(cons.get(c, 0.0) - self._last_target.get(c, 0.0)) for c in keys) / 2.0
                if diff < self.min_change:
                    return None
            self._last_target = dict(cons)
            return cons

        def func(i: int) -> dict[str, float] | None:
            if i < self.warmup:
                return None
            self._update_members(i, dates[i])
            if i < self.master_warmup:
                return None
            if self.master_daily:
                # 每日校准：相位不再决定执行日，只有子策略的调仓日影响共识
                return _maybe_emit(self.consensus(dates[i]))
            if (i - self.master_warmup) % reb != 0:
                return None
            return _maybe_emit(self.consensus(dates[i]))

        return func

    def phase_disagreement(self) -> list[dict]:
        """诊断：各相位当前持仓的重合度（用于确认集成是否真的在「投票」）。"""
        if not self._current:
            return []
        sets = [set(w) for w in self._current]
        union = set().union(*sets) if sets else set()
        inter = set.intersection(*sets) if sets else set()
        return [
            {
                "n_phases": len(sets),
                "union": len(union),
                "intersection": len(inter),
                "mean_jaccard": self._mean_jaccard(sets),
            }
        ]

    @staticmethod
    def _mean_jaccard(sets: list[set]) -> float:
        vals = []
        for a_i in range(len(sets)):
            for b_i in range(a_i + 1, len(sets)):
                a, b = sets[a_i], sets[b_i]
                if not a and not b:
                    continue
                vals.append(len(a & b) / len(a | b))
        return sum(vals) / len(vals) if vals else 0.0
