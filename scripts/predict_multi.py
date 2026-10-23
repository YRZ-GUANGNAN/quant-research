"""模型 v4（跨市场组合）的每日目标组合生成器。

按 60/30/10 分配资金到三个袖子，分别给出每个市场的目标持仓：

- A 股（60%）：ic_tilt 因子 + 4 相位共识，top_k=40；
- 美股（30%）：mom_60 动量，top_k=20；
- 港股（10%）：mom_60 动量，top_k=15。

说明（重要）：
本脚本只输出「各市场内部的目标权重」（相对该袖子资金的比例），
**不做汇率换算、不做跨市场资金划转、不保证同时段可执行**。
它对应的是研究模型，不是可实盘组合。

用法：
    python scripts/predict_multi.py
    python scripts/predict_multi.py --capital 1000000
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant.config import BacktestConfig  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.strategies.ensemble import PhaseEnsembleStrategy  # noqa: E402
from quant.strategies.presets import PRESETS  # noqa: E402
from quant.strategies.topk import TopKStrategy  # noqa: E402

# 模型 v4 规格（与 models/model_v4.json 一致）
SLEEVES = [
    {"name": "A股", "market": "cn", "weight": 0.60, "top_k": 40, "rebalance": 35,
     "preset": "ic_tilt", "ensemble": True},
    {"name": "美股", "market": "us", "weight": 0.30, "top_k": 20, "rebalance": 20,
     "preset": "momentum", "ensemble": False},
    {"name": "港股", "market": "hk", "weight": 0.10, "top_k": 15, "rebalance": 20,
     "preset": "momentum", "ensemble": False},
]


def sleeve_targets(sleeve: dict) -> dict[str, float]:
    panel = Panel.load(index_code=None, market=sleeve["market"])
    cfg = BacktestConfig.for_market(
        sleeve["market"], top_k=sleeve["top_k"], rebalance_days=sleeve["rebalance"]
    )
    last = panel.dates[-1]
    if sleeve["ensemble"]:
        strat = PhaseEnsembleStrategy(panel=panel, cfg=cfg, name=sleeve["preset"], warmup=130)
        strat.replay(len(panel.dates) - 1)  # 真实相位重放（修复假共识）
        return strat.target_weights(last)

    strat = TopKStrategy(panel=panel, cfg=cfg, factor_weights=PRESETS[sleeve["preset"]])
    return strat.target_weights(last)


def main() -> int:
    ap = argparse.ArgumentParser(description="模型 v4 多市场目标组合")
    ap.add_argument("--capital", type=float, default=1_000_000.0, help="总资金（人民币近似）")
    ap.add_argument("--show", type=int, default=10, help="每个市场显示前 N 只")
    args = ap.parse_args()

    print(f"=== 模型 v4 · 跨市场组合 @ {args.capital:,.0f} ===\n")
    for sleeve in SLEEVES:
        targets = sleeve_targets(sleeve)
        budget = args.capital * sleeve["weight"]
        print(f"[{sleeve['name']}] 资金 {budget:,.0f}（{sleeve['weight']:.0%}） "
              f"目标 {len(targets)} 只，总仓位 {sum(targets.values()):.1%}")
        names = Panel.load(index_code=None, market=sleeve["market"]).names
        for code, w in sorted(targets.items(), key=lambda kv: -kv[1])[: args.show]:
            print(f"   {code:<10}{names.get(code, ''):<12}{w:>7.2%}  约 {w * budget:>11,.0f}")
        if len(targets) > args.show:
            print(f"   ... 其余 {len(targets) - args.show} 只")
        print()
    print("注意：各市场权重相对本市场资金；未做汇率换算与跨市场资金划转。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
