from __future__ import annotations

from quant.automation.risk_execute import Limits, to_orders
from quant.automation.risk_guard import Action


POS = {"700.HK": {"qty": 100, "last": 380.0}, "0939.HK": {"qty": 1000, "last": 10.0}}
Q = {"700.HK": {"last": 380.0}, "0939.HK": {"last": 10.0}, "0005.HK": {"last": 50.0}}


def test_trim_generates_sell_of_excess():
    acts = [Action(symbol="700.HK", action="trim", reason="超重", target_qty=40)]
    orders = to_orders(acts, POS, Q)
    assert len(orders) == 1
    assert orders[0].side == "sell" and orders[0].qty == 60


def test_exit_sells_all():
    acts = [Action(symbol="0939.HK", action="exit", reason="止损")]
    orders = to_orders(acts, POS, Q)
    assert orders[0].qty == 1000


def test_exit_with_replacement_adds_buy():
    acts = [Action(symbol="0939.HK", action="exit", reason="止损", replace_with="0005.HK")]
    orders = to_orders(acts, POS, Q)
    assert [o.side for o in orders] == ["sell", "buy"]
    assert orders[1].symbol == "0005.HK"


def test_de_risk_scales_down():
    acts = [Action(symbol="700.HK", action="de_risk", reason="回撤", target_qty=0.5)]
    orders = to_orders(acts, POS, Q)
    assert orders[0].qty == 50


def test_unknown_symbol_skipped():
    acts = [Action(symbol="TSLA.US", action="exit", reason="x")]
    assert to_orders(acts, POS, Q) == []


def test_max_orders_respected():
    acts = [Action(symbol="700.HK", action="exit", reason="x") for _ in range(30)]
    orders = to_orders(acts, POS, Q, Limits(max_orders=5))
    assert len(orders) <= 5
