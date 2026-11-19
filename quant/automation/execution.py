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
LEDGER_ROOT = ROOT / "state" / "ledger"


@dataclass
class Policy:
    """订单硬限额（全部默认保守；命令行/配置只能收紧）。"""
    max_orders_per_run: int = 20
    max_orders_per_day: int = 40
    max_notional_per_order_usd: float = 20_000.0
    max_notional_per_day_usd: float = 120_000.0
    min_order_notional_usd: float = 500.0
    cash_buffer: float = 0.02
    allowed_order_type: str = "LO"
    allowed_tif: str = "Day"


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


def build_plan(market: str, policy: Policy) -> dict:
    """从冻结快照生成订单计划（不提交）。任何异常 → 返回空计划原因。"""
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    sleeve = (snap.get("sleeves") or {}).get(market)
    if not sleeve or not sleeve.get("weights"):
        return {"ok": False, "reason": "快照缺失或为空", "orders": []}
    today = datetime.now(timezone.utc).date().isoformat()
    if today > sleeve.get("next_rebalance_date", "9999-12-31"):
        return {"ok": False, "reason": "快照已过期", "orders": []}

    weights = {k: float(v) for k, v in sleeve["weights"].items()}
    ccy = "USD" if market == "us" else "HKD"
    quotes = _quotes(list(weights))
    positions = _positions()
    pending = _pending_symbols()
    cash = _cash(ccy)
    budget = max(0.0, cash) * (1 - policy.cash_buffer)
    if budget <= 0:
        return {"ok": False, "reason": f"{ccy} 可用现金 {cash:,.0f} 不足", "orders": []}

    orders, notional = [], 0.0
    for sym, w in weights.items():
        if sym in positions or sym in pending:
            continue
        q = quotes.get(sym)
        if not q or not q.get("last"):
            continue
        price = float(q["last"])
        target = budget * w
        if target < policy.min_order_notional_usd:
            continue
        qty = target / price
        lot = 100 if market == "hk" else 1
        qty = int(qty // lot * lot) if market == "hk" else int(qty)
        if qty <= 0:
            continue
        px = round(price + _tick_hk(price), 3) if market == "hk" else round(price * 1.005, 2)
        value = qty * px
        if value > policy.max_notional_per_order_usd:
            continue
        if len(orders) >= policy.max_orders_per_run:
            break
        if notional + value > policy.max_notional_per_day_usd:
            break
        orders.append({"symbol": sym, "side": "buy", "qty": qty, "price": px,
                       "order_type": policy.allowed_order_type, "tif": policy.allowed_tif,
                       "notional": round(value, 2)})
        notional += value
    return {"ok": True, "market": market, "currency": ccy, "cash": cash,
            "budget": round(budget, 2), "orders": orders,
            "snapshot_rebalance_date": sleeve.get("rebalance_date"),
            "snapshot_next": sleeve.get("next_rebalance_date")}


def execute(market: str, *, dry_run: bool, policy: Policy | None = None) -> dict:
    policy = policy or Policy()
    if not dry_run:
        if os.environ.get("LB_ALLOW_EXECUTION") != "1":
            return {"ok": False, "reason": "缺少 LB_ALLOW_EXECUTION=1，拒绝提交", "orders": []}
        if os.environ.get("LB_ACCOUNT_KIND", "").lower() not in ("paper", "simulated"):
            return {"ok": False, "reason": "缺少 LB_ACCOUNT_KIND=paper，拒绝提交", "orders": []}

    sess = market_sessions()
    mkt = "US" if market == "us" else "HK"
    if sess.get(mkt) != "Trading":
        return {"ok": False, "reason": f"{mkt} 当前 {sess.get(mkt, 'UNKNOWN')}，非交易时段不提交", "orders": []}

    plan = build_plan(market, policy)
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
        return {"ok": False, "reason": "该快照/交易日已提交过（一次消费）", "orders": []}

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
    ap.add_argument("--market", choices=["us", "hk"], required=True)
    ap.add_argument("--execute", action="store_true", help="真正提交（还需 LB_ALLOW_EXECUTION=1）")
    args = ap.parse_args(argv)

    result = execute(args.market, dry_run=not args.execute)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
    ok = result.get("ok") and not any(str(r.get("status", "")).startswith(("error", "unknown"))
                                      for r in result.get("results", []))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
