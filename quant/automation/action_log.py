"""风险动作台账与效果追踪。

每次风控动作（trim/exit/de_risk/replace）落盘一条记录，
之后用行情回看：卖出标的后 N 日涨跌、替换标的后 N 日涨跌，
从而回答「止损/替换到底有没有用」。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

LOG_PATH = Path(__file__).resolve().parents[2] / "state" / "action_log.jsonl"


def log_actions(market: str, actions: list, *, prices: dict[str, float],
                on: str | None = None) -> int:
    """把动作追加到台账（jsonl）。返回写入条数。"""
    if not actions:
        return 0
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    day = on or date.today().isoformat()
    n = 0
    with LOG_PATH.open("a", encoding="utf-8") as f:
        for a in actions:
            rec = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "date": day,
                "market": market,
                "symbol": getattr(a, "symbol", ""),
                "action": getattr(a, "action", ""),
                "severity": getattr(a, "severity", ""),
                "reason": getattr(a, "reason", ""),
                "price": float(prices.get(getattr(a, "symbol", ""), 0.0)),
                "replace_with": getattr(a, "replace_with", "") or "",
                "replace_price": float(prices.get(getattr(a, "replace_with", ""), 0.0))
                if getattr(a, "replace_with", "") else 0.0,
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return n


def load_log() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    out = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # noqa: silent-ok - 跳过损坏的历史行，保持台账可读
    return out


def evaluate_actions(records: list[dict], price_now: dict[str, float], *,
                     min_days: int = 0) -> dict:
    """用「当前价」回看每条动作：卖出标的与替换标的的相对表现。"""
    rows = []
    for r in records:
        sym = r.get("symbol")
        p0 = float(r.get("price") or 0)
        p1 = float(price_now.get(sym, 0) or 0)
        if p0 <= 0 or p1 <= 0:
            continue
        change = p1 / p0 - 1.0
        repl = r.get("replace_with") or ""
        rp0 = float(r.get("replace_price") or 0)
        rp1 = float(price_now.get(repl, 0) or 0) if repl else 0.0
        repl_change = (rp1 / rp0 - 1.0) if (repl and rp0 > 0 and rp1 > 0) else None
        rows.append({
            "date": r.get("date"), "market": r.get("market"), "symbol": sym,
            "action": r.get("action"), "reason": r.get("reason"),
            "price_then": p0, "price_now": p1, "change": round(change, 4),
            "replace_with": repl,
            "replace_change": round(repl_change, 4) if repl_change is not None else None,
            "verdict": (
                "good" if (change < 0 and (repl_change is None or repl_change > change))
                else "bad" if (change > 0) else "neutral"
            ),
        })
    good = sum(1 for x in rows if x["verdict"] == "good")
    bad = sum(1 for x in rows if x["verdict"] == "bad")
    return {
        "total": len(rows),
        "good": good, "bad": bad,
        "hit_rate": round(good / len(rows), 4) if rows else 0.0,
        "rows": rows,
    }
