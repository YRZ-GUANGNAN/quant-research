"""自动化看门狗：确认定时任务真的跑了、产物新鲜、账户状态在推进。

定位：定时任务失败时没人看控制台，所以要有独立的一层来「发现没跑」。
它只做检测与告警，不交易、不改仓位。

用法：
    python scripts/automation_watchdog.py            # 检查并写报告
    python scripts/automation_watchdog.py --quiet    # 只在有问题时说话
输出：reports/automation_health_YYYY-MM-DD.json，异常时追加 state/ALERT.json
退出码：0 正常 / 1 有缺漏（供调度器与人工使用）/ 2 周末跳过
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 工作日必须存在的产物：(文件名模板, 说明, 最晚允许出现的时刻)
REQUIRED_REPORTS = [
    ("post_check_{d}.json", "每日自检", "16:30"),
    ("broker_daily_{d}.md", "经纪商日报", "16:30"),
    ("dashboard_{d}.md", "绩效面板", "17:00"),
    ("attribution_{d}.json", "业绩归因", "17:00"),
    ("action_tracking_{d}.json", "风险动作效果", "17:00"),
]

TASKS = ["QuantDailyRun", "QuantUSOpen", "QuantCnOpen"]


def _ps_json(cmd: str, timeout: int = 60):
    """跑一段 PowerShell 拿 JSON（Windows 调度器查询没有跨语言稳定 CLI）。"""
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    out = (p.stdout or "").strip()
    if not out:
        return None, (p.stderr or "无输出").strip()[:200]
    try:
        return json.loads(out), ""
    except json.JSONDecodeError:
        return None, out[:200]


def task_status(names: list[str] | None = None) -> list[dict]:
    """查询计划任务上次运行时间与结果码。"""
    names = names or TASKS
    joined = ",".join(f"'{n}'" for n in names)
    cmd = (
        f"Get-ScheduledTask -TaskName @({joined}) -ErrorAction SilentlyContinue | "
        "ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; "
        "[pscustomobject]@{ name = $_.TaskName; state = \"$($_.State)\"; "
        "last = if ($i.LastRunTime) { $i.LastRunTime.ToString('yyyy-MM-dd HH:mm:ss') } else { '' }; "
        "result = $i.LastTaskResult } } | ConvertTo-Json -Compress"
    )
    data, err = _ps_json(cmd)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return [{"name": n, "state": "unknown", "last": "", "result": None,
                 "note": f"查询失败: {err}"} for n in names]
    return data


def check_reports(today: date, reports_dir: Path) -> list[dict]:
    """检查当日产物是否齐全。"""
    out = []
    d = today.isoformat()
    for pattern, desc, deadline in REQUIRED_REPORTS:
        path = reports_dir / pattern.format(d=d)
        if path.exists():
            out.append({"item": desc, "target": path.name, "status": "PASS",
                        "note": f"最晚 {deadline}"})
        else:
            out.append({"item": desc, "target": path.name, "status": "FAIL",
                        "note": f"缺失（应在 {deadline} 前生成）"})
    return out


def check_state(today: date) -> list[dict]:
    """本地账户状态是否推进到今天（数据管道是否断掉）。"""
    out = []
    state_file = ROOT / ".paper" / "state.json"
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        last = str(state.get("last_date", ""))
    except (OSError, json.JSONDecodeError) as exc:
        return [{"item": "本地账户状态", "target": str(state_file.name), "status": "FAIL",
                 "note": f"读取失败: {type(exc).__name__}"}]
    ok = last >= today.isoformat()
    out.append({"item": "本地账户状态", "target": f"last_date={last}", "status": "PASS" if ok else "FAIL",
                "note": "" if ok else f"停在 {last}，今天没有推进"})
    return out


def check_run_log(today: date) -> list[dict]:
    """当日是否留下 daily_run 运行日志（证明任务真的启动过）。"""
    log_dir = ROOT / "logs"
    prefix = f"daily_run_{today.strftime('%Y%m%d')}"
    hits = sorted(log_dir.glob(f"{prefix}*.log")) if log_dir.exists() else []
    return [{
        "item": "当日运行日志", "target": hits[-1].name if hits else f"{prefix}*.log",
        "status": "PASS" if hits else "WARN",
        "note": "" if hits else "没有日志，可能任务未启动或跑在旧版本脚本上",
    }]


def evaluate(today: date | None = None) -> dict:
    """汇总所有检查，返回可直接落盘的报告。"""
    today = today or date.today()
    if today.weekday() >= 5:
        return {"date": today.isoformat(), "status": "SKIP", "problems": [],
                "checks": [{"item": "工作日检查", "target": "weekend", "status": "PASS",
                            "note": "周末不跑日更流程"}]}
    checks: list[dict] = []
    checks += check_reports(today, ROOT / "reports")
    checks += check_state(today)
    checks += check_run_log(today)
    for t in task_status():
        last = str(t.get("last") or "")
        result = t.get("result")
        never_ran = result == 267011 or last.startswith("1999")
        task_ok = bool(last.startswith(today.isoformat()))
        problems = []
        if never_ran:
            status = "WARN"
            problems.append("任务从未运行（可能刚建立，等下一个触发点）")
        else:
            if not task_ok:
                problems.append(f"今天没跑（上次 {last or '从未'}）")
            if result not in (0, None):
                problems.append(f"退出码 {result}")
            status = "PASS" if not problems else "FAIL"
        checks.append({
            "item": f"任务 {t.get('name')}", "target": f"result={result}",
            "status": status, "note": "；".join(problems),
        })
    problems = [f"{c['item']}: {c['note']}" for c in checks if c["status"] == "FAIL"]
    return {"date": today.isoformat(), "status": "OK" if not problems else "ALERT",
            "problems": problems, "checks": checks}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="自动化看门狗")
    ap.add_argument("--quiet", action="store_true", help="只在有异常时输出")
    ap.add_argument("--date", default=None, help="检查指定日期（默认今天）")
    args = ap.parse_args(argv)
    target = date.fromisoformat(args.date) if args.date else date.today()

    report = evaluate(target)
    out = ROOT / "reports" / f"automation_health_{target}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({**report, "generated_at": datetime.now().isoformat(timespec="seconds")},
                              ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet or report["problems"]:
        print(f"=== 自动化看门狗 {target}（{report['status']}）===")
        for c in report["checks"]:
            print(f"  [{c['status']}] {c['item']}: {c['target']} {c['note']}".rstrip())
        if report["problems"]:
            print("  --- 异常 ---")
            for p in report["problems"]:
                print(f"  ! {p}")

    if report["status"] == "ALERT":
        alert = ROOT / "state" / "ALERT.json"
        alert.parent.mkdir(parents=True, exist_ok=True)
        alert.write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                     "date": target.isoformat(),
                                     "problems": report["problems"]}, ensure_ascii=False, indent=2),
                         encoding="utf-8")
        return 1
    alert = ROOT / "state" / "ALERT.json"
    if alert.exists():
        alert.unlink()
    return 2 if report["status"] == "SKIP" else 0


if __name__ == "__main__":
    raise SystemExit(main())
