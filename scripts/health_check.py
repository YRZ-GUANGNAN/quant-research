"""模型健康检查：实盘表现 + 因子有效性 → 健康状态与建议动作。

用法：python scripts/health_check.py [--market all|us|hk|cn]
退出码：0=healthy/warning，1=degraded（建议降仓）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation.health import (assess_factors, assess_live, evaluate_health,
                                     recent_factor_ic)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="all")
    args = ap.parse_args()

    nav_path = ROOT / "reports" / "nav_history.csv"
    markets = ["us", "hk", "cn"] if args.market == "all" else [args.market]

    factors_map = {}
    recent = recent_factor_ic()
    if recent:
        factors_map = assess_factors(recent)
        print(f"[health] 近期因子 IC（60 日）：{ {k: round(v, 3) for k, v in recent.items()} }")
    else:
        print("[health] 因子 IC 计算跳过（数据不足）")

    worst = "healthy"
    payload = {}
    for m in markets:
        live = assess_live(nav_path, m)
        report = evaluate_health(live, factors_map)
        print(f"\n[{m}]")
        print(report.summary())
        payload[m] = {"status": report.status, "action": report.action,
                      "metrics": report.metrics, "alerts": report.alerts}
        if report.status == "degraded":
            worst = "degraded"
        elif report.status == "warning" and worst == "healthy":
            worst = "warning"

    out = ROOT / "reports" / "health_latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"worst": worst, "markets": payload}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n[health] 总体：{worst} -> {out}")
    return 1 if worst == "degraded" else 0


if __name__ == "__main__":
    raise SystemExit(main())
