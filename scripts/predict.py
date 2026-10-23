"""本地量化模型：每日生成目标组合与调仓指令。

这是「研究成果 → 可运行产物」的最后一公里：
1. 读取本地行情缓存（增量更新后）；
2. 用冻结的因子权重（决策 0005：ic_tilt）计算当日目标持仓；
3. 与模拟盘当前持仓对比，输出**具体股数与方向**的交易指令；
4. 支持导出模型规格 JSON（模型版本、权重、创建时间、数据截止日）。

用法：
    python scripts/predict.py                 # 今日目标组合 + 调仓指令
    python scripts/predict.py --export        # 同时导出 models/model_v1.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import BacktestConfig, ROOT, ensure_dirs  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.paper.state import PaperState  # noqa: E402
from quant.strategies.topk import TopKStrategy  # noqa: E402

# 冻结模型 v3：决策 0007/0008 —— ic_tilt + 4 相位共识 + 40 只 + 35 日调仓
MODEL = {
    "name": "ic_tilt_ensemble_v3",
    "strategy": "4 相位共识 + TopK40 每日校准（相位不变组合）",
    "factor_weights": {
        "amihud_20": 0.35,
        "rev_20": 0.20,
        "rev_5": 0.15,
        "log_amount": -0.20,
        "ma_gap_60": -0.10,
        "vol_20": -0.05,
    },
    "ensemble": True,
    "phases": [0, 5, 10, 15],
    "horizon_days": 5,
    "top_k": 40,
    "rebalance_days": 35,
    "universe": "沪深300 时点成分股（BaoStock 半年快照）",
    "notes": "稳定标准 S1-S7 全过（决策 0007/0008）：年化 15.5%、回撤 -26.4%、"
             "换手 10.3x、滚动 1 年正收益比例 82%、最长水下 279 个交易日。"
             "样本外超额仍接近 0：这是稳定但无 alpha 的模型，仅用于模拟盘演练。",
}

MODEL_V1_NOTE = "v1（单相位）已归档：models/model_v1.json；v2 见 models/model_v2.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成目标组合与调仓指令")
    ap.add_argument("--topk", type=int, default=MODEL["top_k"])
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--cash", type=float, default=1_000_000.0)
    ap.add_argument("--exposure", type=float, default=0.70,
                    help="静态仓位系数（0.70=低回撤模式）")
    args = ap.parse_args()

    ensure_dirs()
    cfg = BacktestConfig(top_k=args.topk, rebalance_days=MODEL["rebalance_days"])
    panel = Panel.load(index_code="000300")
    last_date = panel.dates[-1]

    if MODEL.get("ensemble"):
        # 相位共识：每个相位独立选股，再把权重加总取前 top_k（与 PhaseEnsembleStrategy 一致）
        total: dict[str, float] = {}
        for off in MODEL["phases"]:
            member = TopKStrategy(
                panel=panel, cfg=cfg, factor_weights=MODEL["factor_weights"],
                warmup=130 + off,
            )
            for code, w in member.target_weights(last_date).items():
                total[code] = total.get(code, 0.0) + w
        ranked = sorted(total.items(), key=lambda kv: -kv[1])[: cfg.top_k]
        s = sum(v for _, v in ranked) or 1.0
        gross = 1.0 - cfg.cash_buffer
        weights = {code: v / s * gross for code, v in ranked}
    else:
        strat = TopKStrategy(panel=panel, cfg=cfg, factor_weights=MODEL["factor_weights"])
        weights = strat.target_weights(last_date)
    if not weights:
        print(f"[model] {last_date.date()} 无有效目标组合（数据不足或非调仓日）")
        return 1

    if args.exposure != 1.0:
        weights = {code: w * args.exposure for code, w in weights.items()}
        print(f"[exposure] 已按 {args.exposure:.2f} 仓位缩放（低回撤模式）")
    print(f"=== {MODEL['name']} · 目标组合 @ {last_date.date()} ===")
    print(f"因子权重: {MODEL['factor_weights']}")
    print(f"目标持仓 {len(weights)} 只，总仓位 {sum(weights.values()):.1%}\n")
    print(f"{'代码':<8}{'名称':<10}{'权重':>8}{'收盘价':>10}")
    close = panel.close.loc[last_date]
    for code, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        name = panel.names.get(code, "")
        print(f"{code:<8}{name:<10}{w:>8.2%}{close.get(code, float('nan')):>10.2f}")

    # 与模拟盘当前持仓对比，生成交易指令
    state = PaperState.load()
    if state and state.positions:
        print("\n=== 相对当前模拟盘的调仓指令（按 100 股整手估算）===")
        equity = state.equity()
        orders = []
        for code, w in weights.items():
            px = float(close.get(code, 0) or 0)
            if px <= 0:
                continue
            target_shares = int(w * equity / px) // 100 * 100
            cur = state.positions.get(code, {}).get("shares", 0)
            delta = target_shares - cur
            if abs(delta) >= 100:
                orders.append((code, panel.names.get(code, ""), delta, px))
        for code, name, delta, px in sorted(orders, key=lambda x: -abs(x[2] * x[3])):
            side = "买入" if delta > 0 else "卖出"
            print(f"  {side} {code} {name:<8} {abs(delta):>7} 股  约 {abs(delta) * px:>12,.0f} 元")
        for code in state.positions:
            if code not in weights:
                pos = state.positions[code]
                print(f"  卖出 {code} {panel.names.get(code, ''):<8} {pos['shares']:>7} 股  （清仓）")
    else:
        print("\n（无模拟盘持仓，指令为初始建仓）")

    if args.export:
        out = ROOT / "models" / "model_v3.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **MODEL,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "data_start": str(panel.dates[0].date()),
            "data_end": str(last_date.date()),
            "n_codes": len(panel.codes),
            "exposure_scale": args.exposure,
            "targets": {code: round(w, 6) for code, w in weights.items()},
        }
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[model] 模型规格已导出 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
