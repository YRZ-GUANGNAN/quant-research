from __future__ import annotations

from quant.automation.attribution import concentration, position_attribution, summarize

POS = [
    {"symbol": "A", "qty": 100, "cost": 10.0, "last": 12.0},   # +200
    {"symbol": "B", "qty": 200, "cost": 10.0, "last": 9.0},    # -200
    {"symbol": "C", "qty": 50, "cost": 20.0, "last": 20.0},    # 0
]
EQUITY = 5000.0


def test_pnl_and_contribution():
    cs = position_attribution(POS, equity=EQUITY)
    by = {c.symbol: c for c in cs}
    assert by["A"].pnl == 200 and by["A"].pnl_pct == 0.2
    assert by["B"].pnl == -200
    assert by["A"].contribution == round(200 / EQUITY, 4)


def test_sorted_by_pnl_desc():
    cs = position_attribution(POS, equity=EQUITY)
    assert [c.symbol for c in cs] == ["A", "C", "B"]


def test_concentration():
    got = concentration(POS, equity=EQUITY)
    assert got["positions"] == 3
    assert 0 < got["top1"] <= 1
    assert got["hhi"] > 0


def test_summary_hit_rate():
    cs = position_attribution(POS, equity=EQUITY)
    s = summarize(cs)
    assert s["total_pnl"] == 0.0
    assert s["winners"] == 1 and s["losers"] == 1
    assert s["hit_rate"] == round(1 / 3, 4)


def test_empty_positions():
    assert position_attribution([], equity=0) == []
    assert concentration([], equity=0)["positions"] == 0
