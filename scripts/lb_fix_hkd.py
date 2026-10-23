"""把港币负现金补正：卖出 HK 持仓直到 HKD 可用现金 >= 阈值。

背景：Longbridge OpenAPI 没有换汇接口，港股买入导致 HKD 负现金（保证金透支），
后续订单报 Insufficient Credit Limit。本脚本提供「卖出补正」的兜底方案。

用法：
    python scripts/lb_fix_hkd.py --dry-run      # 只看计划（默认）
    python scripts/lb_fix_hkd.py --execute      # 真正卖出
    python scripts/lb_fix_hkd.py --execute --target-buffer 0.05
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys
from pathlib import Path

LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"


def cli(args, timeout=40):
    p = subprocess.run([str(LB), *args, "--format", "json"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    out = (p.stdout or "").strip()
    if p.returncode != 0:
        raise RuntimeError((p.stderr or out).strip()[:200])
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        s, e = out.find("{"), out.rfind("}")
        return json.loads(out[s:e + 1]) if s >= 0 else {}


def hkd_cash() -> float:
    for a in cli(["assets"]) or []:
        for c in a.get("cash_infos") or []:
            if c.get("currency") == "HKD":
                return float(c.get("available_cash") or 0)
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="真正卖出（默认 dry-run）")
    ap.add_argument("--target-buffer", type=float, default=0.05)
    args = ap.parse_args()

    cash = hkd_cash()
    print(f"[hkd] 当前可用现金 {cash:,.2f} HKD")
    if cash >= 0:
        print("[hkd] 已为正，无需补正")
        return 0

    pos = [p for p in (cli(["positions"]) or []) if str(p.get("symbol", "")).endswith(".HK")]
    quotes = {}
    syms = [p["symbol"] for p in pos]
    for i in range(0, len(syms), 20):
        for q in cli(["quote", *syms[i:i + 20]]) or []:
            quotes[q["symbol"]] = float(q.get("last") or 0)
    plan = []
    for p in pos:
        sym = p["symbol"]
        qty = int(float(p["quantity"]))
        price = quotes.get(sym, 0) * 0.99
        if price <= 0 or qty <= 0:
            continue
        plan.append({"symbol": sym, "qty": qty, "price": round(price, 3),
                     "value": qty * price})
    plan.sort(key=lambda x: -x["value"])
    need = -cash * (1 + args.target_buffer)
    print(f"[hkd] 需要补正约 {need:,.0f} HKD（含 {args.target_buffer:.0%} 缓冲）")

    done = []
    for item in plan:
        if need <= 0:
            break
        if item["value"] > need + item["value"] * 0.2:
            # 只卖一部分：按需要量估算股数（按 100 股整手向下取整）
            lots = int((need / item["price"]) // 100) * 100
            if lots <= 0:
                lots = 100
            qty = min(item["qty"], lots)
        else:
            qty = item["qty"]
        need -= qty * item["price"]
        done.append({**item, "qty": qty})

    print(f"[plan] 卖出 {len(done)} 笔：")
    for d in done:
        print(f"  sell {d['symbol']:<10}{d['qty']:>8} @ {d['price']:<9} 约 {d['qty']*d['price']:,.0f} HKD")
    if not args.execute:
        print("[plan] dry-run，未下单；加 --execute 执行")
        return 0

    results = []
    for d in done:
        try:
            prev = cli(["order", "sell", d["symbol"], str(d["qty"]), "--price", f"{d['price']:.3f}"])
            code = prev.get("confirmation_code")
            if not code:
                results.append({"symbol": d["symbol"], "status": "no-code"}); continue
            resp = cli(["order", "sell", d["symbol"], str(d["qty"]), "--price", f"{d['price']:.3f}",
                        "--execute", code])
            results.append({"symbol": d["symbol"], "status": "submitted",
                            "order_id": resp.get("order_id")})
        except Exception as exc:
            results.append({"symbol": d["symbol"], "status": f"error: {str(exc)[:80]}"})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"[hkd] 完成后 HKD 可用预计约 {hkd_cash():,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
