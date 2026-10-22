"""本地缓存层：行情面板、指数行情与时点成分股（多市场）。

文件命名：
- ``bars.csv.gz`` / ``index_bars.csv.gz`` / ``members_000300.csv.gz``   A 股（兼容旧路径）
- ``bars_<market>.csv.gz`` / ``index_bars_<market>.csv.gz``             美股 / 港股 / 加密

选择 gzip CSV 而不是 parquet：不引入 pyarrow 依赖，读写秒级。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..config import CACHE_DIR, ensure_dirs

BARS_FILE = CACHE_DIR / "bars.csv.gz"
INDEX_BARS_FILE = CACHE_DIR / "index_bars.csv.gz"

BARS_COLUMNS = [
    "code", "date", "open", "high", "low", "close", "volume", "amount",
    "pct_chg", "turnover", "name",
]


def bars_file(market: str = "cn") -> Path:
    return BARS_FILE if market == "cn" else CACHE_DIR / f"bars_{market}.csv.gz"


def index_bars_file(market: str = "cn") -> Path:
    return INDEX_BARS_FILE if market == "cn" else CACHE_DIR / f"index_bars_{market}.csv.gz"


def members_file(index_code: str) -> Path:
    return CACHE_DIR / f"members_{str(index_code).zfill(6)}.csv.gz"


def _read_csv(path: Path, date_cols: list[str] | None = None, code: bool = True) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    dtype = {"code": str} if code else None
    return pd.read_csv(path, dtype=dtype, parse_dates=date_cols or [])


def _atomic_to_csv(df: pd.DataFrame, path: Path) -> None:
    """先写临时文件再替换，避免中断产生半截缓存。"""
    ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False, compression="gzip", float_format="%.6f")
    tmp.replace(path)


# ---------- 个股行情 ----------

def load_bars(market: str = "cn") -> pd.DataFrame:
    df = _read_csv(bars_file(market), ["date"])
    if df.empty:
        return df
    return df.sort_values(["date", "code"]).reset_index(drop=True)


def save_bars(df: pd.DataFrame, market: str = "cn") -> None:
    _atomic_to_csv(df.sort_values(["code", "date"]), bars_file(market))


def upsert_bars(new: pd.DataFrame, market: str = "cn") -> pd.DataFrame:
    """合并新行情；同 code+date 以新数据为准（复权价会随分红/拆股重算）。"""
    old = load_bars(market)
    merged = new.copy() if old.empty else pd.concat([old, new], ignore_index=True)
    merged = merged.drop_duplicates(subset=["code", "date"], keep="last")
    save_bars(merged, market)
    return merged


# ---------- 指数行情 ----------

def load_index_bars(market: str = "cn") -> pd.DataFrame:
    path = index_bars_file(market)
    if not path.exists():
        return pd.DataFrame()
    return (
        pd.read_csv(path, dtype={"index_code": str}, parse_dates=["date"])
        .sort_values("date")
        .reset_index(drop=True)
    )


def save_index_bars(df: pd.DataFrame, market: str = "cn") -> None:
    _atomic_to_csv(df.sort_values("date"), index_bars_file(market))


# ---------- 时点成分股（A 股专用） ----------

def load_members(index_code: str = "000300") -> pd.DataFrame:
    df = _read_csv(members_file(index_code), ["snapshot_date"])
    if df.empty:
        return df
    return df.drop_duplicates(["snapshot_date", "code"]).reset_index(drop=True)


def save_members(index_code: str, df: pd.DataFrame) -> None:
    _atomic_to_csv(
        df.sort_values(["snapshot_date", "code"]).drop_duplicates(["snapshot_date", "code"]),
        members_file(index_code),
    )
