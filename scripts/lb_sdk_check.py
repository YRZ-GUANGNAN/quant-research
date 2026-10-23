"""Longbridge SDK 交易通道自检：凭据是否齐全、账户/持仓/订单是否可读。

用法：
    python scripts/lb_sdk_check.py
    python scripts/lb_sdk_check.py --quote 700.HK,AAPL.US
    python scripts/lb_sdk_check.py --order-preview 700.HK buy 100 380
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant.broker import lb_sdk  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Longbridge SDK 交易通道自检")
    ap.add_argument("--quote", default="")
    ap.add_argument("--order-preview", nargs=4, metavar=("SYMBOL", "SIDE", "QTY", "PRICE"))
    args = ap.parse_args()

    status = lb_sdk.credential_status()
    print("[env] " + ", ".join(f"{k}={v}" for k, v in status.items()))

    if not status["ready"]:
        print("\n[next] 在项目根目录创建 .env，写入三行：")
        print("  LONGBRIDGE_APP_KEY=...\n  LONGBRIDGE_APP_SECRET=...\n  LONGBRIDGE_ACCESS_TOKEN=...")
        print("凭据来自 Longbridge OpenAPI 控制台（应用需勾选账户+交易权限）。")
        return 2

    for name, fn in [("account_balance", lb_sdk.account_balance),
                     ("positions", lb_sdk.positions),
                     ("today_orders", lb_sdk.today_orders)]:
        try:
            result = fn()
            print(f"[{name}] OK: {str(result)[:300]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{name}] FAIL: {str(exc)[:200]}")

    if args.quote:
        try:
            ctx = lb_sdk.quote_context()
            symbols = [s.strip() for s in args.quote.split(",") if s.strip()]
            print(f"[quote] {ctx.quote(symbols)}")
        except Exception as exc:  # noqa: BLE001
            print(f"[quote] FAIL: {str(exc)[:200]}")

    if args.order_preview:
        symbol, side, qty, price = args.order_preview
        print(f"[order-preview] {side} {symbol} {qty} @ {price}（仅预览，不提交）")
        print("  真实下单请用 scripts/lb_trade.py（待接入目标组合映射）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
