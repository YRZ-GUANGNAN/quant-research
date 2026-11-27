"""晋级闸门：模拟阶段跑多久、跑多稳，才算「可以上实钱」。

设计：这道闸门只做判定和建议，不自动切真实资金——上钱的最后一步必须是人按的。
判定口径（全部可复算，不用主观拍脑袋）：
  1. 样本够：模拟连续运行 ≥ MIN_DAYS 个交易日（按台账/净值记录的日期去重计数）；
  2. 执行稳：台账里没有执行错误（status 以 error 开头），且「已提交但券商未确认」
     的比例 ≤ MAX_UNCONFIRMED；
  3. 跟踪有效：风险动作效果追踪里 good 占比 ≥ MIN_HIT（样本 ≥ MIN_ACTIONS 次才判定）；
  4. 回撤可控：模拟净值最大回撤 ≥ MAX_DD（负数比较）；
  5. 数据不断：产物/看门狗最近 MIN_DATA_DAYS 天内没有缺失记录。

用法：
    python scripts/promotion_check.py            # 判定并打印
    python scripts/promotion_check.py --json     # 只输出 JSON
输出：reports/promotion_status.json
退出码：0 已具备上实钱条件 / 1 还差条件
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIN_DAYS = 20          # 至少 20 个交易日
MAX_UNCONFIRMED = 0.05  # 未确认委托占比 ≤ 5%
MIN_HIT = 0.55         # 风险动作命中率 ≥ 55%
MIN_SHARPE = 0.5       # 实盘夏普 ≥ 0.5（用净值曲线算，不进回测）
MIN_ACTIONS = 10       # 至少 10 次动作才判定命中率
MAX_DD = -0.25         # 最大回撤不低于 -25%
MIN_DATA_DAYS = 10     # 最近 10 个交易日的看门狗记录不能缺
ERROR_WINDOW_DAYS = 20  # 执行报错只看最近 20 个交易日（窗口与样本天数一致）


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _read_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # noqa: silent-ok - 损坏行跳过，统计口径不受影响
    return out


def trade_days(reports_dir: Path, state_dir: Path) -> set[str]:
    """真正跑过交易的日子：归因报告 / 动作日志 / 各市场下单台账 / 净值曲线，取并集。"""
    days: set[str] = set()
    for f in reports_dir.glob("attribution_*.json"):
        days.add(f.stem.replace("attribution_", ""))
    for pattern in ("cn_orders_*.jsonl", "risk_orders_*.jsonl"):
        for f in state_dir.glob(pattern):
            days.add(f.stem.split("_")[-1])
    log = state_dir / "action_log.jsonl"
    if log.exists():
        for rec in _read_jsonl(log):
            if rec.get("date"):
                days.add(str(rec["date"]))
    nav = reports_dir / "nav_history.csv"
    if nav.exists():
        import csv

        with nav.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("date"):
                    days.add(str(row["date"]))
    return {d for d in days if len(d) == 10 and d[4] == "-"}


def execution_stats(orders_dir: Path, reports_dir: Path | None = None) -> dict:
    """台账统计：交易日数（全市场并集）、执行错误、确认率。"""
    files = sorted(orders_dir.glob("cn_orders_*.jsonl"))
    all_days = trade_days(reports_dir or (ROOT / "reports"), orders_dir)
    days = all_days
    cutoff = sorted(all_days)[-ERROR_WINDOW_DAYS] if len(all_days) > ERROR_WINDOW_DAYS else ""
    files_recent = [f for f in files if not cutoff or f.stem.split("_")[-1] >= cutoff]
    total = errors = confirmed = 0
    for f in files_recent:
        for rec in _read_jsonl(f):
            status = str(rec.get("status", ""))
            if status in ("sent",):
                continue
            if status.startswith("error"):
                errors += 1
                continue
            if status == "confirmed":
                confirmed += 1
                total += 1
            elif status == "unconfirmed":
                total += 1
    unconfirmed_ratio = 0.0 if total == 0 else round((total - confirmed) / total, 4)
    return {"trade_days": len(days), "exec_errors": errors, "error_window_days": min(len(days), ERROR_WINDOW_DAYS),
            "confirm_total": total, "confirmed": confirmed,
            "unconfirmed_ratio": unconfirmed_ratio}


def tracking_hit(reports_dir: Path) -> dict:
    """风险动作效果：最近一份 action_tracking 的 good/bad 统计。"""
    files = sorted(reports_dir.glob("action_tracking_*.json"))
    if not files:
        return {"actions": 0, "good": 0, "bad": 0, "hit": None, "source": None}
    data = _read_json(files[-1])
    # track_actions 的输出把汇总放在顶层（total/good/bad/hit_rate），明细在 rows；
    # 之前只读 actions/records，导致「有证据却判定为 0 样本」。
    rows = data.get("rows") or data.get("actions") or data.get("records") or []
    good = data.get("good")
    bad = data.get("bad")
    if good is None or bad is None:
        good = sum(1 for r in rows if r.get("verdict") == "good")
        bad = sum(1 for r in rows if r.get("verdict") == "bad")
    judged = int(good) + int(bad)
    hit = data.get("hit_rate")
    if hit is None:
        hit = round(good / judged, 4) if judged else None
    return {"actions": judged, "good": int(good), "bad": int(bad),
            "hit": round(float(hit), 4) if hit is not None else None,
            "total_rows": len(rows), "source": files[-1].name}


def nav_drawdown(reports_dir: Path) -> dict:
    """最大回撤：分市场算。

    不能把各市场当日净值相加——各市场记录天数不一致（有的日子只有港股），
    相加会凭空造出几十个点的假回撤。分市场算完取最差的那个，口径更保守。
    """
    nav = reports_dir / "nav_history.csv"
    if not nav.exists():
        return {"max_drawdown": None, "source": None}
    import csv
    from collections import defaultdict

    series: dict[str, list[tuple[str, float]]] = defaultdict(list)
    with nav.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                series[str(row["market"])].append((str(row["date"]), float(row["equity"])))
            except (KeyError, TypeError, ValueError):
                continue  # noqa: silent-ok - 坏行跳过，不影响整体回撤口径

    per_market: dict[str, float] = {}
    for market, rows in series.items():
        rows.sort()
        peak, mdd = 0.0, 0.0
        for _, eq in rows:
            peak = max(peak, eq)
            if peak > 0:
                mdd = min(mdd, eq / peak - 1.0)
        per_market[market] = round(mdd, 4)

    usable = {m: v for m, v in per_market.items() if len(series[m]) >= 2}
    if not usable:
        return {"max_drawdown": None, "source": nav.name, "per_market": per_market}
    worst_market = min(usable, key=lambda m: usable[m])
    return {"max_drawdown": usable[worst_market], "source": f"{nav.name}:{worst_market}",
            "per_market": usable}


def realized_sharpe(reports_dir: Path) -> dict:
    """从净值绩效报告取合计夏普（没有就先跑 equity_curve.py）。"""
    data = _read_json(reports_dir / "equity_curve.json")
    total = data.get("total") or {}
    return {"sharpe": total.get("sharpe"), "days": total.get("days"),
            "return": total.get("return"), "source": "equity_curve.json" if data else None}


def watchdog_gap(reports_dir: Path, today: date | None = None) -> int:
    """最近 MIN_DATA_DAYS 个自然日里缺了多少天看门狗记录（非交易日不管）。"""
    today = today or date.today()
    missing = 0
    for i in range(MIN_DATA_DAYS):
        d = today.fromordinal(today.toordinal() - i)
        if d.weekday() >= 5:
            continue
        if not (reports_dir / f"automation_health_{d.isoformat()}.json").exists():
            missing += 1
    return missing


def evaluate(today: date | None = None) -> dict:
    today = today or date.today()
    exec_stats = execution_stats(ROOT / "state")
    hit = tracking_hit(ROOT / "reports")
    dd = nav_drawdown(ROOT / "reports")
    gap = watchdog_gap(ROOT / "reports", today)

    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"item": name, "pass": bool(ok), "detail": detail})

    add("模拟运行天数", exec_stats["trade_days"] >= MIN_DAYS,
        f"{exec_stats['trade_days']} 天（需 ≥ {MIN_DAYS}）")
    add("执行零报错", exec_stats["exec_errors"] == 0,
        f"{exec_stats['exec_errors']} 次报错")
    add("委托确认率", exec_stats["unconfirmed_ratio"] <= MAX_UNCONFIRMED,
        f"未确认占比 {exec_stats['unconfirmed_ratio'] * 100:.1f}%（需 ≤ {MAX_UNCONFIRMED * 100:.0f}%）")
    add("风险动作命中率", hit["hit"] is not None and hit["actions"] >= MIN_ACTIONS and hit["hit"] >= MIN_HIT,
        f"good {hit['good']} / 判定 {hit['actions']}（需 ≥ {MIN_ACTIONS} 次且 ≥ {MIN_HIT * 100:.0f}%）")
    add("最大回撤", dd["max_drawdown"] is not None and dd["max_drawdown"] >= MAX_DD,
        f"{dd['max_drawdown'] if dd['max_drawdown'] is not None else '无数据'}（需 ≥ {MAX_DD * 100:.0f}%）")
    sh = realized_sharpe(ROOT / "reports")
    add("实盘夏普", sh["sharpe"] is not None and sh["sharpe"] >= MIN_SHARPE,
        f"{sh['sharpe'] if sh['sharpe'] is not None else '无数据'}（需 ≥ {MIN_SHARPE}，样本 {sh['days'] or 0} 天）")
    add("数据/任务无缺口", gap == 0, f"最近 10 个工作日缺 {gap} 天记录")

    ready = all(c["pass"] for c in checks)
    return {"date": today.isoformat(), "stage": "sim", "ready_for_real": ready,
            "checks": checks,
            "gaps": [f"{c['item']}: {c['detail']}" for c in checks if not c["pass"]],
            "raw": {"exec": exec_stats, "hit": hit, "dd": dd, "sharpe": sh, "watchdog_gap": gap}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="模拟 → 实钱 晋级闸门")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args(argv)

    report = evaluate()
    out = ROOT / "reports" / "promotion_status.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**report, "generated_at": datetime.now().isoformat(timespec="seconds")},
                              ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"=== 晋级闸门（{report['date']}）：{'可以上实钱' if report['ready_for_real'] else '继续模拟'} ===")
        for c in report["checks"]:
            print(f"  [{'PASS' if c['pass'] else '待办'}] {c['item']}: {c['detail']}")
        if report["gaps"]:
            print("  --- 还差 ---")
            for g in report["gaps"]:
                print(f"  · {g}")
        print(f"  -> {out}")
    return 0 if report["ready_for_real"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
