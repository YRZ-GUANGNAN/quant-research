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
MIN_ACTIONS = 10       # 至少 10 次动作才判定命中率
MAX_DD = -0.25         # 最大回撤不低于 -25%
MIN_DATA_DAYS = 10     # 最近 10 个交易日的看门狗记录不能缺


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


def execution_stats(orders_dir: Path) -> dict:
    """台账统计：交易天数、执行错误、确认率。"""
    files = sorted(orders_dir.glob("cn_orders_*.jsonl"))
    days = {f.stem.replace("cn_orders_", "") for f in files}
    total = errors = confirmed = 0
    for f in files:
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
    return {"trade_days": len(days), "exec_errors": errors,
            "confirm_total": total, "confirmed": confirmed,
            "unconfirmed_ratio": unconfirmed_ratio}


def tracking_hit(reports_dir: Path) -> dict:
    """风险动作效果：最近一份 action_tracking 的 good/bad 统计。"""
    files = sorted(reports_dir.glob("action_tracking_*.json"))
    if not files:
        return {"actions": 0, "good": 0, "bad": 0, "hit": None, "source": None}
    data = _read_json(files[-1])
    rows = data.get("actions") or data.get("records") or []
    good = sum(1 for r in rows if r.get("verdict") == "good")
    bad = sum(1 for r in rows if r.get("verdict") == "bad")
    judged = good + bad
    return {"actions": judged, "good": good, "bad": bad,
            "hit": round(good / judged, 4) if judged else None, "source": files[-1].name}


def nav_drawdown(reports_dir: Path) -> dict:
    files = sorted(reports_dir.glob("dashboard_*.json"))
    for f in reversed(files):
        data = _read_json(f)
        dd = data.get("max_drawdown") or data.get("mdd")
        if isinstance(dd, (int, float)):
            return {"max_drawdown": float(dd), "source": f.name}
    return {"max_drawdown": None, "source": None}


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
    add("数据/任务无缺口", gap == 0, f"最近 10 个工作日缺 {gap} 天记录")

    ready = all(c["pass"] for c in checks)
    return {"date": today.isoformat(), "stage": "sim", "ready_for_real": ready,
            "checks": checks,
            "gaps": [f"{c['item']}: {c['detail']}" for c in checks if not c["pass"]],
            "raw": {"exec": exec_stats, "hit": hit, "dd": dd, "watchdog_gap": gap}}


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
