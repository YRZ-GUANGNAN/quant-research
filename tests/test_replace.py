from __future__ import annotations

import pytest

from quant.automation.replace import drawdown_from_nav, pick_replacements, record_nav

W = {"A": 0.5, "B": 0.3, "C": 0.1, "D": 0.05}
Q = {k: {"last": 10.0} for k in W}


def test_pick_replacement_highest_weight_not_held():
    out = pick_replacements(["X"], W, {"A"}, Q)
    assert out["X"] == "B"


def test_pick_skips_no_quote():
    q = {"A": {"last": 10.0}, "B": {}, "C": {"last": 10.0}, "D": {"last": 10.0}}
    out = pick_replacements(["X"], W, {"A"}, q)
    assert out["X"] == "C"


def test_pick_avoids_reuse():
    out = pick_replacements(["X", "Y"], W, set(), Q, per_exit=1)
    assert out["X"] != out["Y"]


def test_nav_record_and_drawdown(tmp_path):
    p = tmp_path / "nav.csv"
    record_nav(p, market="us", equity=100.0, cash=10.0, positions=2, on="2026-11-17")
    record_nav(p, market="us", equity=120.0, cash=10.0, positions=2, on="2026-11-18")
    record_nav(p, market="us", equity=90.0, cash=10.0, positions=2, on="2026-11-19")
    assert drawdown_from_nav(p, "us") == pytest.approx(-0.25)
    # 同日覆盖
    record_nav(p, market="us", equity=110.0, cash=10.0, positions=2, on="2026-11-19")
    assert drawdown_from_nav(p, "us") == pytest.approx(110.0 / 120.0 - 1)
