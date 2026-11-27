"""净值曲线与绩效指标：把 nav_history.csv 变成可判定的收益/风险数字。

为什么需要它：晋级闸门要回答「模拟这段时间到底赚没赚、波动多大」，
面板只写了每天的净值数字，没人能一眼看出年化、夏普、回撤、胜率。

口径（写死，避免每次口径漂移）：
- 收益：首日 → 末日累计收益；
- 夏普：日收益均值/标准差 × √252，无风险利率按 0；
- 回撤：peak → trough 的最大回撤（不是只看当前）；
- 胜率：上涨交易日占比；
- 合计：只取三个市场都有数据的交易日（避免某天缺数据造成假涨跌）。

用法：
    python scripts/equity_curve.py            # 打印 + 写 reports/equity_curve.json
    python scripts/equity_curve.py --json     # 只输出 JSON
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
TRADING_DAYS = 252
COMBINED_MARKETS = ("us", "hk", "cn")   # 合计只算券商实盘账户；cn_model 是模型轨道，不并入


def load_nav(path: Path | None = None) -> dict[str, list[tuple[str, float]]]:
    """读净值历史 → {market: [(date, equity)]}，按日期升序。"""
    p = path or (REPORTS / "nav_history.csv")
    out: dict[str, list[tuple[str, float]]] = {}
    if not p.exists():
        return out
    with p.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                market = str(row["market"]).strip().lower()
                date = str(row["date"]).strip()
                equity = float(row["equity"])
            except (KeyError, TypeError, ValueError):
                continue  # noqa: silent-ok - 坏行跳过，不影响整体曲线
            out.setdefault(market, []).append((date, equity))
    for rows in out.values():
        rows.sort()
    return out


def series_metrics(rows: list[tuple[str, float]]) -> dict:
    """单条净值序列 → 收益/夏普/回撤/胜率。"""
    if len(rows) < 2:
        return {"days": len(rows), "return": None, "sharpe": None,
                "max_drawdown": None, "win_rate": None, "daily_vol": None}
    values = [v for _, v in rows]
    rets = [values[i] / values[i - 1] - 1.0 for i in range(1, len(values)) if values[i - 1] > 0]
    total = values[-1] / values[0] - 1.0 if values[0] > 0 else 0.0
    n = len(rets)
    mean = sum(rets) / n if n else 0.0
    var = sum((r - mean) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    sharpe = (mean / std * math.sqrt(TRADING_DAYS)) if std > 0 else None
    peak, mdd = 0.0, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1.0)
    wins = sum(1 for r in rets if r > 0)
    return {
        "days": len(values),
        "start": rows[0][0], "end": rows[-1][0],
        "return": round(total, 4),
        "max_drawdown": round(mdd, 4),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "daily_vol": round(std, 5),
        "win_rate": round(wins / n, 3) if n else None,
        "best_day": round(max(rets), 4) if rets else None,
        "worst_day": round(min(rets), 4) if rets else None,
    }


def combined_series(nav: dict[str, list[tuple[str, float]]]) -> list[tuple[str, float]]:
    """合计曲线：只取券商账户各市场都有数据的日期（缺一天就跳过，避免假跳变）。

    模型轨道（cn_model）和其他记录不并入合计——它和券商账户资金规模不同，
    混在一起算出来的「总收益」没有意义。
    """
    nav = {m: rows for m, rows in nav.items() if m in COMBINED_MARKETS}
    if not nav:
        return []
    per = {m: dict(rows) for m, rows in nav.items()}
    common = set.intersection(*(set(d.keys()) for d in per.values())) if per else set()
    return sorted((d, sum(per[m][d] for m in per)) for d in common)


def build_metrics(nav: dict[str, list[tuple[str, float]]]) -> dict:
    """所有市场 + 合计的指标，附带数据充分性提示。"""
    markets = {m: series_metrics(rows) for m, rows in nav.items()}
    total = series_metrics(combined_series(nav))
    warnings = []
    for m, stat in markets.items():
        if stat["days"] < 5:
            warnings.append(f"{m.upper()} 只有 {stat['days']} 天净值，指标不可靠")
        gaps = _gaps(nav[m])
        if gaps:
            warnings.append(f"{m.upper()} 有 {len(gaps)} 处日期断档（最近 {gaps[-1]}）")
    if total["days"] == 0:
        warnings.append("三市场没有公共交易日，合计曲线为空")
    return {"markets": markets, "total": total, "warnings": warnings}


def _gaps(rows: list[tuple[str, float]], max_days: int = 5) -> list[str]:
    """相邻记录间隔超过 max_days 自然日 → 视为断档（周末不算）。"""
    out = []
    for i in range(1, len(rows)):
        try:
            d0 = datetime.fromisoformat(rows[i - 1][0]).date()
            d1 = datetime.fromisoformat(rows[i][0]).date()
        except ValueError:
            continue  # noqa: silent-ok - 日期格式坏行跳过
        if (d1 - d0).days > max_days:
            out.append(f"{rows[i - 1][0]}→{rows[i][0]}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="净值曲线绩效指标")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args(argv)

    nav = load_nav()
    if not nav:
        print("[equity] 没有净值记录（reports/nav_history.csv 不存在或为空）")
        return 3
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"), **build_metrics(nav)}
    out = REPORTS / "equity_curve.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("=== 净值绩效 ===")
    print(f"{'市场':<6}{'天数':>4}{'累计':>10}{'最大回撤':>10}{'夏普':>8}{'胜率':>8}")
    for m, s in sorted(report["markets"].items()):
        def fmt(v, pct=False):
            if v is None:
                return "—"
            return f"{v * 100:.1f}%" if pct else f"{v:.2f}"
        print(f"{m.upper():<6}{s['days']:>4}{fmt(s['return'], True):>10}{fmt(s['max_drawdown'], True):>10}"
              f"{fmt(s['sharpe']):>8}{fmt(s['win_rate'], True):>8}")
    t = report["total"]
    print(f"{'合计':<6}{t['days']:>4}{fmt(t['return'], True):>10}{fmt(t['max_drawdown'], True):>10}"
          f"{fmt(t['sharpe']):>8}{fmt(t['win_rate'], True):>8}")
    for w in report["warnings"]:
        print(f"  [warn] {w}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
