"""风险动作效果追踪：卖出标的后是否继续跌、替换标的是否更好。

用法：python scripts/track_actions.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation.action_log import evaluate_actions, load_log

LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"


def _quotes(symbols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    if not symbols:
        return out
    for i in range(0, len(symbols), 20):
        p = subprocess.run([str(LB), "quote", *symbols[i:i + 20], "--format", "json"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        try:
            data = json.loads((p.stdout or "").strip() or "[]")
        except json.JSONDecodeError:
            continue
        for q in data if isinstance(data, list) else []:
            if isinstance(q, dict) and q.get("symbol") and q.get("last"):
                out[q["symbol"]] = float(q["last"])
    return out


def main() -> int:
    records = load_log()
    if not records:
        print("[track] 台账为空（尚无风险动作记录）")
        return 0

    symbols = sorted({r.get("symbol") for r in records if r.get("symbol")}
                     | {r.get("replace_with") for r in records if r.get("replace_with")})
    # A 股代码需要后缀转换
    lb_syms = [s if "." in s else (f"{s}.SZ" if s.startswith(("00", "30")) else f"{s}.SH") for s in symbols]
    prices = _quotes(lb_syms)
    price_now = {}
    for s, p in zip(symbols, lb_syms):
        if p in prices:
            price_now[s] = prices[p]
        elif s in prices:
            price_now[s] = prices[s]

    result = evaluate_actions(records, price_now)
    print(f"[track] 台账 {len(records)} 条，可评估 {result['total']} 条 | "
          f"有效 {result['good']} / 无效 {result['bad']} | 命中率 {result['hit_rate']:.0%}")
    for row in result["rows"][-10:]:
        repl = (f" 替换 {row['replace_with']} {row['replace_change']:+.1%}"
                if row.get("replace_change") is not None else "")
        print(f"  {row['date']} {row['market']} {row['symbol']} {row['action']}: "
              f"{row['change']:+.1%}（{row['verdict']}）{repl}")

    out = ROOT / "reports" / f"action_tracking_{date.today().isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[track] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
