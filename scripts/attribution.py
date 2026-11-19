"""业绩归因报告：按市场拆解盈亏、集中度与胜率。

用法：python scripts/attribution.py [--market all|us|hk|cn]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation.attribution import (concentration, position_attribution, summarize)

LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"


def _cli(args: list[str]) -> object:
    p = subprocess.run([str(LB), *args, "--format", "json"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    out = (p.stdout or "").strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        s, e = out.find("{"), out.rfind("}")
        return json.loads(out[s:e + 1]) if s >= 0 else {}


def load_lb(market: str):
    suffix = ".US" if market == "us" else ".HK"
    rows = [p for p in (_cli(["positions"]) or []) if isinstance(p, dict)
            and str(p.get("symbol", "")).endswith(suffix)]
    syms = [p["symbol"] for p in rows]
    quotes = {}
    for i in range(0, len(syms), 20):
        for q in _cli(["quote", *syms[i:i + 20]]) or []:
            if isinstance(q, dict) and q.get("symbol"):
                quotes[q["symbol"]] = q
    positions = []
    for p in rows:
        sym = p["symbol"]
        positions.append({
            "symbol": sym,
            "qty": float(p.get("quantity") or 0),
            "cost": float(p.get("cost_price") or 0),
            "last": float((quotes.get(sym) or {}).get("last") or p.get("cost_price") or 0),
        })
    cash = 0.0
    for a in _cli(["assets"]) or []:
        for c in a.get("cash_infos") or []:
            if c.get("currency") == ("USD" if market == "us" else "HKD"):
                cash = float(c.get("available_cash") or 0)
    return positions, cash


def load_cn():
    from quant.broker import cn_broker

    try:
        rows = cn_broker._read_positions_via_clipboard()
    except Exception:  # noqa: BLE001
        rows = []
    positions = []
    for r in rows:
        positions.append({
            "symbol": str(r.get("证券代码")),
            "qty": float(r.get("股票余额") or 0),
            "cost": float(r.get("成本价") or 0),
            "last": float(r.get("市价") or 0),
        })
    cash = 0.0
    try:
        bal = cn_broker._read_balance_via_gui()
        cash = float(bal.get("可用金额") or 0)
    except Exception:  # noqa: BLE001
        pass
    return positions, cash


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="all", choices=["us", "hk", "cn", "all"])
    args = ap.parse_args()
    markets = ["us", "hk", "cn"] if args.market == "all" else [args.market]

    report = {}
    for m in markets:
        positions, cash = load_cn() if m == "cn" else load_lb(m)
        if not positions:
            print(f"[attr] {m}: 无持仓，跳过")
            continue
        mv = sum(p["qty"] * p["last"] for p in positions)
        equity = mv + cash
        cs = position_attribution(positions, equity=equity)
        s = summarize(cs)
        conc = concentration(positions, equity=equity)
        print(f"\n[{m}] 持仓 {len(positions)} 只 | 市值 {mv:,.0f} | 现金 {cash:,.0f} | "
              f"未实现盈亏 {s['total_pnl']:+,.0f} | 胜率 {s['hit_rate']:.0%}")
        print(f"  集中度：top1 {conc['top1']:.1%} / top5 {conc['top5']:.1%} / HHI {conc['hhi']:.3f}")
        print("  贡献最大：" + ", ".join(f"{x['symbol']} {x['pnl']:+,.0f}" for x in s["best"][:3]))
        print("  拖累最大：" + ", ".join(f"{x['symbol']} {x['pnl']:+,.0f}" for x in s["worst"][:3]))
        report[m] = {"equity": round(equity, 2), "market_value": round(mv, 2), "cash": round(cash, 2),
                     "summary": s, "concentration": conc,
                     "positions": [c.__dict__ for c in cs]}

    out = ROOT / "reports" / f"attribution_{date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[attr] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
