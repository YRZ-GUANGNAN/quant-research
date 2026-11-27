"""袖套目标生成：默认四相位共识，可用 LB_SLEEVE_ENSEMBLE=0 退回单相位。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import lb_order_map as lom  # noqa: E402


class _FakePanel:
    def __init__(self):
        self.dates = [pd.Timestamp("2026-11-27")]
        self.names = {}
        self.codes = ["AAA", "BBB"]

    def __getitem__(self, key):
        return pd.DataFrame()


def _patch_common(monkeypatch, calls: list[str]):
    monkeypatch.setattr(lom, "Panel", SimpleNamespace(load=staticmethod(lambda **kw: _FakePanel())))
    monkeypatch.setattr(lom, "BacktestConfig", SimpleNamespace(for_market=staticmethod(
        lambda market, top_k, rebalance_days: SimpleNamespace())))
    monkeypatch.setattr(lom, "SNAPSHOT_PATH", Path("/nonexistent/snapshot.json"), raising=False)

    class _Ensemble:
        def __init__(self, **kw):
            calls.append("ensemble")

        def replay(self, i):
            self._i = i

        def target_weights(self, date):
            return {"AAA": 0.5, "BBB": 0.5}

    class _TopK:
        def __init__(self, **kw):
            calls.append("topk")

        def target_weights(self, date):
            return {"AAA": 0.6}

    # 共识策略是函数内 import，要 patch 源模块；TopK 是模块级导入，patch 本地引用
    import quant.strategies.ensemble as ens

    monkeypatch.setattr(ens, "PhaseEnsembleStrategy", _Ensemble)
    monkeypatch.setattr(lom, "TopKStrategy", _TopK)


def test_ensemble_is_default(monkeypatch):
    calls: list[str] = []
    _patch_common(monkeypatch, calls)
    monkeypatch.delenv("LB_SLEEVE_ENSEMBLE", raising=False)
    monkeypatch.setattr(lom, "Path", Path)  # 快照不存在 → 直接算
    w = lom.sleeve_targets("us", 20, ".US")
    assert calls == ["ensemble"]
    assert w == {"AAA.US": 0.5, "BBB.US": 0.5}


def test_can_fall_back_to_single_phase(monkeypatch):
    calls: list[str] = []
    _patch_common(monkeypatch, calls)
    monkeypatch.setenv("LB_SLEEVE_ENSEMBLE", "0")
    w = lom.sleeve_targets("us", 20, ".US")
    assert calls == ["topk"]
    assert w == {"AAA.US": 0.6}
