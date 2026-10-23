"""模拟盘入口：初始化账户、推进到最新交易日、打印账户状态。

用法：
    python scripts/run_paper.py                 # 用已有数据推进
    python scripts/run_paper.py --fetch         # 先增量抓数据再推进
    python scripts/run_paper.py --reset         # 重置账户（重新开始练手）
    python scripts/run_paper.py --strategy multi
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import PAPER_DIR, BacktestConfig, ensure_dirs  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.paper.state import PaperState  # noqa: E402
from quant.paper.trader import PaperTrader  # noqa: E402
from quant.strategies.topk import TopKStrategy  # noqa: E402

PRESETS: dict[str, dict[str, float]] = {
    "momentum": {"mom_60": 1.0},
    "reversal": {"rev_5": 1.0},
    "multi": {"mom_60": 0.4, "rev_5": 0.3, "vol_20": -0.2, "turnover_20": -0.1},
    # 决策 0005 冻结模型（模拟盘默认使用）
    "ic_tilt": {
        "amihud_20": 0.35, "rev_20": 0.20, "rev_5": 0.15,
        "log_amount": -0.20, "ma_gap_60": -0.10, "vol_20": -0.05,
    },
}


def main() -> int:
    ap = argparse.ArgumentParser(description="模拟盘日更")
    ap.add_argument("--index", default="000300")
    ap.add_argument("--strategy", default="ic_tilt", choices=sorted(PRESETS))
    ap.add_argument("--cash", type=float, default=1_000_000.0)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--topk", type=int, default=40)
    ap.add_argument("--rebalance", type=int, default=35)
    ap.add_argument("--ensemble", action="store_true", default=True,
                    help="使用相位不变组合（模型 v3 默认）")
    ap.add_argument("--no-ensemble", dest="ensemble", action="store_false")
    ap.add_argument("--exposure", type=float, default=0.70,
                    help="静态仓位系数（0.70=70%% 股票+30%% 现金，回撤目标 -20%%）")
    args = ap.parse_args()

    ensure_dirs()
    if args.fetch:
        print("[fetch] 增量抓取 ...")
        subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "fetch_data.py"), "--index", args.index],
            check=False,
        )

    cfg = BacktestConfig(index_code=args.index, top_k=args.topk, rebalance_days=args.rebalance)
    panel = Panel.load(index_code=args.index)
    if args.ensemble:
        from quant.strategies.ensemble import PhaseEnsembleStrategy

        strategy = PhaseEnsembleStrategy(
            panel=panel, cfg=cfg, name=args.strategy, warmup=130,
            exposure_scale=args.exposure,
        )
        print(f"[paper] 模型：相位不变组合（{len(strategy.offsets)} 相位共识），"
              f"仓位系数 {args.exposure:.2f}")
    else:
        strategy = TopKStrategy(panel=panel, cfg=cfg, factor_weights=PRESETS[args.strategy])

    if args.reset:
        PaperState.path().unlink(missing_ok=True)

    state = PaperState.load()
    if state is None:
        state = PaperState.create(args.cash, warmup=130)
        print("[paper] 新建模拟账户，初始资金 %.0f 元" % args.cash)

    trader = PaperTrader(panel=panel, cfg=cfg, strategy=strategy, state=state, warmup=130)
    records = trader.run()

    if not records:
        print("[paper] 已是最新交易日，无新数据")
    else:
        print(f"[paper] 推进 {len(records)} 个交易日："
              f"{records[0]['date']} → {records[-1]['date']}")

    # 账户快照
    nav = pd.Series(
        {r["date"]: r["equity"] for r in state.nav_history}, dtype=float
    )
    print("\n=== 模拟账户 ===")
    print(f"  最后交易日 : {state.last_date}")
    print(f"  总资产     : {state.equity():,.2f} 元")
    print(f"  现金       : {state.cash:,.2f} 元")
    print(f"  持仓数     : {len(state.positions)}")
    if len(nav) > 1:
        ret = nav.iloc[-1] / nav.iloc[0] - 1
        peak = nav.cummax()
        mdd = (nav / peak - 1).min()
        print(f"  累计收益   : {ret * 100:+.2f}%")
        print(f"  最大回撤   : {mdd * 100:.2f}%")
    print(f"  状态文件   : {PaperState.path()}")

    if state.positions:
        print("\n  当前持仓（前 10）：")
        rows = sorted(state.positions.items(), key=lambda kv: -kv[1]["shares"] * kv[1]["last_price"])[:10]
        for code, p in rows:
            value = p["shares"] * p["last_price"]
            name = panel.names.get(code, "")
            print(f"    {code} {name:<6} {p['shares']:>8} 股  市值 {value:>12,.0f}  现价 {p['last_price']:.2f}")
    if state.pending:
        print(f"\n  次日待执行目标：{len(state.pending)} 只（总仓位 {sum(state.pending.values()):.1%}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
