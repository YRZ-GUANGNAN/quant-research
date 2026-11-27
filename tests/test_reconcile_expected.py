"""期望订单落盘与对账口径的单测。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation import reconcile as rc  # noqa: E402


def _use_tmp(monkeypatch, tmp_path: Path) -> date:
    monkeypatch.setattr(rc, "EXPECTED_DIR", tmp_path)
    return date(2026, 11, 27)


def test_expected_merges_two_batches(monkeypatch, tmp_path):
    """同一天先跑再平衡、再跑风控：两批都要留下，不能后者覆盖前者。"""
    day = _use_tmp(monkeypatch, tmp_path)
    rc.record_expected("us", [{"symbol": "META.US", "side": "buy", "qty": 10}], on=day.isoformat())
    rc.record_expected("us", [{"symbol": "NVDA.US", "side": "sell", "qty": 20}], on=day.isoformat())
    rows = json.loads((tmp_path / f"{day}_us.json").read_text(encoding="utf-8"))
    assert {r["symbol"] for r in rows} == {"META.US", "NVDA.US"}


def test_expected_dedupes_same_symbol_side(monkeypatch, tmp_path):
    day = _use_tmp(monkeypatch, tmp_path)
    rc.record_expected("hk", [{"symbol": "0939.HK", "side": "buy", "qty": 100}], on=day.isoformat())
    rc.record_expected("hk", [{"symbol": "0939.HK", "side": "buy", "qty": 300}], on=day.isoformat())
    rows = json.loads((tmp_path / f"{day}_hk.json").read_text(encoding="utf-8"))
    assert len(rows) == 1 and rows[0]["qty"] == 300  # 后一批覆盖同一键的数值


def test_expected_recovers_from_corrupt_file(monkeypatch, tmp_path):
    day = _use_tmp(monkeypatch, tmp_path)
    (tmp_path / f"{day}_us.json").write_text("不是 JSON", encoding="utf-8")
    rc.record_expected("us", [{"symbol": "ACN.US", "side": "buy", "qty": 1}], on=day.isoformat())
    rows = json.loads((tmp_path / f"{day}_us.json").read_text(encoding="utf-8"))
    assert [r["symbol"] for r in rows] == ["ACN.US"]
