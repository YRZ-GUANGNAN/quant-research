"""本地缓存层：行情面板与指数成分股。

存储格式：
- ``data/cache/bars.csv.gz``        个股日线（长表，列 code/date/…）
- ``data/cache/index_bars.csv.gz``  基准指数日线
- ``data/cache/universe_<code>.json`` 指数成分股快照（含抓取时间）

选择 gzip CSV 而不是 parquet：不引入 pyarrow 依赖，300 只 × 2000 日约 60 万行，
读写都在秒级，足够本项目使用。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ..config import CACHE_DIR, ensure_dirs

BARS_FILE = CACHE_DIR / "bars.csv.gz"
INDEX_BARS_FILE = CACHE_DIR / "index_bars.csv.gz"

BARS_COLUMNS = [
    "code", "date", "open", "close", "high", "low", "volume", "amount",
    "amplitude", "pct_chg", "chg", "turnover", "name",
]


def _universe_file(index_code: str) -> Path:
    return CACHE_DIR / f"universe_{str(index_code).zfill(6)}.json"


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"code": str}, parse_dates=["date"])


def _atomic_to_csv(df: pd.DataFrame, path: Path) -> None:
    """先写临时文件再替换，避免中断产生半截缓存。"""
    ensure_dirs()
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False, compression="gzip", float_format="%.4f")
    tmp.replace(path)


def load_bars() -> pd.DataFrame:
    df = _read_csv(BARS_FILE)
    if df.empty:
        return df
    return df.sort_values(["date", "code"]).reset_index(drop=True)


def save_bars(df: pd.DataFrame) -> None:
    df = df.sort_values(["code", "date"])
    _atomic_to_csv(df, BARS_FILE)


def upsert_bars(new: pd.DataFrame) -> pd.DataFrame:
    """合并新行情（同 code+date 以新数据为准，处理前复权重算）。"""
    old = load_bars()
    if old.empty:
        merged = new.copy()
    else:
        merged = pd.concat([old, new], ignore_index=True)
    merged = merged.drop_duplicates(subset=["code", "date"], keep="last")
    save_bars(merged)
    return merged


def load_index_bars() -> pd.DataFrame:
    path = INDEX_BARS_FILE
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, dtype={"index_code": str}, parse_dates=["date"]).sort_values("date")


def save_index_bars(df: pd.DataFrame) -> None:
    df = df.sort_values("date")
    _atomic_to_csv(df, INDEX_BARS_FILE)


def load_universe(index_code: str) -> pd.DataFrame:
    path = _universe_file(index_code)
    if not path.exists():
        return pd.DataFrame()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame(payload.get("members", []))


def save_universe(index_code: str, members: pd.DataFrame) -> None:
    path = _universe_file(index_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "index_code": str(index_code).zfill(6),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "members": members.to_dict(orient="records"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
