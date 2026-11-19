"""东方财富兜底：补齐腾讯/BaoStock 缺失的最近交易日数据（前复权）。

关键点：只有「还在交易」的标的才值得补。
长期停更（>STALE_DAYS 天）的代码属于退市/长停，永远补不到当日 bar，
过去会把它们当失败 → 每日任务恒返回 exit 1，把真实的失败信号淹掉。

用法：
    python scripts/fetch_eastmoney_fallback.py            # 补齐所有缺口
    python scripts/fetch_eastmoney_fallback.py --limit 50
退出码：0 正常（含"在交易的标的本来就没缺口"）/ 1 仍有在交易的标的缺数据
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from quant.data import eastmoney, store  # noqa: E402

KEEP = ["code", "date", "open", "high", "low", "close", "volume", "amount"]
STALE_DAYS = 10  # 超过这么久没新 bar，视为退市/长停，不再逐日重试
DELISTED_FILE = ROOT / "state" / "delisted_cn.json"


def split_stale(latest: pd.Series, global_last: pd.Timestamp) -> tuple[pd.Series, pd.Series]:
    """把最新 bar 时间序列切成「仍在交易」与「已停更」两段。"""
    cut = global_last - pd.Timedelta(days=STALE_DAYS)
    return latest[latest >= cut], latest[latest < cut]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    existing = store.load_bars("cn")
    if existing.empty:
        print("[em] 本地无 A 股缓存，跳过兜底")
        return 0

    latest = existing.groupby("code")["date"].max()
    global_last = existing["date"].max()
    target = pd.Timestamp(date.today())
    if target <= global_last:
        target = global_last  # 全库已到最新，只需要检查谁掉队

    active, stale = split_stale(latest, global_last)
    todo = active[active < target].index.tolist()
    if args.limit:
        todo = todo[: args.limit]

    if len(stale):
        DELISTED_FILE.parent.mkdir(parents=True, exist_ok=True)
        DELISTED_FILE.write_text(json.dumps(
            {"updated_at": datetime.now().isoformat(timespec="seconds"),
             "stale_days": STALE_DAYS,
             "codes": {c: str(d.date()) for c, d in stale.items()}},
            ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[em] 在交易 {len(active)} 只 / 停更 {len(stale)} 只（已跳过）")
    print(f"[em] 需补齐 {len(todo)} 只（目标日期 {target.date()}）")
    if not todo:
        print("[em] 无缺口")
        return 0

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

    frames, failed, no_bar = [], [], []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(fetch, c): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            code = futs[fut]
            try:
                _, df = fut.result()
                if df is None or df.empty:
                    no_bar.append(code)  # 今天没成交（停牌/无数据），不算故障
                else:
                    frames.append(df)
            except Exception as exc:  # noqa: BLE001, 请求级错误：记录后统一判定
                failed.append((code, str(exc)[:60]))
            if i % 100 == 0:
                print(f"  [em] {i}/{len(todo)}  {time.time()-t0:.0f}s  失败 {len(failed)}", flush=True)

    if frames:
        new = pd.concat(frames, ignore_index=True)
        merged = store.upsert_bars(new, market="cn")
        print(f"[em] 写入 {len(new)} 行，缓存 {len(merged)} 行，最新 {merged['date'].max().date()}")
    print(f"[em] 完成：成功 {len(frames)} 只，无数据 {len(no_bar)} 只，请求失败 {len(failed)} 只，"
          f"用时 {time.time()-t0:.0f}s")
    if failed:
        print("[em] 失败示例:", failed[:5])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
