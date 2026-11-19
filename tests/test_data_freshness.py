"""数据缺口判定与自动化看门狗的单测（不起网络、不碰调度器）。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_eastmoney_fallback as em  # noqa: E402
import automation_watchdog as wd  # noqa: E402


def test_split_stale_separates_delisted_from_active():
    latest = pd.Series({
        "600000": pd.Timestamp("2026-11-19"),
        "000001": pd.Timestamp("2026-11-18"),
        "600068": pd.Timestamp("2021-09-13"),
        "601059": pd.Timestamp("2026-10-16"),
    })
    active, stale = em.split_stale(latest, pd.Timestamp("2026-11-19"))
    assert set(active.index) == {"600000", "000001"}
    assert set(stale.index) == {"600068", "601059"}


def test_split_stale_respects_threshold_edges():
    # 正好 10 天前仍算在交易，11 天前算停更
    latest = pd.Series({"A": pd.Timestamp("2026-11-09"), "B": pd.Timestamp("2026-11-08")})
    active, stale = em.split_stale(latest, pd.Timestamp("2026-11-19"))
    assert list(active.index) == ["A"]
    assert list(stale.index) == ["B"]


def test_check_reports_flags_missing_artifact(tmp_path: Path):
    today = date(2026, 11, 19)
    (tmp_path / "post_check_2026-11-19.json").write_text("{}", encoding="utf-8")
    rows = wd.check_reports(today, tmp_path)
    by_item = {r["item"]: r["status"] for r in rows}
    assert by_item["每日自检"] == "PASS"
    assert by_item["经纪商日报"] == "FAIL"
    assert len(rows) == len(wd.REQUIRED_REPORTS)


def test_evaluate_skips_weekend(monkeypatch):
    monkeypatch.setattr(wd, "task_status", lambda names=None: [])
    report = wd.evaluate(date(2026, 11, 21))  # 周六
    assert report["status"] == "SKIP"
    assert report["problems"] == []


def test_evaluate_alerts_when_task_failed(monkeypatch, tmp_path):
    monkeypatch.setattr(wd, "ROOT", tmp_path)
    monkeypatch.setattr(wd, "task_status", lambda names=None: [
        {"name": "QuantDailyRun", "state": "Ready", "last": "2026-11-19 15:40:00", "result": 1},
    ])
    report = wd.evaluate(date(2026, 11, 19))
    assert report["status"] == "ALERT"
    assert any("QuantDailyRun" in p for p in report["problems"])


def test_evaluate_never_ran_task_is_warn_only(monkeypatch, tmp_path):
    monkeypatch.setattr(wd, "ROOT", tmp_path)
    monkeypatch.setattr(wd, "task_status", lambda names=None: [
        {"name": "QuantCnOpen", "state": "Ready", "last": "1999-11-30 00:00:00", "result": 267011},
    ])
    report = wd.evaluate(date(2026, 11, 19))
    task_rows = [c for c in report["checks"] if c["item"].startswith("任务 ")]
    assert task_rows[0]["status"] == "WARN"
    assert not any("QuantCnOpen" in p for p in report["problems"])
