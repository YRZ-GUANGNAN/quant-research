"""跨市场收益源组合实验（支持多相位复核）。

问题：A 股 v3 的月度盈利比例只有 48.6%（收益分布负偏），
而「仓位缩放」类优化已证明无效（只改幅度、不改符号）。
本实验换收益源层级：把低相关的美股/港股/加密袖子按固定权重拼进来，
检验组合的月度、季度、6 个月、12 个月盈利比例能否提升，
并用**多相位**复核（避免把相位运气当成收益源改善）。

重要简化（必须写进报告，不能假装这是可实盘组合）：
1. 不同市场交易日历不同：按日期并集对齐，非交易日收益记 0（视为持仓不变）；
2. 未计入汇率（A 股人民币、美股美元、港股港币、加密 USDT）；
3. 未计入跨境资金成本、交易时段差异、税费差异；
4. 非 A 股使用静态名单，存在幸存者偏差。
因此本实验只回答「换收益源能否改善盈利频率」，不构成可交易方案。

用法：
    python scripts/portfolio.py                     # 单相位
    python scripts/portfolio.py --phases 0,5,10,15  # 多相位复核
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant.config import REPORTS_DIR, BacktestConfig, ensure_dirs  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.engine.backtest import Backtester  # noqa: E402
from quant.engine.metrics import max_drawdown  # noqa: E402
from quant.strategies.ensemble import PhaseEnsembleStrategy  # noqa: E402
from quant.strategies.presets import PRESETS  # noqa: E402
from quant.strategies.topk import TopKStrategy  # noqa: E402

COMBOS: list[tuple[str, dict[str, float]]] = [
    ("A股单独", {"A股v3": 1.0}),
    ("A70/美30", {"A股v3": 0.70, "美股动量": 0.30}),
    ("A60/美30/港10", {"A股v3": 0.60, "美股动量": 0.30, "港股动量": 0.10}),
    ("A50/美30/港20", {"A股v3": 0.50, "美股动量": 0.30, "港股动量": 0.20}),
]


def sleeve_returns(name: str, market: str, start: str, *, ensemble: bool = False,
                   top_k: int = 40, rebalance: int = 35, preset: str = "ic_tilt",
                   warmup: int = 130) -> pd.Series:
    """跑一个市场的策略，返回日收益序列。"""
    panel = Panel.load(index_code=None, start=start, market=market)
    cfg = BacktestConfig.for_market(market, start=start, top_k=top_k,
                                    rebalance_days=rebalance)
    if ensemble:
        strat = PhaseEnsembleStrategy(panel=panel, cfg=cfg, name=preset, warmup=warmup)
    else:
        strat = TopKStrategy(panel=panel, cfg=cfg, factor_weights=PRESETS[preset],
                             warmup=warmup)
    res = Backtester(panel, cfg).run(strat.weight_func(), name=name)
    return res.nav.pct_change().rename(name)


def combine(rets: dict[str, pd.Series], weights: dict[str, float]) -> pd.Series:
    """按权重合成日收益；非交易日按 0 收益处理（持仓不变）。"""
    df = pd.DataFrame({k: v for k, v in rets.items() if k in weights}).sort_index()
    df = df.fillna(0.0)
    w = pd.Series({k: v for k, v in weights.items() if k in df.columns})
    return (df * w).sum(axis=1).rename("portfolio")


def granular_report(returns: pd.Series, label: str) -> dict:
    """组合层面的收益/风险与多尺度盈利比例。"""
    nav = (1.0 + returns).cumprod()
    years = max(len(nav) / 252.0, 1e-9)
    annual = float(nav.iloc[-1] ** (1.0 / years) - 1.0)

    def pos_ratio(freq: str | int) -> float:
        if isinstance(freq, int):
            s = nav.pct_change(freq).dropna()
        else:
            s = nav.resample(freq).last().pct_change().dropna()
        return float((s > 0).mean()) if len(s) else 0.0

    monthly = nav.resample("ME").last().pct_change().dropna()
    return {
        "label": label,
        "annual_return": annual,
        "annual_vol": float(returns.std() * np.sqrt(252)),
        "sharpe": float(returns.mean() / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0.0,
        "max_drawdown": max_drawdown(nav),
        "month_positive": pos_ratio("ME"),
        "quarter_positive": pos_ratio("QE"),
        "r6_positive": pos_ratio(126),
        "r12_positive": pos_ratio(252),
        "worst_month": float(monthly.min()) if len(monthly) else 0.0,
        "n_months": int(len(monthly)),
    }


def build_sleeves(start: str, warmup: int) -> dict[str, pd.Series]:
    sleeves: dict[str, pd.Series] = {}
    sleeves["A股v3"] = sleeve_returns("A股v3", "cn", start, ensemble=True,
                                      top_k=40, rebalance=35, preset="ic_tilt",
                                      warmup=warmup)
    sleeves["美股动量"] = sleeve_returns("美股动量", "us", start, top_k=20,
                                        rebalance=20, preset="momentum", warmup=warmup)
    sleeves["港股动量"] = sleeve_returns("港股动量", "hk", start, top_k=15,
                                        rebalance=20, preset="momentum", warmup=warmup)
    return sleeves


def main() -> int:
    ap = argparse.ArgumentParser(description="跨市场收益源组合")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--phases", default="0", help="相位偏移列表，如 0,5,10,15")
    args = ap.parse_args()
    ensure_dirs()
    phases = [int(x) for x in args.phases.split(",") if x.strip()]

    per_phase: list[dict] = []
    correlations: list[pd.DataFrame] = []

    for off in phases:
        warmup = 130 + off
        print(f"\n########## 相位 {off}（warmup={warmup}） ##########")
        sleeves = build_sleeves(args.start, warmup)
        corr = pd.DataFrame(sleeves).fillna(0.0).corr()
        correlations.append(corr)
        print("[相关] A-美 %.2f  A-港 %.2f" % (corr.loc["A股v3", "美股动量"],
                                               corr.loc["A股v3", "港股动量"]))
        for label, weights in COMBOS:
            rets = combine(sleeves, weights)
            rep = granular_report(rets, label)
            rep["phase"] = off
            per_phase.append(rep)
            print(f"  [{label}] 年化 {rep['annual_return'] * 100:6.2f}%  夏普 {rep['sharpe']:5.2f}  "
                  f"回撤 {rep['max_drawdown'] * 100:6.2f}%  月胜率 {rep['month_positive'] * 100:5.1f}%  "
                  f"季 {rep['quarter_positive'] * 100:5.1f}%  12月 {rep['r12_positive'] * 100:5.1f}%")

    df = pd.DataFrame(per_phase)
    print("\n=== 多相位汇总（均值 ± 标准差）===")
    summary: dict[str, dict] = {}
    for label, _ in COMBOS:
        sub = df[df["label"] == label]
        if sub.empty:
            continue
        row = {}
        for key in ("annual_return", "sharpe", "max_drawdown", "month_positive",
                    "quarter_positive", "r6_positive", "r12_positive"):
            vals = sub[key].tolist()
            row[key] = {"mean": statistics.mean(vals),
                        "std": statistics.pstdev(vals) if len(vals) > 1 else 0.0}
        summary[label] = row
        print(f"\n{label}（{len(sub)} 相位）")
        print(f"  年化 {row['annual_return']['mean'] * 100:6.2f}% ± {row['annual_return']['std'] * 100:4.2f}%   "
              f"夏普 {row['sharpe']['mean']:5.2f} ± {row['sharpe']['std']:4.2f}")
        print(f"  回撤 {row['max_drawdown']['mean'] * 100:6.2f}%   月胜率 {row['month_positive']['mean'] * 100:5.1f}% "
              f"± {row['month_positive']['std'] * 100:4.1f}%   季 {row['quarter_positive']['mean'] * 100:5.1f}%   "
              f"12月 {row['r12_positive']['mean'] * 100:5.1f}%")

    out = {
        "start": args.start,
        "phases": phases,
        "correlations": [c.round(3).to_dict() for c in correlations],
        "per_phase": per_phase,
        "summary": summary,
    }
    path = REPORTS_DIR / "portfolio_multi_market.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[report] -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
