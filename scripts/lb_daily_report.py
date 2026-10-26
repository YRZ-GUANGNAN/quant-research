"""日度对照报告：模型目标 vs 券商模拟盘实际成交。

输出三张表：
1. **成交率**：每只标的的下单量 / 成交量 / 剩余挂单量；
2. **成交价 vs 限价**：成交均价相对委托限价的偏差（滑点，正=更贵/更差）；
3. **汇总**：订单数、成交率、加权滑点、未成交原因分布。

说明：模型参考价用「委托限价」近似（限价本身由实时价派生），
因此这里的滑点是**相对委托限价**的实现质量指标，不是相对收盘价的 alpha。

用法：
    python scripts/lb_daily_report.py
    python scripts/lb_daily_report.py --date 2026-10-23
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"


def cli_json(args: list[str]) -> object:
    proc = subprocess.run([str(LB), *args, "--format", "json"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=40)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or out).strip()[:200])
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        start, end = out.find("{"), out.rfind("}")
        if start >= 0 and end > start:
            return json.loads(out[start:end + 1])
        raise


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    args = ap.parse_args()

    orders = [o for o in (cli_json(["order"]) or []) if isinstance(o, dict)]
    if not orders:
        md = f"# 券商模拟盘日度对照 · {args.date}\n\n- 今日无订单（休市或被门控跳过）\n"
        reports = ROOT / "reports"
        reports.mkdir(exist_ok=True)
        (reports / f"broker_daily_{args.date}.md").write_text(md, encoding="utf-8")
        print("[report] 今日无订单，已写入空报告")
        return 0

    agg: dict[str, dict] = defaultdict(lambda: {
        "symbol": "", "orders": 0, "submitted_qty": 0.0, "filled_qty": 0.0,
        "amount": 0.0, "limit_amount": 0.0, "statuses": defaultdict(int),
    })
    for o in orders:
        sym = o.get("symbol", "?")
        a = agg[sym]
        a["symbol"] = sym
        a["orders"] += 1
        qty = float(o.get("quantity") or 0)
        exq = float(o.get("executed_quantity") or 0)
        price = o.get("price")
        exprice = o.get("executed_price")
        a["submitted_qty"] += qty
        a["filled_qty"] += exq
        a["statuses"][o.get("status", "?")] += 1
        if price and price != "-":
            a["limit_amount"] += qty * float(price)
        if exprice and exprice != "-" and exq:
            a["amount"] += exq * float(exprice)

    rows = []
    for sym, a in agg.items():
        fill_rate = a["filled_qty"] / a["submitted_qty"] if a["submitted_qty"] else 0.0
        avg_fill = a["amount"] / a["filled_qty"] if a["filled_qty"] else None
        avg_limit = a["limit_amount"] / a["submitted_qty"] if a["submitted_qty"] else None
        slip = (avg_fill / avg_limit - 1.0) if (avg_fill and avg_limit) else None
        rows.append({
            "symbol": sym,
            "market": sym.split(".")[-1] if "." in sym else "?",
            "orders": a["orders"],
            "submitted_qty": a["submitted_qty"],
            "filled_qty": a["filled_qty"],
            "fill_rate": round(fill_rate, 4),
            "avg_fill_price": round(avg_fill, 4) if avg_fill else None,
            "avg_limit_price": round(avg_limit, 4) if avg_limit else None,
            "slippage_vs_limit": round(slip, 6) if slip is not None else None,
            "statuses": dict(a["statuses"]),
        })
    def norm(symbol: str) -> str:
        if symbol.endswith(".HK"):
            code = symbol.split(".")[0]
            return f"{code.zfill(4)}.HK"
        return symbol

    for r in rows:
        r["symbol"] = norm(r["symbol"])
    rows.sort(key=lambda r: -r["submitted_qty"])

    # 拒单原因：对 Rejected 订单逐笔查 detail 的 remark
    reasons: dict[str, int] = defaultdict(int)
    seen_ids: set[str] = set()
    for o in orders:
        if o.get("status") != "Rejected":
            continue
        oid = str(o.get("order_id"))
        if oid in seen_ids:
            continue
        seen_ids.add(oid)
        try:
            d = cli_json(["order", "detail", oid])
            remark = ""
            if isinstance(d, dict):
                remark = str(d.get("remark") or "")
                if not remark and d.get("history"):
                    remark = str(d["history"][-1].get("msg") or "")
            key = remark.split(".")[0][:80] if remark else "unknown"
            reasons[key] += 1
        except Exception:  # noqa: BLE001
            reasons["detail-unavailable"] += 1

    total_sub = sum(r["submitted_qty"] for r in rows)
    total_fill = sum(r["filled_qty"] for r in rows)
    weighted_slip = None
    num = den = 0.0
    for r in rows:
        if r["slippage_vs_limit"] is not None and r["filled_qty"]:
            num += r["slippage_vs_limit"] * r["filled_qty"]
            den += r["filled_qty"]
    if den:
        weighted_slip = num / den

    market_summary: dict[str, dict] = {}
    for market in sorted({r["market"] for r in rows}):
        sub = [r for r in rows if r["market"] == market]
        m_sub = sum(r["submitted_qty"] for r in sub)
        m_fill = sum(r["filled_qty"] for r in sub)
        num_m = den_m = 0.0
        for r in sub:
            if r["slippage_vs_limit"] is not None and r["filled_qty"]:
                num_m += r["slippage_vs_limit"] * r["filled_qty"]
                den_m += r["filled_qty"]
        market_summary[market] = {
            "orders": sum(r["orders"] for r in sub),
            "symbols": len(sub),
            "fill_rate": round(m_fill / m_sub, 4) if m_sub else 0.0,
            "weighted_slippage_vs_limit": round(num_m / den_m, 6) if den_m else None,
        }

    statuses: dict[str, int] = defaultdict(int)
    for o in orders:
        statuses[o.get("status", "?")] += 1

    summary = {
        "date": args.date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "orders": len(orders),
        "symbols": len(rows),
        "submitted_qty": total_sub,
        "filled_qty": total_fill,
        "fill_rate": round(total_fill / total_sub, 4) if total_sub else 0.0,
        "weighted_slippage_vs_limit": round(weighted_slip, 6) if weighted_slip is not None else None,
        "statuses": dict(statuses),
        "reject_reasons": dict(reasons),
        "markets": market_summary,
    }

    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / f"broker_daily_{args.date}.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    lines = [
        f"# 券商模拟盘日度对照 · {args.date}", "",
        f"- 订单 {summary['orders']} 笔 / 标的 {summary['symbols']} 个",
        f"- 下单量 {total_sub:.0f}，成交量 {total_fill:.0f}，**成交率 {summary['fill_rate']:.1%}**",
        f"- 加权滑点（成交价 vs 限价）："
        f"{'—' if weighted_slip is None else f'{weighted_slip * 100:+.3f}%'}",
        f"- 状态分布：{dict(statuses)}",
        f"- 拒单原因：{dict(reasons)}",
        "- 按市场：" + "；".join(
            f"{m} 成交率 {v['fill_rate']:.0%} 滑点 "
            f"{'—' if v['weighted_slippage_vs_limit'] is None else f"{v['weighted_slippage_vs_limit'] * 100:+.3f}%"}"
            for m, v in market_summary.items()), "",
        "| 标的 | 订单 | 下单量 | 成交量 | 成交率 | 成交均价 | 限价均价 | 滑点 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows[:40]:
        slip_txt = "—" if r["slippage_vs_limit"] is None else f"{r['slippage_vs_limit'] * 100:+.3f}%"
        lines.append(
            f"| {r['symbol']} | {r['orders']} | {r['submitted_qty']:.0f} | {r['filled_qty']:.0f} | "
            f"{r['fill_rate']:.0%} | {r['avg_fill_price'] or '—'} | {r['avg_limit_price'] or '—'} | {slip_txt} |"
        )
    md = "\n".join(lines) + "\n"
    (reports / f"broker_daily_{args.date}.md").write_text(md, encoding="utf-8")

    print(md[:2000])
    print(f"[report] -> reports/broker_daily_{args.date}.json / .md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
