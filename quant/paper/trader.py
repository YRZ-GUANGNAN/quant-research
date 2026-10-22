"""模拟盘推进器：把回测引擎的撮合逻辑接到持久化账户上。

每个交易日按固定顺序推进：
    1. settle()     解锁 T+1 股份
    2. 执行上一交易日收盘后生成的 pending 目标权重（按当日开盘价）
    3. 按收盘价重估，记录净值
    4. 若当日是调仓日，用当日收盘数据生成新的 pending（下一交易日开盘执行）
    5. 落盘

和回测唯一的区别是「状态来自磁盘」而不是内存，因此可以每天跑一次，
也可以停几天再跑——补跑时会按顺序逐日推进。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from ..config import BacktestConfig
from ..data.panel import Panel
from ..engine.backtest import execute_target
from ..strategies.topk import TopKStrategy
from .state import PaperState


@dataclass
class PaperTrader:
    panel: Panel
    cfg: BacktestConfig
    strategy: TopKStrategy
    state: PaperState
    warmup: int = 130

    def step(self, i: int) -> dict:
        """推进到 dates[i] 这一天。"""
        d = self.panel.dates[i]
        prev_d = self.panel.dates[i - 1] if i > 0 else None
        broker = self.state.to_broker(self.cfg)
        broker.settle()

        if self.state.pending:
            execute_target(
                broker,
                self.cfg,
                str(d.date()),
                self.panel.open.loc[d],
                self.panel.close.loc[prev_d] if prev_d is not None else None,
                self.state.pending,
            )
            self.state.pending = {}

        broker.mark(self.panel.close.loc[d].dropna().to_dict())
        self.state.sync_from_broker(broker)

        equity = self.state.equity()
        self.state.nav_history.append(
            {
                "date": str(d.date()),
                "equity": round(equity, 2),
                "cash": round(self.state.cash, 2),
                "n_positions": len(self.state.positions),
            }
        )

        # 调仓日：生成下一交易日要执行的目标权重
        if i >= self.warmup and (i - self.warmup) % self.cfg.rebalance_days == 0:
            self.state.pending = self.strategy.target_weights(d)

        self.state.day_index = i
        self.state.last_date = str(d.date())
        return {"date": str(d.date()), "equity": equity, "n_positions": len(self.state.positions)}

    def run(self) -> list[dict]:
        """从上次停下的位置推进到今天。"""
        dates = self.panel.dates
        if self.state.last_date is None:
            start = max(self.warmup, 0)
        else:
            last = pd.Timestamp(self.state.last_date)
            pos = dates.searchsorted(last, side="right")
            start = int(pos)
        records = []
        for i in range(start, len(dates)):
            records.append(self.step(i))
            self.state.save()
        return records
