"""交易日历：判断某个市场「最近一个应该已经有数据的交易日」。

为什么需要：`pre_trade_gate` 之前只看周末，遇到节假日就会把「昨天是感恩节、本来就没有数据」
误判成「数据落后」，直接把当天的美股流程卡死（2026-11-26 感恩节实测踩到）。

实现策略：
- **美股**：完全规则化（元旦/马丁路德金/总统日/耶稣受难日/阵亡将士/六月节/独立日/劳动节/感恩节/圣诞），
  含「周六提前、周日顺延」的观察日规则，不需要维护假期表；
- **港股/ A 股**：农历节日无法规则化，这里只放固定公历日（元旦/劳动节/国庆/圣诞等），
  农历部分交给上层「指数与个股同步缺一天 → 视为休市」的兜底判断（见 gate）。
"""
from __future__ import annotations

from datetime import date, timedelta


def _observed(d: date) -> date:
    """美国惯例：周六的假日提前到周五，周日的假日顺延到周一。"""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """某月第 n 个周几（weekday: 周一=0）。"""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month, 1)
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def easter(year: int) -> date:
    """复活节（Anonymous Gregorian algorithm），用于推算耶稣受难日。"""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def us_holidays(year: int) -> set[date]:
    """NYSE 休市日（规则化，覆盖 2020-2035 足够用）。"""
    days = {
        _observed(date(year, 1, 1)),                     # 元旦
        _nth_weekday(year, 1, 0, 3),                     # 马丁路德金
        _nth_weekday(year, 2, 0, 3),                     # 总统日
        easter(year) - timedelta(days=2),                # 耶稣受难日
        _last_weekday(year, 5, 0),                       # 阵亡将士
        _observed(date(year, 6, 19)),                    # 六月节
        _observed(date(year, 7, 4)),                     # 独立日
        _nth_weekday(year, 9, 0, 1),                     # 劳动节
        _nth_weekday(year, 11, 3, 4),                    # 感恩节
        _observed(date(year, 12, 25)),                   # 圣诞
    }
    return {d for d in days if d.year == year}


def hk_holidays(year: int) -> set[date]:
    """港股固定公历假日（农历节日不在此列，由上层兜底判断）。"""
    return {d for d in {
        _observed(date(year, 1, 1)),
        date(year, 5, 1),
        date(year, 7, 1),
        date(year, 10, 1),
        date(year, 12, 25),
        date(year, 12, 26),
    } if d.year == year}


def cn_holidays(year: int) -> set[date]:
    """A 股固定公历假日（春节/中秋等农历节日不在此列）。"""
    return {d for d in {
        date(year, 1, 1),
        date(year, 5, 1),
        date(year, 10, 1),
        date(year, 10, 2),
        date(year, 10, 3),
    } if d.year == year}


def is_trading_day(market: str, day: date) -> bool:
    """周末与已知公历假日之外都算交易日（农历假日按交易日处理，由数据兜底）。"""
    if day.weekday() >= 5:
        return False
    market = market.lower()
    if market == "us":
        return day not in us_holidays(day.year)
    if market == "hk":
        return day not in hk_holidays(day.year)
    if market == "cn":
        return day not in cn_holidays(day.year)
    return True


def previous_trading_day(market: str, day: date, *, include_self: bool = False) -> date:
    """从 day 往回找最近一个交易日（include_self=True 时若当天是交易日则返回自己）。"""
    d = day if include_self else day - timedelta(days=1)
    for _ in range(30):
        if is_trading_day(market, d):
            return d
        d -= timedelta(days=1)
    return d


def previous_trading_day_cached(market: str, day: date, *, include_self: bool = False) -> date:
    """同 previous_trading_day，名字保留以便调用方表达「可缓存」的意图。"""
    return previous_trading_day(market, day, include_self=include_self)
