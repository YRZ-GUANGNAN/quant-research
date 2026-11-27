"""通用再平衡计划器：目标权重 + 当前持仓 → 先卖后买的委托清单（纯函数）。

为什么抽出来：A 股（cn_broker.plan_orders）和美股/港股（execution.build_plan）
各自实现了一遍，口径不断漂移——一个会卖旧票、一个不会；一个预算按账户权益算、
一个按袖套市值算，结果港股权重怎么调都回不到目标。

这里只做一件事：把「每股值多少钱」算准。
- 目标金额 = 该袖套可投资本 × 归一化目标权重
- 卖出所得当天可用于买入（同一交易日内先卖后买）
- 单票超上限要截断；碎单不动；超上限的委托拆成多笔
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Limits:
    lot_size: int = 1
    min_notional: float = 300.0        # 小于这个金额不动手（手续费不划算）
    max_order_notional: float = 50_000.0
    max_orders: int = 40
    max_position_weight: float = 1.0   # 单票权重上限（1.0=不限制）
    cash_buffer: float = 0.02
    trim_tolerance: float = 0.0        # 超目标多少才减仓（占资本比例）
    orphan_min_weight: float = 0.005   # 目标外仓位小于这个比例不动
    orphan_max_turnover: float = 0.50  # 单轮最多清掉这么多比例的资本（分次执行）
    fee_reserve: float = 0.02          # 卖出所得预留手续费/印花税/滑点，避免买完现金变负
    max_tranches: int = 5              # 一笔订单最多拆几笔；超过说明限额设置不合理，整笔跳过


def plan_rebalance(
    targets: dict[str, float],
    *,
    cash: float,
    capital: float,
    holdings: dict[str, float],
    prices: dict[str, float],
    limits: Limits | None = None,
    sent: set[tuple[str, str]] | None = None,
) -> tuple[list[dict], list[str]]:
    """返回 (委托清单, 说明)。

    targets:  目标权重（会自动归一化到 1，描述的是组合结构而非绝对仓位）
    capital:  该袖套可投资本（持仓市值 + 可动用现金）
    holdings: {symbol: 股数}
    prices:   {symbol: 最新价}
    """
    limits = limits or Limits()
    sent = sent or set()
    lot = max(1, limits.lot_size)
    notes: list[str] = []

    w_sum = sum(v for v in targets.values() if v > 0)
    if w_sum <= 0 or capital <= 0:
        return [], ["目标权重或资本为空"]
    w = {k: v / w_sum for k, v in targets.items() if v > 0}

    def px(sym: str) -> float:
        return float(prices.get(sym) or 0.0)

    sells: list[dict] = []
    buys: list[dict] = []
    proceeds = 0.0

    # 1) 目标外持仓：卖出（碎仓不动，单轮换手有上限）
    orphan_sold = 0.0
    for sym, qty in sorted(holdings.items(), key=lambda kv: -(float(kv[1]) * px(str(kv[0])))):
        if sym in w or qty <= 0:
            continue
        p = px(sym)
        if p <= 0:
            notes.append(f"{sym} 无报价，暂不处理")
            continue
        value = qty * p
        if value < capital * limits.orphan_min_weight:
            continue
        if orphan_sold + value > capital * limits.orphan_max_turnover and sells:
            notes.append(f"{sym} 清仓顺延：本轮换手已达上限")
            continue
        orphan_sold += value
        units = int(qty) // lot * lot
        if units <= 0:
            continue
        sells.append({"symbol": sym, "side": "sell", "qty": units,
                      "notional": round(units * p, 2), "reason": "exit"})
        proceeds += units * p

    # 2) 超配减仓
    for sym, weight in w.items():
        qty = float(holdings.get(sym) or 0.0)
        p = px(sym)
        if qty <= 0 or p <= 0:
            continue
        target_value = capital * min(weight, limits.max_position_weight)
        excess = qty * p - target_value
        if excess <= capital * limits.trim_tolerance:
            continue
        units = int(excess / p) // lot * lot
        units = min(units, int(qty))
        if units <= 0 or units * p < limits.min_notional:
            continue
        sells.append({"symbol": sym, "side": "sell", "qty": units,
                      "notional": round(units * p, 2), "reason": "trim"})
        proceeds += units * p

    # 3) 加仓（卖出所得当日可用）
    available = cash + proceeds * (1 - limits.fee_reserve) - cash * limits.cash_buffer
    for sym, weight in w.items():
        p = px(sym)
        if p <= 0:
            notes.append(f"{sym} 无报价，跳过买入")
            continue
        qty = float(holdings.get(sym) or 0.0)
        target_value = capital * min(weight, limits.max_position_weight)
        gap = target_value - qty * p
        if gap <= 0:
            continue
        units = int(gap / p) // lot * lot
        if units * p > available:
            units = int(available / p) // lot * lot
        value = units * p
        if units <= 0 or value < limits.min_notional:
            continue
        available -= value
        buys.append({"symbol": sym, "side": "buy", "qty": units,
                     "notional": round(value, 2),
                     "reason": "open" if qty == 0 else "add"})

    # 4) 汇总：先卖后买 + 幂等 + 单笔上限拆单 + 笔数上限
    def tranches(order: dict) -> list[dict]:
        cap = limits.max_order_notional
        if order["notional"] <= cap:
            return [order]
        if order["notional"] / cap > limits.max_tranches:
            notes.append(f"{order['symbol']} 单笔 {order['notional']:.0f} 需拆 "
                         f"{order['notional'] / cap:.0f} 笔，超过上限 {limits.max_tranches}，整笔跳过")
            return []
        p = px(order["symbol"])
        cap_units = int(cap / p) // lot * lot
        if cap_units <= 0:
            notes.append(f"{order['symbol']} 单笔超上限且不足一手，跳过")
            return []
        out, left = [], order["qty"]
        while left > 0:
            units = min(cap_units, left) // lot * lot
            if units <= 0:
                break
            out.append({**order, "qty": units, "notional": round(units * p, 2)})
            left -= units
        return out

    # 卖出按金额从大到小排：笔数受限时先解决最大的偏离
    sells.sort(key=lambda o: -o["notional"])
    # 给买入预留笔数：否则卖单吃满 max_orders，买单一个都发不出去（组合只会越卖越空）
    reserve = min(len(buys), max(2, limits.max_orders // 4))
    sell_budget = max(0, limits.max_orders - reserve)

    plan: list[dict] = []
    for order in sells:
        if (order["symbol"], order["side"]) in sent:
            continue
        # 卖单不拆：清仓/减仓本身是一笔合法整单，拆开只会吃掉笔数配额
        for tr in [order]:
            if len([o for o in plan if o["side"] == "sell"]) >= sell_budget:
                notes.append(f"卖单达到本轮配额 {sell_budget} 笔，其余顺延")
                break
            plan.append(tr)
    for order in buys:
        if (order["symbol"], order["side"]) in sent:
            continue
        for tr in tranches(order):
            if len(plan) >= limits.max_orders:
                notes.append(f"达到单轮上限 {limits.max_orders} 笔，其余顺延")
                return plan, notes
            plan.append(tr)
    return plan, notes
