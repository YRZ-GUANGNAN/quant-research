"""抓取美股 / 港股 / 加密货币行情并写入本地缓存。

用法：
    python scripts/fetch_market.py --market us
    python scripts/fetch_market.py --market hk
    python scripts/fetch_market.py --market crypto
    python scripts/fetch_market.py --market all

数据源：Yahoo Finance（一个接口覆盖三个市场，含复权价）。
注意：股票池是**静态名单**（见 quant/data/universe.py），存在幸存者偏差。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import ensure_dirs  # noqa: E402
from quant.data import store  # noqa: E402
from quant.data.universe import UNIVERSES, get_universe  # noqa: E402
from quant.data import yahoo  # noqa: E402


def fetch_one_market(market: str, range_: str) -> None:
    symbols = get_universe(market)
    print(f"[{market}] 标的 {len(symbols)} 只，基准 {yahoo.BENCHMARKS[market]}")
    frames: list[pd.DataFrame] = []
    failed: list[tuple[str, str]] = []
    t0 = time.time()
    for i, symbol in enumerate(symbols, 1):
        try:
            df = yahoo.fetch_chart(symbol, range_=range_)
            if not df.empty:
                df["name"] = symbol
                frames.append(df)
        except Exception as exc:  # noqa: BLE001
            failed.append((symbol, str(exc)[:120]))
        if i % 10 == 0 or i == len(symbols):
            print(f"  [{market}] {i}/{len(symbols)}  {time.time() - t0:.0f}s  失败 {len(failed)}")
        time.sleep(0.15)

    if frames:
        merged = store.upsert_bars(pd.concat(frames, ignore_index=True), market)
        print(f"[{market}] 写入 {sum(len(f) for f in frames)} 行，缓存 {len(merged)} 行，"
              f"覆盖 {merged['date'].min().date()} ~ {merged['date'].max().date()}")
    if failed:
        print(f"[{market}] 失败 {len(failed)}：{failed[:5]}")

    bench = yahoo.fetch_benchmark(market, range_=range_)
    store.save_index_bars(bench, market)
    print(f"[{market}] 基准 {yahoo.BENCHMARKS[market]}：{len(bench)} 行")


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取多市场行情")
    ap.add_argument("--market", default="all", choices=["us", "hk", "crypto", "all"])
    ap.add_argument("--range", default="10y", help="Yahoo 区间，如 5y / 10y / max")
    args = ap.parse_args()

    ensure_dirs()
    markets = list(UNIVERSES) if args.market == "all" else [args.market]
    for market in markets:
        fetch_one_market(market, args.range)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
