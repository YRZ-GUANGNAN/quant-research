"""用腾讯接口批量补齐 A 股日线（多线程）。

背景：BaoStock 并发后会限流（乱码/超时），而成分股快照已经拿到（那是 BaoStock 的独特价值）。
本脚本只负责行情：从 BaoStock 已保存的时点成分股名单取代码，用腾讯接口抓前复权日线。

用法：
    python scripts/fetch_tx.py                 # 全量（增量）
    python scripts/fetch_tx.py --limit 5       # 调试
    python scripts/fetch_tx.py --workers 8
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import ensure_dirs  # noqa: E402
from quant.data import store, tencent  # noqa: E402


def fetch_one(code: str, start: str, end: str) -> pd.DataFrame:
    return tencent.fetch_daily(code, start=start, end=end)


def main() -> int:
    ap = argparse.ArgumentParser(description="腾讯接口批量抓 A 股日线")
    ap.add_argument("--index", default="000300")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2050-12-31")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--full", action="store_true")
    args = ap.parse_args()

    ensure_dirs()
    members = store.load_members(str(args.index).zfill(6))
    if members.empty:
        raise SystemExit("缺少成分股快照，请先运行 scripts/fetch_data.py")
    codes = sorted(members["code"].unique())
    names = members.drop_duplicates("code").set_index("code")["name"].to_dict()
    if args.limit:
        codes = codes[: args.limit]
    print(f"[tx] 股票池 {len(codes)} 只（来自 BaoStock 时点成分股）")

    existing = pd.DataFrame() if args.full else store.load_bars("cn")
    last_date: dict[str, pd.Timestamp] = {}
    if not existing.empty:
        last_date = existing.groupby("code")["date"].max().to_dict()

    tasks = []
    for code in codes:
        begin = args.start
        if code in last_date:
            nxt = (last_date[code] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            if nxt > pd.Timestamp.today().strftime("%Y-%m-%d"):
                continue
            begin = nxt
        tasks.append((code, begin))
    print(f"[tx] 待抓取 {len(tasks)} 只，{args.workers} 线程")

    frames: list[pd.DataFrame] = []
    failed: list[tuple[str, str]] = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, c, s, args.end): c for c, s in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            code = futures[fut]
            try:
                df = fut.result()
                if not df.empty:
                    df["name"] = names.get(code, "")
                    frames.append(df)
            except Exception as exc:  # noqa: BLE001
                failed.append((code, str(exc)[:80]))
            if i % 50 == 0 or i == len(futures):
                print(f"  [tx] {i}/{len(futures)}  {time.time() - t0:.0f}s  失败 {len(failed)}")

    if frames:
        merged = store.upsert_bars(pd.concat(frames, ignore_index=True), "cn")
        print(f"[tx] 本次 {sum(len(f) for f in frames)} 行，缓存 {len(merged)} 行")
        print(f"[tx] 覆盖 {merged['date'].min().date()} ~ {merged['date'].max().date()}，"
              f"股票数 {merged['code'].nunique()}")
    else:
        print("[tx] 无新增数据")
    if failed:
        print(f"[tx] 失败 {len(failed)} 只，示例: {failed[:5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
