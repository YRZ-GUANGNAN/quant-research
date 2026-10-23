"""Yahoo Finance 行情适配器（美股 / 港股 / 加密货币）。

为什么用 Yahoo 覆盖非 A 股市场：
- 一个接口同时拿到美股、港股、加密的 10 年日线，且带复权价（adjclose）；
- 东财对港股/美股同样限流，Tencent 需要翻页，Yahoo 单次返回全部历史；
- A 股仍用 BaoStock（有涨跌停、时点成分股等 A 股特有信息）。

注意：
- Yahoo 返回的是**复权后**价格（adjclose），open/high/low 用同一复权因子缩放；
- 加密货币 7×24，时间戳按 UTC 日期归并；
- 港股每手股数因股而异，引擎里统一按 100 股近似（见 config.MARKET_RULES）。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?range={range}&interval={interval}&events=div%2Csplits"
)

# 市场基准
BENCHMARKS: dict[str, str] = {
    "us": "SPY",
    "hk": "^HSI",
    "sg": "^STI",
    "crypto": "BTC-USD",
}


class DataError(RuntimeError):
    """Yahoo 接口不可用或返回结构异常。"""


def _http_json(url: str, *, retries: int = 3, timeout: float = 25.0) -> dict:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = exc
            time.sleep(0.8 * (attempt + 1))
    raise DataError(f"Yahoo 请求失败（重试 {retries} 次）{url}: {last}")


def fetch_chart(symbol: str, range_: str = "10y", interval: str = "1d") -> pd.DataFrame:
    """抓取单只标的日线，返回标准列。

    返回列：code, date, open, high, low, close, volume, amount, pct_chg, turnover
    其中 amount = close × volume（Yahoo 无成交额字段，按收盘价近似），
    turnover 无数据（置 NaN），仅 A 股因子依赖它。
    """
    url = CHART_URL.format(symbol=urllib.parse.quote(symbol), range=range_, interval=interval)
    payload = _http_json(url)
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise DataError(f"{symbol} 返回错误: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise DataError(f"{symbol} 无数据")
    res = results[0]
    ts = res.get("timestamp") or []
    if not ts:
        raise DataError(f"{symbol} 时间戳为空")
    quote = (res.get("indicators", {}).get("quote") or [{}])[0]
    adj = (res.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose")

    idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert(None).normalize()
    df = pd.DataFrame(
        {
            "date": idx,
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
        }
    )
    if adj is not None:
        df["adjclose"] = adj
    df = df.dropna(subset=["date", "close"])
    df = df[df["close"] > 0]

    # 用 adjclose/close 作为复权因子，统一缩放到放价格
    if "adjclose" in df.columns and not df["adjclose"].isna().all():
        factor = (df["adjclose"] / df["close"]).fillna(1.0)
        for col in ("open", "high", "low", "close"):
            df[col] = df[col] * factor
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    df["amount"] = df["close"] * df["volume"]
    df["pct_chg"] = df["close"].pct_change() * 100.0
    df["turnover"] = float("nan")
    df.insert(0, "code", symbol)
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    keep = ["code", "date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]
    return df[keep]


def fetch_benchmark(market: str, range_: str = "10y") -> pd.DataFrame:
    """抓取市场基准指数。"""
    symbol = BENCHMARKS.get(market)
    if not symbol:
        raise ValueError(f"市场 {market} 未配置基准")
    df = fetch_chart(symbol, range_=range_)
    df = df.rename(columns={"code": "index_code"})
    df["index_code"] = market
    return df


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
