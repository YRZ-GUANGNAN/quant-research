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

import sys

from quant.broker.legacy_guard import reject_execute_argv
import argparse, json, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from lb_order_map import (cli_json, lot_sizes, market_sessions, net_assets_usd,
                          quotes, sleeve_targets, USD_HKD)  # noqa

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



def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _rc = reject_execute_argv(argv)
    if _rc is not None:
        return _rc
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["us", "hk"], required=True)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.15)
    args = ap.parse_args(argv)
    # 代码级护栏：执行真实下单前必须显式声明这是模拟账户（防止登录账号被换成实盘）
    if args.execute:
        if os.environ.get("LB_ACCOUNT_KIND", "").lower() not in ("paper", "simulated"):
            print("[guard] 拒绝执行：请设置 LB_ACCOUNT_KIND=paper")
            return 2
        try:
            from quant.broker.lb_sdk import paper_config
            from longbridge.openapi import TradeContext
            _ctx = TradeContext(paper_config())
            try:
                _ctx.account_balance()
            finally:
                getattr(_ctx, "close", lambda: None)()
            print("[guard] 模拟盘账户校验通过")
        except Exception as exc:  # noqa: BLE001
            print(f"[guard] 模拟盘校验失败，拒绝执行: {str(exc)[:120]}")
            return 3

    sess = market_sessions()
    mkt = "US" if args.market == "us" else "HK"
    if sess.get(mkt) != "Trading":
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
            if isinstance(o, dict) and o.get("status") in ("New", "NotReported", "Submitted", "PartFilled", "PartialFilled", "WaitingSubmit", "Filling"):
                pending_symbols.add(o.get("symbol"))
    except Exception:
        pass
    cash = cash_of("USD" if args.market == "us" else "HKD")
    print(f"[rebalance] {args.market} 预算 {budget:,.0f}，可用现金 {cash:,.0f}，持仓 {len(pos)} 只")

    HK_TICKS = [(0.25, 0.001), (0.5, 0.005), (10, 0.01), (20, 0.02), (100, 0.05),
                (200, 0.1), (500, 0.2), (1000, 0.5), (2000, 1), (5000, 2), (float("inf"), 5)]

    def hk_tick(price: float) -> float:
        for upper, tick in HK_TICKS:
            if price < upper:
                return tick
        return 5.0

    _lots = lot_sizes([x for x in list(weights) if x.endswith(".HK")]) if any(
        x.endswith(".HK") for x in weights) else {}

    def round_qty(sym: str, qty: float) -> int:
        if sym.endswith(".HK"):
            lot = max(1, int(_lots.get(sym, 100)))
            return int(qty // lot * lot)
        return int(qty)

    plan = []
    # 目标外持仓：全部卖出（防止旧仓位残留）
    for sym, qty in pos.items():
        if sym.endswith(suffix) and sym not in weights and qty > 0 and sym not in pending_symbols:
            info = q.get(sym) or (quotes([sym]) or {}).get(sym)
            if info and info.get("last"):
                last = float(info["last"])
                price = round(last - hk_tick(last), 3) if sym.endswith(".HK") else round(last, 2)
                plan.append({"symbol": sym, "side": "sell", "qty": int(qty), "price": price,
                             "orphan": True})

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
            sell_qty = round_qty(sym, (cur - target) / price)
            if sell_qty > 0:
                plan.append({"symbol": sym, "side": "sell", "qty": sell_qty, "price": price})
        elif cur < target * (1 - args.threshold):
            buy_qty = round_qty(sym, (target - cur) / price)
            if buy_qty > 0:
                bprice = round(price + hk_tick(price), 3) if sym.endswith(".HK") else round(price, 2)
                plan.append({"symbol": sym, "side": "buy", "qty": buy_qty, "price": bprice})

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
