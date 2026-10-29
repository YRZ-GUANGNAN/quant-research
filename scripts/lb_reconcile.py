"""清理券商模拟盘的重复挂单（幂等性兜底）。

场景：补单逻辑曾对同一标的重复提交。本脚本按 symbol 分组，
保留最早的 1 笔，撤销其余同标的的未成交挂单。

用法：
    python scripts/lb_reconcile.py --dry-run   # 只看计划
    python scripts/lb_reconcile.py             # 真正撤单
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from quant.broker.legacy_guard import reject_execute_argv
from collections import defaultdict
from pathlib import Path

LB = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "longbridge" / "longbridge.exe"
PENDING = {"NotReported", "New", "Submitted", "WaitingSubmit", "PartFilled"}


def cli_json(args: list[str]) -> object:
    proc = subprocess.run([str(LB), *args, "--format", "json"], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=40)
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or out).strip()[:200])
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        start, end = out.find("{"), out.rfind("}")
        if start >= 0 and end > start:
            return json.loads(out[start:end + 1])
        raise



def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _rc = reject_execute_argv(argv)
    if _rc is not None:
        return _rc
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    orders = cli_json(["order"]) or []
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        if isinstance(o, dict) and o.get("symbol") and o.get("status") in PENDING:
            by_symbol[o["symbol"]].append(o)

    dup = {s: v for s, v in by_symbol.items() if len(v) > 1}
    print(f"[reconcile] 今日挂单 {sum(len(v) for v in by_symbol.values())} 笔，"
          f"涉及 {len(by_symbol)} 个标的，重复标的 {len(dup)} 个")
    if not dup:
        return 0

    cancelled = 0
    for symbol, items in dup.items():
        items.sort(key=lambda x: x.get("created_at") or "")
        keep, extras = items[0], items[1:]
        print(f"  {symbol}: 保留 {keep['order_id']}，撤销 {len(extras)} 笔")
        for o in extras:
            oid = o["order_id"]
            if args.dry_run:
                print(f"    (dry-run) cancel {oid}")
                continue
            try:
                preview = cli_json(["order", "cancel", oid])
                code = preview.get("confirmation_code")
                if not code:
                    print(f"    {oid} 无确认码，跳过")
                    continue
                cli_json(["order", "cancel", oid, "--execute", code])
                cancelled += 1
                print(f"    cancelled {oid}")
            except Exception as exc:  # noqa: BLE001
                print(f"    {oid} 取消失败: {str(exc)[:80]}")
    print(f"[reconcile] 完成，撤销 {cancelled} 笔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
