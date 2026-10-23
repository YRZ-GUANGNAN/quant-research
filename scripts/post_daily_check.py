"""每日自动运行后的自检：数据 / 本地账户 / 券商 / 报告 是否到位。

用法：
    python scripts/post_daily_check.py
输出：reports/post_check_YYYY-MM-DD.json + 控制台 PASS/WARN 列表。
设计原则：只报告不阻塞（WARN 是正常的盘中状态，如当日日线未生成）。
"""
from __future__ import annotations
import json, os, subprocess, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from quant.data import store  # noqa: E402

LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"


def cli_json(args, timeout=40):
    p = subprocess.run([str(LB), *args, "--format", "json"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    out = (p.stdout or "").strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        s, e = out.find("{"), out.rfind("}")
        return json.loads(out[s:e + 1]) if s >= 0 else {}


def main() -> int:
    today = datetime.now().date()
    checks = []

    bars = store.load_bars("cn")
    data_max = bars["date"].max().date() if not bars.empty else None
    checks.append({"item": "A股日线最新日期", "value": str(data_max),
                   "status": "PASS" if data_max and data_max >= today else "WARN",
                   "note": "盘中未收盘时停在上一交易日属正常"})

    paper = json.loads((ROOT / ".paper" / "state.json").read_text(encoding="utf-8")) \
        if (ROOT / ".paper" / "state.json").exists() else {}
    eq = paper.get("cash", 0) + sum(p["shares"] * p.get("last_price", 0)
                                    for p in paper.get("positions", {}).values())
    checks.append({"item": "本地A股账户", "value": f"equity={eq:,.0f} date={paper.get('last_date')} pos={len(paper.get('positions', {}))}",
                   "status": "PASS" if paper else "FAIL"})

    try:
        orders = cli_json(["order"]) or []
        statuses = {}
        for o in orders:
            statuses[o.get("status", "?")] = statuses.get(o.get("status", "?"), 0) + 1
        pos = cli_json(["positions"]) or []
        checks.append({"item": "券商模拟盘", "value": f"orders={statuses} positions={len(pos)}",
                       "status": "PASS"})
    except Exception as exc:
        checks.append({"item": "券商模拟盘", "value": str(exc)[:120], "status": "WARN"})

    report = ROOT / "reports" / f"broker_daily_{today}.md"
    checks.append({"item": "今日经纪报告", "value": str(report.name),
                   "status": "PASS" if report.exists() else "WARN"})

    print(f"=== 每日自检 {today} ===")
    for c in checks:
        print(f"  [{c['status']}] {c['item']}: {c['value']}")
    warns = sum(1 for c in checks if c["status"] != "PASS")
    out = ROOT / "reports" / f"post_check_{today}.json"
    out.write_text(json.dumps({"date": str(today), "checks": checks, "warns": warns},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  WARN {warns} 项 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
