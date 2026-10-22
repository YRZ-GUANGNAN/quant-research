"""东方财富（Eastmoney）原始 HTTP 数据适配器。

为什么不用 akshare / tushare 等中介库：
1. 它们是对同一批公开接口的封装，接口一改就集体失效，且报错信息不透明；
2. 本项目只需要「日线 + 指数成分股」两类数据，自己实现可以控制重试、限流、
   增量和复权口径，出问题时能直接定位到 URL 与返回体。

已验证接口（2026-10）：
- 日线 K 线:  push2his.eastmoney.com/api/qt/stock/kline/get
- 指数成分:  push2.eastmoney.com/api/qt/clist/get (fs=b:BK0500，返回 total=300)

字段口径（fields2=f51..f61）：
date, open, close, high, low, volume(手), amount(元), amplitude(%),
pct_chg(%), chg(元), turnover(%)
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

import pandas as pd

from ..config import INDEX_BOARDS

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REFERER = "https://quote.eastmoney.com/"

KLINE_URL = (
    "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    "?secid={secid}&klt={klt}&fqt={fqt}&beg={beg}&end={end}"
    "&fields1=f1,f2,f3,f4,f5,f6"
    "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
)
CLIST_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get"
    "?pn={pn}&pz={pz}&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281"
    "&fltt=2&invt=2&fid=f3&fs=b:{board}&fields=f12,f13,f14"
)

BAR_COLUMNS = [
    "date", "open", "close", "high", "low", "volume", "amount",
    "amplitude", "pct_chg", "chg", "turnover",
]
NUMERIC_COLUMNS = BAR_COLUMNS[1:]


class DataError(RuntimeError):
    """数据接口不可用，或返回结构与预期不符。"""


def _http_json(url: str, *, retries: int = 4, timeout: float = 20.0) -> dict[str, Any]:
    """带指数退避重试的 GET JSON。"""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": REFERER})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8", errors="replace")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise DataError(f"响应不是 JSON 对象: {url}")
            return payload
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise DataError(f"请求失败（已重试 {retries} 次）{url}: {last}")


def market_of(code: str) -> str:
    """按代码首位判断交易市场：sh / sz / bj。"""
    code = str(code).zfill(6)
    if code[0] in {"5", "6", "9"}:
        return "sh"
    if code[0] in {"0", "1", "2", "3"}:
        return "sz"
    if code[0] in {"4", "8"}:
        return "bj"
    raise ValueError(f"无法判断市场: {code}")


def to_secid(code: str, market: str | None = None) -> str:
    """拼东方财富 secid：沪市前缀 1，深市/北交所前缀 0。"""
    market = market or market_of(code)
    prefix = "1" if market == "sh" else "0"
    return f"{prefix}.{str(code).zfill(6)}"


def _parse_klines(klines: list[str], code: str, name: str = "") -> pd.DataFrame:
    if not klines:
        raise DataError(f"{code} 返回空 K 线")
    rows = [line.split(",") for line in klines]
    bad = [r for r in rows if len(r) != len(BAR_COLUMNS)]
    if bad:
        raise DataError(f"{code} K 线字段数异常，首行={bad[0]}")
    df = pd.DataFrame(rows, columns=BAR_COLUMNS)
    for col in NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d")
    df.insert(0, "code", str(code).zfill(6))
    if name:
        df["name"] = name
    return df.sort_values("date").reset_index(drop=True)


def fetch_kline(
    code: str,
    start: str = "2018-01-01",
    end: str = "",
    *,
    fqt: int = 1,
    klt: int = 101,
    market: str | None = None,
) -> pd.DataFrame:
    """抓取单只股票日线。

    fqt: 0=不复权, 1=前复权, 2=后复权。默认前复权（含分红再投，适合算收益）。
    klt: 101=日线。
    """
    begin = pd.Timestamp(start).strftime("%Y%m%d")
    finish = pd.Timestamp(end).strftime("%Y%m%d") if end else "20500101"
    url = KLINE_URL.format(
        secid=to_secid(code, market), klt=klt, fqt=fqt, beg=begin, end=finish
    )
    payload = _http_json(url)
    data = payload.get("data")
    if not data or not data.get("klines"):
        raise DataError(f"{code} 无行情数据（可能已退市或代码错误）")
    return _parse_klines(data["klines"], code, str(data.get("name") or ""))


def fetch_index_kline(index_code: str, start: str = "2018-01-01", end: str = "") -> pd.DataFrame:
    """抓取指数日线（指数的 secid 固定为 1.<code>）。"""
    begin = pd.Timestamp(start).strftime("%Y%m%d")
    finish = pd.Timestamp(end).strftime("%Y%m%d") if end else "20500101"
    url = KLINE_URL.format(
        secid=f"1.{str(index_code).zfill(6)}", klt=101, fqt=0, beg=begin, end=finish
    )
    payload = _http_json(url)
    data = payload.get("data")
    if not data or not data.get("klines"):
        raise DataError(f"指数 {index_code} 无行情数据")
    df = _parse_klines(data["klines"], index_code, str(data.get("name") or ""))
    return df.rename(columns={"code": "index_code"})


def fetch_index_constituents(index_code: str, *, page_size: int = 500) -> pd.DataFrame:
    """抓取指数当前成分股。返回列：code, market, name。

    注意：这是**当前**成分股，不含历史调仓记录，回测存在幸存者偏差。
    """
    board = INDEX_BOARDS.get(str(index_code).zfill(6))
    if not board:
        raise ValueError(f"未知指数 {index_code}，请在 config.INDEX_BOARDS 中登记板块代码")
    rows: list[dict[str, Any]] = []
    page = 1
    total = None
    while True:
        url = CLIST_URL.format(pn=page, pz=page_size, board=board)
        payload = _http_json(url)
        data = payload.get("data")
        if not data:
            raise DataError(f"指数 {index_code} 成分股接口返回空")
        total = int(data.get("total", 0))
        diff = data.get("diff") or []
        if not diff:
            break
        for item in diff:
            rows.append(
                {
                    "code": str(item.get("f12", "")).zfill(6),
                    "market": "sh" if item.get("f13") == 1 else "sz",
                    "name": str(item.get("f14", "")),
                }
            )
        if total is not None and len(rows) >= total:
            break
        page += 1
        time.sleep(0.05)
    if not rows:
        raise DataError(f"指数 {index_code} 成分股为空")
    return pd.DataFrame(rows).drop_duplicates("code").reset_index(drop=True)
