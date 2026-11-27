"""实验：组合层波动率目标（vol targeting）能不能改善风险调整后收益。

背景：现在实盘是**静态仓位**（A 股 0.70、美股 35% 现金份额、港股 25%）。
引擎里早就有 vol_target 机制（按已实现波动缩放目标仓位，只用历史净值），
但从来没在实盘路径上用过。这个脚本回答一个问题：
**在同样的选股信号下，加不加 vol targeting，收益/回撤/夏普差多少？**

判定标准（先写死，避免事后挑指标）：
- 夏普提升 ≥ 0.05 且最大回撤改善（回撤更浅）
- 或 回撤改善 ≥ 3 个百分点且收益下降 < 2 个百分点
两者都不满足就不用。

用法：
    python scripts/exp_vol_target.py --market cn --start 2020-01-01
    python scripts/exp_vol_target.py --market us --vol-target 0.12
输出：docs/analysis/exp-vol-target-<market>.md + 控制台对照表
"""
from __future__ import annotations

import argparse
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

PRESET = {"cn": ("ic_tilt", 40, 35), "us": ("momentum", 20, 20), "hk": ("momentum", 15, 20)}


def run_one(market: str, start: str, exposure: float, vol_target: float | None,
            min_exposure: float, slippage: float) -> dict:
    name, topk, rebal = PRESET[market]
    base = BacktestConfig.for_market(market, top_k=topk, rebalance_days=rebal)
    cfg = replace(base, start=start) if start else base
    panel = Panel.load(index_code=None, market=market)
    strat = PhaseEnsembleStrategy(panel=panel, cfg=cfg, name=name, warmup=130, exposure_scale=exposure)
    bt = Backtester(panel, cfg, vol_target=vol_target, min_exposure=min_exposure)
    result = bt.run(strat.weight_func(), name=f"{market}-{name}")
    m = result.metrics
    return {"label": "vol_target" if vol_target else "baseline",
            "vol_target": vol_target, "min_exposure": min_exposure,
            "annual": m.get("annual_return"), "sharpe": m.get("sharpe"),
            "mdd": m.get("max_drawdown"), "vol": m.get("volatility"),
            "turnover": m.get("annual_turnover")}


def verdict(base: dict, cand: dict) -> tuple[bool, str]:
    d_sharpe = (cand["sharpe"] or 0) - (base["sharpe"] or 0)
    d_mdd = (cand["mdd"] or 0) - (base["mdd"] or 0)      # 负数，越大越好
    d_annual = (cand["annual"] or 0) - (base["annual"] or 0)
    if d_sharpe >= 0.05 and d_mdd > 0:
        return True, f"夏普 +{d_sharpe:.2f}、回撤改善 {d_mdd * 100:.1f}pp"
    if d_mdd >= 0.03 and d_annual > -0.02:
        return True, f"回撤改善 {d_mdd * 100:.1f}pp、收益下降 {abs(d_annual) * 100:.1f}pp"
    return False, f"夏普变化 {d_sharpe:+.2f}、回撤变化 {d_mdd * 100:+.1f}pp、收益变化 {d_annual * 100:+.1f}pp"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="波动率目标实验")
    ap.add_argument("--market", default="cn", choices=sorted(PRESET))
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--exposure", type=float, default=0.70)
    ap.add_argument("--vol-target", type=float, default=0.12)
    ap.add_argument("--min-exposure", type=float, default=0.50)
    ap.add_argument("--slippage", type=float, default=0.001)
    args = ap.parse_args(argv)

    base = run_one(args.market, args.start, args.exposure, None, 1.0, args.slippage)
    cand = run_one(args.market, args.start, args.exposure, args.vol_target, args.min_exposure, args.slippage)
    ok, why = verdict(base, cand)

    print(f"=== 波动率目标实验：{args.market.upper()}（起始 {args.start}，仓位 {args.exposure:.0%}）===")
    print(f"{'方案':<14}{'年化':>9}{'夏普':>8}{'最大回撤':>10}{'年化波动':>10}{'换手':>8}")
    for r in (base, cand):
        label = r["label"] + (f"({r['vol_target']:.0%}/{r['min_exposure']:.0%})" if r["vol_target"] else "")
        def pct(v):
            return "—" if v is None else f"{v * 100:.2f}%"
        def num(v):
            return "—" if v is None else f"{v:.2f}"
        print(f"{label:<14}{pct(r['annual']):>9}{num(r['sharpe']):>8}{pct(r['mdd']):>10}"
              f"{pct(r['vol']):>10}{num(r['turnover']):>8}")
    print(f"结论：{'采用' if ok else '不采用'} —— {why}")

    out = ROOT / "docs" / "analysis" / f"exp-vol-target-{args.market}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join([
        f"# 波动率目标实验 · {args.market.upper()}",
        "",
        f"- 生成时间：{date.today()}",
        f"- 区间起点：{args.start}；仓位系数：{args.exposure:.0%}；滑点：{args.slippage:.3%}",
        f"- 候选：vol_target={args.vol_target:.0%}，min_exposure={args.min_exposure:.0%}",
        "",
        "| 方案 | 年化 | 夏普 | 最大回撤 | 年化波动 | 换手 |",
        "|---|---|---|---|---|---|",
        f"| 静态仓位 | {base['annual']} | {base['sharpe']} | {base['mdd']} | {base['vol']} | {base['turnover']} |",
        f"| 波动率目标 | {cand['annual']} | {cand['sharpe']} | {cand['mdd']} | {cand['vol']} | {cand['turnover']} |",
        "",
        f"**结论：{'采用' if ok else '不采用'}** —— {why}",
        "",
        "> 判定标准在跑之前就写死（见脚本 docstring），避免事后挑指标。",
    ]) + "\n", encoding="utf-8")
    print(f"  -> {out}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
