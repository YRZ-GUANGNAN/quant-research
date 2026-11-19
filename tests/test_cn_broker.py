from __future__ import annotations

import builtins

import pytest

from quant.broker import cn_broker


def test_detect_reports_local_always_available(monkeypatch):
    monkeypatch.setattr(cn_broker, "_ths_running", lambda: False)
    info = cn_broker.detect()
    assert info["local"]["available"] is True
    assert set(info) == {"qmt", "ths", "local"}


def test_ths_blocked_when_client_not_running(monkeypatch):
    monkeypatch.setattr(cn_broker, "_ths_running", lambda: False)
    monkeypatch.setattr(cn_broker, "THS_EXE", cn_broker.Path("C:/nonexistent/hexin.exe"))
    info = cn_broker.detect()
    assert info["ths"]["available"] is False
    assert any("同花顺" in b for b in info["ths"]["blockers"])


def test_execute_requires_env_gate(monkeypatch):
    monkeypatch.delenv("CN_ALLOW_EXECUTION", raising=False)
    out = cn_broker.execute_target({"600519": 0.1}, dry_run=False)
    assert out["ok"] is False and "CN_ALLOW_EXECUTION" in out["reason"]


def test_execute_blocks_when_channel_unavailable(monkeypatch):
    monkeypatch.setenv("CN_ALLOW_EXECUTION", "1")
    monkeypatch.setattr(cn_broker, "detect", lambda: {
        "qmt": {"available": False, "blockers": ["no"]},
        "ths": {"available": False, "blockers": ["同花顺客户端未运行"]},
        "local": {"available": True, "blockers": []},
    })
    out = cn_broker.execute_target({"600519": 0.1}, dry_run=False)
    assert out["ok"] is False and "同花顺" in out["reason"]


def test_limits_defaults_are_conservative():
    lim = cn_broker.Limits()
    assert lim.max_orders_per_run <= 20
    assert lim.max_order_notional_cny <= 100_000
    assert lim.min_order_notional_cny >= 1_000
    assert lim.lot_size == 100
