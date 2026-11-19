"""业绩归因：把盈亏拆到「市场 → 标的 → 权重」，并给出集中度指标。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Contributor:
    symbol: str
    qty: float
    cost: float
    last: float
    pnl: float            # 未实现盈亏（(last-cost)*qty）
    pnl_pct: float
    value: float
    weight: float         # 占该市场总资产
    contribution: float   # pnl / 总资产（对组合收益率的贡献）


def position_attribution(positions: list[dict], *, equity: float) -> list[Contributor]:
    """positions: [{symbol, qty, cost, last}]，equity = 现金 + 持仓市值。"""
    out: list[Contributor] = []
    for p in positions:
        qty = float(p.get("qty") or 0)
        cost = float(p.get("cost") or 0)
        last = float(p.get("last") or 0)
        if qty <= 0 or last <= 0:
            continue
        pnl = (last - cost) * qty
        value = last * qty
        out.append(Contributor(
            symbol=str(p["symbol"]), qty=qty, cost=cost, last=last,
            pnl=round(pnl, 2),
            pnl_pct=round((last / cost - 1.0) if cost > 0 else 0.0, 4),
            value=round(value, 2),
            weight=round(value / equity, 4) if equity > 0 else 0.0,
            contribution=round(pnl / equity, 4) if equity > 0 else 0.0,
        ))
    out.sort(key=lambda c: -c.pnl)
    return out


def concentration(positions: list[dict], *, equity: float) -> dict:
    """集中度按总资产（含现金）计算，避免把现金排除后放大集中度。"""
    values = sorted((float(p.get("last") or 0) * float(p.get("qty") or 0) for p in positions),
                    reverse=True)
    total = equity if equity > 0 else sum(values)
    if total <= 0:
        return {"positions": 0, "top1": 0.0, "top5": 0.0, "hhi": 0.0}
    weights = [v / total for v in values]
    return {
        "positions": len(values),
        "top1": round(weights[0], 4),
        "top5": round(sum(weights[:5]), 4),
        "hhi": round(sum(w * w for w in weights), 4),
    }


def summarize(contributors: list[Contributor], *, top_n: int = 5) -> dict:
    total_pnl = sum(c.pnl for c in contributors)
    winners = [c for c in contributors if c.pnl > 0]
    losers = [c for c in contributors if c.pnl < 0]
    return {
        "total_pnl": round(total_pnl, 2),
        "winners": len(winners),
        "losers": len(losers),
        "hit_rate": round(len(winners) / len(contributors), 4) if contributors else 0.0,
        "best": [{"symbol": c.symbol, "pnl": c.pnl, "pnl_pct": c.pnl_pct} for c in contributors[:top_n]],
        "worst": [{"symbol": c.symbol, "pnl": c.pnl, "pnl_pct": c.pnl_pct} for c in contributors[-top_n:][::-1]],
    }

    # 注：`worst` 取贡献最低的若干只（已按 pnl 升序反转）
