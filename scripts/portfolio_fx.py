"""汇率调整后的跨市场组合（模型 v4 的可估值版本）。

v4 原实验忽略汇率，因此只能算「研究结论」。本脚本把三个袖子换算成人民币：

    CNY 收益 = (1 + 本地收益) × (1 + 汇率收益) − 1

- A 股袖子：本币即人民币，无汇率项；
- 美股袖子：USD/CNY（Yahoo `CNY=X`）；
- 港股袖子：HKD/CNY（Yahoo `HKDCNY=X`）。

固定权重、按日再平衡（近似）；汇率缺失日用前值填充。
目的：检验「月胜率 58.1%」在计入汇率波动后是否仍然成立（决策 0009 的 Q2）。

用法：
    python scripts/portfolio_fx.py
    python scripts/portfolio_fx.py --phases 0,5
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from portfolio import COMBOS, build_sleeves, combine, granular_report  # noqa: E402
from quant.config import REPORTS_DIR, ensure_dirs  # noqa: E402
from quant.data import yahoo  # noqa: E402


def load_fx() -> tuple[pd.Series, pd.Series]:
    """返回 (USD/CNY 日收益, HKD/CNY 日收益)。"""
    usd = yahoo.fetch_chart("CNY=X", range_="10y")[["date", "close"]].set_index("date")["close"]
    hkd = yahoo.fetch_chart("HKDCNY=X", range_="10y")[["date", "close"]].set_index("date")["close"]
    print(f"[fx] USD/CNY {len(usd)} 行（{usd.index.min().date()}~{usd.index.max().date()}，"
          f"最新 {usd.iloc[-1]:.4f}）")
    print(f"[fx] HKD/CNY {len(hkd)} 行（{hkd.index.min().date()}~{hkd.index.max().date()}，"
          f"最新 {hkd.iloc[-1]:.4f}）")
    return usd.pct_change(), hkd.pct_change()


def to_cny(rets: pd.Series, fx_ret: pd.Series | None) -> pd.Series:
    """把本地货币收益换算成人民币收益。"""
    if fx_ret is None:
        return rets
    aligned = fx_ret.reindex(rets.index).ffill().fillna(0.0)
    return (1.0 + rets) * (1.0 + aligned) - 1.0


def main() -> int:
    ap = argparse.ArgumentParser(description="汇率调整后的跨市场组合")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--phases", default="0", help="相位偏移，如 0,5")
    args = ap.parse_args()
    ensure_dirs()
    phases = [int(x) for x in args.phases.split(",") if x.strip()]

    usd_ret, hkd_ret = load_fx()
    per_phase: list[dict] = []

    for off in phases:
        print(f"\n########## 相位 {off}（warmup={130 + off}） ##########")
        sleeves = build_sleeves(args.start, 130 + off)
        # 换算成人民币
        cny_sleeves = {
            "A股v3": sleeves["A股v3"],
            "美股动量": to_cny(sleeves["美股动量"], usd_ret),
            "港股动量": to_cny(sleeves["港股动量"], hkd_ret),
        }
        for label, weights in COMBOS:
            if any(k not in cny_sleeves for k in weights):
                continue
            rets = combine(cny_sleeves, weights)
            rep = granular_report(rets, label)
            rep["phase"] = off
            per_phase.append(rep)
            print(f"  [{label}] 年化 {rep['annual_return'] * 100:6.2f}%  波动 {rep['annual_vol'] * 100:5.2f}%  "
                  f"夏普 {rep['sharpe']:5.2f}  回撤 {rep['max_drawdown'] * 100:6.2f}%  "
                  f"月胜率 {rep['month_positive'] * 100:5.1f}%  季 {rep['quarter_positive'] * 100:5.1f}%  "
                  f"12月 {rep['r12_positive'] * 100:5.1f}%")

    df = pd.DataFrame(per_phase)
    print("\n=== 人民币计价 · 多相位汇总（均值 ± 标准差）===")
    summary: dict[str, dict] = {}
    for label, _ in COMBOS:
        sub = df[df["label"] == label]
        if sub.empty:
            continue
        row = {}
        for key in ("annual_return", "annual_vol", "sharpe", "max_drawdown",
                    "month_positive", "quarter_positive", "r6_positive", "r12_positive"):
            vals = sub[key].tolist()
            row[key] = {"mean": statistics.mean(vals),
                        "std": statistics.pstdev(vals) if len(vals) > 1 else 0.0}
        summary[label] = row
        print(f"\n{label}（{len(sub)} 相位）")
        print(f"  年化 {row['annual_return']['mean'] * 100:6.2f}% ± {row['annual_return']['std'] * 100:4.2f}%   "
              f"夏普 {row['sharpe']['mean']:5.2f}  回撤 {row['max_drawdown']['mean'] * 100:6.2f}%")
        print(f"  月胜率 {row['month_positive']['mean'] * 100:5.1f}%   季胜率 {row['quarter_positive']['mean'] * 100:5.1f}%   "
              f"12月胜率 {row['r12_positive']['mean'] * 100:5.1f}%")

    # 判定：决策 0009 的预测是「汇率可能把月胜率拉回 2-3pp」
    v4 = summary.get("A60/美30/港10", {})
    if v4:
        mp = v4["month_positive"]["mean"]
        qp = v4["quarter_positive"]["mean"]
        r12 = v4["r12_positive"]["mean"]
        print("\n=== S8/S9 复核（人民币口径）===")
        print(f"  {'✅' if mp >= 0.55 else '❌'} S8 月胜率≥55%：{mp * 100:.1f}%")
        print(f"  {'✅' if qp >= 0.60 else '❌'} S9 季胜率≥60%：{qp * 100:.1f}%")
        print(f"  {'✅' if r12 >= 0.80 else '❌'} 12月胜率≥80%：{r12 * 100:.1f}%")

    out = {
        "start": args.start, "phases": phases, "currency": "CNY",
        "fx_latest": {"USDCNY": float(1 / usd_ret.index.size * 0 + 0) if False else None},
        "per_phase": per_phase, "summary": summary,
    }
    path = REPORTS_DIR / "portfolio_fx_multi_market.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[report] -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
