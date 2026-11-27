"""开跑前金丝雀：一条命令回答「下一个交易时段能不能按计划跑」。

它不是重复实现，而是**编排**已有的检查，把散落在多个脚本里的结论汇成一个 GO / NO-GO：

    preflight → 数据门禁 → 各市场算单（dry） → 下单前模拟 → 权重漂移 → 晋级闸门

任何一项红就 NO-GO，并给出具体原因与对应文件。人工上线前跑这一条就够，
也适合在调参之后做回归验证。

用法：
    python scripts/canary.py                    # 全部市场
    python scripts/canary.py --markets us       # 只看美股
    python scripts/canary.py --json
退出码：0 GO / 1 NO-GO（有阻断项）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
PY = sys.executable

# 美股袖套当前参数（与 us_open_run 保持一致，避免"检查的和实际跑的不是一回事"）
US_CASH_SHARE = 0.35
US_VOL_TARGET = 0.12


def run(script_args: list[str], timeout: int = 600) -> tuple[int, str]:
    try:
        proc = subprocess.run([PY, *script_args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              cwd=str(ROOT))
    except subprocess.TimeoutExpired:
        return 124, f"超时（>{timeout}s）"
    except OSError as exc:
        return 125, f"无法执行: {exc}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, out


def summarize(text: str, keep: int = 3) -> str:
    """取输出里最关键几行（[FAIL]/[!]/结论行）。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    key = [ln for ln in lines if any(k in ln for k in ("[FAIL]", "[!]", "NO-GO", "结论", "未通过", "阻断"))]
    return " | ".join((key or lines)[-keep:])[:300]


def check(name: str, args: list[str], *, blocking: bool, ok_codes=(0,)) -> dict:
    code, out = run(args)
    ok = code in ok_codes
    return {"item": name, "ok": ok, "blocking": blocking, "exit": code,
            "detail": summarize(out) if not ok else ""}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="开跑前金丝雀")
    ap.add_argument("--markets", default="us,hk", help="逗号分隔：us,hk")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    markets = [m.strip() for m in args.markets.split(",") if m.strip()]

    checks: list[dict] = []

    # 1) 环境（凭证/磁盘/通道）：磁盘或凭证挂了就没必要往下走
    checks.append(check("环境自检 preflight", ["scripts/preflight.py"], blocking=True))

    # 2) 数据门禁（每个市场单独）
    for m in markets:
        checks.append(check(f"数据门禁 {m.upper()}", ["scripts/pre_trade_gate.py", "--markets", m],
                            blocking=True))

    # 3) 算单（dry-run）：拿不到计划就是 NO-GO
    for m in markets:
        extra = (["--cash-share", str(US_CASH_SHARE), "--vol-target", str(US_VOL_TARGET)]
                 if m == "us" else [])
        checks.append(check(f"算单 {m.upper()}", ["-m", "quant.automation.execution", "--market", m, *extra],
                            blocking=True, ok_codes=(0, 3)))

    # 4) 下单前模拟：现金为负/超卖是硬阻断
    for m in markets:
        extra = (["--cash-share", str(US_CASH_SHARE), "--vol-target", str(US_VOL_TARGET)]
                 if m == "us" else [])
        checks.append(check(f"计划模拟 {m.upper()}", ["scripts/simulate_plan.py", "--market", m, *extra],
                            blocking=True, ok_codes=(0, 3)))

    # 4b) 资金分布：闲置太多等于模型被摊薄（不阻断交易，但影响上实钱判定）
    checks.append(check("资金分布", ["scripts/capital_check.py"], blocking=False, ok_codes=(0, 1, 3)))

    # 5) 权重漂移：只报告，不阻断（换手上限导致的分次执行是正常的）
    checks.append(check("权重漂移", ["scripts/weight_drift.py", "--markets", ",".join(markets)],
                        blocking=False, ok_codes=(0, 1, 3)))

    # 6) 晋级闸门：不阻断交易，但决定"能不能上实钱"
    checks.append(check("晋级闸门", ["scripts/promotion_check.py"], blocking=False, ok_codes=(0, 1)))

    blocked = [c for c in checks if c["blocking"] and not c["ok"]]
    canary = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "verdict": "NO-GO" if blocked else "GO",
              "blocking_failures": [f"{c['item']}（exit {c['exit']}）：{c['detail']}" for c in blocked],
              "checks": checks}
    out = REPORTS / "canary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(canary, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(canary, ensure_ascii=False, indent=2))
    else:
        print(f"=== 金丝雀：{canary['verdict']} ===")
        for c in checks:
            tag = "✅" if c["ok"] else ("❌" if c["blocking"] else "⚠️")
            print(f"  {tag} {c['item']}")
            if not c["ok"] and c["detail"]:
                print(f"      {c['detail']}")
        if blocked:
            print("  --- 阻断项 ---")
            for b in canary["blocking_failures"]:
                print(f"  · {b}")
        print(f"  -> {out}")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
