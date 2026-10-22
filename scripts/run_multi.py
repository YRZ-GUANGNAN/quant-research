"""跨市场对照实验：同一套因子/引擎，跑 A 股 / 美股 / 港股 / 加密货币。

目的不是找「哪个市场赚钱」，而是检验：
1. 同一因子在不同交易制度下是否仍成立（方向一致性）；
2. 成本结构差异（A 股印花税、港股双边印花税、加密高滑点）如何影响结论；
3. 这套自建引擎能否跨市场复用（工程可移植性）。

用法：
    python scripts/run_multi.py --strategy multi
    python scripts/run_multi.py --strategy momentum --markets cn,us
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import REPORTS_DIR, BacktestConfig, ensure_dirs  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.engine.backtest import Backtester  # noqa: E402
from quant.factors.basic import build_factors  # noqa: E402
from quant.factors.transform import forward_returns, ic_series  # noqa: E402
from quant.strategies.topk import TopKStrategy  # noqa: E402

PRESETS: dict[str, dict[str, float]] = {
    "momentum": {"mom_60": 1.0},
    "reversal": {"rev_5": 1.0},
    "multi": {"mom_60": 0.4, "rev_5": 0.3, "vol_20": -0.2, "turnover_20": -0.1},
}

MARKET_LABEL = {"cn": "A股沪深300", "us": "美股", "hk": "港股", "crypto": "加密货币"}


def run_market(market: str, strategy: str, start: str, end: str, topk: int, rebalance: int) -> dict | None:
    try:
        panel = Panel.load(index_code=None, start=start, end=end or None, market=market)
    except Exception as exc:  # noqa: BLE001
        print(f"[{market}] 数据不可用: {exc}")
        return None
    # 非 A 股市场没有时点成分股，标的少，持仓数按池子大小自适应
    k = min(topk, max(5, len(panel.codes) // 3))
    cfg = BacktestConfig.for_market(
        market, start=start, end=end, top_k=k, rebalance_days=rebalance
    )
    strat = TopKStrategy(panel=panel, cfg=cfg, factor_weights=PRESETS[strategy])
    bt = Backtester(panel, cfg)
    result = bt.run(strat.weight_func(), name=f"{market}-{strategy}")
    m = result.metrics
    print(f"\n[{market}] {MARKET_LABEL[market]}  {len(panel.codes)} 只  "
          f"{panel.dates[0].date()} ~ {panel.dates[-1].date()}  持仓 {k}")
    print(f"  年化 {m['annual_return'] * 100:7.2f}%   基准 {m.get('bench_annual_return', 0) * 100:7.2f}%   "
          f"超额 {m.get('excess_annual', 0) * 100:7.2f}%   夏普 {m['sharpe']:5.2f}   "
          f"回撤 {m['max_drawdown'] * 100:6.2f}%   换手 {m.get('turnover_annual', 0):5.2f}x")
    return {"market": market, "label": MARKET_LABEL[market], "metrics": m, "codes": len(panel.codes),
            "start": str(panel.dates[0].date()), "end": str(panel.dates[-1].date())}


def ic_comparison(markets: list[str], start: str, horizon: int = 5) -> pd.DataFrame:
    """跨市场因子 IC 对照：方向是否一致。"""
    rows = []
    for market in markets:
        try:
            panel = Panel.load(index_code=None, start=start, market=market)
        except Exception:  # noqa: BLE001
            continue
        factors = build_factors(panel)
        fwd = forward_returns(panel.close, horizon)
        for name, fac in factors.items():
            ic = ic_series(fac, fwd).dropna()
            if ic.empty:
                continue
            rows.append({"market": market, "factor": name, "ic_mean": ic.mean(),
                         "ic_ir": ic.mean() / ic.std() if ic.std() > 0 else 0.0, "n": len(ic)})
    return pd.DataFrame(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description="跨市场对照")
    ap.add_argument("--strategy", default="multi", choices=sorted(PRESETS))
    ap.add_argument("--markets", default="cn,us,hk,crypto")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--topk", type=int, default=30)
    ap.add_argument("--rebalance", type=int, default=20)
    ap.add_argument("--ic", action="store_true", help="同时输出跨市场因子 IC 对照")
    args = ap.parse_args()

    ensure_dirs()
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]
    results = []
    for market in markets:
        r = run_market(market, args.strategy, args.start, args.end, args.topk, args.rebalance)
        if r:
            results.append(r)

    if results:
        table = pd.DataFrame(
            [
                {
                    "市场": r["label"],
                    "标的数": r["codes"],
                    "年化%": round(r["metrics"]["annual_return"] * 100, 2),
                    "基准年化%": round(r["metrics"].get("bench_annual_return", 0) * 100, 2),
                    "超额%": round(r["metrics"].get("excess_annual", 0) * 100, 2),
                    "夏普": round(r["metrics"]["sharpe"], 2),
                    "最大回撤%": round(r["metrics"]["max_drawdown"] * 100, 2),
                    "换手x": round(r["metrics"].get("turnover_annual", 0), 2),
                }
                for r in results
            ]
        )
        print("\n=== 跨市场对照 ===")
        print(table.to_string(index=False))
        (REPORTS_DIR / f"multi_market_{args.strategy}.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

    if args.ic:
        icdf = ic_comparison(markets, args.start)
        if not icdf.empty:
            print("\n=== 跨市场因子 IC（方向一致性）===")
            pivot = icdf.pivot(index="factor", columns="market", values="ic_mean")
            print(pivot.round(4).to_string())
            icdf.to_csv(REPORTS_DIR / "cross_market_ic.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
