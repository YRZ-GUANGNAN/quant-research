"""一页式绩效面板：净值 / 归因 / 风控动作 / 健康 / 对账 → Markdown。

用法：python scripts/dashboard.py [--date YYYY-MM-DD]
输出：reports/dashboard_<date>.md（可直接贴给他人看）
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def _load(name: str) -> dict | list | None:
    p = REPORTS / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return None


def _nav_rows(markets=("us", "hk", "cn")) -> dict[str, list[dict]]:
    p = REPORTS / "nav_history.csv"
    if not p.exists():
        return {}
    import csv
    out: dict[str, list[dict]] = {}
    with p.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            out.setdefault(row["market"], []).append(row)
    for m in out:
        out[m].sort(key=lambda r: r["date"])
        out[m] = out[m][-5:]
    return out


def build(day: str) -> str:
    lines = [f"# 量化系统绩效面板 · {day}", ""]

    # 1) 账户概览（来自归因报告）
    attr = _load(f"attribution_{day}.json") or {}
    if attr:
        lines += ["## 1. 账户概览", "",
                  "| 市场 | 总资产 | 现金 | 持仓市值 | 未实现盈亏 | 胜率 | top1 集中度 |",
                  "|---|---|---|---|---|---|---|"]
        for m, d in attr.items():
            s = d.get("summary", {})
            c = d.get("concentration", {})
            lines.append(f"| {m.upper()} | {d.get('equity', 0):,.0f} | {d.get('cash', 0):,.0f} | "
                         f"{d.get('market_value', 0):,.0f} | {s.get('total_pnl', 0):+,.0f} | "
                         f"{s.get('hit_rate', 0):.0%} | {c.get('top1', 0):.1%} |")
        lines.append("")
        for m, d in attr.items():
            s = d.get("summary", {})
            best = "、".join(f"{x['symbol']} {x['pnl']:+,.0f}" for x in s.get("best", [])[:3])
            worst = "、".join(f"{x['symbol']} {x['pnl']:+,.0f}" for x in s.get("worst", [])[:3])
            lines.append(f"- **{m.upper()}** 贡献最大：{best or '—'}；拖累最大：{worst or '—'}")
        lines.append("")
    else:
        lines += ["## 1. 账户概览", "", "（暂无归因报告，先运行 `python scripts/attribution.py`）", ""]

    # 2) 净值走势
    nav = _nav_rows()
    if nav:
        lines += ["## 2. 净值走势（最近 5 个记录）", ""]
        for m, rows in nav.items():
            seq = " → ".join(f"{r['date']}: {float(r['equity']):,.0f}" for r in rows)
            lines.append(f"- **{m.upper()}**：{seq}")
        lines.append("")

    # 3) 风控动作
    lines += ["## 3. 风控动作", ""]
    any_risk = False
    for m in ("us", "hk", "cn"):
        risk = _load(f"risk_act_{m}.json")
        if not risk:
            continue
        actions = risk.get("actions") or []
        if not actions:
            lines.append(f"- **{m.upper()}**：无动作")
            continue
        any_risk = True
        lines.append(f"- **{m.upper()}**：{len(actions)} 项")
        for a in actions[:6]:
            repl = f" → 替换 {a['replace_with']}" if a.get("replace_with") else ""
            lines.append(f"  - [{a.get('severity')}] {a['symbol']} {a['action']}：{a.get('reason')}{repl}")
    if not any_risk:
        lines.append("- 暂无风控动作记录")
    lines.append("")

    # 4) 模型健康
    health = _load("health_latest.json")
    lines += ["## 4. 模型健康", ""]
    if health:
        lines.append(f"- 总体：**{health.get('worst', 'unknown')}**")
        for m, d in (health.get("markets") or {}).items():
            lines.append(f"  - {m.upper()}：{d.get('status')}（建议 {d.get('action')}）")
            for a in (d.get("alerts") or [])[:2]:
                lines.append(f"    - {a}")
    else:
        lines.append("- 暂无健康报告（运行 `python scripts/health_check.py`）")
    lines.append("")

    # 5) 执行对账
    lines += ["## 5. 执行对账", ""]
    any_rec = False
    for m in ("us", "hk"):
        rec = _load(f"reconcile_{m}.json")
        if not rec:
            continue
        any_rec = True
        status = "✅ 一致" if rec.get("ok") else "❌ 存在异常"
        lines.append(f"- **{m.upper()}**：{status} — {rec.get('summary', '')}")
        for line in (rec.get("mismatched") or [])[:3]:
            lines.append(f"  - 异常：{line['symbol']} {line['side']} 期望 {line['expected_qty']} 成交 {line['executed_qty']}")
    if not any_rec:
        lines.append("- 今日无对账记录（未真实下单时属正常）")
    lines.append("")

    # 6) 风险动作效果
    track = _load(f"action_tracking_{day}.json")
    lines += ["## 6. 风险动作效果", ""]
    if track:
        lines.append(f"- 可评估 {track.get('total', 0)} 条 | 有效 {track.get('good', 0)} / "
                     f"无效 {track.get('bad', 0)} | 命中率 {track.get('hit_rate', 0):.0%}")
        for row in (track.get("rows") or [])[-5:]:
            repl = (f"，替换 {row['replace_with']} {row['replace_change']:+.1%}"
                    if row.get("replace_change") is not None else "")
            lines.append(f"  - {row['date']} {row['symbol']} {row['action']}："
                         f"{row['change']:+.1%}（{row['verdict']}）{repl}")
    else:
        lines.append("- 暂无追踪数据（运行 `python scripts/track_actions.py`）")
    lines.append("")

    lines += ["---", "",
              "> 说明：本面板由 `scripts/dashboard.py` 自动生成；数据来源为券商模拟盘/真实 A 股账户快照，",
              "> 不含任何投资建议，历史表现不代表未来收益。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    args = ap.parse_args()
    text = build(args.date)
    out = REPORTS / f"dashboard_{args.date}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text[:1500])
    print(f"[dashboard] -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
