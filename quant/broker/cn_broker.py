"""A 股交易适配层：优先 QMT（官方 xtquant），其次同花顺（easytrader），最后本地引擎。

设计原则：
- 默认 dry-run；真下单需 CN_ALLOW_EXECUTION=1；
- 通道不可用时不静默降级为「假装下单」，而是显式报告缺失项；
- 所有下单前做 policy 限额与最小下单额检查。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THS_DIR = Path(r"C:\同花顺软件\同花顺")
THS_EXE = THS_DIR / "hexin.exe"


@dataclass
class Limits:
    max_orders_per_run: int = 20
    max_order_notional_cny: float = 100_000.0
    max_notional_per_day_cny: float = 500_000.0
    min_order_notional_cny: float = 1_000.0
    lot_size: int = 100


def _ths_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq hexin.exe"],
                             capture_output=True, text=True, timeout=10).stdout
        return "hexin.exe" in out
    except Exception:  # noqa: BLE001
        return False


def detect() -> dict:
    """返回各 A 股通道的可用性与阻塞原因。"""
    inform = {}
    # QMT / xtquant
    qmt_path = os.environ.get("QMT_USERDATA", "")
    try:
        import xtquant  # noqa: F401
        qmt_import = True
    except Exception:  # noqa: BLE001
        qmt_import = False
    inform["qmt"] = {
        "available": bool(qmt_import and qmt_path and Path(qmt_path).exists()),
        "blockers": [b for b in [
            None if qmt_import else "未安装 xtquant（需安装 QMT 客户端）",
            None if qmt_path else "未设置 QMT_USERDATA（QMT 的 userdata_mini 路径）",
            None if not qmt_path or Path(qmt_path).exists() else f"QMT_USERDATA 路径不存在: {qmt_path}",
        ] if b],
    }
    # 同花顺 / easytrader
    try:
        import easytrader  # noqa: F401
        et_import = True
    except Exception:  # noqa: BLE001
        et_import = False
    inform["ths"] = {
        "available": bool(et_import and THS_EXE.exists() and _ths_running()),
        "blockers": [b for b in [
            None if et_import else "未安装 easytrader",
            None if THS_EXE.exists() else f"未找到同花顺客户端: {THS_EXE}",
            None if _ths_running() else "同花顺客户端未运行（需手动启动并登录券商账户）",
        ] if b],
    }
    inform["local"] = {"available": True, "blockers": []}
    return inform


def status_text() -> str:
    info = detect()
    lines = ["A 股交易通道状态："]
    for name, item in info.items():
        mark = "OK " if item["available"] else "NO "
        lines.append(f"  [{mark}] {name}")
        for b in item["blockers"]:
            lines.append(f"        - {b}")
    usable = [k for k, v in info.items() if v["available"] and k != "local"]
    lines.append(f"  可用真实通道: {usable or '无（当前只能走本地模拟引擎）'}")
    return "\n".join(lines)


# ---------------- 下单（受控） ----------------

def _ths_account():
    import easytrader

    user = easytrader.use("ths")
    user.connect(str(THS_EXE))
    return user


def execute_target(weights: dict[str, float], *, dry_run: bool, limits: Limits | None = None) -> dict:
    limits = limits or Limits()
    if not dry_run and os.environ.get("CN_ALLOW_EXECUTION") != "1":
        return {"ok": False, "reason": "缺少 CN_ALLOW_EXECUTION=1，拒绝真实下单", "orders": []}
    info = detect()
    if not info["ths"]["available"]:
        return {"ok": False, "reason": "同花顺通道不可用：" + "；".join(info["ths"]["blockers"]), "orders": []}

    acct = _ths_account()
    balance = acct.balance
    positions = {p["证券代码"]: p for p in (acct.position or [])}
    cash = float(balance[0].get("可用金额", 0)) if isinstance(balance, list) and balance else 0.0
    equity = cash + sum(float(p.get("最新市值", 0)) for p in positions.values())
    orders = []
    for code, w in weights.items():
        price = float(positions.get(code, {}).get("当前价") or 0)
        if price <= 0:
            continue
        target = equity * w
        cur = float(positions.get(code, {}).get("最新市值") or 0)
        gap = target - cur
        if gap < limits.min_order_notional_cny:
            continue
        qty = int(gap / price) // limits.lot_size * limits.lot_size
        if qty <= 0:
            continue
        value = qty * price
        if value > limits.max_order_notional_cny or len(orders) >= limits.max_orders_per_run:
            continue
        orders.append({"code": code, "side": "buy", "qty": qty, "price": price,
                       "notional": round(value, 2)})
        if sum(o["notional"] for o in orders) > limits.max_notional_per_day_cny:
            orders.pop()
            break
    if dry_run or not orders:
        return {"ok": bool(orders), "dry_run": dry_run, "cash": cash, "orders": orders}
    results = []
    for o in orders:
        try:
            acct.buy(o["code"], price=o["price"], amount=o["qty"])
            results.append({**o, "status": "submitted"})
        except Exception as exc:  # noqa: BLE001
            results.append({**o, "status": f"error: {str(exc)[:80]}"})
    return {"ok": all(r["status"] == "submitted" for r in results), "orders": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A 股交易通道（默认只报告状态）")
    ap.add_argument("--status", action="store_true", help="只打印通道状态")
    ap.add_argument("--target", help="目标权重 JSON 路径（{code: weight}）")
    ap.add_argument("--execute", action="store_true", help="真实下单（还需 CN_ALLOW_EXECUTION=1）")
    args = ap.parse_args(argv)

    print(status_text())
    if args.status or not args.target:
        return 0
    weights = json.loads(Path(args.target).read_text(encoding="utf-8"))
    result = execute_target(weights, dry_run=not args.execute)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:3000])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
