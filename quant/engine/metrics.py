"""绩效指标。

统一口径：
- 年化按 252 个交易日；
- 无风险利率默认 0（A 股研究中常用，且便于横向比较）；
- 夏普、索提诺用日收益序列计算后年化；
- 最大回撤基于净值序列的峰谷差；
- 相对基准给出 beta / alpha / 信息比率 / 跟踪误差。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def to_returns(nav: pd.Series) -> pd.Series:
    return nav.pct_change().dropna()


def annualized_return(nav: pd.Series, periods: int = TRADING_DAYS) -> float:
    if len(nav) < 2 or nav.iloc[0] <= 0:
        return 0.0
    total = nav.iloc[-1] / nav.iloc[0]
    years = len(nav) / periods
    if years <= 0:
        return 0.0
    return float(total ** (1.0 / years) - 1.0)


def annualized_vol(returns: pd.Series, periods: int = TRADING_DAYS) -> float:
    if returns.empty:
        return 0.0
    return float(returns.std(ddof=1) * np.sqrt(periods))


def max_drawdown(nav: pd.Series) -> float:
    if nav.empty:
        return 0.0
    peak = nav.cummax()
    dd = nav / peak - 1.0
    return float(dd.min())


def sharpe(returns: pd.Series, rf: float = 0.0, periods: int = TRADING_DAYS) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - rf / periods
    sd = excess.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return 0.0
    return float(excess.mean() / sd * np.sqrt(periods))


def sortino(returns: pd.Series, rf: float = 0.0, periods: int = TRADING_DAYS) -> float:
    if len(returns) < 2:
        return 0.0
    excess = returns - rf / periods
    downside = excess[excess < 0]
    dd = downside.std(ddof=1)
    if dd == 0 or np.isnan(dd):
        return 0.0
    return float(excess.mean() / dd * np.sqrt(periods))


def calmar(nav: pd.Series) -> float:
    mdd = max_drawdown(nav)
    if mdd == 0:
        return 0.0
    return float(annualized_return(nav) / abs(mdd))


def win_rate(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    return float((returns > 0).mean())


def _align(nav: pd.Series, benchmark: pd.Series) -> tuple[pd.Series, pd.Series]:
    nav = nav.astype(float)
    bench = benchmark.astype(float).reindex(nav.index).ffill()
    both = pd.concat([nav, bench], axis=1).dropna()
    return both.iloc[:, 0], both.iloc[:, 1]


def performance(
    nav: pd.Series,
    benchmark: pd.Series | None = None,
    trades: pd.DataFrame | None = None,
    name: str = "strategy",
) -> dict[str, float | str]:
    """计算一份完整绩效表。"""
    nav = nav.dropna()
    rets = to_returns(nav)
    out: dict[str, float | str] = {
        "name": name,
        "total_return": float(nav.iloc[-1] / nav.iloc[0] - 1.0) if len(nav) > 1 else 0.0,
        "annual_return": annualized_return(nav),
        "annual_vol": annualized_vol(rets),
        "sharpe": sharpe(rets),
        "sortino": sortino(rets),
        "max_drawdown": max_drawdown(nav),
        "calmar": calmar(nav),
        "win_rate": win_rate(rets),
        "n_days": float(len(nav)),
    }

    if benchmark is not None and not benchmark.empty:
        s, b = _align(nav, benchmark)
        if len(s) > 2:
            sr, br = s.pct_change().dropna(), b.pct_change().dropna()
            var_b = br.var(ddof=1)
            beta = float(np.cov(sr, br)[0, 1] / var_b) if var_b > 0 else 0.0
            alpha_d = float(sr.mean() - beta * br.mean())
            active = sr - br
            te = float(active.std(ddof=1) * np.sqrt(TRADING_DAYS))
            ir = float(active.mean() / active.std(ddof=1) * np.sqrt(TRADING_DAYS)) if active.std(ddof=1) > 0 else 0.0
            out.update(
                {
                    "bench_annual_return": annualized_return(b),
                    "excess_annual": annualized_return(s) - annualized_return(b),
                    "beta": beta,
                    "alpha_annual": alpha_d * TRADING_DAYS,
                    "tracking_error": te,
                    "information_ratio": ir,
                }
            )

    if trades is not None and not trades.empty:
        gross = float(trades["amount"].abs().sum())
        years = max(len(nav) / TRADING_DAYS, 1e-9)
        avg_eq = float(nav.mean())
        out["turnover_annual"] = gross / avg_eq / years if avg_eq > 0 else 0.0
        out["n_trades"] = float(len(trades))
        out["total_fee"] = float(trades["fee"].sum())

    return out


def format_metrics(m: dict[str, float | str]) -> str:
    """把指标字典格式化成可读文本。"""
    pct_keys = {
        "total_return", "annual_return", "annual_vol", "max_drawdown",
        "bench_annual_return", "excess_annual", "alpha_annual",
        "win_rate", "tracking_error",
    }
    lines = [f"== {m.get('name', 'strategy')} =="]
    order = [
        "total_return", "annual_return", "annual_vol", "sharpe", "sortino",
        "max_drawdown", "calmar", "win_rate", "bench_annual_return",
        "excess_annual", "beta", "alpha_annual", "information_ratio",
        "turnover_annual", "n_trades", "total_fee", "n_days",
    ]
    labels = {
        "total_return": "累计收益",
        "annual_return": "年化收益",
        "annual_vol": "年化波动",
        "sharpe": "夏普比率",
        "sortino": "索提诺",
        "max_drawdown": "最大回撤",
        "calmar": "卡玛比率",
        "win_rate": "日胜率",
        "bench_annual_return": "基准年化",
        "excess_annual": "年化超额",
        "beta": "Beta",
        "alpha_annual": "年化Alpha",
        "information_ratio": "信息比率",
        "turnover_annual": "年化换手",
        "n_trades": "成交笔数",
        "total_fee": "总费用",
        "n_days": "交易日数",
    }
    for key in order:
        if key not in m:
            continue
        val = m[key]
        if isinstance(val, str):
            lines.append(f"  {labels.get(key, key):<10}: {val}")
        elif key in pct_keys:
            lines.append(f"  {labels.get(key, key):<10}: {val * 100:8.2f}%")
        else:
            lines.append(f"  {labels.get(key, key):<10}: {val:8.3f}")
    return "\n".join(lines)


def monthly_returns(nav: pd.Series) -> pd.DataFrame:
    """月度收益表（行=年，列=月）。"""
    if nav.empty:
        return pd.DataFrame()
    m = nav.resample("ME").last().pct_change().dropna()
    if m.empty:
        return pd.DataFrame()
    table = pd.DataFrame(
        {
            "year": m.index.year,
            "month": m.index.month,
            "ret": m.values,
        }
    )
    return table.pivot(index="year", columns="month", values="ret")
