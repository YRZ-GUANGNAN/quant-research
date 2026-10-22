"""抓取行情与指数成分股并写入本地缓存（BaoStock 主源）。

用法：
    python scripts/fetch_data.py                          # 全量：成分股快照 + 个股日线 + 基准
    python scripts/fetch_data.py --limit 5                # 调试
    python scripts/fetch_data.py --start 2018-01-01 --end 2026-10-22
    python scripts/fetch_data.py --refresh-members        # 重抓成分股快照
    python scripts/fetch_data.py --full                   # 忽略已有缓存

工程要点（踩过的坑）：
1. BaoStock 是**全局单 socket**，``login()/logout()`` 会影响其它会话；
   所以按阶段重建会话：先抓指数，再抓成分股（每个快照独立会话），最后抓个股。
2. 服务端可能永久挂起，适配器里设了 socket 全局超时兜底。
3. 单线程顺序抓取（BaoStock 不支持并发），每只之间 sleep 20ms 降低压力。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import INDEX_NAMES, ensure_dirs  # noqa: E402
from quant.data import store  # noqa: E402
from quant.data.baostock_adapter import BaoStockClient  # noqa: E402

# 沪深300 每年 6/12 月调样，快照取半年末；2018Q1 补一份起点。
SNAPSHOT_DATES = ["2018-03-31"] + [
    f"{year}-{md}" for year in range(2018, 2027) for md in ("06-30", "12-31")
]


def fetch_members(start: str, index_code: str) -> pd.DataFrame:
    """抓取成分股快照；每个快照独立会话，成功即增量落盘。"""
    existing = store.load_members(index_code)
    done = set(existing["snapshot_date"].dt.strftime("%Y-%m-%d")) if not existing.empty else set()
    frames = [existing] if not existing.empty else []

    for date in SNAPSHOT_DATES:
        if date < start or date in done:
            continue
        for attempt in (1, 2):
            try:
                with BaoStockClient() as client:
                    snap = client.fetch_hs300_members_snapshot(date)
                if not snap.empty:
                    frames.append(snap)
                    merged = pd.concat(frames, ignore_index=True).drop_duplicates(
                        ["snapshot_date", "code"]
                    )
                    store.save_members(index_code, merged)
                    frames = [merged]
                    print(f"  [members] {date} -> {len(snap)} 只")
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  [members] {date} 第 {attempt} 次失败: {str(exc)[:120]}")
                time.sleep(1.0)
        time.sleep(0.05)

    return (
        pd.concat(frames, ignore_index=True).drop_duplicates(["snapshot_date", "code"])
        if frames
        else pd.DataFrame()
    )


def fetch_bars(start: str, end: str, args, index_code: str) -> None:
    """抓取个股日线（阶段独立会话）。

    支持分片：BaoStock 单进程单 socket 且单只约 5-10 秒，
    全量 560+ 只需要多进程分片并行，每个进程抓 ``codes[shard::shards]``。
    """
    members_all = store.load_members(index_code)
    if members_all.empty:
        raise SystemExit("缺少成分股数据，无法确定股票池")
    codes = sorted(members_all["code"].unique())
    names = members_all.drop_duplicates("code").set_index("code")["name"].to_dict()
    if args.shards > 1:
        codes = codes[args.shard :: args.shards]
        print(f"[universe] 分片 {args.shard + 1}/{args.shards}：{len(codes)} 只")
    else:
        print(f"[universe] 历史出现过的成分股 {len(codes)} 只")
    if args.limit:
        codes = codes[: args.limit]

    existing = pd.DataFrame() if args.full else store.load_bars()
    last_date: dict[str, pd.Timestamp] = {}
    if not existing.empty:
        last_date = existing.groupby("code")["date"].max().to_dict()

    frames: list[pd.DataFrame] = []
    failed: list[tuple[str, str]] = []
    t0 = time.time()
    with BaoStockClient() as client:
        for i, code in enumerate(codes, 1):
            begin = start
            if code in last_date:
                begin = (last_date[code] + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
                if begin > pd.Timestamp.today().strftime("%Y-%m-%d"):
                    continue
            try:
                df = client.fetch_daily(code, begin, end)
                if not df.empty:
                    df["name"] = names.get(code, "")
                    frames.append(df)
            except Exception as exc:  # noqa: BLE001
                failed.append((code, str(exc)[:80]))
            if i % 25 == 0 or i == len(codes):
                print(f"  [bars] {i}/{len(codes)}  {time.time() - t0:.0f}s  失败 {len(failed)}")
            time.sleep(0.02)

    if frames:
        all_bars = pd.concat(frames, ignore_index=True)
        if args.shards > 1:
            from quant.config import CACHE_DIR

            shard_path = CACHE_DIR / f"shard_{args.shard}.csv.gz"
            all_bars.to_csv(shard_path, index=False, compression="gzip", float_format="%.6f")
            print(f"[bars] 分片 {args.shard} 写入 {len(all_bars)} 行 -> {shard_path.name}")
        else:
            merged = store.upsert_bars(all_bars)
            print(f"[bars] 本次 {len(all_bars)} 行，缓存共 {len(merged)} 行")
            print(f"[bars] 覆盖 {merged['date'].min().date()} ~ {merged['date'].max().date()}")
    else:
        print("[bars] 无新增数据")
    if failed:
        print(f"[bars] 失败 {len(failed)} 只，示例: {failed[:5]}")


def merge_shards(shards: int) -> None:
    """把各分片文件合并进主缓存。"""
    from quant.config import CACHE_DIR

    frames = []
    for i in range(shards):
        path = CACHE_DIR / f"shard_{i}.csv.gz"
        if path.exists():
            frames.append(pd.read_csv(path, dtype={"code": str}, parse_dates=["date"]))
            path.unlink()
    if not frames:
        print("[merge] 无分片文件")
        return
    merged = store.upsert_bars(pd.concat(frames, ignore_index=True))
    print(f"[merge] 合并 {len(frames)} 个分片，缓存共 {len(merged)} 行，"
          f"{merged['date'].min().date()} ~ {merged['date'].max().date()}")
    counts = merged.groupby("code")["date"].count()
    print(f"[merge] 股票数 {len(counts)}，每只中位数 {int(counts.median())} 个交易日")


def main() -> int:
    ap = argparse.ArgumentParser(description="抓取 A 股行情与成分股（BaoStock）")
    ap.add_argument("--index", default="000300")
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2050-12-31")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--refresh-members", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--shard", type=int, default=0, help="分片编号（0-based）")
    ap.add_argument("--shards", type=int, default=1, help="分片总数；>1 时结果写分片文件")
    ap.add_argument("--merge-shards", type=int, default=0, help="合并 N 个分片进主缓存")
    ap.add_argument("--skip-index", action="store_true", help="分片模式下跳过指数/成分股")
    args = ap.parse_args()

    ensure_dirs()
    if args.merge_shards:
        merge_shards(args.merge_shards)
        return 0

    index_code = str(args.index).zfill(6)
    label = INDEX_NAMES.get(index_code, index_code)
    print(f"[start] {label}({index_code})  {args.start} ~ {args.end}")

    if not args.skip_index:
        # 阶段 1：基准指数
        with BaoStockClient() as client:
            print("[index] 抓取基准指数 ...")
            idx = client.fetch_index(index_code, args.start, args.end)
            store.save_index_bars(idx)
            print(f"[index] {len(idx)} 行，{idx['date'].min().date()} ~ {idx['date'].max().date()}")

        # 阶段 2：成分股快照
        existing_members = store.load_members(index_code)
        if args.refresh_members or existing_members.empty:
            print("[members] 抓取半年成分股快照 ...")
            members = fetch_members(args.start, index_code)
            if not members.empty:
                store.save_members(index_code, members)
                print(f"[members] 已保存 {len(members)} 条记录，{members['snapshot_date'].nunique()} 个时点")
        else:
            print(f"[members] 已有缓存 {len(existing_members)} 条")

    # 阶段 3：个股日线
    fetch_bars(args.start, args.end, args, index_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
