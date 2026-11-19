"""对真实持仓做风险体检（只读），输出动作清单。

用法：python scripts/risk_scan.py [--market us|hk|all]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from quant.automation.risk_guard import Position, RiskPolicy, evaluate, summarize
from quant.data import store

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


def _stats(market: str) -> dict[str, dict]:
    """从本地行情算 20 日波动与均成交额。"""
    try:
        bars = store.load_bars(market)
    except Exception:
        return {}
    out = {}
    for code, g in bars.groupby("code"):
        g = g.sort_values("date").tail(20)
        if len(g) < 10:
            continue
        ret = g["close"].pct_change().dropna()
        out[str(code)] = {
            "vol_20": float(ret.std()) if len(ret) > 5 else 0.0,
            "avg_turnover": float(g["amount"].mean()) if "amount" in g else 0.0,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="all", choices=["us", "hk", "all"])
    args = ap.parse_args()

    positions = _cli(["positions"]) or []
    codes = [p["symbol"] for p in positions if isinstance(p, dict)]
    quotes = {}
    for i in range(0, len(codes), 20):
        for q in _cli(["quote", *codes[i:i + 20]]) or []:
            if isinstance(q, dict) and q.get("symbol"):
                quotes[q["symbol"]] = q

    stats_us, stats_hk = _stats("us"), _stats("hk")
    cfg = RiskPolicy()
    grouped: dict[str, list[Position]] = {"us": [], "hk": []}
    for p in positions:
        sym = p["symbol"]
        suffix = sym.split(".")[-1].lower()
        if suffix not in grouped:
            continue
        last = float(quotes.get(sym, {}).get("last") or p.get("cost_price") or 0)
        code = sym.split(".")[0]
        st = (stats_us if suffix == "us" else stats_hk).get(code, {})
        grouped[suffix].append(Position(
            symbol=sym, qty=float(p.get("quantity") or 0),
            cost=float(p.get("cost_price") or 0), last=last,
            avg_turnover=st.get("avg_turnover", 0.0),
            vol_20=st.get("vol_20", 0.0),
            last_date=str(p.get("last_date") or ""),
        ))

    report = {}
    for market, plist in grouped.items():
        if args.market != "all" and args.market != market:
            continue
        if not plist:
            continue
        equity = sum(p.market_value for p in plist)
        if equity <= 0:
            continue
        # 用成本价估算组合回撤（真实净值需历史，这里用浮亏比例近似）
        cost = sum(p.qty * p.cost for p in plist)
        drawdown = (equity / cost - 1.0) if cost > 0 else 0.0
        actions = evaluate(plist, equity=equity, drawdown=drawdown, policy=cfg)
        report[market] = {
            "equity": round(equity, 2), "drawdown_est": round(drawdown, 4),
            "actions": [a.__dict__ for a in actions],
        }
        print(f"\n=== {market.upper()} ===")
        print(summarize(actions))

    out = ROOT / "reports" / "risk_scan_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[risk] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
