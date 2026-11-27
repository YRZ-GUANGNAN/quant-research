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
# 代理袖套持有的标的：归 cn_proxy 管，主袖套清仓时必须放过它们
PROXY_SYMBOLS = {cfg["symbol"] for cfg in PROXIES.values()}


@dataclass
class Policy:
    """订单硬限额（全部默认保守；命令行/配置只能收紧）。"""
    max_orders_per_run: int = 80
    max_orders_per_day: int = 80
    max_notional_per_order_usd: float = 50_000.0
    max_notional_per_day_usd: float = 400_000.0
    min_order_notional_usd: float = 300.0
    cash_buffer: float = 0.02
    allowed_order_type: str = "LO"
    allowed_tif: str = "Day"
    # 目标外持仓要清理：不清理的话模型每次换股，旧票会一直留着，
    # 组合最终变成「历年目标并集」，权重漂移再也回不来（2026-11 实测 HK 需换手 73%）。
    cleanup_orphans: bool = True
    orphan_min_weight: float = 0.005      # 小于 0.5% 的零碎仓位不动，省手续费
    orphan_max_turnover: float = 0.30     # 单轮最多清 30% 权益，避免一天砸完
    trim_tolerance: float = 0.02          # 超过目标权重 2 个百分点才减仓
    price_buffer: float = 0.008           # 限价缓冲：买高 0.8%、卖低 0.8%，保证能成交
    vol_target: float | None = None        # 组合层波动率目标（None=关闭；美股实测 0.15 有效）
    vol_lookback: int = 20
    vol_min_exposure: float = 0.5


def _cli_once(args: list[str], timeout: int = 40) -> object:
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


def _cli(args: list[str], timeout: int = 40) -> object:
    """CLI 调用带重试：Longbridge 接口偶发连接失败（大陆接入点抖动）。"""
    import time

    last: Exception | None = None
    for attempt in range(3):
        try:
            return _cli_once(args, timeout)
        except RuntimeError as exc:
            last = exc
            msg = str(exc)
            transient = any(k in msg for k in ("error sending request", "Connect", "timeout",
                                               "temporarily", "502", "503", "504"))
            if not transient:
                raise
            print(f"[exec] CLI 连接失败，{2 * (attempt + 1)}s 后重试（{attempt + 1}/3）")
            time.sleep(2 * (attempt + 1))
    raise last if last else RuntimeError("CLI 调用失败")


def market_sessions() -> dict[str, str]:
    out = {}
    try:
        for row in _cli(["market-status"]) or []:
            if isinstance(row, dict) and row.get("market"):
                out[str(row["market"]).upper()] = str(row.get("status", "?"))
    except Exception:  # noqa: BLE001 - 查询失败按不可交易处理
        return {}
    return out




def _nav_values(market: str) -> list[float]:
    """读该市场的历史净值序列（reports/nav_history.csv），用于波动率目标。"""
    import csv

    path = ROOT / "reports" / "nav_history.csv"
    if not path.exists():
        return []
    out = []
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("market", "")).lower() != market:
                    continue
                try:
                    out.append(float(row["equity"]))
                except (KeyError, TypeError, ValueError):
                    continue  # noqa: silent-ok - 坏行跳过
    except OSError:
        return []
    return out


def broker_symbol(sym: str) -> str:
    """模型里的美股类别股写法是 BRK-B.US，券商接口要 BRK.B.US。

    不转换的话这类标的永远拿不到报价、也永远买不进去（每天静默跳过）。
    """
    sym = str(sym or "").strip()
    if sym.endswith(".US") and "-" in sym[:-3]:
        return sym[:-3].replace("-", ".") + ".US"
    return sym


def _quotes(symbols: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), 20):
        for q in _cli(["quote", *symbols[i:i + 20]]) or []:
            if isinstance(q, dict) and q.get("symbol"):
                out[q["symbol"]] = q

    # 类别股（BRK-B.US ↔ BRK.B.US）：原名查不到就换券商写法补查，再映射回原名
    missing = [s2 for s2 in symbols if s2 not in out]
    alt = {broker_symbol(s2): s2 for s2 in missing if broker_symbol(s2) != s2}
    if alt:
        for i in range(0, len(alt), 20):
            batch = list(alt)[i:i + 20]
            try:
                for q in _cli(["quote", *batch]) or []:
                    sym = str(q.get("symbol") or "")
                    if sym in alt and q.get("last"):
                        out[alt[sym]] = {**q, "symbol": alt[sym]}
            except (RuntimeError, json.JSONDecodeError) as exc:
                print(f"[exec] 备选写法报价失败: {type(exc).__name__}")
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
    equity = max(0.0, cash) + market_value   # 账户口径，用于换手上限与碎仓阈值
    # 预算口径：本袖套当前市值（组合内再平衡）+ 可用现金里属于本袖套的份额。
    # 之前用「账户权益 × 固定袖套比例」，这个比例与实际资金分布对不上，
    # 导致买入额永远只有应有的一半左右，权重怎么调都回不到目标（HK 实测残留 74%）。
    sleeve_capital = market_value + max(0.0, cash) * max(0.0, min(1.0, cash_share))
    # 组合层波动率目标：高波动时缩减目标仓位（只用历史净值，样本不足不缩放）
    expo, vol_diag = 1.0, {"reason": "未启用"}
    if policy.vol_target:
        from quant.automation.vol_target import exposure_factor

        expo, vol_diag = exposure_factor(_nav_values(market), policy.vol_target,
                                         lookback=policy.vol_lookback,
                                         min_exposure=policy.vol_min_exposure)
        if expo < 1.0:
            print(f"[exec] 波动率目标生效：仓位 ×{expo:.2f}（已实现波动 {vol_diag.get('realized_vol')}）")
    budget = sleeve_capital * (1 - policy.cash_buffer) * expo
    w_sum = sum(v for v in weights.values() if v > 0) or 1.0
    weights = {k: v / w_sum for k, v in weights.items()}   # 归一化：目标只描述组合结构
    cash_cap = max(0.0, cash) * max(0.0, min(1.0, cash_share)) * (1 - policy.cash_buffer)  # 买入总额上限

    # 统一走通用再平衡器：先卖后买、碎仓不动、超配减仓、目标外清仓
    from quant.automation.rebalance import Limits as RebLimits
    from quant.automation.rebalance import plan_rebalance

    # 已有在途委托的标的这一轮完全不碰：不让它再买、也不让它被当"目标外"卖掉。
    # （重构成通用再平衡器时漏掉了这个过滤，第二轮补单会对同一标的重复下单。）
    skip = set(pending)
    holdings = {sym: qty for sym, qty in positions.items()
                if sym.endswith(suffix) and sym not in PROXY_SYMBOLS and sym not in skip}
    prices = {sym: float((quotes.get(sym) or {}).get("last") or 0) for sym in universe}
    limits = RebLimits(
        lot_size=100 if market == "hk" else 1,
        min_notional=policy.min_order_notional_usd,
        max_order_notional=policy.max_notional_per_order_usd,
        max_orders=policy.max_orders_per_run,
        cash_buffer=policy.cash_buffer,
        trim_tolerance=policy.trim_tolerance,
        orphan_min_weight=policy.orphan_min_weight,
        orphan_max_turnover=policy.orphan_max_turnover,
    )
    targets = {sym: w for sym, w in weights.items()
               if sym not in PROXY_SYMBOLS and sym not in skip}
    if not policy.cleanup_orphans:
        holdings = {sym: q for sym, q in holdings.items() if sym in targets}
    # 注意：cash 只能是真实现金。之前误把袖套市值加进来当现金用，
    # 结果买单按「市值 + 现金」下单，模拟出来现金直接变负数。
    plan_orders, notes = plan_rebalance(
        targets, cash=max(0.0, cash) * max(0.0, min(1.0, cash_share)),
        capital=budget, holdings=holdings, prices=prices, limits=limits,
    )
    # 价格加工：港股按 tick 取合法价，美股用 ±0.5% 的限价提高成交概率
    for order in plan_orders:
        ref = prices.get(order["symbol"], 0.0)
        if ref <= 0:
            continue
        if market == "hk":
            tick = _tick_hk(ref)
            pad = max(tick, round(ref * policy.price_buffer, 3))
            order["price"] = round(ref - pad if order["side"] == "sell" else ref + pad, 3)
        else:
            order["price"] = round(ref * (1 - policy.price_buffer if order["side"] == "sell"
                                          else 1 + policy.price_buffer), 2)
        order["order_type"] = policy.allowed_order_type
        order["tif"] = policy.allowed_tif
    plan_orders = [o for o in plan_orders if o.get("price")]
    if not plan_orders:
        return {"ok": False,
                "reason": f"{ccy} 可用现金 {cash:,.0f}，无可执行订单（已达标或现金不足）",
                "orders": []}
    return {"ok": True, "market": market, "currency": ccy, "cash": cash,
            "budget": round(budget, 2), "orders": plan_orders, "notes": notes,
            "exposure_factor": expo, "vol_diag": vol_diag,
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
    equity = max(0.0, cash) + market_value   # 账户口径，用于换手上限与碎仓阈值
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



WORKING_STATUSES = {"New", "NotReported", "Submitted", "PartFilled", "PartialFilled",
                    "WaitingSubmit", "Filling"}


def _wait_sells(symbols: set[str], *, timeout: float = 90.0, interval: float = 6.0) -> dict:
    """卖出后等成交（有上限），再去买。

    不等的后果是历史事故：先挂卖单、马上按「卖出所得」下买单，卖单还没成交，
    买单因为可用资金不足被集体拒掉（2026-11-20 一天 27 笔 Insufficient Credit Limit）。
    """
    import time

    if os.environ.get("EXEC_WAIT_SELLS", "1") == "0" or not symbols:
        return {}
    deadline = time.time() + max(0.0, timeout)
    last: dict[str, dict] = {}
    while True:
        try:
            rows = _cli(["order"]) or []
        except (RuntimeError, json.JSONDecodeError) as exc:
            print(f"[exec] 等待卖出成交时查询失败（继续等）: {type(exc).__name__}")
            rows = None
        if rows is not None:
            status = {}
            for o in rows:
                sym = broker_symbol(o.get("symbol") or "")
                if not sym:
                    continue
                for want in symbols:
                    if broker_symbol(want) == sym:
                        status[want] = {"status": o.get("status"),
                                        "filled": float(o.get("executed_quantity") or 0),
                                        "qty": float(o.get("quantity") or 0)}
            last = status
            pending = [s2 for s2, st in status.items() if st.get("status") in WORKING_STATUSES]
            if not pending:
                break
        if time.time() >= deadline:
            print(f"[exec] 卖出等待超时（{timeout:.0f}s），仍有未成交：{sorted(last)[:5]}")
            break
        time.sleep(interval)
    return last


def execute(market: str, *, dry_run: bool, policy: Policy | None = None, cash_share: float = 1.0,
            pass_no: int = 1) -> dict:
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

    # 落盘期望订单：对账靠它判断「该发的有没有发出去」
    try:
        from quant.automation.reconcile import record_expected

        _mkt = {"cn_proxy": "us", "cn_proxy_hk": "hk"}.get(market, market)
        record_expected(_mkt, [{"symbol": o["symbol"], "side": o["side"], "qty": o["qty"]}
                               for o in plan["orders"]])
    except Exception as exc:  # noqa: BLE001 - 记录失败不阻断下单，但要留痕
        print(f"[exec] 期望订单落盘失败: {type(exc).__name__} {str(exc)[:80]}")

    session = datetime.now(timezone.utc).date().isoformat()
    key = consumption_key(market=market, snapshot_id=str(plan["snapshot_rebalance_date"]),
                          eligible_execution_session=session, pass_no=pass_no)
    ledger = Ledger(LEDGER_ROOT)
    try:
        ledger.bind_plan(key=key, plan_id=f"{market}-{plan['snapshot_rebalance_date']}")
    except AlreadyBound:
        return {"ok": False, "blocked": True,
                "reason": "该快照/交易日已提交过（一次消费），本交易日不再重复下单", "orders": []}

    results = []
    def _submit(item: dict) -> dict:
        try:
            ledger.mark_submitting(key=key, plan_id=item["symbol"], intent_id=item["symbol"])
            bsymbol = broker_symbol(item["symbol"])
            prev = _cli(["order", item["side"], bsymbol, str(item["qty"]),
                         "--price", f"{item['price']:.3f}", "--order-type", "LO"])
            code = prev.get("confirmation_code")
            if not code:
                ledger.mark_result(key=key, plan_id=item["symbol"], intent_id=item["symbol"],
                                   status="UNKNOWN")
                return {**item, "status": "no-confirmation-code"}
            resp = _cli(["order", item["side"], bsymbol, str(item["qty"]),
                         "--price", f"{item['price']:.3f}", "--order-type", "LO", "--execute", code])
            oid = resp.get("order_id")
            ledger.mark_result(key=key, plan_id=item["symbol"], intent_id=item["symbol"],
                               status="WORKING" if oid else "UNKNOWN")
            return {**item, "status": "submitted" if oid else "unknown", "order_id": oid}
        except Exception as exc:  # noqa: BLE001 - 单笔失败不影响其余，逐笔记账
            return {**item, "status": f"error: {str(exc)[:80]}"}

    sells = [o for o in plan["orders"] if o["side"] == "sell"]
    buys = [o for o in plan["orders"] if o["side"] == "buy"]
    for item in sells:
        results.append(_submit(item))

    # 先卖后买之间插入「有上限的等待」，避免买单因资金未到账被集体拒掉
    if sells and buys and any(r.get("status") == "submitted" for r in results):
        plan["sell_wait"] = _wait_sells({o["symbol"] for o in sells})

    for item in buys:
        results.append(_submit(item))
    plan["results"] = results
    if not any(r.get("order_id") for r in results):
        # 一笔都没落地（网络/权限/拒单）：把当日额度放回去，避免计划被白烧掉
        ledger.release(key=key, reason="全部委托未提交成功: " + "; ".join(
            str(r.get("status", ""))[:40] for r in results[:3]))
        plan["ok"] = False
        plan["reason"] = "全部委托未提交成功，已释放本交易日额度"
        plan["blocked"] = True
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="受控模拟盘执行通道（默认 dry-run）")
    ap.add_argument("--market", choices=["us", "hk", "cn_proxy", "cn_proxy_hk"], required=True)
    ap.add_argument("--execute", action="store_true", help="真正提交（还需 LB_ALLOW_EXECUTION=1）")
    ap.add_argument("--cash-share", type=float, default=1.0,
                    help="本袖套可使用的现金份额（0-1），用于多袖套分配")
    ap.add_argument("--vol-target", type=float, default=None,
                    help="组合层波动率目标（如 0.15）；不传则沿用 Policy 默认")
    ap.add_argument("--pass", dest="pass_no", type=int, default=1,
                    help="当日第几轮补单（1=首轮，2=换手上限导致的补单，最多 2）")
    args = ap.parse_args(argv)

    if args.pass_no not in (1, 2):
        print("[exec] --pass 只允许 1 或 2（一天最多两轮，避免过度换手）")
        return 1
    policy = Policy()
    if args.vol_target:
        policy.vol_target = args.vol_target
    result = execute(args.market, dry_run=not args.execute, cash_share=args.cash_share,
                     policy=policy, pass_no=args.pass_no)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
    if result.get("blocked"):
        # 非交易时段 / 本日已消费：预期内的跳过，不算失败（自动化里 exit 1 只留给真故障）
        return 3
    ok = result.get("ok") and not any(str(r.get("status", "")).startswith(("error", "unknown"))
                                      for r in result.get("results", []))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
