"""并行分片抓取 A 股日线。

BaoStock 单个进程只能串行查询（单只约 5-10 秒），全量 560+ 只单线程需要 1 小时以上。
这里按 shard 拆成多个进程，每个进程独立登录、独立 socket，最后合并分片。

用法：
    python scripts/fetch_parallel.py --shards 6
    python scripts/fetch_parallel.py --shards 6 --start 2018-01-01 --end 2026-10-22
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FETCH = ROOT / "scripts" / "fetch_data.py"


def main() -> int:
    ap = argparse.ArgumentParser(description="并行分片抓取")
    ap.add_argument("--shards", type=int, default=6)
    ap.add_argument("--start", default="2018-01-01")
    ap.add_argument("--end", default="2050-12-31")
    ap.add_argument("--merge-only", type=int, default=0)
    args = ap.parse_args()

    if args.merge_only:
        subprocess.run([sys.executable, "-u", str(FETCH), "--merge-shards", str(args.merge_only)], check=False)
        return 0

    print(f"[parallel] 启动 {args.shards} 个抓取进程  {args.start} ~ {args.end}")
    t0 = time.time()
    procs = []
    for i in range(args.shards):
        cmd = [
            sys.executable, "-u", str(FETCH),
            "--start", args.start, "--end", args.end,
            "--shard", str(i), "--shards", str(args.shards),
            "--skip-index",
        ]
        procs.append(subprocess.Popen(cmd))
    for i, p in enumerate(procs):
        code = p.wait()
        print(f"[parallel] 分片 {i} 退出码 {code}  ({time.time() - t0:.0f}s)")

    print("[parallel] 合并分片 ...")
    subprocess.run([sys.executable, "-u", str(FETCH), "--merge-shards", str(args.shards)], check=False)
    print(f"[parallel] 完成，总耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
