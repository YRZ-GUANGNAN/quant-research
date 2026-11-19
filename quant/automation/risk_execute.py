"""风险动作执行器：把 risk_guard 的 Action 变成真实订单（受控）。

支持两类通道：
- lb：美股/港股（Longbridge CLI，policy + ledger）
- cn：A 股（同花顺 xiadan.exe，真实资金，默认关闭）

设计：
- 默认 dry-run；真下单需 RISK_ALLOW_EXECUTION=1（A 股另需 CN_ALLOW_EXECUTION=1）
- trim/exit → 卖出；replace → 卖出后买入候选；de_risk → 按比例降仓
- 所有订单受单笔/单轮/单日限额与「可卖数量」约束
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"


@dataclass
class OrderPlan:
    symbol: str
    side: str          # buy / sell
    qty: float
    price: float
    reason: str
    replace_with: str = ""


@dataclass
class Limits:
    max_orders: int = 20
    max_notional: float = 100_000.0
    max_sell_fraction: float = 1.0     # 单次最多卖出比例（防误清仓）


def to_orders(actions, positions: dict[str, dict], quotes: dict[str, dict],
              limits: Limits | None = None) -> list[OrderPlan]:
    """把风险动作转成订单（纯函数，便于测试）。"""
    limits = limits or Limits()
    orders: list[OrderPlan] = []
    for a in actions:
        if len(orders) >= limits.max_orders:
            break
        pos = positions.get(a.symbol)
        q = quotes.get(a.symbol) or {}
        price = float(q.get("last") or (pos or {}).get("last") or 0)
        if price <= 0 or not pos:
            continue
        qty_have = float(pos.get("qty") or 0)
        if qty_have <= 0:
            continue
        if a.action == "exit":
            qty = qty_have * limits.max_sell_fraction
            orders.append(OrderPlan(a.symbol, "sell", qty, price, a.reason, a.replace_with))
            if a.replace_with:
                rq = quotes.get(a.replace_with) or {}
                rprice = float(rq.get("last") or 0)
                if rprice > 0:
                    orders.append(OrderPlan(a.replace_with, "buy", round(qty * price / rprice, 0),
                                            rprice, f"替换 {a.symbol}", ""))
        elif a.action == "trim":
            target = float(a.target_qty or 0)
            qty = max(0.0, qty_have - target)
            if qty > 0:
                orders.append(OrderPlan(a.symbol, "sell", qty, price, a.reason))
        elif a.action == "de_risk":
            factor = float(a.target_qty or 0.7)
            qty = qty_have * (1 - factor)
            if qty > 0:
                orders.append(OrderPlan(a.symbol, "sell", qty, price, a.reason))
    return orders


def _lb_submit(order: OrderPlan) -> dict:
    args = ["order", order.side, order.symbol, str(int(order.qty)), "--price", f"{order.price:.3f}"]
    prev = json.loads(subprocess.run([str(LB), *args, "--format", "json"],
                                     capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", timeout=60).stdout or "{}")
    code = prev.get("confirmation_code")
    if not code:
        return {"symbol": order.symbol, "status": "no-confirmation-code"}
    resp = json.loads(subprocess.run([str(LB), *args, "--execute", code, "--format", "json"],
                                     capture_output=True, text=True, encoding="utf-8",
                                     errors="replace", timeout=60).stdout or "{}")
    return {"symbol": order.symbol, "side": order.side,
            "status": "submitted" if resp.get("order_id") else "unknown",
            "order_id": resp.get("order_id")}


def _cn_submit(order: OrderPlan) -> dict:
    from quant.broker import cn_broker

    acct = cn_broker._ths_account()
    if order.side == "sell":
        acct.sell(order.symbol, price=order.price, amount=int(order.qty))
    else:
        acct.buy(order.symbol, price=order.price, amount=int(order.qty))
    return {"symbol": order.symbol, "side": order.side, "status": "submitted"}


def execute(actions, *, market: str, dry_run: bool, positions: dict, quotes: dict,
            limits: Limits | None = None) -> dict:
    orders = to_orders(actions, positions, quotes, limits)
    if dry_run:
        return {"dry_run": True, "orders": [o.__dict__ for o in orders]}
    if os.environ.get("RISK_ALLOW_EXECUTION") != "1":
        return {"ok": False, "reason": "缺少 RISK_ALLOW_EXECUTION=1", "orders": []}
    if market == "cn" and os.environ.get("CN_ALLOW_EXECUTION") != "1":
        return {"ok": False, "reason": "A 股需 CN_ALLOW_EXECUTION=1", "orders": []}

    submit = _cn_submit if market == "cn" else _lb_submit
    results = []
    for o in orders:
        try:
            results.append(submit(o))
        except Exception as exc:  # noqa: BLE001
            results.append({"symbol": o.symbol, "status": f"error: {str(exc)[:80]}"})
    ok = all(r.get("status") == "submitted" for r in results)
    return {"ok": ok, "results": results}
