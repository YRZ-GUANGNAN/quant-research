"""组合层绩效：把当前三袖套的真实配置拼起来，算组合的收益/风险/相关性。

为什么需要：单袖套指标好不等于组合好。当前配置（2026-11-27）是
A股 ic_tilt top20 @70%、美股 60 日动量 top20 @75%+12% 波动率目标、港股 top15 @25%，
这三者**从未在同一口径下合起来算过**。

口径：
- 三条净值曲线各自归一化到 1，取**共同交易日**；
- 按资金分配 60% / 30% / 10%（项目文档的基准分配）加权；
- 组合按日再平衡到目标权重（不含换手成本，只用于评估风险结构）。

用法：
    python scripts/exp_portfolio_combined.py
输出：docs/analysis/portfolio-combined.md + reports/portfolio_combined.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from quant.config import BacktestConfig  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.engine.backtest import Backtester  # noqa: E402
from quant.strategies.ensemble import PhaseEnsembleStrategy  # noqa: E402
from quant.strategies.presets import PRESETS  # noqa: E402
from quant.strategies.topk import TopKStrategy  # noqa: E402

# 当前真实配置（与 scripts/*_run.ps1、build_cn_target_live 对齐）
SLEEVES = {
    "cn": {"topk": 20, "rebal": 35, "exposure": 0.70, "kind": "ensemble", "preset": "ic_tilt",
           "vol_target": None, "weight": 0.60},
    "us": {"topk": 20, "rebal": 20, "exposure": 0.75, "kind": "topk", "preset": "momentum",
           "vol_target": 0.12, "weight": 0.30},
    "hk": {"topk": 15, "rebal": 20, "exposure": 0.25, "kind": "topk", "preset": "momentum",
           "vol_target": None, "weight": 0.10},
}


def run_sleeve(market: str, start: str) -> pd.Series:
    cfg_spec = SLEEVES[market]
    cfg = BacktestConfig.for_market(market, top_k=cfg_spec["topk"], rebalance_days=cfg_spec["rebal"])
    if start:
        cfg = replace(cfg, start=start)
    # 注意：Backtester 不读 cfg.start，区间必须在这里截断（前 130 天用于预热）
    panel = Panel.load(index_code=None, market=market, start=start) if start else \
        Panel.load(index_code=None, market=market)
    if cfg_spec["kind"] == "ensemble":
        strat = PhaseEnsembleStrategy(panel=panel, cfg=cfg, name=cfg_spec["preset"], warmup=130,
                                      exposure_scale=cfg_spec["exposure"])
        strat.replay(len(panel.dates) - 1)
        wf = strat.weight_func()
    else:
        base = TopKStrategy(panel=panel, cfg=cfg, factor_weights=PRESETS[cfg_spec["preset"]], warmup=130)
        f = base.weight_func()
        expo = cfg_spec["exposure"]
        wf = lambda i: ({k: v * expo for k, v in (f(i) or {}).items()} if f(i) else None)
    bt = Backtester(panel, cfg, vol_target=cfg_spec["vol_target"], min_exposure=0.5) \
        if cfg_spec["vol_target"] else Backtester(panel, cfg)
    res = bt.run(wf, name=f"{market}-live")
    nav = res.nav.copy()
    nav.index = pd.to_datetime(nav.index)
    return nav / nav.iloc[0]


def metrics(nav: pd.Series, periods: int = 252) -> dict:
    rets = nav.pct_change().dropna()
    n = len(rets)
    if n < 2:
        return {"days": len(nav), "annual": None, "sharpe": None, "mdd": None}
    total = nav.iloc[-1] / nav.iloc[0]
    annual = total ** (periods / n) - 1
    mean, std = rets.mean(), rets.std(ddof=1)
    sharpe = (mean / std * math.sqrt(periods)) if std > 0 else None
    peak, mdd = 0.0, 0.0
    for v in nav:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {"days": len(nav), "annual": round(float(annual), 4),
            "sharpe": round(float(sharpe), 2) if sharpe else None, "mdd": round(float(mdd), 4)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="组合层绩效")
    ap.add_argument("--start", default="2023-01-01")
    args = ap.parse_args(argv)

    navs = {}
    for market in SLEEVES:
        print(f"[portfolio] 回测 {market.upper()} ...")
        navs[market] = run_sleeve(market, args.start)

    df = pd.DataFrame(navs).dropna()
    if df.empty:
        print("[portfolio] 三个市场没有共同交易日")
        return 3
    weights = {m: SLEEVES[m]["weight"] for m in df.columns}
    combined = (df * pd.Series(weights)).sum(axis=1) / sum(weights.values())

    per_market = {m: metrics(df[m]) for m in df.columns}
    port = metrics(combined)
    corr = df.pct_change().dropna().corr()

    out = {"generated_at": date.today().isoformat(), "start": args.start,
           "weights": weights, "sleeves": per_market, "portfolio": port,
           "correlation": {a: {b: round(float(corr.loc[a, b]), 3) for b in corr.columns}
                           for a in corr.index},
           "config": SLEEVES}
    (ROOT / "reports" / "portfolio_combined.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n=== 组合层绩效（{args.start} 起，权重 "
          f"{'/'.join(f'{m.upper()} {w:.0%}' for m, w in weights.items())}）===")
    print(f"{'':<8}{'年化':>9}{'夏普':>8}{'最大回撤':>10}")
    for m, st in per_market.items():
        print(f"{m.upper():<8}{st['annual'] * 100:+8.2f}%{st['sharpe']:>8.2f}{st['mdd'] * 100:+9.2f}%")
    print(f"{'组合':<8}{port['annual'] * 100:+8.2f}%{port['sharpe']:>8.2f}{port['mdd'] * 100:+9.2f}%")

    doc = ROOT / "docs" / "analysis" / "portfolio-combined.md"
    lines = [f"# 组合层绩效（当前配置）", "",
             f"- 生成时间：{out['generated_at']}；区间起点：{args.start}",
             f"- 权重：" + " / ".join(f"{m.upper()} {w:.0%}" for m, w in weights.items()),
             "", "| 袖套 | 年化 | 夏普 | 最大回撤 |", "|---|---|---|---|"]
    for m, st in per_market.items():
        lines.append(f"| {m.upper()} | {st['annual'] * 100:+.2f}% | {st['sharpe']} | {st['mdd'] * 100:+.2f}% |")
    lines.append(f"| **组合** | **{port['annual'] * 100:+.2f}%** | **{port['sharpe']}** | **{port['mdd'] * 100:+.2f}%** |")
    lines += ["", "## 相关性（日收益）", "", "| | " + " | ".join(m.upper() for m in corr.columns) + " |",
              "|---|" + "---|" * len(corr.columns)]
    for a in corr.index:
        lines.append(f"| {a.upper()} | " + " | ".join(f"{corr.loc[a, b]:.2f}" for b in corr.columns) + " |")
    lines += ["", "> 组合按日再平衡到目标权重，未计跨市场换手成本；用于评估风险结构而非精确收益。"]
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  -> {doc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
