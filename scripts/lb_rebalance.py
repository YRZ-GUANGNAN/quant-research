"""按目标权重再平衡券商模拟盘（补仓/减仓，而不只是买新标的）。

与 lb_order_map 的区别：
- lb_order_map：只买目标里「尚未持有」的标的（跳过持仓）；
- 本脚本：对每只目标计算 目标市值 = 预算 × 权重，与当前持仓市值比较，
  低于 85% 就买、高于 115% 就卖，买入总额受可用现金约束。

用法：
    python scripts/lb_rebalance.py --market us --dry-run
    python scripts/lb_rebalance.py --market us --execute
"""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from lb_order_map import (cli_json, market_sessions, net_assets_usd, quotes,
                          sleeve_targets, USD_HKD)  # noqa

WEIGHT = {"us": 0.75, "hk": 0.25}


def positions_map() -> dict[str, float]:
    out = {}
    for p in cli_json(["positions"]) or []:
        if isinstance(p, dict) and p.get("symbol"):
            out[p["symbol"]] = float(p.get("quantity") or 0)
    return out


def cash_of(ccy: str) -> float:
    for a in cli_json(["assets"]) or []:
        for c in a.get("cash_infos") or []:
            if c.get("currency") == ccy:
                return float(c.get("available_cash") or 0)
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["us", "hk"], required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.15)
    args = ap.parse_args()

    sess = market_sessions()
    mkt = "US" if args.market == "us" else "HK"
    if sess.get(mkt) not in (None, "Trading"):
        print(f"[skip] {mkt} 当前 {sess.get(mkt)}，跳过再平衡（避免休市拒单）")
        return 0
    total = net_assets_usd()
    budget = total * WEIGHT[args.market] * (USD_HKD if args.market == "hk" else 1.0)
    suffix = ".US" if args.market == "us" else ".HK"
    topk = 20 if args.market == "us" else 15
    weights = sleeve_targets(args.market, topk, suffix)
    q = quotes(list(weights))
    pos = positions_map()
    pending_symbols: set[str] = set()
    try:
        for o in cli_json(["order"]) or []:
            if isinstance(o, dict) and o.get("status") in ("New", "NotReported", "Submitted", "PartFilled"):
                pending_symbols.add(o.get("symbol"))
    except Exception:
        pass
    cash = cash_of("USD" if args.market == "us" else "HKD")
    print(f"[rebalance] {args.market} 预算 {budget:,.0f}，可用现金 {cash:,.0f}，持仓 {len(pos)} 只")

    plan = []
    for sym, w in weights.items():
        if sym in pending_symbols:
            continue
        info = q.get(sym)
        if not info or not info.get("last"):
            continue
        price = float(info["last"])
        target = budget * w
        cur = pos.get(sym, 0) * price
        if cur > target * (1 + args.threshold) and pos.get(sym, 0) > 0:
            sell_qty = int((cur - target) / price)
            if sell_qty > 0:
                plan.append({"symbol": sym, "side": "sell", "qty": sell_qty, "price": price})
        elif cur < target * (1 - args.threshold):
            buy_qty = int((target - cur) / price)
            if buy_qty > 0:
                plan.append({"symbol": sym, "side": "buy", "qty": buy_qty, "price": price})

    # 卖出优先（释放现金），买入受现金约束
    sells = [p for p in plan if p["side"] == "sell"]
    buys = [p for p in plan if p["side"] == "buy"]
    gross_sell = sum(p["qty"] * p["price"] for p in sells)
    # 卖单可能未成交：不把预计卖出金额当购买力（安全优先）
    cap = cash
    kept, used = [], 0.0
    for p in sorted(buys, key=lambda x: -x["qty"] * x["price"]):
        v = p["qty"] * p["price"]
        if used + v > cap:
            continue
        kept.append(p); used += v
    plan = sells + kept
    print(f"[plan] 卖 {len(sells)} 笔（约 {gross_sell:,.0f}），买 {len(kept)} 笔（约 {used:,.0f}，上限 {cap:,.0f}）")
    for p in plan[:25]:
        print(f"  {p['side']:<4}{p['symbol']:<10}{p['qty']:>8} @ {p['price']:.3f}")
    if not args.execute:
        print("[plan] dry-run；加 --execute 执行")
        return 0
    results = []
    for p in plan:
        try:
            prev = cli_json(["order", p["side"], p["symbol"], str(p["qty"]), "--price", f"{p['price']:.3f}"])
            code = prev.get("confirmation_code")
            if not code:
                results.append({"symbol": p["symbol"], "status": "no-code"}); continue
            r = cli_json(["order", p["side"], p["symbol"], str(p["qty"]),
                          "--price", f"{p['price']:.3f}", "--execute", code])
            results.append({"symbol": p["symbol"], "side": p["side"], "status": "submitted",
                            "order_id": r.get("order_id")})
        except Exception as exc:
            results.append({"symbol": p["symbol"], "side": p["side"], "status": f"error: {str(exc)[:70]}"})
    print(json.dumps(results, ensure_ascii=False, indent=2)[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
