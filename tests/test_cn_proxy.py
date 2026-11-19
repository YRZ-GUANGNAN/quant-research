from __future__ import annotations

import json

from quant.automation import execution


def test_cn_proxy_plan_targets_ashr(monkeypatch, tmp_path):
    monkeypatch.setattr(execution, "_quotes", lambda syms: {"ASHR.US": {"last": 30.0}})
    monkeypatch.setattr(execution, "_cash", lambda ccy: 120_000.0)
    monkeypatch.setattr(execution, "_positions", lambda: {})
    plan = execution.build_plan("cn_proxy", execution.Policy())
    assert plan["ok"] is True
    assert plan["orders"][0]["symbol"] == "ASHR.US"
    # 单笔不超过 policy 上限
    assert plan["orders"][0]["notional"] <= execution.Policy().max_notional_per_order_usd


def test_cn_proxy_respects_cash_cap(monkeypatch):
    monkeypatch.setattr(execution, "_quotes", lambda syms: {"ASHR.US": {"last": 30.0}})
    monkeypatch.setattr(execution, "_cash", lambda ccy: 1_000.0)
    monkeypatch.setattr(execution, "_positions", lambda: {})
    plan = execution.build_plan("cn_proxy", execution.Policy())
    assert plan["ok"] is True
    assert plan["orders"][0]["notional"] <= 1_000.0


def test_cn_proxy_no_quote(monkeypatch):
    monkeypatch.setattr(execution, "_quotes", lambda syms: {})
    monkeypatch.setattr(execution, "_cash", lambda ccy: 120_000.0)
    monkeypatch.setattr(execution, "_positions", lambda: {})
    plan = execution.build_plan("cn_proxy", execution.Policy())
    assert plan["ok"] is False
