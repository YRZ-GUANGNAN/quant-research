"""A 股日线备用增量源（BaoStock）——腾讯限流时使用。

背景：2026-10-23 腾讯 `web.ifzq.gtimg.cn` 对全部 563 只返回 HTTP 错误，
      日更数据停在 10-22。Longbridge 历史 K 线配额为 0，不能替代。
本脚本用 BaoStock 增量补齐「每只股票最后一个交易日之后」的数据。

注意（历史踩坑）：
1. BaoStock 是全局单 socket，login/logout 会互相踢掉 → 单会话顺序执行；
2. 服务端可能挂起 → 适配器已设 socket 超时；
3. 单只 5-10 秒，563 只全量太慢 → 只补增量，且支持 --limit 分批；
4. 并发 6 会把服务端打崩 → 本脚本单线程。

用法：
    python scripts/fetch_baostock_fallback.py --limit 30
    python scripts/fetch_baostock_fallback.py            # 全部待补
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
from quant.data.baostock_adapter import BaoStockClient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="BaoStock 增量补 A 股日线")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.05)
    args = ap.parse_args()

    ensure_dirs()
    bars = store.load_bars("cn")
    if bars.empty:
        raise SystemExit("无本地 A 股缓存，请先运行 fetch_tx.py")
    last = bars.groupby("code")["date"].max()
    today = pd.Timestamp.today().normalize()

    tasks = []
    for code, d in last.items():
        begin = (pd.Timestamp(d) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        if pd.Timestamp(begin) <= today:
            tasks.append((code, begin))
    if args.limit:
        tasks = tasks[: args.limit]
    print(f"[bs] 待补 {len(tasks)} 只（本地最后日期中位数 "
          f"{pd.Timestamp(last.median()).date()}）")

    frames, failed = [], []
    t0 = time.time()
    with BaoStockClient() as client:
        for i, (code, begin) in enumerate(tasks, 1):
            try:
                df = client.fetch_daily(code, begin, str(today.date()))
                if not df.empty:
                    frames.append(df)
            except Exception as exc:  # noqa: BLE001
                failed.append((code, str(exc)[:60]))
            if i % 25 == 0 or i == len(tasks):
                print(f"  [bs] {i}/{len(tasks)}  {time.time() - t0:.0f}s  失败 {len(failed)}")
            time.sleep(args.sleep)

    if frames:
        merged = store.upsert_bars(pd.concat(frames, ignore_index=True), "cn")
        print(f"[bs] 本次 {sum(len(f) for f in frames)} 行，缓存 {len(merged)} 行，"
              f"最新 {merged['date'].max().date()}")
    else:
        print("[bs] 无新增")
    if failed:
        print(f"[bs] 失败 {len(failed)}：{failed[:3]}（可重跑，脚本是增量的）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
