"""生成目标快照：把「模型目标」冻结在一个调仓周期内，避免每天重选股。

字段：model_version / data_version / generated_at / rebalance_date / next_rebalance_date / weights
用法：python scripts/build_target_snapshot.py [--markets us,hk]
"""
from __future__ import annotations
import argparse, hashlib, json, sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts"))
from lb_order_map import sleeve_targets  # noqa: E402
from quant.data import store  # noqa: E402

SLEEVES = {"us": (20, ".US", 20), "hk": (15, ".HK", 20)}


def next_rebalance(d: date, days: int) -> date:
    out, added = d, 0
    while added < days:
        out += timedelta(days=1)
        if out.weekday() < 5:
            added += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="us,hk")
    args = ap.parse_args()
    snap_path = ROOT / "state" / "target_snapshot.json"
    snap = json.loads(snap_path.read_text(encoding="utf-8")) if snap_path.exists() else {}
    snap.setdefault("sleeves", {})

    for market in [m.strip() for m in args.markets.split(",") if m.strip()]:
        _existing = (snap.get("sleeves") or {}).get(market, {})
        try:
            if _existing.get("next_rebalance_date") and date.today() <= date.fromisoformat(_existing["next_rebalance_date"]):
                print(f"[snapshot] {market} 周期内跳过（next {_existing['next_rebalance_date']}）")
                continue
        except ValueError:
            pass
        topk, suffix, reb = SLEEVES[market]
        bars = store.load_bars(market)
        data_date = bars["date"].max().date()
        weights = sleeve_targets(market, topk, suffix)
        model_hash = hashlib.md5(
            (ROOT / "models" / "model_v4.json").read_bytes()
        ).hexdigest()[:8]
        rebal = date.fromisoformat(snap.get("sleeves", {}).get(market, {}).get("rebalance_date", "")) \
            if snap.get("sleeves", {}).get(market, {}).get("rebalance_date") else data_date
        snap["sleeves"][market] = {
            "market": market,
            "model_version": model_hash,
            "data_version": str(data_date),
            "rebalance_days": reb,
            "rebalance_date": str(rebal),
            "next_rebalance_date": str(next_rebalance(data_date, reb)),
            "weights": {k: round(float(v), 6) for k, v in weights.items()},
        }
        print(f"[snapshot] {market}: {len(weights)} 只，data {data_date}，next rebalance {snap['sleeves'][market]['next_rebalance_date']}")

    snap["generated_at"] = str(date.today())
    snap_path.parent.mkdir(exist_ok=True)
    snap_path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[snapshot] -> {snap_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
