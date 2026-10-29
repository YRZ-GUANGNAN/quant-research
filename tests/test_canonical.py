from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from quant.automation.canonical import (
    CanonicalError,
    canonical_json_bytes,
    canonical_table_bytes,
    sha256_hex,
)


def test_key_order_and_nfc_are_stable() -> None:
    a = canonical_json_bytes({"b": 1, "a": unicodedata_nfc()})
    b = canonical_json_bytes({"a": unicodedata_nfc(), "b": 1})
    assert a == b


def unicodedata_nfc() -> str:
    return "café"


def test_nfd_and_nfc_hash_equal() -> None:
    assert canonical_json_bytes({"x": "cafe\u0301"}) == canonical_json_bytes({"x": "café"})


def test_nan_rejected() -> None:
    with pytest.raises(CanonicalError):
        canonical_json_bytes({"x": float("nan")})


def test_decimal_is_fixed_point_string() -> None:
    assert canonical_json_bytes({"x": Decimal("1.2300")}) == b'{"x":"1.2300"}'


def test_missing_tag() -> None:
    assert canonical_json_bytes({"x": None}) == b'{"x":{"__missing__":true}}'


def test_table_row_order_independent() -> None:
    df1 = pd.DataFrame({"code": ["b", "a"], "v": [2, 1]})
    df2 = pd.DataFrame({"code": ["a", "b"], "v": [1, 2]})
    kw = dict(columns=["code", "v"], key_columns=["code"], schema_name="t", schema_version=1)
    assert canonical_table_bytes(df1, **kw) == canonical_table_bytes(df2, **kw)


def test_table_schema_version_changes_hash() -> None:
    df = pd.DataFrame({"code": ["a"], "v": [1]})
    h1 = canonical_table_bytes(df, columns=["code", "v"], key_columns=["code"],
                               schema_name="t", schema_version=1)
    h2 = canonical_table_bytes(df, columns=["code", "v"], key_columns=["code"],
                               schema_name="t", schema_version=2)
    assert h1 != h2
    assert sha256_hex(h1) != sha256_hex(h2)
