"""东方财富兜底：补齐腾讯/BaoStock 缺失的最近交易日数据（前复权）。

用法：
    python scripts/fetch_eastmoney_fallback.py            # 补齐所有缺口
    python scripts/fetch_eastmoney_fallback.py --limit 50
"""
from __future__ import annotations
import argparse, sys, time
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from quant.data import eastmoney, store  # noqa: E402

KEEP = ["code", "date", "open", "high", "low", "close", "volume", "amount"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    existing = store.load_bars("cn")
    latest = existing.groupby("code")["date"].max()
    target = pd.Timestamp(date.today())
    todo = latest[latest < target].index.tolist()
    if args.limit:
        todo = todo[: args.limit]
    print(f"[em] 需补齐 {len(todo)} 只（目标日期 {target.date()}）")

    def fetch(code: str):
        start = (latest[code] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        df = eastmoney.fetch_kline(code, start=start, end=target.strftime("%Y-%m-%d"))
        if df is None or df.empty:
            return code, None
        df = df.copy()
        df["code"] = code
        for c in KEEP:
            if c not in df.columns:
                df[c] = None
        return code, df[KEEP]

    frames, failed = [], []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(fetch, c): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            code = futs[fut]
            try:
                _, df = fut.result()
                if df is None or df.empty:
                    failed.append((code, "empty"))
                else:
                    frames.append(df)
            except Exception as exc:  # noqa: BLE001
                failed.append((code, str(exc)[:60]))
            if i % 100 == 0:
                print(f"  [em] {i}/{len(todo)}  {time.time()-t0:.0f}s  失败 {len(failed)}", flush=True)

    if frames:
        new = pd.concat(frames, ignore_index=True)
        merged = store.upsert_bars(new, market="cn")
        print(f"[em] 写入 {len(new)} 行，缓存 {len(merged)} 行，最新 {merged['date'].max().date()}")
    print(f"[em] 完成：成功 {len(frames)} 只，失败 {len(failed)} 只，用时 {time.time()-t0:.0f}s")
    if failed:
        print("[em] 失败示例:", failed[:5])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
