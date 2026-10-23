"""BaoStock 全量前复权刷新：绕开腾讯限流，把全部 A 股历史按最新复权因子重算。

用法：
    python scripts/fetch_baostock_full.py
    python scripts/fetch_baostock_full.py --limit 50
"""
from __future__ import annotations
import argparse, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
from quant.data.baostock_adapter import BaoStockClient  # noqa: E402
from quant.data import store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--start", default="2018-01-01")
    args = ap.parse_args()

    existing = store.load_bars("cn")
    codes = sorted(existing["code"].unique())
    if args.limit:
        codes = codes[: args.limit]
    print(f"[bs-full] 待刷新 {len(codes)} 只（前复权 adjustflag=2）")

    frames, failed = [], []
    t0 = time.time()
    # 单次登录复用会话（每只单独 login 会让 563 只耗时数小时）
    with BaoStockClient() as client:
        for i, code in enumerate(codes, 1):
            try:
                df = client.fetch_daily(code, start=args.start)
                if df is not None and not df.empty:
                    df = df.copy(); df["code"] = code
                    frames.append(df)
                else:
                    failed.append((code, "empty"))
            except Exception as exc:  # noqa: BLE001
                failed.append((code, str(exc)[:60]))
            if i % 50 == 0:
                flush_partial()
                print(f"  [bs-full] {i}/{len(codes)}  {time.time()-t0:.0f}s  失败 {len(failed)}", flush=True)

    def flush_partial() -> None:
        if not frames:
            return
        new = pd.concat(frames, ignore_index=True)
        merged = store.upsert_bars(new, market="cn")
        print(f"[bs-full] 中途落盘 {len(new)} 行（缓存 {len(merged)} 行）", flush=True)
        frames.clear()

    if frames:
        new = pd.concat(frames, ignore_index=True)
        merged = store.upsert_bars(new, market="cn")
        print(f"[bs-full] 写入 {len(new)} 行，合并后缓存 {len(merged)} 行，覆盖 "
              f"{merged['date'].min().date()} ~ {merged['date'].max().date()}")
    print(f"[bs-full] 完成：失败 {len(failed)} 只，用时 {time.time()-t0:.0f}s")
    if failed:
        print("[bs-full] 失败示例:", failed[:5])
        return 1  # 有失败必须让上层感知
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
