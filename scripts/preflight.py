"""开跑前自检：凭证、磁盘、通道。让「静默失效」变成「显式报警」。

为什么要有它：全自动最脆的地方不是模型，是环境——
- Longbridge 的 AccessToken 有效期只有约 7 天，过期后每个调用都报错；
- C 盘曾经只剩 0 字节，pip/临时文件全挂，任务只留下一个看不懂的 exit code；
- 同花顺下单程序没开时，A 股整条链路静默跳过。

用法：
    python scripts/preflight.py            # 打印并写 reports/preflight.json
    python scripts/preflight.py --json
退出码：0 正常 / 1 有硬阻断（凭证过期、磁盘不足）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"

MIN_DISK_GB = 1.0        # 低于这个就硬阻断（pip/临时文件会挂）
WARN_DISK_GB = 2.0       # 低于这个要提醒清理（曾经只剩 0 字节，pip 直接失败）
ACCESS_WARN_DAYS = 2.0   # AccessToken 剩余不足 2 天就告警
REFRESH_WARN_DAYS = 5.0  # RefreshToken 剩余不足 5 天就告警

TOKEN_RE = re.compile(r"(AccessToken|RefreshToken)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")


def parse_token_text(text: str, now: datetime | None = None) -> dict:
    """从 `longbridge auth status` 的文本里解析 token 到期时间（JSON 不返回有效期）。"""
    now = now or datetime.now()
    out: dict[str, dict] = {}
    for kind, stamp in TOKEN_RE.findall(text or ""):
        try:
            expires = datetime.strptime(stamp, "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        out[kind] = {"expires_at": stamp,
                     "days_left": round((expires - now).total_seconds() / 86400, 1)}
    return out


def disk_report(paths: list[Path] | None = None) -> list[dict]:
    """检查关键盘剩余空间（Windows 上 C 盘常被临时文件吃满）。"""
    out = []
    for p in paths or [Path("C:/"), ROOT]:
        try:
            usage = shutil.disk_usage(str(p))
        except OSError as exc:
            out.append({"path": str(p), "free_gb": None, "status": "WARN",
                        "detail": f"无法读取: {type(exc).__name__}"})
            continue
        free_gb = round(usage.free / 1024 ** 3, 2)
        if free_gb < MIN_DISK_GB:
            status = "FAIL"
        elif free_gb < WARN_DISK_GB:
            status = "WARN"
        else:
            status = "PASS"
        out.append({"path": str(p), "free_gb": free_gb, "status": status,
                    "detail": f"剩余 {free_gb} GB（低于 {WARN_DISK_GB} GB 建议清理，低于 {MIN_DISK_GB} GB 阻断）"})
    return out


def eval_tokens(tokens: dict) -> dict:
    """凭证健康度：过期=FAIL，临期=WARN。"""
    if not tokens:
        return {"status": "WARN", "detail": "读不到 token 有效期（可能未登录）", "tokens": {}}
    access = (tokens.get("AccessToken") or {}).get("days_left")
    refresh = (tokens.get("RefreshToken") or {}).get("days_left")
    problems, status = [], "PASS"
    if access is None or access <= 0:
        status = "FAIL"
        problems.append("AccessToken 已过期")
    elif access < ACCESS_WARN_DAYS:
        status = "WARN"
        problems.append(f"AccessToken 只剩 {access} 天")
    if refresh is None or refresh <= 0:
        status = "FAIL"
        problems.append("RefreshToken 已过期，需要重新登录 longbridge")
    elif refresh < REFRESH_WARN_DAYS:
        status = "WARN" if status != "FAIL" else status
        problems.append(f"RefreshToken 只剩 {refresh} 天")
    return {"status": status, "detail": "；".join(problems) or "凭证有效", "tokens": tokens}


def ths_state() -> dict:
    """同花顺下单程序是否在跑（A 股通道）。不致命，只是没它就没法交易 A 股。"""
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq xiadan.exe"],
                             capture_output=True, text=True, timeout=15).stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "WARN", "detail": f"查询失败: {type(exc).__name__}"}
    running = "xiadan.exe" in out.lower()
    return {"status": "PASS" if running else "WARN",
            "detail": "下单程序在运行" if running else "下单程序未运行（A 股将跳过）"}


def lb_token_text() -> str:
    if not LB.exists():
        return ""
    try:
        proc = subprocess.run([str(LB), "auth", "status"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=40)
        return (proc.stdout or "") + (proc.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""



def clean_temp(min_age_days: int = 3) -> dict:
    """清理用户临时目录里 3 天以上没动过的文件（in-use 的跳过）。

    C 盘满了会导致 pip/剪贴板/模型缓存全部失败，而且报错信息完全看不出是磁盘问题。
    """
    import tempfile
    import time

    root = Path(tempfile.gettempdir())
    cutoff = time.time() - min_age_days * 86400
    freed = removed = skipped = 0
    for f in root.rglob("*"):
        try:
            if not f.is_file():
                continue
            st = f.stat()
            if st.st_mtime >= cutoff:
                continue
            size = st.st_size
            f.unlink()
            freed += size
            removed += 1
        except (OSError, PermissionError):
            skipped += 1   # 被占用/无权限：跳过，不影响其它文件
    return {"root": str(root), "removed": removed, "skipped": skipped,
            "freed_mb": round(freed / 1024 ** 2, 1)}


def run(now: datetime | None = None) -> dict:
    tokens = parse_token_text(lb_token_text(), now)
    checks = [
        {"item": "Longbridge 凭证", **eval_tokens(tokens)},
        *[{"item": f"磁盘 {d['path']}", **d} for d in disk_report()],
        {"item": "同花顺通道", **ths_state()},
    ]
    blocked = [c for c in checks if c["status"] == "FAIL"]
    warns = [c for c in checks if c["status"] == "WARN"]
    return {"generated_at": (now or datetime.now()).isoformat(timespec="seconds"),
            "status": "BLOCKED" if blocked else ("WARN" if warns else "OK"),
            "checks": checks,
            "blocked": [f"{c['item']}: {c.get('detail', '')}" for c in blocked]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="开跑前自检")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    ap.add_argument("--clean", action="store_true", help="先清理临时目录里 3 天以上的文件")
    args = ap.parse_args(argv)

    if args.clean:
        cleaned = clean_temp()
        print(f"[preflight] 清理临时文件 {cleaned['removed']} 个，释放 {cleaned['freed_mb']} MB"
              f"（跳过 {cleaned['skipped']} 个占用中的）")
    report = run()
    out = REPORTS / "preflight.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"=== 开跑前自检（{report['status']}）===")
        for c in report["checks"]:
            print(f"  [{c['status']}] {c['item']}: {c.get('detail', '')}")
        print(f"  -> {out}")
    return 1 if report["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
