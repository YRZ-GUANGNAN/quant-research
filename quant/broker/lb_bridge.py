"""Longbridge CLI 桥接：认证状态、行情、账户、下单。

设计选择：走官方 CLI（`longbridge` v0.30.0）而不是 Python SDK。
原因：
1. 认证用 OAuth，token 由 CLI 管理在 `~/.longbridge/openapi/tokens/`，
   我们的代码永远不接触 token 文件（安全边界清晰）；
2. CLI 输出 `--format json`，适合被脚本调用；
3. 换机器/换账号只需重新 `longbridge auth login`，代码零改动。

安装（已在本机完成）：`%LOCALAPPDATA%\\Programs\\longbridge\\longbridge.exe`

用法：
    python scripts/lb_check.py            # 认证状态 + 账户概览
    python scripts/lb_check.py --quote 0700.HK,AAPL.US
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

DEFAULT_BIN = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"
LB_BIN = Path(os.environ.get("LONGBRIDGE_BIN", str(DEFAULT_BIN)))


class LongbridgeError(RuntimeError):
    """CLI 调用失败（未登录、限流、参数错误等）。"""


def _run(args: list[str], *, timeout: int = 40) -> str:
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


def order_list() -> list:
    return _json(["order", "list"]) or []


def order_buy(symbol: str, quantity: int, price: float | None = None,
              order_type: str = "MO") -> dict:
    """买入。order_type: MO=市价单，LO=限价单（需 price）。"""
    args = ["order", "buy", symbol, "--quantity", str(quantity), "--order-type", order_type]
    if price is not None:
        args += ["--price", f"{price:.4f}"]
    return _json(args)


def order_sell(symbol: str, quantity: int, price: float | None = None,
               order_type: str = "MO") -> dict:
    args = ["order", "sell", symbol, "--quantity", str(quantity), "--order-type", order_type]
    if price is not None:
        args += ["--price", f"{price:.4f}"]
    return _json(args)


def region() -> str:
    return os.environ.get("LONGBRIDGE_REGION", "(auto)")
