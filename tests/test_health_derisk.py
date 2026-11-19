from __future__ import annotations

from quant.automation.health import de_risk_from_health
from quant.automation.risk_guard import Position


def test_no_action_when_healthy():
    pos = [Position(symbol="A", qty=100, cost=10, last=10)]
    assert de_risk_from_health({"worst": "healthy"}, pos) == []


def test_de_risk_all_positions_when_degraded():
    pos = [Position(symbol="A", qty=100, cost=10, last=10),
           Position(symbol="B", qty=200, cost=5, last=5)]
    acts = de_risk_from_health({"worst": "degraded"}, pos, keep_ratio=0.5)
    assert len(acts) == 2
    assert all(a.action == "de_risk" and a.target_qty == 0.5 for a in acts)


def test_skip_zero_positions():
    pos = [Position(symbol="A", qty=0, cost=10, last=10)]
    assert de_risk_from_health({"worst": "degraded"}, pos) == []
