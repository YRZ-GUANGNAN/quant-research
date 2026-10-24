"""Longbridge Python SDK 交易通道（App Key / App Secret / Access Token）。

与 `lb_bridge`（CLI，OAuth 行情）的分工：
- `lb_bridge`：OAuth 行情（已验证可用），不需要开发者凭据；
- `lb_sdk`  ：SDK 交易通道，需要开发者控制台的 App Key / Secret / Access Token
  （这是账户持有人才能提供的东西，动态注册的 OAuth client 拿不到交易 scope）。

凭据从环境变量或项目根目录 `.env` 读取：
    LONGBRIDGE_APP_KEY=...
    LONGBRIDGE_APP_SECRET=...
    LONGBRIDGE_ACCESS_TOKEN=...

安全：本模块不打印凭据内容；`.env` 已在 .gitignore 中。
"""

from __future__ import annotations

import os
from pathlib import Path

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


def trade_context():
    from longbridge.openapi import TradeContext

    return TradeContext(_config())


def quote_context():
    from longbridge.openapi import QuoteContext

    return QuoteContext(_config())


def account_balance() -> list:
    return trade_context().account_balance()


def positions() -> list:
    return trade_context().stock_positions()


def today_orders() -> list:
    return trade_context().today_orders()


def submit_order(symbol: str, side: str, quantity: int, price: float | None = None,
                 order_type: str = "LO", remark: str = "quant-research"):
    """提交委托。side: buy/sell；order_type: LO 限价 / MO 市价。

    SDK 的 SubmitOrderOptions 参数名在不同版本略有差异，这里做兼容构造。
    """
    from decimal import Decimal

    import longbridge.openapi as api

    opts_cls = api.SubmitOrderOptions
    side_enum = api.OrderSide.Buy if side.lower() == "buy" else api.OrderSide.Sell
    type_enum = api.OrderType.LO if order_type.upper() == "LO" else api.OrderType.MO
    tif = api.TimeInForceType.Day
    candidates = [
        dict(symbol=symbol, order_type=type_enum, side=side_enum,
             submitted_quantity=Decimal(quantity), time_in_force=tif,
             submitted_price=Decimal(str(price)) if price is not None else None,
             remark=remark),
        dict(symbol=symbol, order_type=type_enum, side=side_enum,
             submitted_quantity=quantity, time_in_force=tif,
             submitted_price=price, remark=remark),
    ]
    last_error: Exception | None = None
    for kwargs in candidates:
        try:
            opts = opts_cls(**kwargs)
            return trade_context().submit_order(opts)
        except TypeError as exc:
            last_error = exc
            continue
    raise RuntimeError(f"SubmitOrderOptions 构造失败: {last_error}")


def paper_config():
    """构造「模拟盘」连接的 Config（enable_papertrading=True）。

    用于在真实下单前确认 SDK 能连上模拟盘账户；连不上即拒绝交易（fail-closed）。
    """
    from pathlib import Path as _P
    from longbridge.openapi import Config

    env = {}
    root = _P(__file__).resolve().parents[2]
    candidates = [root / "tools" / ".env", root / ".env", _P.home() / "quant-research" / ".env"]
    env_file = next((c for c in candidates if c.exists()), None)
    if env_file is None:
        raise FileNotFoundError(f"未找到 .env（尝试: {[str(c) for c in candidates]}）")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return Config.from_apikey(
        app_key=env["LONGBRIDGE_APP_KEY"],
        app_secret=env["LONGBRIDGE_APP_SECRET"],
        access_token=env["LONGBRIDGE_ACCESS_TOKEN"],
        enable_papertrading=True,
    )
