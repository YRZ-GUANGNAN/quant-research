"""Longbridge Legacy SDK 兼容模块。

交易侧仅保留返回数据的只读 wrapper；行情侧暂保留 `quote_context()` 兼容入口。本模块不导出 `TradeContext`/paper `Config` factory；`submit_order()` 及任何非 allowlist trade method 都在读取凭证或导入 SDK 前抛出 `LegacyBrokerWriteBlocked(exit_code=20)`。canonical automation 不得导入本模块。
"""

from __future__ import annotations

import os
from pathlib import Path

from quant.broker.legacy_guard import block_legacy_broker_write

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"

ENV_KEYS = ("LONGBRIDGE_APP_KEY", "LONGBRIDGE_APP_SECRET", "LONGBRIDGE_ACCESS_TOKEN")


def load_env(path: Path | None = None) -> dict[str, str]:
    """读取 .env（若存在），不覆盖已有环境变量；返回三个凭据（缺失为空串）。"""
    path = path or ENV_FILE
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    return {k: os.environ.get(k, "") for k in ENV_KEYS}


def credential_status() -> dict[str, object]:
    """只返回是否齐全与长度，绝不返回内容。"""
    env = load_env()
    return {
        "app_key_len": len(env["LONGBRIDGE_APP_KEY"]),
        "app_secret_len": len(env["LONGBRIDGE_APP_SECRET"]),
        "access_token_len": len(env["LONGBRIDGE_ACCESS_TOKEN"]),
        "ready": all(env[k] for k in ENV_KEYS),
        "env_file": str(ENV_FILE),
        "env_file_exists": ENV_FILE.exists(),
    }


def _config():
    from longbridge.openapi import Config

    env = load_env()
    if not all(env[k] for k in ENV_KEYS):
        raise RuntimeError("缺少 LONGBRIDGE_APP_KEY / APP_SECRET / ACCESS_TOKEN（见 .env）")
    # 注意：SDK 的 Config 不能直接构造，必须走 from_apikey 工厂方法
    return Config.from_apikey(
        app_key=env["LONGBRIDGE_APP_KEY"],
        app_secret=env["LONGBRIDGE_APP_SECRET"],
        access_token=env["LONGBRIDGE_ACCESS_TOKEN"],
    )


def quote_context():
    from longbridge.openapi import QuoteContext

    return QuoteContext(_config())


_ALLOWED_TRADE_READ_METHODS = frozenset(
    {"account_balance", "stock_positions", "today_orders"}
)


def _call_trade_read(method_name: str) -> list:
    if method_name not in _ALLOWED_TRADE_READ_METHODS:
        block_legacy_broker_write(f"quant.broker.lb_sdk.{method_name}")
    from longbridge.openapi import TradeContext

    context = TradeContext(_config())
    return getattr(context, method_name)()


def account_balance() -> list:
    return _call_trade_read("account_balance")


def positions() -> list:
    return _call_trade_read("stock_positions")


def today_orders() -> list:
    return _call_trade_read("today_orders")


def submit_order(
    symbol: str,
    side: str,
    quantity: int,
    price: float | None = None,
    order_type: str = "LO",
    remark: str = "quant-research",
):
    """Block the superseded Legacy SDK write path before reading credentials."""
    del symbol, side, quantity, price, order_type, remark
    block_legacy_broker_write("quant.broker.lb_sdk.submit_order")
