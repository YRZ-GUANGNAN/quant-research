"""回测主循环。

时序约定（防未来函数的核心）：
    t 日收盘  → 用截至 t 日收盘的数据算目标权重
    t+1 开盘 → 执行调仓（开盘价 ± 滑点）
    t+1 收盘 → 记录净值

因此任何策略函数在索引 i 处只能看到 dates[:i+1] 的数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from ..config import BacktestConfig
from ..data.panel import Panel
from .broker import PaperBroker
from .metrics import performance


WeightFunc = Callable[[int], "dict[str, float] | None"]


def execute_target(
    broker: PaperBroker,
    cfg: BacktestConfig,
    date_label: str,
    open_px: pd.Series,
    prev_close: pd.Series | None,
    target: dict[str, float],
) -> None:
    """把账户调整到目标权重（按开盘价撮合）。

    回测与模拟盘共用此函数，保证两条路径的撮合行为完全一致。
    """

    def px_of(code: str) -> float | None:
        v = open_px.get(code)
        return float(v) if v is not None and pd.notna(v) and v > 0 else None

    def pc_of(code: str) -> float | None:
        if prev_close is None:
            return None
        v = prev_close.get(code)
        return float(v) if v is not None and pd.notna(v) and v > 0 else None

    # 1) 先卖出不在目标里的持仓（释放现金）
    for code in list(broker.positions):
        if code in target:
            continue
        px = px_of(code)
        if px is None:
            continue  # 停牌，留到下次
        pos = broker.positions[code]
        broker.sell(date_label, code, pos.available, px, pc_of(code) or px, "exit")

    # 2) 向目标权重靠拢
    for code, weight in target.items():
        px = px_of(code)
        if px is None:
            continue
        eq = broker.equity()
        target_value = max(weight, 0.0) * eq
        pos = broker.positions.get(code)
        cur_value = pos.market_value if pos else 0.0

        if pos and cur_value > target_value * 1.20 and pos.available > 0:
            excess = int((cur_value - target_value) / px) // cfg.lot_size * cfg.lot_size
            if excess > 0:
                broker.sell(date_label, code, excess, px, pc_of(code) or px, "trim")
        elif cur_value < target_value * 0.85:
            buy_price = px * (1 + cfg.costs.slippage)
            need = target_value - cur_value
            shares = int(need / buy_price) // cfg.lot_size * cfg.lot_size
            if shares > 0:
                broker.buy(date_label, code, shares, px, pc_of(code) or px, "rebalance")


@dataclass
class BacktestResult:
    """一次回测的完整产物。"""

    name: str
    nav: pd.Series                       # 账户市值
    benchmark_nav: pd.Series             # 基准净值（初始 = 1）
    trades: pd.DataFrame
    metrics: dict
    weights: pd.DataFrame
    config: BacktestConfig
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))

    def summary(self) -> str:
        from .metrics import format_metrics

        return format_metrics(self.metrics)


class Backtester:
    """日频、整仓调仓的模拟盘回测器。

    ``vol_target`` 为组合层波动率目标（非择时）：每次调仓时根据**已实现**的
    组合波动率缩放目标仓位（只用历史净值，不含未来信息），
    高波动时降仓、低波动时满仓，用来提高「大多数时段不亏」的概率。
    """

    def __init__(
        self,
        panel: Panel,
        cfg: BacktestConfig,
        *,
        vol_target: float | None = None,
        vol_lookback: int = 20,
        min_exposure: float = 0.3,
    ):
        self.panel = panel
        self.cfg = cfg
        self.close = panel.get("close")
        self.open = panel.get("open")
        self.dates = panel.dates
        self.vol_target = vol_target
        self.vol_lookback = vol_lookback
        self.min_exposure = min_exposure

    def _exposure(self, nav_values: list[float]) -> float:
        """按已实现组合波动率计算缩放系数（仅用历史净值）。"""
        if not self.vol_target or len(nav_values) < self.vol_lookback + 2:
            return 1.0
        import numpy as np

        series = pd.Series(nav_values[-(self.vol_lookback + 1):])
        rets = series.pct_change().dropna()
        vol = float(rets.std(ddof=1) * np.sqrt(252))
        if vol <= 0 or not np.isfinite(vol):
            return 1.0
        return float(min(1.0, max(self.min_exposure, self.vol_target / vol)))


    def _execute(self, broker: PaperBroker, i: int, target: dict[str, float], expo: float = 1.0) -> None:
        d = self.dates[i]
        prev_d = self.dates[i - 1] if i > 0 else None
        scaled = {code: w * expo for code, w in target.items()} if expo < 1.0 else target
        execute_target(
            broker,
            self.cfg,
            str(d.date()),
            self.open.loc[d],
            self.close.loc[prev_d] if prev_d is not None else None,
            scaled,
        )

    def run(self, weight_func: WeightFunc, name: str = "strategy") -> BacktestResult:
        cfg = self.cfg
        broker = PaperBroker(cfg.initial_cash, cfg)
        pending: dict[str, float] | None = None
        nav_dates: list[pd.Timestamp] = []
        nav_values: list[float] = []
        weight_rows: list[dict] = []

        # 区间截断：cfg.start/end 之前只让策略状态前进（预热），不交易、不记账。
        # 以前引擎完全不读 cfg.start，导致所有标注「2023 起」的实验实际跑的是全样本。
        start_ts = pd.Timestamp(cfg.start) if getattr(cfg, "start", None) else None
        end_ts = pd.Timestamp(cfg.end) if getattr(cfg, "end", None) else None
        active_from = 0
        if start_ts is not None:
            active_from = next((i for i, d in enumerate(self.dates) if d >= start_ts),
                               len(self.dates))

        for i, d in enumerate(self.dates):
            if i < active_from:
                weight_func(i)          # 只推进状态（相位/因子窗口），不产生交易
                continue
            if end_ts is not None and d > end_ts:
                break
            broker.settle()
            if pending is not None:
                self._execute(broker, i, pending, self._exposure(nav_values))
                pending = None

            close_px = self.close.loc[d].dropna()
            broker.mark(close_px.to_dict())
            nav_dates.append(d)
            nav_values.append(broker.equity())

            target = weight_func(i)
            if target is not None:
                pending = target
                weight_rows.append({"date": d, **target})

        nav = pd.Series(nav_values, index=pd.DatetimeIndex(nav_dates), name="nav")
        equity_curve = nav / nav.iloc[0]

        bench = self.panel.benchmark.reindex(nav.index).ffill()
        bench_nav = bench / bench.iloc[0]

        trades = pd.DataFrame([t.__dict__ for t in broker.trades])
        weights = pd.DataFrame(weight_rows).set_index("date") if weight_rows else pd.DataFrame()

        metrics = performance(
            nav, benchmark=bench_nav, trades=trades if not trades.empty else None, name=name
        )
        return BacktestResult(
            name=name,
            nav=nav,
            benchmark_nav=bench_nav,
            trades=trades,
            metrics=metrics,
            weights=weights,
            config=cfg,
            equity_curve=equity_curve,
        )
