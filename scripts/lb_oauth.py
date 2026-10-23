"""用自注册的 OAuth 客户端完成授权并测试交易接口（无需用户提供密钥）。

我在 Longbridge 的 OAuth 动态注册端点自己注册了公开客户端：
  client_id = ae36bff8-94c4-4bfe-a59c-8983a4362987
  redirect  = http://localhost:60355/callback
  auth      = none（公开客户端，无需 client_secret）

本脚本用 SDK 启动本地回调服务器，打印授权 URL；用户在浏览器确认后，
SDK 自动完成 token 交换，随后测试 TradeContext 的账户/持仓接口。

用法：
    python scripts/lb_oauth.py [client_id]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longbridge.openapi import Config, OAuthBuilder, TradeContext  # noqa: E402

DEFAULT_CLIENT_ID = "ae36bff8-94c4-4bfe-a59c-8983a4362987"


def main() -> int:
    client_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CLIENT_ID
    print(f"[oauth] client_id={client_id}", flush=True)

    def on_url(url: str) -> None:
        print(f"OPEN_URL: {url}", flush=True)

    print("[oauth] 启动本地回调服务器（localhost:60355），等待浏览器授权 ...", flush=True)
    oauth = OAuthBuilder(client_id).build(on_url)
    print("[oauth] 授权完成，token 已获取", flush=True)

    config = Config.from_oauth(oauth)
    trade = TradeContext(config)

    for name, fn in [
        ("account_balance", lambda: trade.account_balance()),
        ("stock_positions", lambda: trade.stock_positions()),
    ]:
        try:
            print(f"[trade] {name}: {fn()}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[trade] {name} FAIL: {str(exc)[:200]}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
