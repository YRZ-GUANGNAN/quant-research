from __future__ import annotations

from quant.automation.health import assess_factors, evaluate_health


def test_insufficient_data_guard():
    """样本 <20 天不下结论。"""
    live = {"live_annual": 0.0, "live_drawdown": 0.0, "days": 1}
    r = evaluate_health(live, {})
    assert r.status == "insufficient_data"
    assert not r.alerts


def test_healthy_when_metrics_in_line():
    live = {"live_annual": 0.12, "live_drawdown": -0.10, "days": 30}
    factors = {"amihud_20": {"retention": 0.8}}
    r = evaluate_health(live, factors)
    assert r.status == "healthy" and r.action == "hold"


def test_warning_on_low_return():
    live = {"live_annual": -0.15, "live_drawdown": -0.10, "days": 30}
    r = evaluate_health(live, {})
    assert r.status == "warning"
    assert any("年化" in a for a in r.alerts)


def test_degraded_on_deep_drawdown_plus_decay():
    live = {"live_annual": -0.20, "live_drawdown": -0.30, "days": 30}
    factors = {"amihud_20": {"retention": 0.1}}
    r = evaluate_health(live, factors)
    assert r.status == "degraded" and r.action == "de_risk"


def test_divergence_alert():
    live = {"live_annual": -0.05, "live_drawdown": -0.05, "days": 30}
    r = evaluate_health(live, {})
    assert any("回测" in a for a in r.alerts)


def test_assess_factors_retention():
    got = assess_factors({"amihud_20": 0.10, "rev_20": 0.11})
    assert got["amihud_20"]["retention"] < 0.5
    assert got["rev_20"]["retention"] > 0.8
