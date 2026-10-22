"""抓取指数成分股日线并写入本地缓存。

用法：
    python scripts/fetch_data.py --index 000300 --start 2018-01-01
    python scripts/fetch_data.py --limit 5          # 调试：只抓 5 只
    python scripts/fetch_data.py --refresh-universe # 强制重抓成分股
    python scripts/fetch_data.py --full             # 忽略缓存全量重抓
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import INDEX_NAMES, ensure_dirs  # noqa: E402
from quant.data import eastmoney as em  # noqa: E402
from quant.data import store  # noqa: E402


def fetch_one(code: str, name: str, start: str, end: str) -> pd.DataFrame:
    df = em.fetch_kline(code, start=start, end=end)
    df["name"] = name
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取指数成分股日线")
    ap.add_argument("--index", default="000300")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="调试用：只抓前 N 只")
    ap.add_argument("--refresh-universe", action="store_true")
    ap.add_argument("--full", action="store_true", help="忽略本地缓存全量重抓")
    args = ap.parse_args()

    ensure_dirs()
    index_code = str(args.index).zfill(6)
    label = INDEX_NAMES.get(index_code, index_code)

    members = store.load_universe(index_code) if not args.refresh_universe else pd.DataFrame()
    if members.empty:
        print(f"[universe] 拉取 {label}({index_code}) 成分股 ...")
        members = em.fetch_index_constituents(index_code)
        store.save_universe(index_code, members)
        print(f"[universe] 已保存 {len(members)} 只成分股")
    print(f"[universe] {label} 成分股 {len(members)} 只")
    if args.limit:
        members = members.head(args.limit)

    existing = pd.DataFrame() if args.full else store.load_bars()
    last_date = {}
    if not existing.empty:
        last_date = existing.groupby("code")["date"].max().to_dict()

    tasks: list[tuple[str, str, str]] = []
    for row in members.itertuples(index=False):
        code = str(row.code).zfill(6)
        start = args.start
        if code in last_date:
            start = (last_date[code] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
            if start > pd.Timestamp.today().strftime("%Y-%m-%d"):
                continue
        tasks.append((code, getattr(row, "name", ""), start))

    print(f"[bars] 待抓取 {len(tasks)} 只（增量模式）")
    frames: list[pd.DataFrame] = []
    failed: list[tuple[str, str]] = []
    t0 = time.time()
    if tasks:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(fetch_one, c, n, s, args.end): c for c, n, s in tasks}
            for i, fut in enumerate(as_completed(futures), 1):
                code = futures[fut]
                try:
                    df = fut.result()
                    if not df.empty:
                        frames.append(df)
                except Exception as exc:  # noqa: BLE001
                    failed.append((code, str(exc)))
                if i % 20 == 0 or i == len(futures):
                    print(f"  {i}/{len(futures)}  {time.time() - t0:.0f}s  失败 {len(failed)}")

    if frames:
        merged = store.upsert_bars(pd.concat(frames, ignore_index=True))
        print(f"[bars] 写入完成：本次 {sum(len(f) for f in frames)} 行，缓存共 {len(merged)} 行")
        print(f"[bars] 缓存覆盖 {merged['date'].min().date()} ~ {merged['date'].max().date()}")
    if failed:
        print(f"[bars] 失败 {len(failed)} 只，示例: {failed[:5]}")

    print("[index] 抓取基准指数日线 ...")
    idx = em.fetch_index_kline(index_code, start=args.start, end=args.end)
    store.save_index_bars(idx)
    print(f"[index] {label} {len(idx)} 行，{idx['date'].min().date()} ~ {idx['date'].max().date()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
