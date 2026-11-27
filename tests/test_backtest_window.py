"""引擎区间截断：cfg.start 之前只预热、不交易不记账。"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.config import BacktestConfig  # noqa: E402
from quant.data.panel import Panel  # noqa: E402
from quant.engine.backtest import Backtester  # noqa: E402
from quant.strategies.presets import PRESETS  # noqa: E402
from quant.strategies.topk import TopKStrategy  # noqa: E402

MARKET = "us"


def _run(start: str | None):
    panel = Panel.load(index_code=None, market=MARKET)
    cfg = BacktestConfig.for_market(MARKET, top_k=10, rebalance_days=20)
    if start:
        cfg = replace(cfg, start=start)
    st = TopKStrategy(panel=panel, cfg=cfg, factor_weights=PRESETS["momentum"], warmup=130)
    return Backtester(panel, cfg).run(st.weight_func(), name="t")


def test_start_date_is_honored():
    result = _run("2023-01-01")
    first = result.nav.index[0]
    assert pd.Timestamp(first) >= pd.Timestamp("2023-01-01")


def test_no_start_uses_full_history():
    result = _run(None)
    assert pd.Timestamp(result.nav.index[0]) < pd.Timestamp("2020-01-01")


def test_end_date_is_honored():
    panel = Panel.load(index_code=None, market=MARKET)
    cfg = replace(BacktestConfig.for_market(MARKET, top_k=10, rebalance_days=20),
                  start="2022-01-01", end="2022-12-31")
    st = TopKStrategy(panel=panel, cfg=cfg, factor_weights=PRESETS["momentum"], warmup=130)
    result = Backtester(panel, cfg).run(st.weight_func(), name="t")
    assert pd.Timestamp(result.nav.index[-1]) <= pd.Timestamp("2022-12-31")
