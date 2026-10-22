"""腾讯行情适配器（A 股日线，前复权）。

为什么需要它：BaoStock 在并发会话后会对客户端限流（返回乱码/超时），
而腾讯接口在实测中稳定、单次 640 根、支持前复权，适合批量补齐 A 股行情。

与 BaoStock 的口径差异（必须记录，避免混用）：
- Tencent 返回 6 列：date, open, close, high, low, volume（注意顺序是开、收、高、低）
- volume 单位为**手**，这里统一 ×100 转成**股**，与 BaoStock 一致
- **没有 amount / turnover**：用 ``amount ≈ close × volume`` 近似（VWAP≈收盘价），
  turnover 置 NaN。这会损失换手率类因子，报告中必须标注。
- ``pct_chg`` 用复权收盘价差分计算（除权日会有微小偏差）

接口：https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=<symbol>,day,<start>,<end>,<count>,<adjust>
单次最多 640 根，因此按时间倒序翻页。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

import pandas as pd

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
TX_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    "?param={symbol},day,{start},{end},{count},{adjust}"
)

PAGE_SIZE = 640
MAX_PAGES = 12


class DataError(RuntimeError):
    """腾讯接口不可用或返回结构异常。"""


def tx_symbol(code: str) -> str:
    code = str(code).zfill(6)
    if code[0] in {"5", "6", "9"}:
        return f"sh{code}"
    if code[0] in {"0", "1", "2", "3"}:
        return f"sz{code}"
    raise ValueError(f"无法判断市场: {code}")


def _http_json(url: str, *, retries: int = 3, timeout: float = 15.0) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise DataError(f"腾讯请求失败（重试 {retries} 次）{url}: {last}")


def fetch_daily(
    code: str,
    start: str = "2018-01-01",
    end: str = "2050-12-31",
    *,
    adjust: str = "qfq",
    count: int = PAGE_SIZE,
) -> pd.DataFrame:
    """抓取单只 A 股日线（默认前复权），自动向前翻页补齐历史。"""
    symbol = tx_symbol(code)
    start_s = pd.Timestamp(start).strftime("%Y-%m-%d")
    cur_end = pd.Timestamp(end).strftime("%Y-%m-%d")
    rows: list[list[str]] = []

    for _ in range(MAX_PAGES):
        url = TX_URL.format(symbol=symbol, start=start_s, end=cur_end, count=count, adjust=adjust)
        payload = _http_json(url)
        data = (payload.get("data") or {}).get(symbol)
        if not data:
            break
        key = f"{adjust}day" if adjust else "day"
        arr = data.get(key) or data.get("day") or data.get("qfqday") or []
        if not arr:
            break
        rows = [list(r) for r in arr] + rows
        first_date = arr[0][0]
        if first_date <= start_s:
            break
        cur_end = (pd.Timestamp(first_date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if cur_end < start_s:
            break
        time.sleep(0.02)

    if not rows:
        raise DataError(f"{code} 无行情数据")

    df = pd.DataFrame([r[:6] for r in rows], columns=["date", "open", "close", "high", "low", "volume"])
    for col in ["open", "close", "high", "low", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).drop_duplicates("date").sort_values("date")
    df = df[df["date"] >= pd.Timestamp(start_s)]
    df["volume"] = df["volume"] * 100.0          # 手 -> 股
    df["amount"] = df["close"] * df["volume"]    # 近似成交额
    df["pct_chg"] = df["close"].pct_change() * 100.0
    df["turnover"] = float("nan")
    df.insert(0, "code", str(code).zfill(6))
    keep = ["code", "date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]
    return df[keep].reset_index(drop=True)
