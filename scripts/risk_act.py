"""风险动作执行入口：识别风险 → 生成动作 →（可选）真实下单。

用法：
    python scripts/risk_act.py --market us            # dry-run
    python scripts/risk_act.py --market cn --execute   # 真实（需 RISK_ALLOW_EXECUTION=1 + CN_ALLOW_EXECUTION=1）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation.replace import (drawdown_from_nav, momentum_candidates,
                                      pick_replacements, record_nav)
from quant.automation.risk_execute import Limits, execute, to_orders
from quant.automation.risk_guard import Position, RiskPolicy, evaluate, summarize

LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"


def _cli_json(args: list[str]) -> object:
    p = subprocess.run([str(LB), *args, "--format", "json"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    out = (p.stdout or "").strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        s, e = out.find("{"), out.rfind("}")
        return json.loads(out[s:e + 1]) if s >= 0 else {}


def load_lb(market: str):
    suffix = ".US" if market == "us" else ".HK"
    raw = [p for p in (_cli_json(["positions"]) or []) if isinstance(p, dict)
           and str(p.get("symbol", "")).endswith(suffix)]
    syms = [p["symbol"] for p in raw]
    quotes = {}
    for i in range(0, len(syms), 20):
        for q in _cli_json(["quote", *syms[i:i + 20]]) or []:
            if isinstance(q, dict) and q.get("symbol"):
                quotes[q["symbol"]] = q
    positions, pmeta = {}, {}
    for p in raw:
        sym = p["symbol"]
        qty = float(p.get("quantity") or 0)
        cost = float(p.get("cost_price") or 0)
        last = float((quotes.get(sym) or {}).get("last") or cost)
        positions[sym] = {"qty": qty, "last": last, "cost": cost}
        pmeta[sym] = Position(symbol=sym, qty=qty, cost=cost, last=last)
    return positions, quotes, list(pmeta.values())


def load_cn():
    from quant.broker import cn_broker

    rows = cn_broker._read_positions_via_clipboard()
    positions, quotes, plist = {}, {}, []
    for r in rows:
        code = str(r.get("证券代码") or "")
        qty = float(r.get("股票余额") or 0)
        price = float(r.get("市价") or 0)
        cost = float(r.get("成本价") or 0)
        avail = float(r.get("可用余额") or 0)
        if not code or qty <= 0 or price <= 0:
            continue
        positions[code] = {"qty": avail or qty, "last": price, "cost": cost}
        quotes[code] = {"last": price}
        plist.append(Position(symbol=code, qty=qty, cost=cost, last=price))
    return positions, quotes, plist


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["us", "hk", "cn"], required=True)
    ap.add_argument("--execute", action="store_true")
    args = ap.parse_args()

    if args.market == "cn":
        positions, quotes, plist = load_cn()
    else:
        positions, quotes, plist = load_lb(args.market)

    if not plist:
        print(f"[risk-act] {args.market}: 无持仓，跳过")
        return 0

    equity = sum(p.qty * p.last for p in plist)
    cost_sum = sum(p.qty * p.cost for p in plist)
    # 真实回撤：优先用净值历史（每日记录），否则退回成本价近似
    nav_path = ROOT / "reports" / "nav_history.csv"
    cash_lb = 0.0
    if args.market != "cn":
        try:
            for a in _cli_json(["assets"]) or []:
                for c in a.get("cash_infos") or []:
                    if c.get("currency") == ("USD" if args.market == "us" else "HKD"):
                        cash_lb = float(c.get("available_cash") or 0)
        except Exception:  # noqa: BLE001
            cash_lb = 0.0
    record_nav(nav_path, market=args.market, equity=equity + cash_lb, cash=cash_lb,
               positions=len(plist))
    drawdown = drawdown_from_nav(nav_path, args.market)
    if drawdown == 0.0 and cost_sum > 0:
        drawdown = (equity / cost_sum - 1.0)
    actions = evaluate(plist, equity=equity, drawdown=drawdown, policy=RiskPolicy())

    # 自动替换：为每个退出标的挑选候选（按目标权重降序）
    snap_path = ROOT / "state" / "target_snapshot.json"
    weights = {}
    if snap_path.exists():
        snap = json.loads(snap_path.read_text(encoding="utf-8"))
        weights = (snap.get("sleeves", {}).get(args.market, {}) or {}).get("weights", {}) or {}
    exits = [a.symbol for a in actions if a.action == "exit"]
    if exits:
        # 先看目标池，再退化到「动量备选池」（目标池全持有时的补充来源）
        repl = pick_replacements(exits, weights, set(positions), quotes) if weights else {}
        missing = [s for s in exits if s not in repl]
        if missing:
            raw = momentum_candidates(args.market, set(positions), top_n=10)
            if raw:
                suffix = ".US" if args.market == "us" else ".HK"
                cand = {(c if c.endswith(suffix) else f"{c}{suffix}"): w for c, w in raw.items()}
                for i, batch in enumerate([list(cand)[j:j + 20] for j in range(0, len(cand), 20)]):
                    for q in _cli_json(["quote", *batch]) or []:
                        if isinstance(q, dict) and q.get("symbol"):
                            quotes.setdefault(q["symbol"], q)
                repl.update(pick_replacements(missing, cand, set(positions), quotes))
        for a in actions:
            if a.action == "exit" and a.symbol in repl:
                a.replace_with = repl[a.symbol]
    if exits and not any(getattr(a, "replace_with", "") for a in actions):
        print("[risk-act] 无可用替换候选（目标池已满仓且动量备选不足）")
    print(f"[risk-act] {args.market}: 持仓 {len(plist)} 只 | 市值 {equity:,.0f} | 浮盈亏 {drawdown:+.2%}")
    print(summarize(actions))

    result = execute(actions, market=args.market, dry_run=not args.execute,
                     positions=positions, quotes=quotes, limits=Limits())
    print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])
    out = ROOT / "reports" / f"risk_act_{args.market}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"market": args.market, "actions": [a.__dict__ for a in actions],
                               "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
