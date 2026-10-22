"""风控叠加层：在不改选股逻辑的前提下，控制「总仓位」。

两种独立的降风险机制，可单独或同时启用：

1. **趋势择时（timing）**：基准收盘价低于 N 日均线 → 总仓位降到 ``min_exposure``。
   逻辑依据：A 股系统性风险高度同步，个股 alpha 在指数趋势向下时很难兑现。
   已知代价：会错过 V 型反转的第一段（2020-03、2022-11 都会被打脸）。

2. **波动率目标（vol_target）**：用基准近 20 日已实现波动率估计市场风险，
   仓位 = min(1, 目标波动率 / 当前波动率)。高波动时降仓，低波动时满仓。

两者都作用在**组合层**（把目标权重整体乘以 exposure），不改变选股，
因此可以和任何 Top-K 策略组合，也便于归因：超额变化来自择时还是选股一目了然。

时序安全：exposure 只用 t 日及之前的基准数据（均线、已实现波动率都是 backward-looking），
在 t 日收盘生成、t+1 开盘执行，与主回测一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import BacktestConfig
from ..data.panel import Panel


@dataclass
class RiskOverlay:
    """给任意策略叠加总仓位控制。"""

    panel: Panel
    cfg: BacktestConfig
    base: object                       # 需要提供 target_weights(date) 与 warmup
    timing: bool = True
    ma_days: int = 200
    vol_target: float | None = None    # 年化目标波动率，如 0.15
    vol_lookback: int = 20
    min_exposure: float = 0.0
    max_exposure: float = 1.0
    warmup: int = 0

    _ma: pd.Series = field(init=False, repr=False)
    _bench_vol: pd.Series = field(init=False, repr=False)

    def __post_init__(self) -> None:
        bench = self.panel.benchmark
        self._ma = bench.rolling(self.ma_days).mean()
        self._bench_vol = bench.pct_change().rolling(self.vol_lookback).std() * np.sqrt(252)
        if not self.warmup:
            self.warmup = int(getattr(self.base, "warmup", 130))

    # ---------- 仓位决策 ----------

    def exposure(self, date: pd.Timestamp) -> float:
        expo = 1.0
        if self.timing:
            px = self.panel.benchmark.get(date)
            ma = self._ma.get(date)
            if px is not None and ma is not None and pd.notna(px) and pd.notna(ma):
                expo = 1.0 if float(px) > float(ma) else self.min_exposure
        if self.vol_target is not None:
            bv = self._bench_vol.get(date)
            if bv is not None and pd.notna(bv) and float(bv) > 0:
                expo = min(expo, float(np.clip(self.vol_target / float(bv), 0.0, 1.0)))
        return float(np.clip(expo, self.min_exposure, self.max_exposure))

    def target_weights(self, date: pd.Timestamp) -> dict[str, float]:
        expo = self.exposure(date)
        if expo <= 0:
            return {}
        base_w = self.base.target_weights(date)
        return {code: w * expo for code, w in base_w.items()}

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

    # ---------- 诊断 ----------

    def exposure_series(self) -> pd.Series:
        """历史上每日的仓位系数，用于确认择时是否真的在动作。"""
        return pd.Series(
            {d: self.exposure(d) for d in self.panel.dates}, name="exposure"
        )


@dataclass
class ICMonitorOverlay:
    """因子自检闸门：让策略用自己最近的 IC 判断「我是不是失效了」。

    思路（对本项目最有针对性的创新）：
    研究发现因子在 2023 后衰减、2025 起无超额。与其等人事后发现，
    不如让策略自己在运行时监控「我的信号最近还灵吗」：

    1. 每个调仓日，计算基础策略的**合成分数**与未来 h 日收益的截面 IC；
    2. 取过去 ``window`` 期的 IC 均值（注意：只用标签期已经结束的样本，
       即 shift(h) 之后的数据，绝不偷看未来）；
    3. 若滚动 IC 均值 < ``min_ic``（默认 0），把仓位降到 ``off_exposure``（默认 0）。

    这样因子失效期间策略自动空仓，而不是继续交学费。
    已知代价：IC 是噪声很大的统计量，会**频繁误杀**（好因子也可能被短暂降仓），
    因此必须用多相位评估检验「降仓是否真的改善了风险调整后收益」，而不是看单条曲线。
    """

    panel: Panel
    cfg: BacktestConfig
    base: object
    horizon: int = 5
    window: int = 60
    min_ic: float = 0.0
    off_exposure: float = 0.0
    warmup: int = 0

    _ic: pd.Series = field(init=False, repr=False)
    _ic_ma: pd.Series = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.warmup:
            self.warmup = int(getattr(self.base, "warmup", 130))
        self._ic = self._compute_ic()
        self._ic_ma = self._ic.rolling(self.window).mean()

    def _compute_ic(self) -> pd.Series:
        """逐日计算「策略合成分数」与未来 h 日收益的截面 Rank IC。"""
        import numpy as np

        score = getattr(self.base, "_score", None)
        if score is None:
            return pd.Series(dtype=float)
        close = self.panel.close
        fwd = close.shift(-self.horizon) / close - 1.0
        vals = []
        for date in score.index:
            if date not in fwd.index:
                vals.append(np.nan)
                continue
            s = score.loc[date].dropna()
            r = fwd.loc[date].reindex(s.index).dropna()
            common = s.index.intersection(r.index)
            if len(common) < 30:
                vals.append(np.nan)
                continue
            vals.append(float(s.loc[common].corr(r.loc[common], method="spearman")))
        return pd.Series(vals, index=score.index, name="ic")

    def exposure(self, date: pd.Timestamp) -> float:
        ic_ma = self._ic_ma.get(date)
        # 关键：IC 由 t→t+h 的收益算出，最早只能在 t+h 使用
        if ic_ma is None or pd.isna(ic_ma):
            return 1.0
        return 1.0 if float(ic_ma) >= self.min_ic else self.off_exposure

    def target_weights(self, date: pd.Timestamp) -> dict[str, float]:
        expo = self.exposure(date)
        if expo <= 0:
            return {}
        base_w = self.base.target_weights(date)
        return {code: w * expo for code, w in base_w.items()}

    def weight_func(self):
        dates = self.panel.dates
        reb = self.cfg.rebalance_days

        def func(i: int) -> dict[str, float] | None:
            if i < self.warmup:
                return None
            # 用 i-h 之前的 IC（标签期结束），避免未来函数
            date = dates[i]
            ref = dates[max(0, i - self.horizon)]
            ic_ma = self._ic_ma.get(ref)
            if i < self.warmup + self.window + self.horizon:
                return None
            if (i - self.warmup) % reb != 0:
                return None
            expo = 1.0 if (ic_ma is not None and pd.notna(ic_ma) and float(ic_ma) >= self.min_ic) else self.off_exposure
            if expo <= 0:
                return {}
            base_w = self.base.target_weights(date)
            return {code: w * expo for code, w in base_w.items()}

        return func

    def diagnosis(self) -> pd.DataFrame:
        """输出 IC 与闸门状态，便于确认「它到底有没有在工作」。"""
        return pd.DataFrame(
            {
                "ic": self._ic,
                "ic_ma": self._ic_ma,
                "on": (self._ic_ma >= self.min_ic).astype(int),
            }
        )

