"""成交质量（execution quality）：计划发出去之后，实际成交得怎么样。

关心三件事，都是上实钱前必须量化的：
1. **成交率**：委托股数里真正成交的比例（未成交=计划没落地）；
2. **滑点**：实际成交价 vs 挂单价的偏离（bps），买单偏高/卖单偏低都是成本；
3. **异常**：拒单、撤单比例。

数据来源：Longbridge `order --history`（含 executed_quantity / executed_price）。

用法：
    python scripts/execution_quality.py --days 10
    python scripts/execution_quality.py --days 30 --strict   # 成交率低于 95% 时返回 1
输出：reports/execution_quality.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"

MIN_FILL_RATE = 0.95     # 成交率下限
REJECT_STATUSES = {"Rejected", "Canceled", "Expired"}


def _num(value, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def summarize_orders(orders: list[dict]) -> dict:
    """纯函数：订单列表 → 成交率 / 滑点 / 异常计数。"""
    rows, slippages = [], []
    total_qty = total_filled = 0.0
    rejects = 0
    for o in orders or []:
        symbol = str(o.get("symbol") or "")
        side = str(o.get("side") or "").lower()
        status = str(o.get("status") or "")
        qty = _num(o.get("quantity"))
        filled = _num(o.get("executed_quantity"))
        limit = _num(o.get("price"))
        avg = _num(o.get("executed_price"))
        total_qty += qty
        total_filled += filled
        if status in REJECT_STATUSES:
            rejects += 1
        slip_bps = None
        if filled > 0 and limit > 0 and avg > 0:
            raw = (avg - limit) / limit * 10000
            slip_bps = round(-raw if side.startswith("s") else raw, 1)  # 卖单方向取反：越低越好
            slippages.append(slip_bps)
        rows.append({"symbol": symbol, "side": side, "status": status,
                     "qty": qty, "filled": filled,
                     "fill_ratio": round(filled / qty, 3) if qty else None,
                     "limit": limit, "avg": avg, "slippage_bps": slip_bps})
    slippages.sort()
    median = slippages[len(slippages) // 2] if slippages else None
    return {
        "orders": len(rows),
        "fill_rate": round(total_filled / total_qty, 4) if total_qty else None,
        "rejects": rejects,
        "reject_rate": round(rejects / len(rows), 4) if rows else None,
        "slippage_bps_median": median,
        "slippage_bps_worst": slippages[-1] if slippages else None,
        "slippage_bps_best": slippages[0] if slippages else None,
        "rows": sorted(rows, key=lambda r: -(r["slippage_bps"] or 0))[:10],
    }


def fetch_orders(days: int) -> list[dict]:
    start = (date.today() - timedelta(days=days)).isoformat()
    try:
        proc = subprocess.run([str(LB), "order", "--history", "--start", start, "--format", "json"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=90)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"[quality] 拉取历史订单失败: {type(exc).__name__} {exc}")
        return []
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        print(f"[quality] 拉取历史订单失败: {(proc.stderr or '无输出').strip()[:120]}")
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        print("[quality] 历史订单不是合法 JSON")
        return []


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="成交质量报告")
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--strict", action="store_true", help="成交率不足时返回 1")
    args = ap.parse_args(argv)

    orders = fetch_orders(args.days)
    if not orders:
        print("[quality] 窗口内没有订单（可能一直没真实下单）")
        return 3

    report = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "window_days": args.days, **summarize_orders(orders)}
    out = REPORTS / "execution_quality.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    fr = report["fill_rate"]
    print(f"=== 成交质量（近 {args.days} 天，{report['orders']} 笔）===")
    print(f"  成交率 {fr * 100:.1f}%（下限 {MIN_FILL_RATE * 100:.0f}%）" if fr is not None else "  成交率 无数据")
    print(f"  拒单 {report['rejects']} 笔（{report['reject_rate'] * 100:.1f}%）")
    if report["slippage_bps_median"] is not None:
        print(f"  滑点 中位 {report['slippage_bps_median']:+.1f}bps / 最差 {report['slippage_bps_worst']:+.1f}bps")
    for row in report["rows"][:5]:
        if row["slippage_bps"] is not None:
            print(f"    {row['symbol']:<10} {row['side']:<5} 成交 {row['filled']:g}/{row['qty']:g} "
                  f"挂 {row['limit']:.3f} → 成 {row['avg']:.3f}（{row['slippage_bps']:+.1f}bps）")
    print(f"  -> {out}")

    if args.strict and fr is not None and fr < MIN_FILL_RATE:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
