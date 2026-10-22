"""BaoStock 数据适配器（主数据源）。

为什么换到 BaoStock（2026-10 实测）：
1. 东方财富在并发抓取约 5 只后开始对客户端返回 ``Remote end closed connection``，
   连行情快照也拒绝，属于按客户端限流，不适合批量。
2. BaoStock 一次调用返回完整历史（2018-2026 共 2134 行），且自带
   ``amount``（成交额）、``turn``（换手率）、``pctChg``（涨跌幅），不用自己拼。
3. **关键**：``query_hs300_stocks(date)`` 能查历史时点的沪深300 成分股，
   配合 use_pit 可直接消除幸存者偏差——这是东财接口给不了的。

接口限制：
- BaoStock 是全局会话，非线程安全，必须单线程顺序抓取；
- 需要先 login（约 1 秒），失败会抛 DataError；
- 字段以字符串返回，空值需转 NaN。

字段口径：
- volume: 股（不是手）
- amount: 元
- turn: 换手率 %
- pctChg: 涨跌幅 %
- adjustflag="2" 前复权, "1" 后复权, "3" 不复权
"""

from __future__ import annotations

import time

import pandas as pd

try:  # pragma: no cover - 依赖缺失时给出可读错误
    import baostock as bs
except ImportError as exc:  # pragma: no cover
    raise ImportError("请先安装 baostock: python -m pip install baostock") from exc

SOCKET_TIMEOUT = 25.0   # 秒；BaoStock 无内置超时，必须外部兜底

KLINE_FIELDS = "date,open,high,low,close,volume,amount,turn,pctChg"
NUMERIC = ["open", "high", "low", "close", "volume", "amount", "turn", "pctChg"]
RENAME = {"turn": "turnover", "pctChg": "pct_chg"}


class DataError(RuntimeError):
    """BaoStock 登录失败或查询返回错误。"""


def to_bs_code(code: str) -> str:
    """把 6 位代码转成 baostock 格式：sh.600000 / sz.000001。"""
    code = str(code).zfill(6)
    if code[0] in {"5", "6", "9"}:
        return f"sh.{code}"
    if code[0] in {"0", "1", "2", "3"}:
        return f"sz.{code}"
    raise ValueError(f"无法判断市场: {code}")


class BaoStockClient:
    """带会话管理的 BaoStock 客户端，用 with 语句确保 logout。"""

    def __init__(self, *, verbose: bool = False) -> None:
        self.verbose = verbose
        self._session = None

    # ---------- 会话 ----------

    def __enter__(self) -> "BaoStockClient":
        # BaoStock 的 socket 读没有超时，服务端卡住会永久挂起，
        # 这里给全局 socket 设一个兜底超时，配合外层重试。
        import socket

        self._prev_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(SOCKET_TIMEOUT)
        try:
            result = bs.login()
            if result.error_code != "0":
                raise DataError(f"BaoStock 登录失败: {result.error_code} {result.error_msg}")
        except Exception:
            socket.setdefaulttimeout(self._prev_timeout)
            raise
        self._session = bs
        return self

    def __exit__(self, *exc_info) -> None:
        import socket

        try:
            bs.logout()
        finally:
            self._session = None
            socket.setdefaulttimeout(getattr(self, "_prev_timeout", None))

    @property
    def session(self):
        if self._session is None:
            raise RuntimeError("BaoStockClient 必须在 with 语句中使用")
        return self._session

    # ---------- 行情 ----------

    def fetch_daily(
        self,
        code: str,
        start: str = "2018-01-01",
        end: str = "2050-12-31",
        *,
        adjustflag: str = "2",
    ) -> pd.DataFrame:
        """抓取单只股票日线（默认前复权）。"""
        bs_code = to_bs_code(code)
        rs = self.session.query_history_k_data_plus(
            bs_code,
            KLINE_FIELDS,
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag=adjustflag,
        )
        if rs.error_code != "0":
            raise DataError(f"{code} 查询失败: {rs.error_code} {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=rs.fields)
        for col in NUMERIC:
            df[col] = pd.to_numeric(df[col].replace("", "nan"), errors="coerce")
        df = df.rename(columns=RENAME)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        df.insert(0, "code", str(code).zfill(6))
        keep = ["code", "date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]
        return df[keep].sort_values("date").reset_index(drop=True)

    def fetch_index(
        self,
        index_code: str = "000300",
        start: str = "2018-01-01",
        end: str = "2050-12-31",
    ) -> pd.DataFrame:
        """抓取指数日线（指数代码形如 sh.000300）。"""
        bs_code = f"sh.{str(index_code).zfill(6)}"
        rs = self.session.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount,pctChg",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="3",
        )
        if rs.error_code != "0":
            raise DataError(f"指数 {index_code} 查询失败: {rs.error_code} {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            raise DataError(f"指数 {index_code} 无数据")
        df = pd.DataFrame(rows, columns=rs.fields)
        for col in ["open", "high", "low", "close", "volume", "amount", "pctChg"]:
            df[col] = pd.to_numeric(df[col].replace("", "nan"), errors="coerce")
        df = df.rename(columns={"pctChg": "pct_chg", "code": "index_code"})
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df.insert(0, "index_code", str(index_code).zfill(6))
        keep = ["index_code", "date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]
        return df[keep].dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

    # ---------- 指数成分 ----------

    def fetch_hs300_members_snapshot(self, date: str) -> pd.DataFrame:
        """查询某一时点的沪深300 成分股。返回列：snapshot_date, code, name。"""
        rs = self.session.query_hs300_stocks(date)
        if rs.error_code != "0":
            raise DataError(f"成分股 {date} 查询失败: {rs.error_code} {rs.error_msg}")
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows, columns=rs.fields)
        # baostock 返回字段: updateDate, code, code_name
        out = pd.DataFrame(
            {
                "snapshot_date": pd.to_datetime(df["updateDate"]),
                "code": df["code"].str.split(".").str[-1].str.zfill(6),
                "name": df["code_name"],
            }
        )
        return out.drop_duplicates("code").reset_index(drop=True)

    def close(self) -> None:
        """显式退出（幂等）。"""
        try:
            bs.logout()
        except Exception:  # noqa: BLE001
            pass
        self._session = None


def fetch_daily(code: str, start: str = "2018-01-01", end: str = "2050-12-31") -> pd.DataFrame:
    """一次性抓取（内部自动登录/退出），适合少量调试。"""
    with BaoStockClient() as client:
        df = client.fetch_daily(code, start, end)
        time.sleep(0.02)
        return df
