"""把长表行情转成宽表面板（index=日期，columns=股票代码），支持多市场。

除行情外，A 股额外加载**时点成分股掩码**（member_mask）：
- 每个半年末抓一次沪深300 成分股快照；
- 快照生效区间 [本快照日, 下一快照日)，之间不回溯；
- 选股只在当日掩码为 True 的股票中进行。

非 A 股市场用静态名单，无掩码——这意味它们仍有幸存者偏差，
报告里必须标注（见 docs/decisions/0004）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import store

FIELDS = ("open", "close", "high", "low", "volume", "amount", "pct_chg", "turnover")


@dataclass
class Panel:
    """行情面板 + 基准指数 + （A 股）时点成分股掩码。"""

    frames: dict[str, pd.DataFrame]
    benchmark: pd.Series
    names: dict[str, str]
    index_code: str
    member_mask: pd.DataFrame | None = field(default=None)
    market: str = "cn"

    @property
    def dates(self) -> pd.DatetimeIndex:
        return self.frames["close"].index

    @property
    def codes(self) -> list[str]:
        return list(self.frames["close"].columns)

    @property
    def close(self) -> pd.DataFrame:
        return self.frames["close"]

    @property
    def open(self) -> pd.DataFrame:
        return self.frames["open"]

    def get(self, field: str) -> pd.DataFrame:
        if field not in self.frames:
            raise KeyError(f"面板缺少字段 {field}")
        return self.frames[field]

    def returns(self) -> pd.DataFrame:
        return self.close.pct_change()

    def universe_at(self, date: pd.Timestamp) -> pd.Index:
        """该日可交易股票池（时点成分股 ∩ 有行情）。"""
        close_row = self.close.loc[date]
        has_price = close_row[close_row.notna()].index
        if self.member_mask is None or date not in self.member_mask.index:
            return has_price
        mask_row = self.member_mask.loc[date]
        return has_price.intersection(mask_row[mask_row].index)

    @staticmethod
    def _build_mask(
        members: pd.DataFrame, dates: pd.DatetimeIndex, codes: list[str]
    ) -> pd.DataFrame | None:
        if members.empty:
            return None
        snapshots = sorted(pd.to_datetime(members["snapshot_date"].unique()))
        mask = pd.DataFrame(False, index=dates, columns=codes)
        for i, snap in enumerate(snapshots):
            end = snapshots[i + 1] if i + 1 < len(snapshots) else dates.max() + pd.Timedelta(days=1)
            window = mask.index[(mask.index >= snap) & (mask.index < end)]
            if len(window) == 0:
                continue
            codes_in = [c for c in members.loc[members["snapshot_date"] == snap, "code"] if c in mask.columns]
            if codes_in:
                mask.loc[window, codes_in] = True
        return mask

    @classmethod
    def load(
        cls,
        index_code: str | None = None,
        start: str | None = None,
        end: str | None = None,
        min_obs: int = 0,
        market: str = "cn",
    ) -> "Panel":
        """从本地缓存加载面板（默认 A 股）。"""
        index_code = index_code or ("000300" if market == "cn" else market)
        bars = store.load_bars(market)
        if bars.empty:
            raise RuntimeError(f"{market} 无行情缓存，请先运行 scripts/fetch_data.py 或 fetch_market.py")
        if start:
            bars = bars[bars["date"] >= pd.Timestamp(start)]
        if end:
            bars = bars[bars["date"] <= pd.Timestamp(end)]
        if bars.empty:
            raise RuntimeError("指定区间内无行情数据")

        if min_obs > 0:
            counts = bars.groupby("code")["date"].nunique()
            bars = bars[bars["code"].isin(counts[counts >= min_obs].index)]

        # 补齐派生字段：不同数据源字段不一致，在面板层统一
        if "pct_chg" not in bars.columns:
            bars = bars.sort_values(["code", "date"])
            bars["pct_chg"] = bars.groupby("code")["close"].pct_change() * 100
        if "amount" not in bars.columns:
            bars["amount"] = bars["close"] * bars["volume"]
        if "turnover" not in bars.columns:
            bars["turnover"] = np.nan

        frames: dict[str, pd.DataFrame] = {}
        for field_name in FIELDS:
            if field_name not in bars.columns:
                continue
            frames[field_name] = (
                bars.pivot(index="date", columns="code", values=field_name).sort_index()
            )

        names = (
            bars.drop_duplicates("code").set_index("code")["name"].to_dict()
            if "name" in bars.columns
            else {}
        )

        idx = store.load_index_bars(market)
        if idx.empty:
            raise RuntimeError(f"缺少 {market} 基准指数缓存")
        bench = idx.set_index("date")["close"].sort_index().reindex(frames["close"].index).ffill()

        mask = None
        if market == "cn":
            members = store.load_members(str(index_code).zfill(6))
            mask = cls._build_mask(members, frames["close"].index, list(frames["close"].columns))

        return cls(
            frames=frames,
            benchmark=bench,
            names=names,
            index_code=str(index_code),
            member_mask=mask,
            market=market,
        )
