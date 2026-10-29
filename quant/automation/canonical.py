"""Canonical 序列化与内容哈希（P1 Task 2）。

设计约束（来自方案 B 设计 8.3）：
- JSON 键排序、Unicode NFC 归一化、无空白差异；
- 拒绝 NaN/Infinity（避免不可复现的浮点表示）；
- Decimal 用定点字符串，缺失值用显式 tag；
- 表格按 key_columns 排序后逐行编码，dtype/schema 变化必须改变哈希。
"""
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd

MISSING_TAG = {"__missing__": True}


class CanonicalError(ValueError):
    """输入无法被 canonical 化时抛出。"""


def _normalize(value: Any) -> Any:
    if value is None:
        return MISSING_TAG
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise CanonicalError("拒绝 NaN/Infinity：无法稳定复现")
        return value
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise CanonicalError("JSON 键必须是字符串")
            out[unicodedata.normalize("NFC", k)] = _normalize(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise CanonicalError(f"不支持的 canonical 类型: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """把任意 JSON 值编码为稳定的 UTF-8 字节。"""
    normalized = _normalize(value)
    text = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return text.encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    if not isinstance(payload, (bytes, bytearray)):
        raise CanonicalError("sha256_hex 只接受 bytes")
    return hashlib.sha256(payload).hexdigest()


def canonical_table_bytes(
    frame: pd.DataFrame,
    *,
    columns: Sequence[str],
    key_columns: Sequence[str],
    schema_name: str,
    schema_version: int,
) -> bytes:
    """把表格编码为稳定字节：先按 key 排序，再逐行 JSON。"""
    missing = [c for c in [*columns, *key_columns] if c not in frame.columns]
    if missing:
        raise CanonicalError(f"缺少列: {missing}")
    sub = frame.loc[:, list(dict.fromkeys([*columns, *key_columns]))].copy()
    sub = sub.sort_values(list(key_columns), kind="mergesort").reset_index(drop=True)
    rows = []
    for record in sub.to_dict(orient="records"):
        row = {}
        for col in columns:
            val = record.get(col)
            if val is None or (isinstance(val, float) and math.isnan(val)):
                row[col] = MISSING_TAG
            elif isinstance(val, pd.Timestamp):
                row[col] = val.isoformat()
            else:
                row[col] = _normalize(val)
        rows.append(row)
    header = {"schema_name": schema_name, "schema_version": schema_version,
              "columns": list(columns), "key_columns": list(key_columns)}
    return canonical_json_bytes({"header": header, "rows": rows})


def hash_file_set(root: Path | str, relative_paths: Iterable[str]) -> str:
    """对一组文件做稳定哈希（路径排序 + 内容哈希组合），不依赖 mtime。"""
    root = Path(root)
    entries = []
    for rel in sorted(relative_paths):
        p = root / rel
        entries.append({"path": str(rel), "sha256": sha256_hex(p.read_bytes())})
    return sha256_hex(canonical_json_bytes({"files": entries}))
