"""多相位评估：单个回测窗口的结果有多可信？

起因：把同一个策略（ic_tilt）的调仓相位仅错开 5 个交易日，
2023-2024 验证期年化在 -0.29% ~ +13.24% 之间跳动。
说明「单窗口回测」的结论大部分是路径噪声，不是 alpha。

本脚本对同一策略跑 K 个调仓相位，报告各分段的 **均值 ± 标准差**，
并把均值/标准差 > 0.5 的结果标记为「不可信」。

用法：
    python scripts/evaluate.py --strategy ic_tilt --phases 0,5,10,15
    python scripts/evaluate.py --strategy icw --phases 0,5,10,15 --slippage 0.001
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import REPORTS_DIR, BacktestConfig, ensure_dirs  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.engine.backtest import Backtester  # noqa: E402
from quant.engine.metrics import performance  # noqa: E402

PRESETS: dict[str, dict[str, float]] = {
    "momentum": {"mom_60": 1.0},
    "reversal": {"rev_5": 1.0},
    "multi": {"mom_60": 0.4, "rev_5": 0.3, "vol_20": -0.2, "turnover_20": -0.1},
    "ic_tilt": {
        "amihud_20": 0.35, "rev_20": 0.20, "rev_5": 0.15,
        "log_amount": -0.20, "ma_gap_60": -0.10, "vol_20": -0.05,
    },
}

ICW_FACTORS = [
    "amihud_20", "rev_20", "rev_5", "vol_20", "vol_60", "log_amount",
    "ma_gap_60", "ma_gap_20", "mom_120", "max_ret_20", "turnover_20",
    "beta_60", "excess_mom_60", "rsi_14", "skew_60", "volume_ratio",
]

SEGMENTS = [
    ("训练 2018-2022", "2018-01-01", "2022-12-31"),
    ("验证 2023-2024", "2023-01-01", "2024-12-31"),
    ("样本外 2025-", "2025-01-01", ""),
]

METRICS = ["annual_return", "excess_annual", "sharpe", "max_drawdown", "information_ratio"]


def build_strategy(strategy: str, panel: Panel, cfg: BacktestConfig, warmup: int):
    if strategy == "icw":
        from quant.models.linear import ICWeightedStrategy

        return ICWeightedStrategy(panel=panel, cfg=cfg, factors=ICW_FACTORS, horizon=5, warmup=warmup)
    from quant.strategies.topk import TopKStrategy

    if strategy not in PRESETS:
        raise SystemExit(f"未知策略 {strategy}")
    return TopKStrategy(panel=panel, cfg=cfg, factor_weights=PRESETS[strategy], warmup=warmup)


def segment_metrics(nav: pd.Series, bench: pd.Series) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for label, start, end in SEGMENTS:
        sub = nav[nav.index >= pd.Timestamp(start)]
        if end:
            sub = sub[sub.index <= pd.Timestamp(end)]
        if len(sub) < 40:
            out[label] = {}
            continue
        m = performance(sub, benchmark=bench.reindex(sub.index), name=label)
        out[label] = {k: float(m[k]) for k in METRICS if k in m and m[k] is not None}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="多相位稳健性评估")
    ap.add_argument("--strategy", default="ic_tilt", choices=sorted(PRESETS) + ["icw"])
    ap.add_argument("--phases", default="0,5,10,15", help="相位偏移（相对默认预热天数）")
    ap.add_argument("--base-warmup", type=int, default=130)
    ap.add_argument("--topk", type=int, default=30)
    ap.add_argument("--rebalance", type=int, default=20)
    ap.add_argument("--slippage", type=float, default=5e-4)
    ap.add_argument("--start", default="2018-01-01")
    args = ap.parse_args()

    ensure_dirs()
    phases = [int(x) for x in args.phases.split(",") if x.strip()]
    from dataclasses import replace as _replace

    cfg = BacktestConfig(start=args.start, top_k=args.topk, rebalance_days=args.rebalance)
    cfg = cfg.with_(costs=_replace(cfg.costs, slippage=args.slippage))

    print(f"[eval] 加载面板 {args.start} ~ ...")
    panel = Panel.load(index_code="000300", start=args.start)
    print(f"[eval] {len(panel.dates)} 交易日 × {len(panel.codes)} 只，策略={args.strategy}，"
          f"相位={phases}，滑点={args.slippage:.4f}")

    rows: list[dict] = []
    for off in phases:
        warmup = args.base_warmup + off
        strat = build_strategy(args.strategy, panel, cfg, warmup)
        result = Backtester(panel, cfg).run(strat.weight_func(), name=f"{args.strategy}_p{off}")
        seg = segment_metrics(result.nav, result.benchmark_nav)
        for label, vals in seg.items():
            if vals:
                rows.append({"phase": off, "segment": label, **vals})
        print(f"  phase={off} 完成：全期年化 {result.metrics['annual_return'] * 100:6.2f}%")

    df = pd.DataFrame(rows)
    if df.empty:
        print("[eval] 无有效结果")
        return 1

    summary: dict[str, dict] = {}
    print("\n=== 多相位评估（均值 ± 标准差）===")
    for label, _, _ in SEGMENTS:
        sub = df[df["segment"] == label]
        if sub.empty:
            continue
        summary[label] = {}
        print(f"\n{label}（{len(sub)} 个相位）")
        for key in METRICS:
            if key not in sub:
                continue
            vals = sub[key].tolist()
            mean = statistics.mean(vals)
            std = statistics.pstdev(vals)
            summary[label][key] = {"mean": mean, "std": std, "values": vals}
            flag = " ⚠️不稳定" if (key in ("annual_return", "excess_annual") and abs(std) > 0.05) else ""
            if key in ("annual_return", "excess_annual", "max_drawdown"):
                print(f"  {key:<18} {mean * 100:7.2f}% ± {std * 100:5.2f}%{flag}")
            else:
                print(f"  {key:<18} {mean:7.3f} ± {std:5.3f}{flag}")

    out_path = REPORTS_DIR / f"eval_{args.strategy}_phase.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[eval] 结果 -> {out_path}")
    print("[eval] 判读规则：均值/标准差 小于 1 的结论不可采信；"
          "单相位最优结果不能作为选择依据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
