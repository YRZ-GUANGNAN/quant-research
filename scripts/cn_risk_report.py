"""A 股实盘持仓风险报告（只读）：连接同花顺，生成风险动作。"""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation.risk_guard import Position, RiskPolicy, evaluate, summarize
from quant.broker import cn_broker


def main() -> int:
    acct = cn_broker._ths_account()
    try:
        raw = cn_broker._read_with_retry(cn_broker._read_positions_via_clipboard, attempts=3, delay=1.5)
    except Exception as exc:  # noqa: BLE001 - 休市/表格导出失败时降级为「仅资金」
        raw = []
        print(f"[cn] 持仓明细读取失败（{type(exc).__name__}），降级为仅资金视图")
    try:
        bal = cn_broker._read_with_retry(cn_broker._read_balance_via_gui, attempts=2)
    except Exception:  # noqa: BLE001
        bal = cn_broker._read_with_retry(lambda: acct.balance)
    row = bal if isinstance(bal, dict) else (bal[0] if bal else {})
    cash = float(row.get("可用金额") or 0)
    total = float(row.get("总资产") or 0)
    positions, cost_sum = [], 0.0
    for p in raw:
        qty = float(p.get("股票余额") or 0)
        price = float(p.get("市价") or 0)
        cost = float(p.get("成本价") or 0)
        if qty <= 0 or price <= 0:
            continue
        positions.append(Position(symbol=str(p.get("证券代码")), qty=qty, cost=cost, last=price))
        cost_sum += qty * cost
    equity = total or (cash + sum(x.market_value for x in positions))
    mv = sum(x.market_value for x in positions)
    drawdown = (mv / cost_sum - 1.0) if cost_sum > 0 else 0.0   # 只算持仓浮盈亏，不含现金
    actions = evaluate(positions, equity=equity, drawdown=drawdown, policy=RiskPolicy()) if positions else []
    print(f"[cn] 总资产 {equity:,.0f} 元 | 可用 {cash:,.0f} | 持仓 {len(positions)} 只 | 浮盈亏 {drawdown:+.2%}")
    print(summarize(actions))
    out = ROOT / "reports" / "cn_risk_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "equity": round(equity, 2), "cash": round(cash, 2), "drawdown": round(drawdown, 4),
        "positions": [{"symbol": p.symbol, "qty": p.qty, "cost": p.cost, "last": p.last,
                       "pnl_pct": round(p.pnl_pct, 4),
                       "weight": round(p.market_value / equity, 4) if equity else 0}
                      for p in positions],
        "actions": [a.__dict__ for a in actions],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[cn] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
