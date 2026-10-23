"""抓取中证500（000905）成分股与日线，存入独立市场键 `cn500`。

- 成分股：BaoStock `query_zz500_stocks(date)`，半年快照（2018-2026）
- 指数：BaoStock `sh.000905`
- 个股：腾讯前复权日线（558 只 72 秒的经验，这里约 1000 只在 2-3 分钟）

目的：用同一个 amihud 因子在更小市值池子上做**新样本外**验证。
独立市场键 `cn500` 让 Panel 与既有沪深300 数据完全隔离，互不污染。
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import ensure_dirs  # noqa: E402
from quant.data import store, tencent  # noqa: E402
from quant.data.baostock_adapter import BaoStockClient  # noqa: E402

INDEX_CODE = "000905"
SNAPSHOTS = ["2018-03-31"] + [
    f"{year}-{md}" for year in range(2018, 2027) for md in ("06-30", "12-31")
]


def fetch_members() -> pd.DataFrame:
    rows = []
    for date in SNAPSHOTS:
        for attempt in (1, 2):
            try:
                with BaoStockClient() as client:
                    rs = client.session.query_zz500_stocks(date)
                    while rs.next():
                        rows.append(rs.get_row_data())
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  [members] {date} 失败 {attempt}: {str(exc)[:80]}")
                time.sleep(1.0)
        time.sleep(0.05)
    if not rows:
        raise SystemExit("中证500 成分股抓取失败")
    raw = pd.DataFrame(rows, columns=["updateDate", "code", "code_name"])
    df = pd.DataFrame(
        {
            "snapshot_date": pd.to_datetime(raw["updateDate"]),
            "code": raw["code"].str.split(".").str[-1].str.zfill(6),
            "name": raw["code_name"],
        }
    ).drop_duplicates(["snapshot_date", "code"])
    print(f"[members] {len(df)} 条，{df['snapshot_date'].nunique()} 个时点，"
          f"{df['code'].nunique()} 只不同股票")
    return df


def main() -> int:
    ensure_dirs()
    print("[cn500] 拉取时点成分股 ...")
    members = fetch_members()
    store.save_members(INDEX_CODE, members)

    print("[cn500] 拉取基准指数 000905 ...")
    with BaoStockClient() as client:
        idx = client.fetch_index(INDEX_CODE, "2018-01-01", "2050-12-31")
    store.save_index_bars(idx, market="cn500")
    print(f"[cn500] 指数 {len(idx)} 行")

    codes = sorted(members["code"].unique())
    names = members.drop_duplicates("code").set_index("code")["name"].to_dict()
    existing = store.load_bars("cn500")
    done = set(existing["code"].unique()) if not existing.empty else set()
    todo = [c for c in codes if c not in done]
    print(f"[cn500] 股票 {len(codes)} 只，待抓 {len(todo)} 只")

    frames, failed = [], []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(tencent.fetch_daily, c, "2018-01-01", "2050-12-31"): c for c in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            code = futures[fut]
            try:
                df = fut.result()
                if not df.empty:
                    df["name"] = names.get(code, "")
                    frames.append(df)
            except Exception as exc:  # noqa: BLE001
                failed.append((code, str(exc)[:60]))
            if i % 100 == 0 or i == len(futures):
                print(f"  [bars] {i}/{len(futures)}  {time.time() - t0:.0f}s  失败 {len(failed)}")
    if frames:
        merged = store.upsert_bars(pd.concat(frames, ignore_index=True), market="cn500")
        print(f"[cn500] 写入 {sum(len(f) for f in frames)} 行，缓存 {len(merged)} 行，"
              f"{merged['code'].nunique()} 只，{merged['date'].min().date()}~{merged['date'].max().date()}")
    if failed:
        print(f"[cn500] 失败 {len(failed)}：{failed[:3]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
