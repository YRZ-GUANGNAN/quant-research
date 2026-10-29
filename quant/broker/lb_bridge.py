"""Longbridge CLI Legacy 兼容模块（只读函数保留；broker 写命令已永久阻断）。

`_run()` 只允许历史只读查询（`quote`、`positions`、`assets`、`order` 列表等）；`order buy/sell/cancel/replace` 命令以及所有 execute token 在启动 CLI 前返回 `BLOCKED_LEGACY_EXECUTE`。canonical automation 不得调用本模块。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from quant.broker.legacy_guard import assert_legacy_cli_readonly, block_legacy_broker_write

DEFAULT_BIN = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"
LB_BIN = Path(os.environ.get("LONGBRIDGE_BIN", str(DEFAULT_BIN)))


class LongbridgeError(RuntimeError):
    """CLI 调用失败（未登录、限流、参数错误等）。"""


def _run(args: list[str], *, timeout: int = 40) -> str:
    assert_legacy_cli_readonly(args, "quant.broker.lb_bridge._run")
    if not LB_BIN.exists():
        raise LongbridgeError(f"未找到 longbridge CLI: {LB_BIN}")
    cmd = [str(LB_BIN), *args, "--format", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise LongbridgeError((proc.stderr or proc.stdout or "").strip()[:500])
    return proc.stdout.strip()


def _json(args: list[str], **kw) -> object:
    out = _run(args, **kw)
    if not out:
        return {}
    return json.loads(out)


def auth_status() -> dict:
    try:
        return _json(["auth", "status"])
    except (LongbridgeError, json.JSONDecodeError) as exc:
        return {"authenticated": False, "error": str(exc)}


def assets() -> dict:
    return _json(["assets"])


def positions() -> list:
    return _json(["positions"]) or []


def portfolio() -> dict:
    return _json(["portfolio"])


def quote(symbols: list[str]) -> list:
    return _json(["quote", *symbols]) or []


def kline(symbol: str, start: str, end: str, period: str = "day") -> list:
    return _json(["kline", "history", symbol, "--start", start, "--end", end,
                  "--period", period]) or []


def order_list(history: bool = False) -> list:
    """订单列表：不带子命令即列出当日订单；history=True 返回历史订单。"""
    args = ["order", "--history"] if history else ["order"]
    return _json(args) or []


def order_buy(
    symbol: str,
    quantity: int,
    price: float | None = None,
    order_type: str = "MO",
) -> dict:
    del symbol, quantity, price, order_type
    block_legacy_broker_write("quant.broker.lb_bridge.order_buy")


def order_sell(
    symbol: str,
    quantity: int,
    price: float | None = None,
    order_type: str = "MO",
) -> dict:
    del symbol, quantity, price, order_type
    block_legacy_broker_write("quant.broker.lb_bridge.order_sell")


def region() -> str:
    return os.environ.get("LONGBRIDGE_REGION", "(auto)")
