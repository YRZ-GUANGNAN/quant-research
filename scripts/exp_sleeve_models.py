"""实验：美股/港股袖套该用单相位动量，还是四相位共识？

现状：《模型与配置》文档里美股/港股用 `mom_60` 单相位 TopK（top 20 / top 15）。
而 A 股用的是「4 相位共识」，设计初衷是抵消路径噪声。问题是：**这个共识在美股/港股上到底有没有用？**

预登记判定（跑之前写死）：
- 候选方案夏普 ≥ 基线 + 0.05，且最大回撤不比基线差 2pp 以上 → 采用
- 否则维持现状

用法：
    python scripts/exp_sleeve_models.py --market us
    python scripts/exp_sleeve_models.py --market hk --start 2023-01-01
输出：docs/analysis/exp-sleeve-models-<market>.md
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.config import BacktestConfig  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.engine.backtest import Backtester  # noqa: E402
from quant.strategies.ensemble import PhaseEnsembleStrategy  # noqa: E402
from quant.strategies.topk import TopKStrategy  # noqa: E402

TOP_K = {"us": (20, 20), "hk": (15, 20)}
SLEEVE_EXPOSURE = {"us": 0.75, "hk": 0.25}


def _metrics(name: str, result) -> dict:
    m = result.metrics
    return {"name": name, "annual": m.get("annual_return"), "sharpe": m.get("sharpe"),
            "mdd": m.get("max_drawdown"), "vol": m.get("volatility")}


def run_variant(market: str, kind: str, start: str, slippage: float) -> dict:
    topk, rebal = TOP_K[market]
    cfg = BacktestConfig.for_market(market, top_k=topk, rebalance_days=rebal)
    if start:
        cfg = replace(cfg, start=start)
    panel = Panel.load(index_code=None, market=market, start=start) if start else \
        Panel.load(index_code=None, market=market)
    bt = Backtester(panel, cfg)

    if kind == "momentum_topk":
        from quant.strategies.presets import PRESETS as TOPK_PRESETS

        strat = TopKStrategy(panel=panel, cfg=cfg, factor_weights=TOPK_PRESETS["momentum"], warmup=130)
        return _metrics(kind, bt.run(strat.weight_func(), name=kind))

    ensemble = PhaseEnsembleStrategy(panel=panel, cfg=cfg, name=kind, warmup=130,
                                     exposure_scale=SLEEVE_EXPOSURE[market])
    return _metrics(kind, bt.run(ensemble.weight_func(), name=kind))


def verdict(base: dict, cand: dict) -> tuple[bool, str]:
    d_sharpe = (cand["sharpe"] or 0) - (base["sharpe"] or 0)
    d_mdd = (cand["mdd"] or 0) - (base["mdd"] or 0)
    if d_sharpe >= 0.05 and d_mdd >= -0.02:
        return True, f"夏普 +{d_sharpe:.2f}，回撤变化 {d_mdd * 100:+.1f}pp"
    return False, f"夏普变化 {d_sharpe:+.2f}，回撤变化 {d_mdd * 100:+.1f}pp"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="袖套模型对比")
    ap.add_argument("--market", default="us", choices=sorted(TOP_K))
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--slippage", type=float, default=0.001)
    args = ap.parse_args(argv)

    variants = ["momentum_topk", "momentum", "ic_tilt"]
    results = [run_variant(args.market, k, args.start, args.slippage) for k in variants]
    base = results[0]

    print(f"=== 袖套模型对比：{args.market.upper()}（{args.start} 起）===")
    print(f"{'方案':<16}{'年化':>10}{'夏普':>8}{'最大回撤':>10}")
    for r in results:
        def pct(v):
            return "—" if v is None else f"{v * 100:.2f}%"
        def num(v):
            return "—" if v is None else f"{v:.2f}"
        print(f"{r['name']:<16}{pct(r['annual']):>10}{num(r['sharpe']):>8}{pct(r['mdd']):>10}")

    lines = [f"# 袖套模型对比 · {args.market.upper()}", "",
             f"- 生成时间：{date.today()}；区间起点：{args.start}；滑点：{args.slippage:.3%}",
             f"- 基线（当前实盘）：`momentum_topk`（单相位 60 日动量，top {TOP_K[args.market][0]}）",
             "",
             "| 方案 | 年化 | 夏普 | 最大回撤 |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['name']} | {r['annual']} | {r['sharpe']} | {r['mdd']} |")
    lines.append("")
    for r in results[1:]:
        ok, why = verdict(base, r)
        lines.append(f"- **{r['name']} vs 基线**：{'采用' if ok else '不采用'} —— {why}")
        print(f"   {r['name']}: {'采用' if ok else '不采用'} —— {why}")
    lines.append("")
    lines.append("> 判定标准在跑之前写死：夏普 ≥ 基线 +0.05 且回撤不差 2pp 以上。")

    out = ROOT / "docs" / "analysis" / f"exp-sleeve-models-{args.market}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
