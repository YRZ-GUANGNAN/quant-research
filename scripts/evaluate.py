"""多相位评估：单个回测窗口的结果有多可信？

起因：把同一个策略（ic_tilt）的调仓相位仅错开 5 个交易日，
2023-2024 验证期年化在 -0.29% ~ +13.24% 之间跳动。
说明「单窗口回测」的结论大部分是路径噪声，不是 alpha。

本脚本对同一策略跑 K 个调仓相位，报告各分段的 **均值 ± 标准差**，
并把均值/标准差 < 1 的结果标记为「不可信」。

用法：
    python scripts/evaluate.py --strategy ic_tilt --phases 0,5,10,15
    python scripts/evaluate.py --strategy ic_tilt --timing --phases 0,5,10,15
    python scripts/evaluate.py --strategy ic_tilt --timing --buffer 1.5 --rebalance 40
    python scripts/evaluate.py --strategy ic_tilt --vol-target 0.15
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import REPORTS_DIR, BacktestConfig, ensure_dirs  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.engine.backtest import Backtester  # noqa: E402
from quant.engine.metrics import performance, stability_report, yearly_returns  # noqa: E402
from quant.strategies.factory import build_strategy  # noqa: E402
from quant.strategies.presets import PRESETS  # noqa: E402

SEGMENTS = [
    ("训练 2018-2022", "2018-01-01", "2022-12-31"),
    ("验证 2023-2024", "2023-01-01", "2024-12-31"),
    ("样本外 2025-", "2025-01-01", ""),
]

METRICS = ["annual_return", "excess_annual", "sharpe", "max_drawdown",
           "information_ratio"]


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
    ap.add_argument("--buffer", type=float, default=1.0, help="持仓缓冲倍数，>1 降换手")
    ap.add_argument("--timing", action="store_true", help="基准 200 日均线择时")
    ap.add_argument("--ma-days", type=int, default=200)
    ap.add_argument("--min-exposure", type=float, default=0.0,
                    help="择时触发时的仓位（0=清仓，0.5=半仓）")
    ap.add_argument("--vol-cap", type=float, default=None,
                    help="波动率上限分位（0.8=剔除波动率最高20%）")
    ap.add_argument("--beta-cap", type=float, default=None,
                    help="个股 beta 上限（如 0.9）")
    ap.add_argument("--min-change", type=float, default=0.0,
                    help="共识变化阈值：目标权重单边换手低于此值不交易")
    ap.add_argument("--vol-target", type=float, default=None, help="年化波动率目标，如 0.15")
    ap.add_argument("--slippage", type=float, default=5e-4)
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--ensemble", action="store_true",
                    help="相位不变组合：多个错开相位的子策略投票")
    ap.add_argument("--ensemble-offsets", default="0,5,10,15")
    ap.add_argument("--ic-gate", action="store_true",
                    help="因子自检闸门：滚动 IC 转负时自动空仓")
    ap.add_argument("--ic-window", type=int, default=60)
    ap.add_argument("--ic-min", type=float, default=0.0)
    ap.add_argument("--ic-off-exposure", type=float, default=0.0,
                    help="闸门关闭时的仓位（0=清仓，0.5=半仓）")
    args = ap.parse_args()

    ensure_dirs()
    phases = [int(x) for x in args.phases.split(",") if x.strip()]
    cfg = BacktestConfig(
        start=args.start, top_k=args.topk, rebalance_days=args.rebalance,
    )
    cfg = cfg.with_(costs=replace(cfg.costs, slippage=args.slippage))

    label_parts = [args.strategy]
    if args.ensemble:
        label_parts.append("ens")
    if args.ic_gate:
        label_parts.append("icgate")
    if args.timing:
        label_parts.append(f"timing{args.ma_days}")
    if args.vol_target:
        label_parts.append(f"vol{args.vol_target}")
    if args.vol_cap:
        label_parts.append(f"vcap{args.vol_cap}")
    if args.beta_cap:
        label_parts.append(f"bcap{args.beta_cap}")
    if args.buffer > 1.0:
        label_parts.append(f"buf{args.buffer}")
    label_parts.append(f"k{args.topk}")
    label_parts.append(f"reb{args.rebalance}")
    run_label = "_".join(label_parts)

    print(f"[eval] 加载面板 {args.start} ~ ...")
    panel = Panel.load(index_code="000300", start=args.start)
    print(f"[eval] {len(panel.dates)} 交易日 × {len(panel.codes)} 只，配置={run_label}，"
          f"相位={phases}，滑点={args.slippage:.4f}")

    rows: list[dict] = []
    stabs: list[dict] = []
    full_perf: list[dict] = []
    yearly: list[pd.Series] = []
    for off in phases:
        warmup = args.base_warmup + off
        if args.ensemble:
            from quant.strategies.ensemble import PhaseEnsembleStrategy

            offsets = tuple(int(x) for x in args.ensemble_offsets.split(",") if x.strip())
            strat = PhaseEnsembleStrategy(
                panel=panel, cfg=cfg, name=args.strategy, offsets=offsets,
                warmup=warmup, buffer_ratio=args.buffer,
                timing=args.timing, vol_target=args.vol_target,
                min_exposure=args.min_exposure, vol_cap=args.vol_cap,
                min_change=args.min_change,
                beta_cap=args.beta_cap,
            )
        elif args.ic_gate:
            from quant.strategies.overlay import ICMonitorOverlay

            base = build_strategy(
                args.strategy, panel, cfg, warmup=warmup,
                buffer_ratio=args.buffer, timing=args.timing,
                vol_target=args.vol_target, ma_days=args.ma_days,
                min_exposure=args.min_exposure, vol_cap=args.vol_cap,
            )
            strat = ICMonitorOverlay(
                panel=panel, cfg=cfg, base=base,
                window=args.ic_window, min_ic=args.ic_min,
                off_exposure=args.ic_off_exposure, warmup=warmup,
            )
        else:
            strat = build_strategy(
                args.strategy, panel, cfg,
                warmup=warmup,
                buffer_ratio=args.buffer,
                timing=args.timing,
                vol_target=args.vol_target,
                ma_days=args.ma_days,
                min_exposure=args.min_exposure,
                vol_cap=args.vol_cap,
                beta_cap=args.beta_cap,
            )
        result = Backtester(panel, cfg).run(strat.weight_func(), name=f"{run_label}_p{off}")
        seg = segment_metrics(result.nav, result.benchmark_nav)
        stabs.append(stability_report(result.nav, result.benchmark_nav))
        full_perf.append(
            {
                "annual_return": float(result.metrics["annual_return"]),
                "max_drawdown": float(result.metrics["max_drawdown"]),
                "turnover_annual": float(result.metrics.get("turnover_annual", 0.0)),
            }
        )
        yearly.append(yearly_returns(result.nav))
        for label, vals in seg.items():
            if vals:
                rows.append({"phase": off, "segment": label, **vals})
        print(f"  phase={off} 全期年化 {result.metrics['annual_return'] * 100:6.2f}%  "
              f"回撤 {result.metrics['max_drawdown'] * 100:6.2f}%  "
              f"换手 {result.metrics.get('turnover_annual', 0):5.2f}x")

    df = pd.DataFrame(rows)
    if df.empty:
        print("[eval] 无有效结果")
        return 1

    # ---------- 稳定性指标（全期，跨相位） ----------
    stab_keys = [
        "annual_return", "sharpe", "max_drawdown", "calmar",
        "max_underwater_days", "rolling_1y_min", "rolling_1y_pct_positive",
        "worst_year", "pct_years_positive", "best_year_share", "excess_annual",
    ]
    stab_summary: dict[str, dict] = {}
    if stabs:
        print("\n=== 稳定性指标（全期 · 跨相位均值 ± 标准差）===")
        for key in stab_keys:
            vals = [s[key] for s in stabs if key in s]
            if not vals:
                continue
            mean = statistics.mean(vals)
            std = statistics.pstdev(vals)
            stab_summary[key] = {"mean": mean, "std": std, "values": vals}
            if key in ("max_underwater_days", "n_years"):
                print(f"  {key:<24} {mean:7.1f} ± {std:5.2f}")
            elif key in ("sharpe", "calmar"):
                print(f"  {key:<24} {mean:7.3f} ± {std:5.3f}")
            else:
                print(f"  {key:<24} {mean * 100:7.2f}% ± {std * 100:5.2f}%")

        # 按决策 0007 的 S1–S7 逐条判定（全期指标用每相位完整回测的结果）
        s1 = statistics.pstdev([f["annual_return"] for f in full_perf]) if len(full_perf) > 1 else 0.0
        turnover = statistics.mean([f["turnover_annual"] for f in full_perf])
        mdd = statistics.mean([f["max_drawdown"] for f in full_perf])
        val = stab_summary.get("rolling_1y_pct_positive", {}).get("mean", 0.0)
        underwater = stab_summary.get("max_underwater_days", {}).get("mean", 1e9)
        best_share = stab_summary.get("best_year_share", {}).get("mean", 1.0)
        val_ann = [r["annual_return"] for r in rows if r["segment"] == "验证 2023-2024"]
        oos_ann = [r["annual_return"] for r in rows if r["segment"] == "样本外 2025-"]
        s4_val = statistics.mean(val_ann + oos_ann) if (val_ann or oos_ann) else 0.0

        # S7 修订（决策 0008）：相对基准校准。基准自身的年度集中度是天然下限，
        # 用 max(40%, 基准share + 5pp) 作为阈值，避免绝对阈值落在噪声带内。
        bench_yearly = yearly_returns(panel.benchmark)
        bench_pos = float(bench_yearly[bench_yearly > 0].sum())
        bench_share = float(bench_yearly.max() / bench_pos) if bench_pos > 0 else 0.0
        s7_threshold = max(0.40, bench_share + 0.05)
        checks = {
            "S1 相位离散≤1.5%": s1 <= 0.015,
            "S2 换手≤15x": turnover <= 15.0,
            "S3 回撤≤28%": abs(mdd) <= 0.28,
            "S4 验证+样本外均值为正": s4_val > 0.0,
            "S5 滚动1年正收益≥60%": val >= 0.60,
            "S6 水下≤400日": underwater <= 400,
            f"S7 单年占比≤{s7_threshold:.1%}(基准{bench_share:.1%})": best_share <= s7_threshold,
        }
        print("\n=== 稳定标准判定（决策 0007）===")
        print(f"  全期：年化 {statistics.mean([f['annual_return'] for f in full_perf]) * 100:.2f}% "
              f"± {s1 * 100:.2f}%，回撤 {mdd * 100:.2f}%，换手 {turnover:.2f}x，"
              f"验证+OOS 年化 {statistics.mean(val_ann + oos_ann) * 100:.2f}%")
        for name, ok in checks.items():
            print(f"  {'✅' if ok else '❌'} {name}")        # 年度收益（跨相位均值）——用于诊断 S7「单年占比」
        if yearly:
            ydf = pd.DataFrame(yearly)
            ymean = ydf.mean()
            print("\n=== 年度收益（跨相位均值）===")
            print("  " + "  ".join(f"{y}:{v * 100:6.2f}%" for y, v in zip(ymean.index, ymean.values)))
            stab_summary["yearly_mean"] = {int(y): float(v) for y, v in zip(ymean.index, ymean.values)}

        stab_summary["S_checks"] = {k: bool(v) for k, v in checks.items()}
        stab_summary["S_all_pass"] = all(checks.values())
        stab_summary["turnover_full"] = {
            "mean": turnover,
            "std": statistics.pstdev([f["turnover_annual"] for f in full_perf]),
        }
        stab_summary["bench_best_year_share"] = bench_share


    summary: dict[str, dict] = {"segments": {}, "stability": stab_summary}
    print(f"\n=== 多相位评估 · {run_label}（均值 ± 标准差）===")
    for seg_label, _, _ in SEGMENTS:
        sub = df[df["segment"] == seg_label]
        if sub.empty:
            continue
        summary["segments"][seg_label] = {}
        print(f"\n{seg_label}（{len(sub)} 个相位）")
        for key in METRICS:
            if key not in sub:
                continue
            vals = sub[key].tolist()
            mean = statistics.mean(vals)
            std = statistics.pstdev(vals)
            summary["segments"][seg_label][key] = {"mean": mean, "std": std, "values": vals}
            unstable = key in ("annual_return", "excess_annual") and std > 0.05
            flag = " ⚠️不稳定" if unstable else ""
            if key in ("annual_return", "excess_annual", "max_drawdown"):
                print(f"  {key:<18} {mean * 100:7.2f}% ± {std * 100:5.2f}%{flag}")
            else:
                print(f"  {key:<18} {mean:7.3f} ± {std:5.3f}{flag}")

    out_path = REPORTS_DIR / f"eval_{run_label}.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[eval] 结果 -> {out_path}")
    print("[eval] 判读规则：均值/标准差 < 1 的结论不可采信；单相位最优不能作为选择依据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
