"""替换标的筛选 + 净值历史（供风控判断真实回撤）。"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path


def pick_replacements(
    exits: list[str],
    weights: dict[str, float],
    held: set[str],
    quotes: dict[str, dict],
    *,
    per_exit: int = 1,
) -> dict[str, str]:
    """为每个退出标的挑选替代者：按目标权重降序，排除已持有/将退出/无报价。"""
    taken: set[str] = set()
    pool = [
        (sym, float(w)) for sym, w in weights.items()
        if sym not in held and sym not in taken and (quotes.get(sym) or {}).get("last")
    ]
    pool.sort(key=lambda kv: -kv[1])
    out: dict[str, str] = {}
    idx = 0
    for sym in exits:
        picks = []
        while len(picks) < per_exit and idx < len(pool):
            cand = pool[idx][0]
            idx += 1
            if cand == sym or cand in taken:
                continue
            picks.append(cand)
            taken.add(cand)
        if picks:
            out[sym] = picks[0]
    return out


def momentum_candidates(market: str, held: set[str], *, top_n: int = 10,
                        lookback: int = 60) -> dict[str, float]:
    """从本地行情算 60 日动量，返回未持有的候选（权重按动量归一，仅供排序）。"""
    try:
        from quant.data import store
    except Exception:  # noqa: BLE001
        return {}
    try:
        bars = store.load_bars(market)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, float] = {}
    for code, g in bars.groupby("code"):
        sym = str(code)
        if sym in held or len(g) < lookback:
            continue
        g = g.sort_values("date")
        first, last = float(g["close"].iloc[-lookback]), float(g["close"].iloc[-1])
        if first <= 0:
            continue
        out[sym] = last / first - 1.0
    ranked = sorted(out.items(), key=lambda kv: -kv[1])[:top_n]
    total = sum(max(v, 0) for _, v in ranked) or 1.0
    return {k: max(v, 0) / total for k, v in ranked}


NAV_FIELDS = ["date", "market", "equity", "cash", "positions"]


def record_nav(path: Path | str, *, market: str, equity: float, cash: float, positions: int,
               on: str | None = None) -> None:
    """按日记录净值（同日覆盖，不重复追加）。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    day = on or date.today().isoformat()
    rows: list[dict] = []
    if p.exists():
        with p.open("r", encoding="utf-8", newline="") as f:
            rows = [r for r in csv.DictReader(f) if r.get("date") and r.get("market")]
    rows = [r for r in rows if not (r["date"] == day and r["market"] == market)]
    rows.append({"date": day, "market": market, "equity": f"{equity:.2f}",
                 "cash": f"{cash:.2f}", "positions": str(positions)})
    rows.sort(key=lambda r: (r["market"], r["date"]))
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=NAV_FIELDS)
        w.writeheader()
        w.writerows(rows)


def drawdown_from_nav(path: Path | str, market: str) -> float:
    """从净值历史算当前回撤（peak → 最新）。"""
    p = Path(path)
    if not p.exists():
        return 0.0
    with p.open("r", encoding="utf-8", newline="") as f:
        eq = [float(r["equity"]) for r in csv.DictReader(f) if r.get("market") == market]
    if not eq:
        return 0.0
    peak = max(eq)
    return (eq[-1] / peak - 1.0) if peak > 0 else 0.0
