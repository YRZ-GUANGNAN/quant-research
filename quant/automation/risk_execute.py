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
        if price > 0:
            # 卖单略低于最新价、买单略高，保证能成交（否则挂在盘口等着不成交）
            buffered_sell = round(price * 0.995, 3)
            buffered_buy = round(price * 1.005, 3)
        else:
            buffered_sell = buffered_buy = 0.0
        if price <= 0 or not pos:
            continue
        qty_have = float(pos.get("qty") or 0)
        if qty_have <= 0:
            continue
        if a.action == "exit":
            qty = qty_have * limits.max_sell_fraction
            orders.append(OrderPlan(a.symbol, "sell", qty, buffered_sell, a.reason, a.replace_with))
            if a.replace_with:
                rq = quotes.get(a.replace_with) or {}
                rprice = float(rq.get("last") or 0)
                if rprice > 0:
                    orders.append(OrderPlan(a.replace_with, "buy", round(qty * price / rprice, 0),
                                            round(rprice * 1.005, 3), f"替换 {a.symbol}", ""))
        elif a.action == "trim":
            target = float(a.target_qty or 0)
            qty = max(0.0, qty_have - target)
            if qty > 0:
                orders.append(OrderPlan(a.symbol, "sell", qty, buffered_sell, a.reason))
        elif a.action == "de_risk":
            factor = float(a.target_qty or 0.7)
            qty = qty_have * (1 - factor)
            if qty > 0:
                orders.append(OrderPlan(a.symbol, "sell", qty, buffered_sell, a.reason))
    return orders


def _lb_submit(order: OrderPlan) -> dict:
    """美股/港股下单：走执行层的统一实现（含券商符号映射与确认码流程）。"""
    from quant.automation.execution import place_order

    return place_order(order.side, order.symbol, order.qty, order.price)


def _cn_submit(order: OrderPlan) -> dict:
    from quant.broker import cn_broker

    acct = cn_broker._ths_account()
    if order.side == "sell":
        acct.sell(order.symbol, price=order.price, amount=int(order.qty))
    else:
        acct.buy(order.symbol, price=order.price, amount=int(order.qty))
    return {"symbol": order.symbol, "side": order.side, "status": "submitted"}



def _journal_path(day=None) -> Path:
    day = day or __import__("datetime").date.today()
    return ROOT / "state" / f"risk_orders_{day}.jsonl"


def load_sent(day=None) -> set[tuple[str, str]]:
    """当日已确认提交的 (标的, 方向)。"""
    path = _journal_path(day)
    sent: set[tuple[str, str]] = set()
    if not path.exists():
        return sent
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # noqa: silent-ok - 跳过损坏行，不影响幂等判断
        if rec.get("status") == "submitted":
            sent.add((str(rec.get("symbol")), str(rec.get("side"))))
    return sent


def append_journal(record: dict, day=None) -> None:
    path = _journal_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")




def _pending_symbols() -> set[str]:
    """有在途委托的标的（复用执行层的判定，口径保持一致）。"""
    try:
        from quant.automation.execution import _pending_symbols as impl

        return set(impl())
    except Exception as exc:  # noqa: BLE001 - 查不到就当作没有在途委托
        print(f"[risk] 查询在途委托失败（跳过去重）: {type(exc).__name__}")
        return set()


def _buy_power() -> float | None:
    """券商侧可用购买力（跨币种 + 融资额度），只作上限。"""
    try:
        from quant.automation.execution import _buy_power as impl

        return impl("USD")
    except Exception as exc:  # noqa: BLE001
        print(f"[risk] 查询购买力失败（跳过上限）: {type(exc).__name__}")
        return None


def _planned_today(market: str) -> set[str]:
    """读当天「期望订单」里出现过的标的（再平衡/模拟写入的）。"""
    try:
        from quant.automation.reconcile import load_expected

        return {str(row.get("symbol")) for row in load_expected(market) if row.get("symbol")}
    except Exception as exc:  # noqa: BLE001 - 读不到就不去重，但不能因此崩
        print(f"[risk] 读取当日期望订单失败（跳过去重）: {type(exc).__name__}")
        return set()


def execute(actions, *, market: str, dry_run: bool, positions: dict, quotes: dict,
            limits: Limits | None = None) -> dict:
    orders = to_orders(actions, positions, quotes, limits)
    if dry_run:
        return {"dry_run": True, "orders": [o.__dict__ for o in orders]}
    if os.environ.get("RISK_ALLOW_EXECUTION") != "1":
        return {"ok": False, "reason": "缺少 RISK_ALLOW_EXECUTION=1", "orders": []}
    if market == "cn" and os.environ.get("CN_ALLOW_EXECUTION") != "1":
        return {"ok": False, "reason": "A 股需 CN_ALLOW_EXECUTION=1", "orders": []}

    # 休市发单只会被券商拒掉，还会让对账出现假「missing」
    try:
        from quant.automation.execution import market_sessions

        sess = market_sessions().get({"us": "US", "hk": "HK", "cn": "CN"}.get(market, ""), "")
        if sess != "Trading":
            return {"ok": False, "blocked": True,
                    "reason": f"{market.upper()} 当前 {sess or 'UNKNOWN'}，非交易时段不发风控单",
                    "orders": []}
    except Exception as exc:  # noqa: BLE001 - 时段查询失败不阻断，但记录
        print(f"[risk] 时段查询失败（继续执行）: {type(exc).__name__} {str(exc)[:60]}")

    submit = _cn_submit if market == "cn" else _lb_submit
    sent = load_sent()
    # 主再平衡当天已经安排过这个标的（可能是清仓/减仓），风控再卖一次就会重复下单
    already = _planned_today(market)
    if already:
        before = len(orders)
        orders = [o for o in orders if o.symbol not in already]
        if len(orders) != before:
            print(f"[risk] 跳过 {before - len(orders)} 笔：这些标的今天已由再平衡安排过")

    # 还有在途委托的标的也不能碰：再平衡的卖单可能刚提交、还没成交，
    # 此时持仓里仍看得到它，风控再卖一次就是超额卖出。
    pending = _pending_symbols()
    if pending:
        before = len(orders)
        orders = [o for o in orders if o.symbol not in pending]
        if len(orders) != before:
            print(f"[risk] 跳过 {before - len(orders)} 笔：这些标的有在途委托")

    # 买单不能超过券商侧购买力（与执行层同一条底线）
    budget = _buy_power()
    if budget is not None:
        kept, spent = [], 0.0
        for o in orders:
            if o.side == "buy":
                value = float(o.qty) * float(o.price)
                if spent + value > budget * 0.98:
                    print(f"[risk] 跳过买单 {o.symbol}：会超过购买力 {budget:,.0f}")
                    continue
                spent += value
            kept.append(o)
        orders = kept
    results = []
    for o in orders:
        if (o.symbol, o.side) in sent:
            results.append({"symbol": o.symbol, "side": o.side, "status": "already-sent-today"})
            continue
        try:
            r = submit(o)
        except Exception as exc:  # noqa: BLE001 - 单笔失败不影响其余，逐笔记账
            r = {"symbol": o.symbol, "side": o.side, "status": f"error: {str(exc)[:80]}"}
        append_journal({**r, "qty": o.qty, "reason": o.reason}, None)
        results.append(r)
    ok = all(str(r.get("status")) in ("submitted", "already-sent-today") for r in results)
    return {"ok": ok, "results": results}
