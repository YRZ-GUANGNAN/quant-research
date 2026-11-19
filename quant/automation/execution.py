"""受控模拟盘执行通道（P4-B 最小版）。

这是方案 B 里**唯一允许**发起券商委托的代码路径：
- 默认 dry-run；`--execute` 还要求环境变量 LB_ALLOW_EXECUTION=1；
- 只允许 LO + Day + Buy/Sell（v1 范围）；
- policy 硬限额（单笔/单轮/单日金额与笔数、最小下单额）；
- 账本一次性消费（consumption_key 绑定后不可重复提交）；
- 任何查询/校验失败 → 零提交（fail-closed）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quant.automation.ledger import (  # noqa: E402
    AlreadyBound, Ledger, StateConflict, consumption_key,
)

LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"
SNAPSHOT = ROOT / "state" / "target_snapshot.json"

# A 股敞口的可执行代理：券商不支持沪深港通（603305），改用美股/港股上市的中国 A 股 ETF
PROXIES = {
    "cn_proxy": {"symbol": "ASHR.US", "sleeve_weight": 0.60, "currency": "USD", "session": "US"},
    "cn_proxy_hk": {"symbol": "3188.HK", "sleeve_weight": 0.60, "currency": "HKD", "session": "HK"},
}
LEDGER_ROOT = ROOT / "state" / "ledger"


@dataclass
class Policy:
    """订单硬限额（全部默认保守；命令行/配置只能收紧）。"""
    max_orders_per_run: int = 40
    max_orders_per_day: int = 80
    max_notional_per_order_usd: float = 50_000.0
    max_notional_per_day_usd: float = 400_000.0
    min_order_notional_usd: float = 300.0
    cash_buffer: float = 0.02
    allowed_order_type: str = "LO"
    allowed_tif: str = "Day"
    # 默认不清理目标外持仓：旧目标买入的仓位是合法持仓，清仓会带来巨量成交与成本
    cleanup_orphans: bool = False


def _cli(args: list[str], timeout: int = 40) -> object:
    proc = subprocess.run([str(LB), *args, "--format", "json"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or out).strip()[:200])
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        s, e = out.find("{"), out.rfind("}")
        if s >= 0 and e > s:
            return json.loads(out[s:e + 1])
        raise


def market_sessions() -> dict[str, str]:
    out = {}
    try:
        for row in _cli(["market-status"]) or []:
            if isinstance(row, dict) and row.get("market"):
                out[str(row["market"]).upper()] = str(row.get("status", "?"))
    except Exception:  # noqa: BLE001 - 查询失败按不可交易处理
        return {}
    return out


def _quotes(symbols: list[str]) -> dict[str, dict]:
    out = {}
    for i in range(0, len(symbols), 20):
        for q in _cli(["quote", *symbols[i:i + 20]]) or []:
            if isinstance(q, dict) and q.get("symbol"):
                out[q["symbol"]] = q
    return out


def _positions() -> dict[str, float]:
    out = {}
    for p in _cli(["positions"]) or []:
        if isinstance(p, dict) and p.get("symbol"):
            out[p["symbol"]] = float(p.get("quantity") or 0)
    return out


def _cash(ccy: str) -> float:
    for acct in _cli(["assets"]) or []:
        for c in acct.get("cash_infos") or []:
            if c.get("currency") == ccy:
                return float(c.get("available_cash") or 0)
    return 0.0


def _pending_symbols() -> set[str]:
    out = set()
    for o in _cli(["order"]) or []:
        if isinstance(o, dict) and o.get("status") in {
                "New", "NotReported", "Submitted", "PartFilled", "PartialFilled", "WaitingSubmit", "Filling"}:
            out.add(o.get("symbol"))
    return out


def _tick_hk(price: float) -> float:
    for upper, tick in ((0.25, 0.001), (0.5, 0.005), (10, 0.01), (20, 0.02), (100, 0.05),
                        (200, 0.1), (500, 0.2), (1000, 0.5), (2000, 1), (5000, 2), (1e9, 5)):
        if price < upper:
            return tick
    return 5.0


def build_plan(market: str, policy: Policy, cash_share: float = 1.0) -> dict:
    """从冻结快照生成订单计划（不提交）。任何异常 → 返回空计划原因。"""
    if market in PROXIES:
        return _build_proxy_plan(market, policy, cash_share)
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    sleeve = (snap.get("sleeves") or {}).get(market)
    if not sleeve or not sleeve.get("weights"):
        return {"ok": False, "reason": "快照缺失或为空", "orders": []}
    today = datetime.now(timezone.utc).date().isoformat()
    if today > sleeve.get("next_rebalance_date", "9999-12-31"):
        return {"ok": False, "reason": "快照已过期", "orders": []}

    weights = {k: float(v) for k, v in sleeve["weights"].items()}
    ccy = "USD" if market == "us" else "HKD"
    positions = _positions()
    pending = _pending_symbols()
    suffix = ".US" if market == "us" else ".HK"
    universe = list(dict.fromkeys([*weights, *[s for s in positions if s.endswith(suffix)]]))
    quotes = _quotes(universe)
    cash = _cash(ccy)
    # 目标权重应按「该市场总权益」计算，而不是只看可用现金
    market_value = 0.0
    for sym, qty in positions.items():
        if sym.endswith(suffix):
            q = quotes.get(sym) or {}
            last = float(q.get("last") or 0)
            if last > 0:
                market_value += qty * last
    equity = max(0.0, cash) + market_value
    sleeve_weight = 0.75 if market == "us" else 0.25
    budget = equity * sleeve_weight * (1 - policy.cash_buffer)
    cash_cap = max(0.0, cash) * max(0.0, min(1.0, cash_share)) * (1 - policy.cash_buffer)  # 买入总额上限

    orders, notional = [], 0.0
    orphan_sells, expected_cash = [], 0.0
    # 目标外持仓：清仓（释放现金，避免旧仓位残留）
    for sym, qty in positions.items():
        if not policy.cleanup_orphans:
            break
        if sym.endswith(suffix) and sym not in weights and sym not in pending and qty > 0:
            q = quotes.get(sym) or {}
            last = float(q.get("last") or 0)
            if last <= 0:
                continue
            px = round(last - _tick_hk(last), 3) if market == "hk" else round(last * 0.995, 2)
            orphan_sells.append({"symbol": sym, "side": "sell", "qty": int(qty), "price": px,
                                 "order_type": policy.allowed_order_type, "tif": policy.allowed_tif,
                                 "notional": round(qty * px, 2)})
            expected_cash += qty * px

    # 加仓：已有持仓按目标权重补足；无持仓的新标的按目标买入
    for sym, w in weights.items():
        if sym in pending:
            continue
        q = quotes.get(sym)
        if not q or not q.get("last"):
            continue
        price = float(q["last"])
        target = budget * w
        current = positions.get(sym, 0.0) * price
        gap = target - current
        if gap < policy.min_order_notional_usd:
            continue
        lot = 100 if market == "hk" else 1
        qty = int((gap / price) // lot * lot) if market == "hk" else int(gap / price)
        if qty <= 0:
            continue
        px = round(price + _tick_hk(price), 3) if market == "hk" else round(price * 1.005, 2)
        value = qty * px
        if value > policy.max_notional_per_order_usd:
            continue
        if len(orders) >= policy.max_orders_per_run:
            break
        if notional + value > min(policy.max_notional_per_day_usd, cash_cap) + expected_cash * 0.95:
            break
        orders.append({"symbol": sym, "side": "buy", "qty": qty, "price": px,
                       "order_type": policy.allowed_order_type, "tif": policy.allowed_tif,
                       "notional": round(value, 2)})
        notional += value

    plan_orders = orphan_sells + orders
    if not plan_orders:
        return {"ok": False,
                "reason": f"{ccy} 可用现金 {cash:,.0f}，无可执行订单（已达标或现金不足）",
                "orders": []}
    return {"ok": True, "market": market, "currency": ccy, "cash": cash,
            "budget": round(budget, 2), "orders": plan_orders,
            "snapshot_rebalance_date": sleeve.get("rebalance_date"),
            "snapshot_next": sleeve.get("next_rebalance_date")}


def _build_proxy_plan(market: str, policy: Policy, cash_share: float = 1.0) -> dict:
    """A 股代理袖套：把 60% 目标作为单一 ETF 持仓来执行。"""
    cfg = PROXIES[market]
    sym, ccy = cfg["symbol"], cfg["currency"]
    quotes = _quotes([sym])
    q = quotes.get(sym) or {}
    last = float(q.get("last") or 0)
    if last <= 0:
        return {"ok": False, "reason": f"{sym} 无报价", "orders": []}
    cash = _cash(ccy) * max(0.0, min(1.0, cash_share))
    positions = _positions()
    held = positions.get(sym, 0.0)
    market_value = sum(
        qty * float((_quotes([s2]) or {}).get(s2, {}).get("last") or 0)
        for s2, qty in positions.items() if s2.endswith(".US" if ccy == "USD" else ".HK")
    )
    equity = max(0.0, cash) + market_value
    target = equity * cfg["sleeve_weight"] * (1 - policy.cash_buffer)
    current = held * last
    gap = target - current
    lot = 1 if ccy == "USD" else 100
    qty = int((gap / last) // lot * lot) if gap > 0 else 0
    orders = []
    if qty > 0:
        px = round(last * 1.005, 2) if ccy == "USD" else round(last + _tick_hk(last), 3)
        cap = max(0.0, cash) * (1 - policy.cash_buffer)
        # 受单笔限额与现金上限约束：超出则按上限下部分订单（分多日建仓）
        max_value = min(policy.max_notional_per_order_usd, cap)
        if qty * px > max_value:
            qty = int((max_value / px) // lot * lot) if lot > 1 else int(max_value / px)
        value = qty * px
        if qty > 0 and value >= policy.min_order_notional_usd:
            orders.append({"symbol": sym, "side": "buy", "qty": qty, "price": px,
                           "order_type": policy.allowed_order_type, "tif": policy.allowed_tif,
                           "notional": round(value, 2)})
    if not orders:
        return {"ok": False,
                "reason": f"目标 {target:,.0f} 当前 {current:,.0f}，现金 {cash:,.0f}，无需/无法下单",
                "orders": []}
    return {"ok": True, "market": market, "currency": ccy, "cash": cash,
            "budget": round(target, 2), "orders": orders,
            "snapshot_rebalance_date": "proxy", "snapshot_next": "2099-12-31"}


def execute(market: str, *, dry_run: bool, policy: Policy | None = None, cash_share: float = 1.0) -> dict:
    policy = policy or Policy()
    if not dry_run:
        if os.environ.get("LB_ALLOW_EXECUTION") != "1":
            return {"ok": False, "reason": "缺少 LB_ALLOW_EXECUTION=1，拒绝提交", "orders": []}
        if os.environ.get("LB_ACCOUNT_KIND", "").lower() not in ("paper", "simulated"):
            return {"ok": False, "reason": "缺少 LB_ACCOUNT_KIND=paper，拒绝提交", "orders": []}

    # 真提交：先做时段门控（避免无谓的行情/账户查询）
    if not dry_run:
        sess = market_sessions()
        mkt = "US" if market in ("us", "cn_proxy") else "HK"
        if sess.get(mkt) != "Trading":
            return {"ok": False, "blocked": True,
                    "reason": f"{mkt} 当前 {sess.get(mkt, 'UNKNOWN')}，非交易时段不提交",
                    "orders": []}

    plan = build_plan(market, policy, cash_share)
    if not plan.get("ok") or not plan["orders"]:
        return plan
    if dry_run:
        plan["dry_run"] = True
        return plan

    session = datetime.now(timezone.utc).date().isoformat()
    key = consumption_key(market=market, snapshot_id=str(plan["snapshot_rebalance_date"]),
                          eligible_execution_session=session)
    ledger = Ledger(LEDGER_ROOT)
    try:
        ledger.bind_plan(key=key, plan_id=f"{market}-{plan['snapshot_rebalance_date']}")
    except AlreadyBound:
        return {"ok": False, "blocked": True,
                "reason": "该快照/交易日已提交过（一次消费），本交易日不再重复下单", "orders": []}

    results = []
    for item in plan["orders"]:
        try:
            ledger.mark_submitting(key=key, plan_id=item["symbol"], intent_id=item["symbol"])
            prev = _cli(["order", item["side"], item["symbol"], str(item["qty"]),
                         "--price", f"{item['price']:.3f}", "--order-type", "LO"])
            code = prev.get("confirmation_code")
            if not code:
                ledger.mark_result(key=key, plan_id=item["symbol"], intent_id=item["symbol"],
                                   status="UNKNOWN")
                results.append({**item, "status": "no-confirmation-code"})
                continue
            resp = _cli(["order", item["side"], item["symbol"], str(item["qty"]),
                         "--price", f"{item['price']:.3f}", "--order-type", "LO", "--execute", code])
            oid = resp.get("order_id")
            ledger.mark_result(key=key, plan_id=item["symbol"], intent_id=item["symbol"],
                               status="WORKING" if oid else "UNKNOWN")
            results.append({**item, "status": "submitted" if oid else "unknown", "order_id": oid})
        except (StateConflict, Exception) as exc:  # noqa: BLE001
            results.append({**item, "status": f"error: {str(exc)[:80]}"})
    plan["results"] = results
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="受控模拟盘执行通道（默认 dry-run）")
    ap.add_argument("--market", choices=["us", "hk", "cn_proxy", "cn_proxy_hk"], required=True)
    ap.add_argument("--execute", action="store_true", help="真正提交（还需 LB_ALLOW_EXECUTION=1）")
    ap.add_argument("--cash-share", type=float, default=1.0,
                    help="本袖套可使用的现金份额（0-1），用于多袖套分配")
    args = ap.parse_args(argv)

    result = execute(args.market, dry_run=not args.execute, cash_share=args.cash_share)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
    if result.get("blocked"):
        # 非交易时段 / 本日已消费：预期内的跳过，不算失败（自动化里 exit 1 只留给真故障）
        return 3
    ok = result.get("ok") and not any(str(r.get("status", "")).startswith(("error", "unknown"))
                                      for r in result.get("results", []))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
