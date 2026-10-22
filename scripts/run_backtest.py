"""回测入口。

示例：
    python scripts/run_backtest.py --strategy momentum
    python scripts/run_backtest.py --strategy reversal --slippage 0.001
    python scripts/run_backtest.py --strategy multi --topk 20 --rebalance 10
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import REPORTS_DIR, BacktestConfig, Costs, ensure_dirs  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.engine.backtest import Backtester  # noqa: E402
from quant.engine.metrics import monthly_returns, performance  # noqa: E402
from quant.factors.basic import build_factors  # noqa: E402
from quant.factors.transform import forward_returns, ic_series  # noqa: E402
from quant.strategies.topk import TopKStrategy  # noqa: E402

# 策略预设：只写「有先验逻辑」的因子权重，方向来自经济含义，最终仍需 IC 检验
PRESETS: dict[str, dict[str, float]] = {
    "momentum": {"mom_60": 1.0},
    "reversal": {"rev_5": 1.0},
    # v2 旧预设：凭先验拍的方向（动量为主），用于和 IC 驱动版本对照
    "multi": {"mom_60": 0.4, "rev_5": 0.3, "vol_20": -0.2, "turnover_20": -0.1},
    # v3 IC 倾斜：符号来自 2018-2022 训练期 IC 诊断（amihud 流动性溢价 + 反转 + 小成交额）
    "ic_tilt": {
        "amihud_20": 0.35,
        "rev_20": 0.20,
        "rev_5": 0.15,
        "log_amount": -0.20,
        "ma_gap_60": -0.10,
        "vol_20": -0.05,
    },
}


# IC 加权策略使用的因子集合：覆盖动量/反转/波动/流动性/价格结构
ICW_FACTORS = [
    "amihud_20", "rev_20", "rev_5", "vol_20", "vol_60", "log_amount",
    "ma_gap_60", "ma_gap_20", "mom_120", "max_ret_20", "turnover_20",
    "beta_60", "excess_mom_60", "rsi_14", "skew_60", "volume_ratio",
]

# 评估协议（决策 0002）规定的三段切分
SEGMENTS = [
    ("训练 2018-2022", "2018-01-01", "2022-12-31"),
    ("验证 2023-2024", "2023-01-01", "2024-12-31"),
    ("样本外 2025-", "2025-01-01", ""),
]


def factor_ic_report(panel: Panel, horizon: int = 5) -> pd.DataFrame:
    """输出各因子 IC 的均值/标准差/IR/胜率，用于判断因子方向是否成立。"""
    factors = build_factors(panel)
    fwd = forward_returns(panel.close, horizon)
    rows = []
    for name, fac in factors.items():
        ic = ic_series(fac, fwd)
        ic = ic.dropna()
        if ic.empty:
            continue
        rows.append(
            {
                "factor": name,
                "ic_mean": ic.mean(),
                "ic_std": ic.std(),
                "ic_ir": ic.mean() / ic.std() if ic.std() > 0 else 0.0,
                "ic_win": (ic > 0).mean(),
                "n": len(ic),
            }
        )
    table = pd.DataFrame(rows)
    if table.empty:
        print("[IC] 警告：没有可计算的因子（样本太少或数据缺失）")
        return pd.DataFrame(columns=["factor", "ic_mean", "ic_std", "ic_ir", "ic_win", "n"])
    return table.sort_values("ic_ir", ascending=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="回测 Top-K 因子策略")
    ap.add_argument("--strategy", default="momentum", choices=sorted(PRESETS) + ["ic", "icw", "lgbm"])
    ap.add_argument("--market", default="cn", choices=["cn", "us", "hk", "crypto"])
    ap.add_argument("--index", default="000300")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--topk", type=int, default=30)
    ap.add_argument("--rebalance", type=int, default=20)
    ap.add_argument("--slippage", type=float, default=5e-4)
    ap.add_argument("--horizon", type=int, default=5, help="IC / 机器学习的前瞻收益天数")
    ap.add_argument("--warmup", type=int, default=0, help="预热天数（用于调仓相位敏感性测试）")
    ap.add_argument("--segments", action="store_true", help="输出训练/验证/样本外分段绩效")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    ensure_dirs()
    cfg = BacktestConfig.for_market(
        args.market,
        start=args.start,
        end=args.end,
        top_k=args.topk,
        rebalance_days=args.rebalance,
    )
    cfg = cfg.with_(costs=replace(cfg.costs, slippage=args.slippage))

    print(f"[panel] 加载 {args.market} 面板 ...")
    panel = Panel.load(
        index_code=args.index if args.market == "cn" else None,
        start=args.start,
        end=args.end or None,
        market=args.market,
    )
    print(f"[panel] {len(panel.dates)} 个交易日 × {len(panel.codes)} 只股票  "
          f"{panel.dates[0].date()} ~ {panel.dates[-1].date()}")

    if args.strategy == "ic":
        table = factor_ic_report(panel, horizon=args.horizon)
        pd.set_option("display.width", 160)
        print("\n[IC] 因子截面 IC 诊断（horizon=%d）" % args.horizon)
        print(table.to_string(index=False, float_format=lambda x: f"{x:+.4f}"))
        out = REPORTS_DIR / f"factor_ic_{args.market}.csv"
        table.to_csv(out, index=False)
        print(f"[IC] 已保存 {out}")
        return 0

    if args.strategy not in PRESETS and args.strategy not in ("icw", "lgbm"):
        raise SystemExit(f"未知策略 {args.strategy}")
    factor_weights = PRESETS.get(args.strategy, {})

    if args.strategy == "icw":
        from quant.models.linear import ICWeightedStrategy

        strat = ICWeightedStrategy(
            panel=panel, cfg=cfg, factors=ICW_FACTORS, horizon=args.horizon,
            warmup=args.warmup or 150,
        )
        bt = Backtester(panel, cfg)
        print(f"\n[backtest] 策略=IC加权 horizon={args.horizon} topk={cfg.top_k} "
              f"rebalance={cfg.rebalance_days}")
        result = bt.run(strat.weight_func(), name="icw")
        print("\n" + result.summary())
        print("\n[IC加权] 最近一次因子权重（按 |IC| 排序）：")
        print(strat.ic_diagnostics().head(8).round(4).to_string())
        tag = args.tag or "icw"
    elif args.strategy == "lgbm":
        from quant.models.gbdt import LGBMStrategy

        strat = LGBMStrategy(panel=panel, cfg=cfg, horizon=args.horizon,
                             warmup=args.warmup or 300)
        bt = Backtester(panel, cfg)
        print(f"\n[backtest] 策略=LightGBM horizon={args.horizon} topk={cfg.top_k} "
              f"rebalance={cfg.rebalance_days}")
        result = bt.run(strat.weight_func(), name="lgbm")
        print("\n" + result.summary())
        print("\n[LightGBM] 特征重要性 top10：")
        print(strat.feature_importance().head(10).to_string())
        tag = args.tag or "lgbm"
    else:
        factor_weights = PRESETS[args.strategy]
        strat = TopKStrategy(
            panel=panel, cfg=cfg, factor_weights=factor_weights,
            warmup=args.warmup or 130,
        )
        bt = Backtester(panel, cfg)
        print(f"\n[backtest] 策略={args.strategy} topk={cfg.top_k} rebalance={cfg.rebalance_days} "
              f"slippage={cfg.costs.slippage:.4f}")
        result = bt.run(strat.weight_func(), name=args.strategy)
        print("\n" + result.summary())
        tag = args.tag or f"{args.strategy}_k{cfg.top_k}_r{cfg.rebalance_days}"

    print(f"\n  基准年化   : {result.metrics.get('bench_annual_return', 0) * 100:8.2f}%")
    print(f"  年化超额   : {result.metrics.get('excess_annual', 0) * 100:8.2f}%")

    if args.segments:
        print("\n=== 分段绩效（同一净值序列切片，避免重复训练/泄漏）===")
        for label, s, e in SEGMENTS:
            sub = result.nav[result.nav.index >= pd.Timestamp(s)]
            if e:
                sub = sub[sub.index <= pd.Timestamp(e)]
            if len(sub) < 40:
                print(f"  {label}: 样本不足")
                continue
            bsub = result.benchmark_nav.reindex(sub.index)
            m = performance(sub, benchmark=bsub, name=label)
            print(f"  {label}: 年化 {m['annual_return'] * 100:6.2f}%  "
                  f"超额 {m.get('excess_annual', 0) * 100:6.2f}%  "
                  f"夏普 {m['sharpe']:5.2f}  回撤 {m['max_drawdown'] * 100:6.2f}%  "
                  f"IR {m.get('information_ratio', 0):5.2f}")

    nav_df = pd.DataFrame(
        {
            "nav": result.nav / result.nav.iloc[0],
            "benchmark": result.benchmark_nav,
        }
    )
    nav_path = REPORTS_DIR / f"nav_{tag}.csv"
    nav_df.to_csv(nav_path)
    print(f"\n[report] 净值序列 -> {nav_path}")

    monthly = monthly_returns(result.nav)
    if not monthly.empty:
        print("\n[report] 月度收益(%)：")
        print((monthly * 100).round(2).to_string())

    summary_path = REPORTS_DIR / f"summary_{tag}.json"
    summary_path.write_text(
        json.dumps(
            {
                "strategy": args.strategy,
                "factor_weights": factor_weights,
                "config": {
                    "index": cfg.index_code,
                    "start": cfg.start,
                    "end": cfg.end,
                    "topk": cfg.top_k,
                    "rebalance_days": cfg.rebalance_days,
                    "slippage": cfg.costs.slippage,
                },
                "metrics": result.metrics,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"[report] 指标 -> {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
