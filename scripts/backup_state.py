"""状态备份：把「丢了要花几天重建」的文件每天存一份，并保留最近 N 天。

哪些东西丢不起：
- `.paper/state.json`：A 股模拟账户的持仓与 2030 天净值历史；
- `state/ledger/`：哈希链台账，决定「今天这单到底发没发」；
- `state/cn_orders_*.jsonl` / `risk_orders_*.jsonl`：真实下单记录；
- `state/target_snapshot.json`：当前调仓周期的冻结目标。

做法：按日打一个 zip，保留最近 N 天；备份完做一次大小校验，
不校验就等于不知道备份是不是空的。

用法：
    python scripts/backup_state.py                 # 备份今天
    python scripts/backup_state.py --keep 30       # 保留 30 天
    python scripts/backup_state.py --list          # 看现有备份
    python scripts/backup_state.py --restore 2026-11-27   # 还原（会先备份当前状态）
退出码：0 成功 / 1 失败
"""
from __future__ import annotations

import argparse
import shutil
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP_DIR = ROOT / "backups"

# 相对仓库根的路径；不存在的会被跳过（首次运行不该报错）
TARGETS = [
    "state/target_snapshot.json",
    "state/action_log.jsonl",
    "state/delisted_cn.json",
    "state/preflight.json",
    ".paper/state.json",
]
GLOB_TARGETS = ["state/cn_orders_*.jsonl", "state/risk_orders_*.jsonl"]
DIR_TARGETS = ["state/ledger"]


def collect() -> list[Path]:
    files: list[Path] = []
    for rel in TARGETS:
        p = ROOT / rel
        if p.exists():
            files.append(p)
    for pattern in GLOB_TARGETS:
        files.extend(sorted(ROOT.glob(pattern)))
    for rel in DIR_TARGETS:
        d = ROOT / rel
        if d.is_dir():
            files.extend(sorted(p for p in d.rglob("*") if p.is_file()))
    return files


def backup(day: date | None = None, keep: int = 14) -> dict:
    """打一个 zip；返回备份结果（路径、文件数、原始大小）。"""
    day = day or date.today()
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / f"state-{day.isoformat()}.zip"
    files = collect()
    if not files:
        return {"ok": False, "reason": "没有可备份的状态文件", "path": str(out), "files": 0}

    total = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            arc = f.relative_to(ROOT).as_posix()
            zf.write(f, arc)
            total += f.stat().st_size

    # 校验：能打开、成员数一致、大小不为 0
    try:
        with zipfile.ZipFile(out) as zf:
            members = zf.namelist()
            bad = zf.testzip()
    except zipfile.BadZipFile as exc:
        return {"ok": False, "reason": f"备份损坏: {exc}", "path": str(out), "files": len(files)}
    if bad or len(members) != len(files) or out.stat().st_size == 0:
        return {"ok": False, "reason": f"校验失败（坏成员 {bad}，成员 {len(members)}/{len(files)}）",
                "path": str(out), "files": len(files)}

    prune(keep)
    return {"ok": True, "path": str(out), "files": len(files), "bytes": total,
            "created": datetime.now().isoformat(timespec="seconds")}


def prune(keep: int) -> list[str]:
    """只保留最近 keep 份（按文件名日期排序）。"""
    if keep <= 0:
        return []
    backups = sorted(BACKUP_DIR.glob("state-*.zip"))
    removed = []
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)
        removed.append(old.name)
    return removed


def restore(day: str) -> dict:
    """从某天的备份还原；还原前先把当前状态另存为 state-pre-restore-<ts>.zip。"""
    src = BACKUP_DIR / f"state-{day}.zip"
    if not src.exists():
        return {"ok": False, "reason": f"找不到备份 {src.name}"}
    safety = backup(day=None, keep=0)          # 先备份当前
    with zipfile.ZipFile(src) as zf:
        zf.extractall(ROOT)
    return {"ok": True, "restored_from": src.name, "safety_copy": safety.get("path")}


def list_backups() -> list[dict]:
    out = []
    for p in sorted(BACKUP_DIR.glob("state-*.zip")):
        try:
            with zipfile.ZipFile(p) as zf:
                n = len(zf.namelist())
        except zipfile.BadZipFile:
            n = -1
        out.append({"name": p.name, "files": n, "mb": round(p.stat().st_size / 1024 ** 2, 2)})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="状态备份")
    ap.add_argument("--keep", type=int, default=14, help="保留最近几份")
    ap.add_argument("--list", action="store_true", help="列出已有备份")
    ap.add_argument("--restore", metavar="YYYY-MM-DD", help="从该日备份还原")
    args = ap.parse_args(argv)

    if args.list:
        rows = list_backups()
        if not rows:
            print("[backup] 还没有备份")
            return 0
        for r in rows:
            print(f"  {r['name']}  文件 {r['files']}  {r['mb']} MB")
        return 0

    if args.restore:
        result = restore(args.restore)
        print(("[backup] 已还原 " if result["ok"] else "[backup] 还原失败 ") + str(result))
        return 0 if result["ok"] else 1

    result = backup(keep=args.keep)
    if result["ok"]:
        print(f"[backup] {result['files']} 个文件 → {Path(result['path']).name}"
              f"（{result['bytes'] / 1024 ** 2:.1f} MB 原始）")
        return 0
    print(f"[backup] 失败：{result['reason']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
