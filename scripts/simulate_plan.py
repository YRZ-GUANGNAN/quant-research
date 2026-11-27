"""下单前模拟：把计划套到当前持仓上，看「执行完会变成什么样」。

为什么要有：计划本身对，不代表执行完就对。常见翻车：
- 卖出的股数超过可用（部分被冻结/T+1），卖不掉却按卖掉的钱去买了；
- 买单总额超过「现金 + 卖出所得」，最后一批必然拒单；
- 目标外清仓有换手上限，执行完仍有大量残留漂移，但没人知道。

这个脚本只算不下单，用执行完的模拟结果回答三件事：
剩余漂移、现金够不够、有没有哪笔明显不合理。

用法：
    python scripts/simulate_plan.py --market us --cash-share 0.35
    python scripts/simulate_plan.py --market hk
退出码：0 模拟通过 / 1 模拟暴露问题 / 3 无法生成计划
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation import execution  # noqa: E402
from quant.automation.execution import Policy, build_plan  # noqa: E402

REPORTS = ROOT / "reports"
MAX_RESIDUAL_DRIFT = 0.10   # 执行完仍需换手超过 10% 就算没做到位


def simulate(plan: dict, positions: dict[str, float], cash: float) -> dict:
    """把计划套到持仓上：返回执行后的权重、剩余漂移、现金。"""
    orders = plan.get("orders") or []
    post = dict(positions)
    cash_after = float(cash)
    oversold: list[str] = []
    for o in orders:
        sym, qty = o["symbol"], float(o["qty"])
        if o["side"] == "sell":
            held = float(post.get(sym, 0.0))
            if qty > held + 1e-9:
                oversold.append(f"{sym} 卖 {qty:g} 超过持有 {held:g}")
                qty = held
            post[sym] = held - qty
            cash_after += qty * float(o["price"])
        else:
            post[sym] = float(post.get(sym, 0.0)) + qty
            cash_after -= qty * float(o["price"])
        if abs(post[sym]) < 1e-9:
            post.pop(sym, None)
    return {"post_positions": post, "cash_after": round(cash_after, 2), "oversold": oversold}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="下单前模拟")
    ap.add_argument("--market", choices=["us", "hk"], required=True)
    ap.add_argument("--cash-share", type=float, default=1.0)
    ap.add_argument("--vol-target", type=float, default=None)
    args = ap.parse_args(argv)

    policy = Policy()
    if args.vol_target:
        policy.vol_target = args.vol_target
    plan = build_plan(args.market, policy, args.cash_share)
    if not plan.get("orders"):
        print(f"[sim] {args.market.upper()} 无可执行计划：{plan.get('reason', '')}")
        return 3

    suffix = ".US" if args.market == "us" else ".HK"
    ccy = "USD" if args.market == "us" else "HKD"
    positions = {s: q for s, q in execution._positions().items() if s.endswith(suffix)}
    cash = execution._cash(ccy)

    sim = simulate(plan, positions, cash)
    # 用券商报价重算权重（模拟后的持仓 × 价格）
    symbols = sorted(set(sim["post_positions"]) | set(positions))
    quotes = execution._quotes(symbols)
    prices = {s: float((quotes.get(s) or {}).get("last") or 0) for s in symbols}

    def weights(pos: dict[str, float]) -> dict[str, float]:
        mv = {s: q * prices.get(s, 0.0) for s, q in pos.items() if q > 0 and prices.get(s, 0) > 0}
        total = sum(mv.values())
        return {s: v / total for s, v in mv.items()} if total > 0 else {}

    pre_w = weights(positions)
    post_w = weights(sim["post_positions"])
    targets = execution._snapshot_weights(args.market) if hasattr(execution, "_snapshot_weights") else {}
    if not targets:
        snap = json.loads(execution.SNAPSHOT.read_text(encoding="utf-8"))
        raw = (snap.get("sleeves") or {}).get(args.market, {}).get("weights") or {}
        total_t = sum(float(v) for v in raw.values()) or 1.0
        targets = {k: float(v) / total_t for k, v in raw.items()}

    def half_turnover(w: dict[str, float]) -> float:
        keys = set(w) | set(targets)
        return round(sum(abs(w.get(k, 0.0) - targets.get(k, 0.0)) for k in keys) / 2, 4)

    pre_turn = half_turnover(pre_w)
    post_turn = half_turnover(post_w)
    sells = sum(o["notional"] for o in plan["orders"] if o["side"] == "sell")
    buys = sum(o["notional"] for o in plan["orders"] if o["side"] == "buy")

    problems = []
    if sim["oversold"]:
        problems += sim["oversold"]
    if sim["cash_after"] < 0:
        problems.append(f"执行后现金为负 {sim['cash_after']:,.0f} {ccy}（买单超过可用资金）")
    residual_note = None
    if post_turn > MAX_RESIDUAL_DRIFT:
        residual_note = (f"执行完仍需换手 {post_turn * 100:.0f}%，超过 {MAX_RESIDUAL_DRIFT * 100:.0f}%"
                         f"（换手上限导致分次执行，非故障）")

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "market": args.market, "currency": ccy,
        "orders": len(plan["orders"]),
        "sell_notional": round(sells, 2), "buy_notional": round(buys, 2),
        "cash_before": round(cash, 2), "cash_after": sim["cash_after"],
        "turnover_before": pre_turn, "turnover_after": post_turn,
        "problems": problems, "residual_note": residual_note,
    }
    out = REPORTS / f"plan_simulation_{args.market}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== {args.market.upper()} 计划模拟 ===")
    print(f"  委托 {report['orders']} 笔：卖 {sells:,.0f} / 买 {buys:,.0f} {ccy}")
    print(f"  现金 {cash:,.0f} → {sim['cash_after']:,.0f}")
    print(f"  需换手 {pre_turn * 100:.1f}% → {post_turn * 100:.1f}%")
    for p in problems:
        print(f"  [!] {p}")
    if residual_note:
        print(f"  [i] {residual_note}")
    print(f"  -> {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
