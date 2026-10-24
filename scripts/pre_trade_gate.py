"""交易前数据门禁：数据不达标则退出非零，禁止任何 --execute 下单。

检查每个市场：
1. 个股缓存最新日期 >= 该市场指数缓存最新日期（防止「指数更新了、个股没更新」）；
2. 最新日期的股票覆盖率 >= 阈值（默认 90%）；
3. 无重复 (code,date)。
用法：python scripts/pre_trade_gate.py [--markets cn,us,hk]
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from quant.data import store  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

CN_TZ = timezone(timedelta(hours=8))
CLOSE_BJ = {"cn": (16, 0), "hk": (16, 30), "us": (5, 0)}  # 北京时间「已收盘」判定点


def last_completed_session(market: str, now: datetime | None = None):
    """按北京时间估算该市场最近一个已完成交易日（忽略节假日，节假日会误报 FAIL，安全优先）。"""
    now = now or datetime.now(CN_TZ)
    hour, minute = CLOSE_BJ.get(market, (23, 59))
    d = now.date()
    # 美股：北京早上 5 点前算「上一交易日」
    if market == "us":
        d = d - timedelta(days=1) if (now.hour, now.minute) < (hour, minute) else d
    else:
        if (now.hour, now.minute) < (hour, minute):
            d = d - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d

MIN_COVERAGE = {"cn": 0.90, "us": 0.90, "hk": 0.90}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="cn,us,hk")
    ap.add_argument("--min-coverage", type=float, default=0.0)
    args = ap.parse_args()
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]

    fails = []
    for m in markets:
        try:
            bars = store.load_bars(m)
            idx = store.load_index_bars(m)
        except Exception as exc:  # noqa: BLE001
            fails.append(f"{m}: 读取失败 {str(exc)[:80]}")
            continue
        if bars.empty:
            fails.append(f"{m}: 个股缓存为空")
            continue
        d = bars["date"].max()
        idx_d = idx["date"].max() if not idx.empty else None
        cov = (bars[bars["date"] == d]["code"].nunique() / max(1, bars["code"].nunique()))
        dup = int(bars.duplicated(["code", "date"]).sum())
        need = MIN_COVERAGE.get(m, 0.9) if args.min_coverage <= 0 else args.min_coverage
        ok = True
        expected = last_completed_session(m)
        if d.date() < expected:
            ok = False
            fails.append(f"{m}: 个股最新 {d.date()} 落后最近已完成交易日 {expected}")
        if idx_d is not None and d < idx_d:
            ok = False; fails.append(f"{m}: 个股最新 {d.date()} 落后指数 {idx_d.date()}")
        if cov < need:
            ok = False; fails.append(f"{m}: 最新日覆盖率 {cov:.1%} < {need:.0%}")
        if dup:
            ok = False; fails.append(f"{m}: 重复行 {dup}")
        print(f"[gate] {m}: 个股最新 {d.date()} / 指数 {idx_d.date() if idx_d is not None else 'NA'} / 覆盖 {cov:.1%} / 重复 {dup} -> {'PASS' if ok else 'FAIL'}")

    if fails:
        print("[gate] 未通过，禁止下单：")
        for f in fails:
            print("  -", f)
        return 1
    print("[gate] 全部通过，允许进入下单步骤")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
