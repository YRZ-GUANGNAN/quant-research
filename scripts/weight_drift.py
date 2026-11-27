"""持仓漂移检查：实际组合与目标权重的偏离有多大。

为什么需要：自动交易最隐蔽的失败不是报错，而是「该买的没买上、该卖的没卖完」。
几次部分成交、一次拒单、一次忘记跑，账本就会悄悄偏离模型意图，而净值短期看不出来。
这个脚本把偏离量化成可判定的数字，并对超出容忍度的标的直接点名。

口径：
- 权重按「持仓市值占比」算（不含现金），只看组合结构；
- 目标权重同口径归一化（模型给的是含现金的总权重，这里剥掉现金部分）；
- 漂移 = 实际权重 − 目标权重，逐标的给出；总偏离用 L1/2（=需换手比例）。

用法：
    python scripts/weight_drift.py                 # us+hk
    python scripts/weight_drift.py --markets us
退出码：0 在容忍度内 / 1 有标的超阈值 / 3 无法取数
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
SNAPSHOT = ROOT / "state" / "target_snapshot.json"
LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"

TOLERANCE = 0.02          # 单标的允许偏离 2 个百分点
# 代理袖套持有的标的（ASHR/3188）不归主袖套管，算漂移时必须剔除
PROXY_SYMBOLS = {"ASHR.US", "3188.HK"}
MAX_TURNOVER = 0.25       # 总偏离（需换手比例）超过 25% 视为需要修正



def normalize_symbol(symbol: str) -> str:
    """港股代码统一去掉前导零：快照里写 0322.HK，券商持仓返回 322.HK。

    不统一就会出现「目标里全是新票、持仓全是 extra」的假漂移（第一版就是这样）。
    """
    sym = str(symbol or "").strip().upper()
    if sym.endswith(".HK"):
        num = sym[:-3].lstrip("0")
        return f"{num or '0'}.HK"
    return sym


def normalize(weights: dict[str, float]) -> dict[str, float]:
    """权重归一化到 1（剥掉现金部分，只比较组合结构）。"""
    total = sum(v for v in weights.values() if v > 0)
    if total <= 0:
        return {}
    return {k: v / total for k, v in weights.items() if v > 0}


def compute_drift(targets: dict[str, float], positions: list[dict],
                  quotes: dict[str, float]) -> dict:
    """纯计算：目标权重 vs 实际持仓 → 逐标的漂移与总换手需求。"""
    mv: dict[str, float] = {}
    for p in positions:
        sym = normalize_symbol(p.get("symbol"))
        try:
            qty = float(p.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        price = float(quotes.get(sym) or 0)
        if not sym or qty <= 0 or price <= 0:
            continue
        mv[sym] = mv.get(sym, 0.0) + qty * price

    total_mv = sum(mv.values())
    if total_mv <= 0:
        return {"ok": False, "reason": "取不到持仓市值（行情或持仓缺失）", "rows": [], "turnover": None}

    tgt = normalize({normalize_symbol(k): v for k, v in targets.items()})
    act = {s: v / total_mv for s, v in mv.items()}
    rows = []
    for sym in sorted(set(tgt) | set(act)):
        t, a = tgt.get(sym, 0.0), act.get(sym, 0.0)
        drift = a - t
        if t == 0:
            kind = "extra"       # 不在目标里但持有
        elif a == 0:
            kind = "missing"     # 在目标里但没持有
        else:
            kind = "hold"
        rows.append({"symbol": sym, "target": round(t, 4), "actual": round(a, 4),
                     "drift": round(drift, 4), "kind": kind,
                     "over": abs(drift) > TOLERANCE})
    turnover = round(sum(abs(r["drift"]) for r in rows) / 2, 4)
    offenders = [r for r in rows if r["over"]]
    return {
        "ok": bool(rows) and not offenders and turnover <= MAX_TURNOVER,
        "turnover": turnover,
        "tolerance": TOLERANCE,
        "max_turnover": MAX_TURNOVER,
        "positions": len(act),
        "targets": len(tgt),
        "offenders": sorted(offenders, key=lambda r: -abs(r["drift"]))[:8],
        "rows": sorted(rows, key=lambda r: -abs(r["drift"])),
    }


def _cli(args: list[str], timeout: int = 60):
    proc = subprocess.run([str(LB), *args, "--format", "json"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or out).strip()[:160])
    return json.loads(out) if out else {}


def load_snapshot(market: str) -> dict[str, float]:
    try:
        snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        return {k: float(v) for k, v in (snap["sleeves"][market]["weights"] or {}).items()}
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError):
        return {}


def quotes_for(symbols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for i in range(0, len(symbols), 20):
        try:
            for q in _cli(["quote", *symbols[i:i + 20]]) or []:
                if q.get("symbol") and q.get("last"):
                    out[q["symbol"]] = float(q["last"])
        except (RuntimeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            print(f"[drift] 行情获取失败（跳过这一批）: {type(exc).__name__}")
    return out


def check_market(market: str) -> dict:
    suffix = ".US" if market == "us" else ".HK"
    targets = load_snapshot(market)
    if not targets:
        return {"market": market, "ok": False, "reason": "没有目标快照（先跑 build_target_snapshot.py）"}
    try:
        positions = [p for p in (_cli(["positions"]) or [])
                     if str(p.get("symbol", "")).endswith(suffix)
                     and str(p.get("symbol")) not in PROXY_SYMBOLS]
    except (RuntimeError, json.JSONDecodeError) as exc:
        return {"market": market, "ok": False, "reason": f"持仓读取失败: {type(exc).__name__} {str(exc)[:80]}"}
    quotes = quotes_for([str(p["symbol"]) for p in positions])
    quotes = {normalize_symbol(k): v for k, v in quotes.items()}
    result = compute_drift(targets, positions, quotes)
    return {"market": market, **result}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="持仓漂移检查")
    ap.add_argument("--markets", default="us,hk")
    ap.add_argument("--strict", action="store_true",
                    help="有标的超阈值时返回 1（流水线默认只报告，不算失败）")
    args = ap.parse_args(argv)

    reports = [check_market(m.strip()) for m in args.markets.split(",") if m.strip()]
    if not any(r.get("turnover") is not None for r in reports):
        print("[drift] 没有可用数据（持仓/行情/快照任一缺失）")
        return 3

    out = REPORTS / "weight_drift.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"),
                               "reports": reports}, ensure_ascii=False, indent=2), encoding="utf-8")

    bad = False
    for r in reports:
        if r.get("turnover") is None:
            print(f"=== {r['market'].upper()}：{r.get('reason', '无数据')} ===")
            continue
        flag = "✅ 在容忍度内" if r["ok"] else "⚠️ 需要修正"
        print(f"=== {r['market'].upper()}：{flag}（需换手 {r['turnover'] * 100:.1f}%，"
              f"持仓 {r['positions']} / 目标 {r['targets']}）===")
        for row in (r.get("offenders") or [])[:5]:
            print(f"  {row['symbol']:<10} 目标 {row['target'] * 100:5.1f}%  实际 {row['actual'] * 100:5.1f}%  "
                  f"偏离 {row['drift'] * 100:+5.1f}pp  ({row['kind']})")
        bad = bad or not r["ok"]
    print(f"  -> {out}")
    if bad and args.strict:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
