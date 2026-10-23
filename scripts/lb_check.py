"""Longbridge 连接检查：认证状态、账户、持仓、行情。

用法：
    python scripts/lb_check.py                     # 认证 + 账户 + 持仓
    python scripts/lb_check.py --quote 0700.HK,AAPL.US,600519.SH
    python scripts/lb_check.py --orders            # 订单列表
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant.broker import lb_bridge as lb  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Longbridge 连接检查")
    ap.add_argument("--quote", default="", help="逗号分隔的标的，如 0700.HK,AAPL.US")
    ap.add_argument("--orders", action="store_true")
    args = ap.parse_args()

    print(f"[lb] CLI: {lb.LB_BIN}")
    print(f"[lb] region: {lb.region()}")
    status = lb.auth_status()
    print(f"[auth] {json.dumps(status, ensure_ascii=False)[:300]}")
    if not status.get("authenticated", True) and status.get("error"):
        print("\n[next] 需要授权：见 docs/longbridge-setup.md（OAuth 授权码）")
        return 2

    try:
        acc = lb.assets()
        print(f"\n[assets] {json.dumps(acc, ensure_ascii=False)[:500]}")
    except lb.LongbridgeError as exc:
        print(f"[assets] 失败: {exc}")

    try:
        pos = lb.positions()
        print(f"\n[positions] {len(pos)} 条")
        for p in pos[:10]:
            print("  " + json.dumps(p, ensure_ascii=False)[:160])
    except lb.LongbridgeError as exc:
        print(f"[positions] 失败: {exc}")

    if args.quote:
        symbols = [s.strip() for s in args.quote.split(",") if s.strip()]
        try:
            for q in lb.quote(symbols):
                print("[quote] " + json.dumps(q, ensure_ascii=False)[:200])
        except lb.LongbridgeError as exc:
            print(f"[quote] 失败: {exc}")

    if args.orders:
        try:
            for o in lb.order_list():
                print("[order] " + json.dumps(o, ensure_ascii=False)[:200])
        except lb.LongbridgeError as exc:
            print(f"[order] 失败: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
