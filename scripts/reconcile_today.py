"""每日对账：期望订单 vs 券商实际订单 → 报告；异常退出非零。

用法：python scripts/reconcile_today.py --market us
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

from quant.automation.reconcile import load_expected, reconcile

LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"


def _orders() -> list[dict]:
    p = subprocess.run([str(LB), "order", "--format", "json"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=60)
    out = (p.stdout or "").strip()
    if not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        s, e = out.find("["), out.rfind("]")
        data = json.loads(out[s:e + 1]) if s >= 0 else []
    return data if isinstance(data, list) else []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["us", "hk", "cn"], required=True)
    args = ap.parse_args()

    expected = load_expected(args.market)
    if not expected:
        print(f"[reconcile] {args.market}: 今日无期望订单，跳过")
        return 0
    if args.market == "cn":
        print("[reconcile] A 股对账需读取委托表（暂未实现），跳过")
        return 0

    suffix = ".US" if args.market == "us" else ".HK"
    orders = [o for o in _orders() if str(o.get("symbol", "")).endswith(suffix)]
    result = reconcile(expected, orders)
    print(result["summary"])
    for line in result["mismatched"]:
        print(f"  [异常] {line['symbol']} {line['side']} 期望 {line['expected_qty']} 成交 {line['executed_qty']} ({line['status']})")
    for line in result["partial"]:
        print(f"  [部分] {line['symbol']} {line['side']} 期望 {line['expected_qty']} 成交 {line['executed_qty']}")
    for u in result["unexpected"]:
        print(f"  [计划外] {u['symbol']} {u['side']} x{u['count']}")

    out = ROOT / "reports" / f"reconcile_{args.market}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if result["ok"]:
        return 0

    # 休市时「期望有单、实际没有」属于正常：单子根本没发出去（风控/时段门控拦截）。
    # 这种情况不该让整个日更流程判失败，标记为待下一交易日处理。
    missing_only = result["mismatched"] and all(
        str(m.get("status")) == "missing" for m in result["mismatched"])
    try:
        from quant.automation.execution import market_sessions

        sess = market_sessions().get("US" if args.market == "us" else "HK", "")
    except Exception:  # noqa: BLE001 - 查不到时段就按异常处理，保守
        sess = ""
    if missing_only and sess != "Trading":
        print(f"[reconcile] {args.market}: {sess or 'UNKNOWN'} 非交易时段，{len(result['mismatched'])} 笔待下一交易日执行")
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
