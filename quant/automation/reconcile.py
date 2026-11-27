"""执行对账：期望订单 vs 实际订单/成交，找出差异。

用途：每次自动下单后核对，确认「计划 → 委托 → 成交」三者一致，
避免出现「以为下了但没成交」「重复下单」「卖了没买」等静默失败。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


STATUS_OK = {"Filled", "PartialFilled", "PartFilled", "Working", "New", "NotReported"}
STATUS_BAD = {"Rejected", "Canceled", "Expired", "Failed"}


EXPECTED_DIR = Path(__file__).resolve().parents[2] / "state" / "expected"


def record_expected(market: str, orders: list[dict], *, on: str | None = None) -> Path:
    """把「本轮期望订单」落盘，供事后对账。"""
    import json
    from datetime import date

    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    day = on or date.today().isoformat()
    path = EXPECTED_DIR / f"{day}_{market}.json"
    # 合并而不是覆盖：一个交易日可能先跑再平衡、再跑风控动作，
    # 覆盖会让对账只记得最后一批，前面真实发出的单变成「计划外」。
    merged: dict[tuple[str, str], dict] = {}
    if path.exists():
        try:
            for row in json.loads(path.read_text(encoding="utf-8")):
                merged[(str(row.get("symbol")), str(row.get("side")))] = row
        except (json.JSONDecodeError, TypeError):
            merged = {}
    for row in orders:
        merged[(str(row.get("symbol")), str(row.get("side")))] = row
    path.write_text(json.dumps(list(merged.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_expected(market: str, *, on: str | None = None) -> list[dict]:
    import json
    from datetime import date

    day = on or date.today().isoformat()
    path = EXPECTED_DIR / f"{day}_{market}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Line:
    symbol: str
    side: str
    expected_qty: float
    executed_qty: float
    status: str           # filled / partial / pending / rejected / missing
    order_ids: list[str]

    @property
    def ok(self) -> bool:
        return self.status in {"filled", "partial", "pending"}


def _norm_side(s: str) -> str:
    s = (s or "").lower()
    return "buy" if s in {"buy", "b", "买入"} else "sell" if s in {"sell", "s", "卖出"} else s


def reconcile(expected: list[dict], orders: list[dict], *, qty_tolerance: float = 0.0) -> dict:
    """expected: [{symbol, side, qty}]；orders: 券商返回的订单列表。"""
    buckets: dict[tuple[str, str], list[dict]] = {}
    for o in orders:
        if not isinstance(o, dict):
            continue
        key = (str(o.get("symbol")), _norm_side(str(o.get("side"))))
        buckets.setdefault(key, []).append(o)

    lines: list[Line] = []
    matched_keys = set()
    for e in expected:
        sym, side, qty = str(e["symbol"]), _norm_side(str(e["side"])), float(e.get("qty") or 0)
        key = (sym, side)
        matched_keys.add(key)
        items = buckets.get(key, [])
        executed = 0.0
        statuses = []
        ids = []
        for o in items:
            try:
                executed += float(o.get("executed_quantity") or 0)
            except (TypeError, ValueError):
                # 交易所返回的成交量为空/非数字：按 0 计，但留痕
                statuses.append("PARSE_ERROR")
            statuses.append(str(o.get("status") or ""))
            if o.get("order_id"):
                ids.append(str(o["order_id"]))
        if not items:
            status = "missing"
        elif any(s in STATUS_BAD for s in statuses):
            status = "rejected"
        elif executed >= qty - qty_tolerance:
            status = "filled"
        elif executed > 0:
            status = "partial"
        else:
            status = "pending"
        lines.append(Line(sym, side, qty, executed, status, ids))

    unexpected = [
        {"symbol": k[0], "side": k[1], "count": len(v),
         "statuses": [str(o.get("status") or "") for o in v]}
        for k, v in buckets.items() if k not in matched_keys
    ]

    mismatched = [l for l in lines if l.status in {"rejected", "missing"}]
    partial = [l for l in lines if l.status == "partial"]
    ok = not mismatched and not unexpected
    return {
        "ok": ok,
        "lines": [l.__dict__ for l in lines],
        "partial": [l.__dict__ for l in partial],
        "mismatched": [l.__dict__ for l in mismatched],
        "unexpected": unexpected,
        "summary": (f"对账：期望 {len(lines)} 笔 | 完全成交 "
                    f"{sum(1 for l in lines if l.status == 'filled')} | 部分 {len(partial)} | "
                    f"异常 {len(mismatched)} | 计划外 {len(unexpected)}"),
    }
