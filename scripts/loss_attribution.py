"""亏损归因：把策略的亏损拆成 beta、alpha、成本与回撤事件。

回答三个问题：
1. 亏在什么时候（年度/月度拆解 vs 基准）；
2. 亏在什么（beta 暴露 vs 选股 alpha vs 交易成本）；
3. 亏多深、多久（回撤事件：峰值→谷底→修复）。

方法：
- 用 v3 模型（ic_tilt + 4 相位共识，k40/reb35）跑一遍回测；
- 对基准（沪深300）做日收益回归，得到 beta 与 alpha；
- 年度/月度对比，找出「市场涨我们跌」「市场跌我们跌更多」的月份；
- 成本用换手率 × 单边综合费率估算。

用法：
    python scripts/loss_attribution.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from quant.config import BacktestConfig  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.engine.backtest import Backtester  # noqa: E402
from quant.strategies.ensemble import PhaseEnsembleStrategy  # noqa: E402

# 单边综合费率：佣金 0.025% + 印花税（卖出 0.05%，折半计入单边）+ 滑点 0.05% + 过户费
COST_PER_SIDE = 0.00025 + 0.00025 + 0.0005 + 0.00001


def drawdown_episodes(nav: pd.Series, top: int = 5) -> list[dict]:
    peak = nav.cummax()
    dd = nav / peak - 1.0
    episodes = []
    in_dd = False
    start = trough = None
    trough_val = 0.0
    for date, value in dd.items():
        if value < -1e-9:
            if not in_dd:
                in_dd = True
                start = date
                trough = date
                trough_val = value
            elif value < trough_val:
                trough = date
                trough_val = value
        elif in_dd:
            in_dd = False
            episodes.append({"start": start, "trough": trough, "depth": trough_val,
                             "recovery": date, "days": (date - start).days})
    episodes.sort(key=lambda e: e["depth"])
    return episodes[:top]


def main() -> int:
    panel = Panel.load(index_code="000300", start="2018-01-01")
    cfg = BacktestConfig(start="2018-01-01", top_k=40, rebalance_days=35)
    strat = PhaseEnsembleStrategy(panel=panel, cfg=cfg, name="ic_tilt", warmup=130)
    res = Backtester(panel, cfg).run(strat.weight_func(), name="v3")
    nav, bench = res.nav, res.benchmark_nav
    r, b = nav.pct_change().dropna(), bench.pct_change().reindex(nav.index).dropna()
    common = r.index.intersection(b.index)
    r, b = r.loc[common], b.loc[common]

    beta = float(np.cov(r, b)[0, 1] / b.var())
    alpha_d = float(r.mean() - beta * b.mean())
    print("=" * 62)
    print("一、总体分解")
    print("=" * 62)
    print(f"策略年化 {res.metrics['annual_return'] * 100:6.2f}%  "
          f"基准年化 {res.metrics.get('bench_annual_return', 0) * 100:6.2f}%  "
          f"年化超额 {res.metrics.get('excess_annual', 0) * 100:6.2f}%")
    print(f"beta = {beta:.3f}  alpha(年化) = {alpha_d * 252 * 100:+.2f}%  "
          f"最大回撤 {res.metrics['max_drawdown'] * 100:.2f}%")
    print(f"估算成本拖累：换手 {res.metrics.get('turnover_annual', 0):.2f}x/年 × "
          f"单边 {COST_PER_SIDE * 100:.4f}% ≈ "
          f"{res.metrics.get('turnover_annual', 0) * COST_PER_SIDE * 100:.2f}%/年")

    print("\n" + "=" * 62)
    print("二、年度拆解：亏在市场还是亏在选股")
    print("=" * 62)
    s_year = nav.groupby(nav.index.year).last().pct_change()
    b_year = bench.groupby(bench.index.year).last().pct_change()
    s_year.iloc[0] = nav.groupby(nav.index.year).last().iloc[0] / nav.iloc[0] - 1
    b_year.iloc[0] = bench.groupby(bench.index.year).last().iloc[0] / bench.iloc[0] - 1
    print(f"{'年份':<6}{'策略':>9}{'基准':>9}{'超额':>9}{'beta贡献':>10}{'alpha贡献':>10}")
    for year in s_year.index:
        sr, br = s_year[year], b_year[year]
        beta_contrib = beta * br
        alpha_contrib = sr - beta_contrib
        print(f"{year:<6}{sr * 100:8.2f}%{br * 100:8.2f}%{(sr - br) * 100:8.2f}%"
              f"{beta_contrib * 100:9.2f}%{alpha_contrib * 100:9.2f}%")

    print("\n" + "=" * 62)
    print("三、最差月份：是市场拖累还是选股失误")
    print("=" * 62)
    m_s = nav.resample("ME").last().pct_change().dropna()
    m_b = bench.resample("ME").last().pct_change().reindex(m_s.index).fillna(0)
    rows = []
    for date in m_s.index:
        rows.append((date.strftime("%Y-%m"), m_s[date], m_b[date], m_s[date] - m_b[date]))
    worst = sorted(rows, key=lambda x: x[1])[:10]
    print(f"{'月份':<9}{'策略':>9}{'基准':>9}{'超额':>9}  判断")
    for month, sr, br, ex in worst:
        verdict = "市场拖累" if br < -0.02 else ("选股失误" if ex < -0.02 else "混合")
        print(f"{month:<9}{sr * 100:8.2f}%{br * 100:8.2f}%{ex * 100:8.2f}%  {verdict}")

    print("\n" + "=" * 62)
    print("四、回撤事件（亏损从哪来、多久修复）")
    print("=" * 62)
    for e in drawdown_episodes(nav):
        print(f"  {e['start'].date()} → 谷底 {e['trough'].date()} "
              f"深度 {e['depth'] * 100:6.2f}%  修复 {e['recovery'].date()}（{e['days']} 天）")

    print("\n" + "=" * 62)
    print("五、盈亏结构")
    print("=" * 62)
    print(f"月度正收益比例 {(m_s > 0).mean() * 100:.1f}%（共 {len(m_s)} 个月）")
    print(f"月度中位数 {m_s.median() * 100:+.2f}% / 均值 {m_s.mean() * 100:+.2f}% / "
          f"最差 {m_s.min() * 100:.2f}%")
    print(f"偏度 {m_s.skew():.2f}（负=左尾风险），峰度 {m_s.kurt():.2f}")
    print(f"最好月份 {m_s.idxmax().strftime('%Y-%m')} {m_s.max() * 100:+.2f}%  "
          f"最差月份 {m_s.idxmin().strftime('%Y-%m')} {m_s.min() * 100:+.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
