"""基础因子计算。

全部为**截面因子**（每个交易日横截面排序），且严格只用 t 日及之前的收盘数据，
滚动窗口全部 backward-looking，不引入未来信息。

因子设计原则：
1. 每个因子必须有先验的经济/行为逻辑，不做纯数据挖掘；
2. 因子之间尽量低相关，否则合成没有增量；
3. 输出统一为 (date x code) 的 DataFrame，缺失表示不可计算。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.panel import Panel


def _rsi(close: pd.DataFrame, window: int = 14) -> pd.DataFrame:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window).mean()
    loss = (-delta.clip(upper=0)).rolling(window).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def build_factors(panel: Panel) -> dict[str, pd.DataFrame]:
    """构造全部候选因子。返回 {因子名: date x code 矩阵}。"""
    close = panel.get("close")
    volume = panel.get("volume")
    amount = panel.get("amount")
    turnover = panel.get("turnover")
    ret = close.pct_change()

    f: dict[str, pd.DataFrame] = {}

    # --- 动量 / 反转：A 股短期反转、中期动量都常见，方向靠 IC 检验而不是先验拍死 ---
    f["mom_20"] = close.pct_change(20)
    f["mom_60"] = close.pct_change(60)
    f["mom_120"] = close.pct_change(120)
    f["rev_5"] = -close.pct_change(5)
    f["rev_20"] = -close.pct_change(20)

    # --- 波动 / 风险 ---
    f["vol_20"] = ret.rolling(20).std()
    f["vol_60"] = ret.rolling(60).std()
    f["downside_vol_60"] = ret.where(ret < 0).rolling(60).std()
    f["max_ret_20"] = ret.rolling(20).max()       # 彩票偏好代理
    f["skew_60"] = ret.rolling(60).skew()

    # --- 量能 / 流动性 ---
    f["turnover_20"] = turnover.rolling(20).mean()
    f["turnover_ratio"] = turnover.rolling(5).mean() / turnover.rolling(60).mean()
    f["volume_ratio"] = volume.rolling(5).mean() / volume.rolling(60).mean()
    f["amihud_20"] = (ret.abs() / amount.replace(0, np.nan)).rolling(20).mean() * 1e8
    f["amount_20"] = amount.rolling(20).mean()

    # --- 价格结构 ---
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    f["ma_gap_20"] = close / ma20 - 1
    f["ma_gap_60"] = close / ma60 - 1
    f["rsi_14"] = _rsi(close, 14)

    # --- 规模代理（无市值数据，用成交额 + 价格近似） ---
    f["log_amount"] = np.log(amount.rolling(20).mean().replace(0, np.nan))
    f["log_price"] = np.log(close)

    # --- 相对基准的 beta / 超额动量 ---
    bench_ret = panel.benchmark.pct_change()
    cov = ret.rolling(60).cov(bench_ret)
    var = bench_ret.rolling(60).var()
    f["beta_60"] = cov.div(var, axis=0)
    f["excess_mom_60"] = close.pct_change(60).sub(
        panel.benchmark.pct_change(60), axis=0
    )

    return f
