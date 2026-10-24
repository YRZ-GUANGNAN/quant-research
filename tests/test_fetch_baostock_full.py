"""fetch_baostock_full 分块落盘边界测试（回归：第 50 只曾 UnboundLocalError）。"""
from __future__ import annotations

import sys

import pandas as pd
import pytest

import scripts.fetch_baostock_full as mod


class FakeClient:
    def __init__(self, fail_at: int | None = None):
        self.fail_at = fail_at
        self.calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def fetch_daily(self, code, start="2018-01-01"):
        self.calls += 1
        if self.fail_at is not None and self.calls == self.fail_at:
            raise RuntimeError("boom")
        return pd.DataFrame({"date": [pd.Timestamp("2026-10-23")], "close": [1.0]})


def _make_env(monkeypatch, n_codes: int, fail_at: int | None = None):
    codes = [f"{i:06d}" for i in range(n_codes)]
    existing = pd.DataFrame({"code": codes, "date": [pd.Timestamp("2026-10-22")] * n_codes})
    upserts = []

    monkeypatch.setattr(mod.store, "load_bars", lambda market="cn": existing)
    monkeypatch.setattr(mod.store, "upsert_bars", lambda new, market="cn": upserts.append(new) or existing)
    monkeypatch.setattr(mod, "BaoStockClient", lambda: FakeClient(fail_at=fail_at))
    return upserts


def _run(monkeypatch, limit: int):
    monkeypatch.setattr(sys, "argv", ["fetch_baostock_full.py", "--limit", str(limit)])
    return mod.main()


@pytest.mark.parametrize("n,expect_upserts", [(49, 1), (50, 1), (51, 2), (99, 2), (100, 2), (101, 3)])
def test_flush_boundaries(monkeypatch, n, expect_upserts):
    upserts = _make_env(monkeypatch, n)
    rc = _run(monkeypatch, n)
    assert rc == 0
    assert len(upserts) == expect_upserts, f"{n} 只应落盘 {expect_upserts} 次，实际 {len(upserts)}"


def test_failure_returns_nonzero(monkeypatch):
    _make_env(monkeypatch, 60, fail_at=7)
    rc = _run(monkeypatch, 60)
    assert rc == 1, "有失败股票必须返回非零"
