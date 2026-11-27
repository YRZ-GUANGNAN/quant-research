from __future__ import annotations

import pytest

from quant.automation.ledger import AlreadyBound, Ledger, StateConflict, consumption_key


def test_consumption_key_ignores_enrollment_and_token() -> None:
    a = consumption_key(market="us", snapshot_id="s1", eligible_execution_session="2026-10-27")
    b = consumption_key(market="us", snapshot_id="s1", eligible_execution_session="2026-10-27")
    assert a == b and len(a) == 64


def test_key_differs_by_session_market_snapshot() -> None:
    base = dict(market="us", snapshot_id="s1", eligible_execution_session="2026-10-27")
    assert consumption_key(**base) != consumption_key(**{**base, "market": "hk"})
    assert consumption_key(**base) != consumption_key(**{**base, "eligible_execution_session": "2026-10-28"})


def test_one_shot_binding(tmp_path) -> None:
    led = Ledger(tmp_path)
    key = consumption_key(market="us", snapshot_id="s1", eligible_execution_session="2026-10-27")
    led.bind_plan(key=key, plan_id="plan-1")
    with pytest.raises(AlreadyBound):
        led.bind_plan(key=key, plan_id="plan-2")


def test_submitting_blocks_retry(tmp_path) -> None:
    led = Ledger(tmp_path)
    key = consumption_key(market="us", snapshot_id="s1", eligible_execution_session="2026-10-27")
    led.bind_plan(key=key, plan_id="plan-1")
    led.mark_submitting(key=key, plan_id="plan-1", intent_id="i1")
    with pytest.raises(StateConflict):
        led.mark_submitting(key=key, plan_id="plan-1", intent_id="i1")


def test_chain_verify_and_tamper_detection(tmp_path) -> None:
    led = Ledger(tmp_path)
    key = consumption_key(market="us", snapshot_id="s1", eligible_execution_session="2026-10-27")
    led.bind_plan(key=key, plan_id="p")
    led.mark_submitting(key=key, plan_id="p", intent_id="i")
    led.mark_result(key=key, plan_id="p", intent_id="i", status="UNKNOWN")
    assert led.status(key) == "UNKNOWN"
    assert led.verify_chain() is True
    ev = sorted(led.events.glob("*.json"))[0]
    data = ev.read_text(encoding="utf-8")
    ev.write_text(data.replace('"plan_id": "p"', '"plan_id": "tampered"'), encoding="utf-8")
    assert led.verify_chain() is False


def test_pass_no_changes_consumption_key():
    """同一交易日的第二轮补单是独立的一次消费，但不能与首轮撞键。"""
    base = {"market": "hk", "snapshot_id": "s1", "eligible_execution_session": "2026-11-27"}
    assert consumption_key(**base, pass_no=1) != consumption_key(**base, pass_no=2)
    assert consumption_key(**base, pass_no=1) == consumption_key(**base)
