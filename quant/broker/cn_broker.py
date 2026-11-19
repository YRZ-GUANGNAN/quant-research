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
THS_EXE = THS_DIR / "hexin.exe"          # 行情主程序
THS_TRADE_EXE = THS_DIR / "xiadan.exe"   # 网上股票交易系统（下单程序）


@dataclass
class Limits:
    max_orders_per_run: int = 20
    max_order_notional_cny: float = 100_000.0
    max_notional_per_day_cny: float = 500_000.0
    min_order_notional_cny: float = 1_000.0
    lot_size: int = 100


def _process_running(image: str) -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}"],
                             capture_output=True, text=True, timeout=10).stdout
        return image.lower() in out.lower()
    except Exception:  # noqa: BLE001
        return False


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
    trade_running = _process_running("xiadan.exe")
    inform["ths"] = {
        "available": bool(et_import and THS_TRADE_EXE.exists() and trade_running),
        "backend": "universal_client",
        "blockers": [b for b in [
            None if et_import else "未安装 easytrader",
            None if THS_TRADE_EXE.exists() else f"未找到下单程序: {THS_TRADE_EXE}",
            None if trade_running else "下单程序未运行（需打开同花顺「交易」并登录券商账户）",
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

BALANCE_LABELS = ("资金余额", "可用金额", "可取金额", "股票市值", "总 资 产", "总资产",
                  "持仓盈亏", "当日盈亏", "当日盈亏比")


def _focus_trade_window() -> bool:
    """激活「网上股票交易系统」窗口（easytrader 的表格导出依赖焦点）。"""
    try:
        from pywinauto import Desktop
        for w in Desktop(backend="win32").windows():
            if "网上股票交易系统" in (w.window_text() or ""):
                w.set_focus()
                w.restore()
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _read_positions_via_clipboard() -> list[dict]:
    """通过「全选复制」读取持仓表（THS 的 xls 导出不可靠，剪贴板稳定）。"""
    import time

    from pywinauto import Desktop
    from pywinauto.keyboard import send_keys
    import win32clipboard

    win = None
    for w in Desktop(backend="win32").windows():
        if "网上股票交易系统" in (w.window_text() or ""):
            win = w
            break
    if win is None:
        raise RuntimeError("未找到交易窗口")
    win.set_focus(); win.restore(); time.sleep(0.6)
    grids = win.descendants(class_name="CVirtualGridCtrl")
    if not grids:
        raise RuntimeError("未找到持仓表格控件")
    grids[0].click_input(); time.sleep(0.3)
    send_keys("^a"); time.sleep(0.2); send_keys("^c"); time.sleep(0.8)

    win32clipboard.OpenClipboard()
    try:
        data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()

    lines = [l for l in data.splitlines() if l.strip()]
    if len(lines) < 2:
        return []
    header = [h.strip() for h in lines[0].split("\t")]
    rows = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("\t")]
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append(dict(zip(header, cells)))
    return rows


def _read_balance_via_gui() -> dict:
    """直接从「网上股票交易系统」窗口读取资金静态标签（easytrader 与本版本不兼容）。"""
    from pywinauto import Desktop

    win = None
    for w in Desktop(backend="win32").windows():
        if "网上股票交易系统" in (w.window_text() or ""):
            win = w
            break
    if win is None:
        raise RuntimeError("未找到「网上股票交易系统」窗口")

    texts = []
    for c in win.descendants(class_name="Static"):
        try:
            t = (c.window_text() or "").strip()
        except Exception:  # noqa: BLE001
            continue
        if t:
            texts.append(t)

    out: dict[str, float] = {}
    for i, t in enumerate(texts):
        label = t.replace(" ", "")
        if any(label == lb.replace(" ", "") for lb in BALANCE_LABELS) and i + 1 < len(texts):
            raw = texts[i + 1].replace(",", "").replace("%", "")
            try:
                out[label] = float(raw)
            except ValueError:
                continue
    if not out:
        raise RuntimeError("未能从 GUI 解析出资金字段")
    return out


def _read_with_retry(getter, *, attempts: int = 4, delay: float = 1.2):
    """easytrader 的表格导出是 GUI 竞态，偶发临时文件缺失；重试即可。"""
    import time

    last = None
    for i in range(attempts):
        try:
            return getter()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(delay * (i + 1))
    raise last if last else RuntimeError("unknown read failure")


def _ths_account():
    """优先连接「网上股票交易系统」（universal_client），回退主程序（ths）。"""
    import easytrader

    errors = []
    for backend, exe in (("universal_client", THS_TRADE_EXE), ("ths", THS_EXE)):
        if not exe.exists():
            continue
        try:
            user = easytrader.use(backend)
            user.connect(str(exe))
            _ = user.balance          # 触发一次读取，确认可用
            return user
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{backend}: {type(exc).__name__} {str(exc)[:80]}")
    raise RuntimeError("同花顺通道连接失败：" + "；".join(errors))


def execute_target(weights: dict[str, float], *, dry_run: bool, limits: Limits | None = None) -> dict:
    limits = limits or Limits()
    if not dry_run and os.environ.get("CN_ALLOW_EXECUTION") != "1":
        return {"ok": False, "reason": "缺少 CN_ALLOW_EXECUTION=1，拒绝真实下单", "orders": []}
    info = detect()
    if not info["ths"]["available"]:
        return {"ok": False, "reason": "同花顺通道不可用：" + "；".join(info["ths"]["blockers"]), "orders": []}

    _focus_trade_window()
    acct = _ths_account()
    try:
        balance = _read_with_retry(_read_balance_via_gui, attempts=2)
    except Exception:  # noqa: BLE001 - 回退 easytrader（旧版本可用）
        balance = _read_with_retry(lambda: acct.balance)
    raw_positions = _read_with_retry(_read_positions_via_clipboard, attempts=3, delay=1.5)
    positions = {str(p.get("证券代码")): p for p in raw_positions if p.get("证券代码")}
    if isinstance(balance, dict):
        row = balance
    elif isinstance(balance, list) and balance:
        row = balance[0]
    else:
        row = {}

    def _num(d: dict, *keys: str) -> float:
        for k in keys:
            if k in d and d[k] not in ("", None):
                try:
                    return float(str(d[k]).replace(",", ""))
                except ValueError:
                    continue
        return 0.0

    cash = _num(row, "可用金额", "可用资金", "资金可用金额")
    equity = _num(row, "总资产", "资产总值") or (cash + sum(
        _num(p, "最新市值", "市值") for p in positions.values()))
    orders = []
    for code, w in weights.items():
        price = _num(positions.get(code, {}), "市价", "当前价", "最新价")
        if price <= 0:
            continue
        target = equity * w
        cur = _num(positions.get(code, {}), "市值", "最新市值")
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
