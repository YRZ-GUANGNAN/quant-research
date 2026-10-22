"""因子的截面处理与合成。

所有变换都是**逐日截面**的：同一天内对 300 只股票做去极值/标准化/排序，
不会把不同日期的信息混在一起（否则又是一条未来函数）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def mad_winsorize(df: pd.DataFrame, n_mad: float = 5.0) -> pd.DataFrame:
    """按当日截面中位数 ± n_mad×MAD 去极值。"""
    med = df.median(axis=1)
    mad = (df.sub(med, axis=0)).abs().median(axis=1)
    upper = med + n_mad * 1.4826 * mad
    lower = med - n_mad * 1.4826 * mad
    return df.clip(lower=lower, upper=upper, axis=0)


def zscore(df: pd.DataFrame, winsorize: bool = True, n_mad: float = 5.0) -> pd.DataFrame:
    """截面 z-score。"""
    if winsorize:
        df = mad_winsorize(df, n_mad)
    mu = df.mean(axis=1)
    sd = df.std(axis=1, ddof=1)
    out = df.sub(mu, axis=0).div(sd.replace(0, np.nan), axis=0)
    return out.replace([np.inf, -np.inf], np.nan)


def rank_normalize(df: pd.DataFrame) -> pd.DataFrame:
    """截面分位数排序，映射到 [-0.5, 0.5]，对极值稳健。"""
    ranks = df.rank(axis=1, pct=True)
    return ranks - 0.5


def composite_score(
    factors: dict[str, pd.DataFrame],
    weights: dict[str, float],
    method: str = "zscore",
) -> pd.DataFrame:
    """按权重合成因子得分。

    method:
        "zscore" —— 先 z-score 再加权（保留幅度信息，对异常值敏感）
        "rank"   —— 先截面排序再加权（稳健，但丢失尾部信息）
    """
    total = pd.DataFrame(index=next(iter(factors.values())).index)
    for name, weight in weights.items():
        if name not in factors:
            raise KeyError(f"未知因子 {name}")
        norm = zscore(factors[name]) if method == "zscore" else rank_normalize(factors[name])
        total = total.add(norm * weight, fill_value=0.0)
    return total


def ic_series(factor: pd.DataFrame, forward_return: pd.DataFrame, method: str = "spearman") -> pd.Series:
    """逐期截面 IC（默认 Spearman 秩相关）。"""
    ics = []
    for date in factor.index:
        f = factor.loc[date].dropna()
        r = forward_return.loc[date].reindex(f.index).dropna() if date in forward_return.index else pd.Series(dtype=float)
        common = f.index.intersection(r.index)
        if len(common) < 20:
            ics.append(np.nan)
            continue
        ics.append(f.loc[common].corr(r.loc[common], method=method))
    return pd.Series(ics, index=factor.index, name="ic")


def forward_returns(close: pd.DataFrame, horizon: int = 1) -> pd.DataFrame:
    """未来 horizon 日收益（仅用于评估，绝不可进入策略特征）。"""
    return close.shift(-horizon) / close - 1.0
