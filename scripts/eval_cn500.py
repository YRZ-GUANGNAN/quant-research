"""中证500 宇宙验证：amihud 流动性溢价在更小市值池子里是否成立（新样本外）。

假设（先写后验）：
H1 amihud 溢价在 500 池子中**更强**（流动性溢价理论：越不流动补偿越高）；
H2 但反转因子可能**更弱**（小盘股噪音大、涨跌停/停牌更频繁）。

方法：与沪深300 完全相同的因子定义、评估协议与相位体系；
数据为 2018-2026 中证500 时点成分股（1179 只历史成员），前复权日线。
注意：本轮属**新样本外**（此前所有调参都在沪深300 上完成），
因此不允许根据本轮结果回头修改 300 上的参数。

用法：
    python scripts/eval_cn500.py --phases 0,5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import BacktestConfig  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.engine.backtest import Backtester  # noqa: E402
from quant.engine.metrics import performance, stability_report  # noqa: E402
from quant.factors.basic import build_factors  # noqa: E402
from quant.factors.transform import forward_returns, ic_series  # noqa: E402
from quant.strategies.ensemble import PhaseEnsembleStrategy  # noqa: E402

SEGMENTS = [
    ("训练 2018-2022", "2018-01-01", "2022-12-31"),
    ("验证 2023-2024", "2023-01-01", "2024-12-31"),
    ("样本外 2025-", "2025-01-01", ""),
]


def ic_table(panel: Panel) -> pd.DataFrame:
    factors = build_factors(panel)
    fwd = forward_returns(panel.close, 5)
    rows = []
    for name in ["amihud_20", "rev_20", "rev_5", "log_amount", "vol_20", "mom_60"]:
        ic = ic_series(factors[name], fwd).dropna()
        if ic.empty:
            continue
        rows.append({"factor": name, "ic_mean": ic.mean(), "ic_ir": ic.mean() / ic.std(),
                     "ic_win": (ic > 0).mean(), "n": len(ic)})
    return pd.DataFrame(rows).sort_values("ic_ir", ascending=False)


def main() -> int:
    ap = argparse.ArgumentParser(description="中证500 宇宙验证")
    ap.add_argument("--phases", default="0,5")
    ap.add_argument("--offsets", default="0,10", help="相位组合的错开天数（500 池子内存较大，默认 2 个相位）")
    args = ap.parse_args()
    phases = [int(x) for x in args.phases.split(",")]
    offsets = tuple(int(x) for x in args.offsets.split(","))

    panel = Panel.load(index_code="000905", market="cn500", start="2018-01-01")
    print(f"[cn500] {len(panel.dates)} 交易日 × {len(panel.codes)} 只  "
          f"{panel.dates[0].date()}~{panel.dates[-1].date()}")
    if panel.member_mask is not None:
        print(f"[cn500] 时点掩码：每日成分股中位数 {int(panel.member_mask.sum(axis=1).median())}")

    print("\n[cn500] 因子 IC（horizon=5，全样本）")
    print(ic_table(panel).round(4).to_string(index=False))

    cfg = BacktestConfig(index_code="000905", start="2018-01-01", top_k=40, rebalance_days=35)
    reps = []
    for off in phases:
        strat = PhaseEnsembleStrategy(panel=panel, cfg=cfg, name="ic_tilt",
                                      offsets=offsets, warmup=130 + off)
        res = Backtester(panel, cfg).run(strat.weight_func(), name=f"cn500_p{off}")
        rep = stability_report(res.nav, res.benchmark_nav)
        rep["phase"] = off
        rep["turnover"] = float(res.metrics.get("turnover_annual", 0))
        reps.append(rep)
        print(f"\n[cn500] phase={off} 年化 {rep['annual_return'] * 100:6.2f}%  "
              f"夏普 {rep['sharpe']:5.2f}  回撤 {rep['max_drawdown'] * 100:6.2f}%  "
              f"月胜率 {rep['rolling_1y_pct_positive'] * 100:5.1f}%(滚动1年)  "
              f"换手 {rep['turnover']:5.2f}x")
        for label, s, e in SEGMENTS:
            sub = res.nav[res.nav.index >= pd.Timestamp(s)]
            if e:
                sub = sub[sub.index <= pd.Timestamp(e)]
            if len(sub) < 40:
                continue
            m = performance(sub, benchmark=res.benchmark_nav.reindex(sub.index))
            print(f"    {label}: 年化 {m['annual_return'] * 100:6.2f}%  "
                  f"超额 {m.get('excess_annual', 0) * 100:6.2f}%  IR {m.get('information_ratio', 0):5.2f}")

    df = pd.DataFrame(reps)
    if len(df) > 1:
        print("\n[cn500] 多相位汇总：")
        for key in ("annual_return", "max_drawdown", "month_positive" if "month_positive" in df else "rolling_1y_pct_positive"):
            if key in df:
                print(f"  {key}: {df[key].mean() * 100:.2f}% ± {df[key].std() * 100:.2f}%")
    print("\n[cn500] 结论需与沪深300 对照（amihud IC_IR 300 上为 0.19）后写入日志。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
