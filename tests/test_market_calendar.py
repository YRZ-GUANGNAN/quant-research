"""交易日历测试：节假日必须被识别，否则门禁会把「没数据」误判成「数据落后」。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.data import market_calendar as mc  # noqa: E402


def test_us_thanksgiving_2026_is_holiday():
    assert not mc.is_trading_day("us", date(2026, 11, 26))
    assert mc.previous_trading_day("us", date(2026, 11, 26), include_self=True) == date(2026, 11, 25)


def test_good_friday_2026():
    # 2026 复活节 4 月 5 日 → 耶稣受难日 4 月 3 日
    assert mc.easter(2026) == date(2026, 4, 5)
    assert not mc.is_trading_day("us", date(2026, 4, 3))


def test_us_independence_day_observed():
    # 2026-07-04 是周六 → 观察日提前到 07-03（周五）
    assert date(2026, 7, 4).weekday() == 5
    assert not mc.is_trading_day("us", date(2026, 7, 3))


def test_weekend_is_never_trading():
    assert not mc.is_trading_day("us", date(2026, 11, 28))   # 周六
    assert not mc.is_trading_day("hk", date(2026, 11, 29))   # 周日


def test_regular_weekday_is_trading():
    assert mc.is_trading_day("us", date(2026, 11, 27))
    assert mc.is_trading_day("cn", date(2026, 11, 27))


def test_previous_trading_day_skips_holiday_and_weekend():
    # 11-27 周五 → 前一个交易日是 11-25（11-26 感恩节）
    assert mc.previous_trading_day("us", date(2026, 11, 27)) == date(2026, 11, 25)


def test_cn_national_day_block():
    for d in (date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)):
        assert not mc.is_trading_day("cn", d)
