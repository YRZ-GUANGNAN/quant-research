"""A 股交易适配层：优先 QMT（官方 xtquant），其次同花顺（easytrader），最后本地引擎。

设计原则：
- 默认 dry-run；真下单需 CN_ALLOW_EXECUTION=1；
- 通道不可用时不静默降级为「假装下单」，而是显式报告缺失项；
- 所有下单前做 policy 限额与最小下单额检查。
"""
from __future__ import annotations

import argparse
import datetime
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
    min_order_notional_cny: float = 2_000.0   # 卖单最小金额（手续费不划算就不动）
    min_buy_notional_cny: float = 3_000.0     # 买单最小金额
    lot_size: int = 100
    max_position_weight: float = 0.30         # 单票目标权重上限
    cash_buffer: float = 0.03                 # 现金留底比例
    price_buffer_buy: float = 0.02            # 买入限价上浮，提高成交概率
    price_buffer_sell: float = 0.02           # 卖出限价下浮


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



def _ensure_visible(win) -> bool:
    """把交易窗口恢复成可见（最小化时控件坐标无效，鼠标点击会点到别处）。"""
    import ctypes
    import time

    user32 = ctypes.windll.user32
    hwnd = win.handle
    for _ in range(3):
        try:
            if not user32.IsIconic(hwnd) and win.rectangle().left > -10000:
                user32.SetForegroundWindow(hwnd)
                time.sleep(0.2)
                return True
        except Exception:  # noqa: BLE001 - 查询失败就继续尝试恢复
            pass
        user32.ShowWindow(hwnd, 9)   # SW_RESTORE
        time.sleep(0.8)
    return False


def _find_trade_window():
    from pywinauto import Desktop

    for w in Desktop(backend="win32").windows():
        if "网上股票交易系统" in (w.window_text() or ""):
            return w
    return None


def _trade_window(wait_seconds: int = 60):
    """定位「网上股票交易系统」主窗口；没有就启动客户端再等（无人值守必需）。"""
    import time

    win = _find_trade_window()
    if win is not None:
        return win
    if THS_TRADE_EXE.exists():
        print("[cn] 交易客户端未运行，尝试启动")
        try:
            subprocess.Popen([str(THS_TRADE_EXE)])
        except OSError as exc:
            raise RuntimeError(f"启动交易客户端失败: {exc}") from exc
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            time.sleep(2)
            win = _find_trade_window()
            if win is not None:
                return win
    raise RuntimeError("未找到交易窗口（请确认同花顺下单程序已登录）")


def _select_query_tab(label: str) -> bool:
    """在左侧查询树里选中某个表（当日委托/资金股票/当日成交）。

    必须显式切换：表格是同一个控件，不切表就会把委托表当持仓表读。
    """
    import time

    from pywinauto import Desktop

    win = _trade_window()
    _ensure_visible(win)
    try:
        for w in Desktop(backend="uia").windows():
            if getattr(w, "handle", None) != win.handle:
                continue
            for item in w.descendants(control_type="TreeItem"):
                if (item.window_text() or "").strip() != label:
                    continue
                try:
                    item.select()          # 首选 UIA 选中
                except Exception:          # noqa: BLE001 - MFC 树控件常拒 COM 选中，退回鼠标点
                    item.click_input()
                time.sleep(0.6)
                return True
            print(f"[cn] 查询树里没有「{label}」节点")
    except Exception as exc:  # noqa: BLE001 - UIA 不可用时只能依赖人工已切好的页面
        print(f"[cn] 切换查询页「{label}」失败: {type(exc).__name__} {str(exc)[:80]}")
    return False


def _read_grid_via_clipboard(tab: str | None = None) -> list[dict]:
    """读取当前表格（全选复制解析）；tab 指定要切换的查询页。"""
    import time

    from pywinauto.keyboard import send_keys
    import win32clipboard

    win = _trade_window()
    if not _ensure_visible(win):
        print("[cn] 交易窗口无法恢复到可见状态，本次读取可能为空")
    win.set_focus(); time.sleep(0.4)
    if tab:
        _select_query_tab(tab)
    grids = win.descendants(class_name="CVirtualGridCtrl")
    if not grids:
        raise RuntimeError("未找到表格控件")
    grids[0].click_input(); time.sleep(0.3)
    send_keys("^a"); time.sleep(0.2); send_keys("^c"); time.sleep(0.8)

    win32clipboard.OpenClipboard()
    try:
        data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    rows, _ = _parse_clipboard_table(data)
    return rows


def _parse_clipboard_table(data: str) -> tuple[list[dict], list[str]]:
    """把制表符分隔的表格文本解析成 ([{列名: 值}], 表头)。

    空表只会复制到表头行，所以表头单独返回：调用方据此判断「读对了页但没数据」
    还是「读错页」——两者的区别在真金白银面前很重要。
    """
    lines = [ln for ln in data.splitlines() if ln.strip()]
    if not lines:
        return [], []
    header = [h.strip() for h in lines[0].split("\t")]
    if len(lines) < 2:
        return [], header
    rows = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.split("\t")]
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        rows.append(dict(zip(header, cells)))
    return rows, header


def _read_checked(tab: str, expect_col: str, attempts: int = 3) -> list[dict]:
    """读表格并按表头校验页面：读错页或窗口异常时必须报错而不是返回别的表。"""
    import time

    header: list[str] = []
    for i in range(attempts):
        rows = _read_grid_via_clipboard(tab)
        if rows and expect_col in rows[0]:
            return rows
        # 空表没有数据行，用表头判断是否切换成功
        header = _peek_header()
        if expect_col in header:
            return rows
        if i + 1 < attempts:
            time.sleep(1.0)
    raise RuntimeError(f"「{tab}」读取失败：表头 {header[:6]} 不含 {expect_col}")


def _peek_header() -> list[str]:
    """只取当前表格的表头（空表也能拿到），用于判断有没有切对页。"""
    import time

    from pywinauto.keyboard import send_keys
    import win32clipboard

    win = _trade_window()
    _ensure_visible(win)
    grids = win.descendants(class_name="CVirtualGridCtrl")
    if not grids:
        return []
    grids[0].click_input(); time.sleep(0.2)
    send_keys("^a"); time.sleep(0.15); send_keys("^c"); time.sleep(0.6)
    win32clipboard.OpenClipboard()
    try:
        data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()
    _, header = _parse_clipboard_table(data)
    return header


def _read_positions_via_clipboard() -> list[dict]:
    """持仓表（显式切到「资金股票」页，避免把委托表当持仓读）。"""
    return _read_checked("资金股票", "股票余额")


def _read_entrusts_via_clipboard() -> list[dict]:
    """当日委托表：空表也算成功读取（靠表头「委托数量」确认没读错页）。"""
    return _read_checked("当日委托", "委托数量")


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
    labels_norm = {lb.replace(" ", "") for lb in BALANCE_LABELS}
    for i, t in enumerate(texts):
        label = t.replace(" ", "")
        if label not in labels_norm:
            continue
        # 向后找第一个可解析为数字的文本（中间可能夹着别的标签）
        for j in range(i + 1, min(i + 4, len(texts))):
            raw = texts[j].replace(",", "").replace("%", "").strip()
            try:
                out[label] = float(raw)
                break
            except ValueError:
                continue  # noqa: silent-ok - 跳过非数值文本，继续找真正的数值
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


def _journal_path(day=None) -> Path:
    day = day or datetime.date.today()
    return ROOT / "state" / f"cn_orders_{day}.jsonl"


def load_sent(day=None) -> set[tuple[str, str]]:
    """当天已经提交过的 (代码, 方向)，用于重试幂等。"""
    path = _journal_path(day)
    sent: set[tuple[str, str]] = set()
    if not path.exists():
        return sent
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # noqa: silent-ok - 跳过损坏行，避免重试时误判
        if rec.get("status") == "confirmed":
            sent.add((str(rec.get("code")), str(rec.get("side"))))
    return sent


def append_journal(record: dict, day=None) -> None:
    path = _journal_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _ref_prices(codes: list[str]) -> dict[str, float]:
    """未持有标的的参考价：本地日线最新收盘（A 股按 6 位代码）。"""
    if not codes:
        return {}
    try:
        from quant.data import store

        bars = store.load_bars("cn")
        if bars.empty:
            return {}
        last = bars.sort_values("date").groupby("code").tail(1)
        series = last.set_index("code")["close"]
        return {c: float(series[c]) for c in codes if c in series.index}
    except Exception as exc:  # noqa: BLE001
        print(f"[cn] 参考价读取失败（无价则跳过新开仓）: {type(exc).__name__} {exc}")
        return {}


def plan_orders(
    weights: dict[str, float],
    *,
    cash: float,
    equity: float,
    holdings: dict[str, dict],
    prices: dict[str, float] | None = None,
    limits: Limits | None = None,
    sent: set[tuple[str, str]] | None = None,
) -> tuple[list[dict], list[str]]:
    """纯计算：目标权重 vs 当前持仓 → 先卖后买的委托清单（不碰下单接口）。

    holdings: {code: {"qty": 持仓股数, "sellable": 可卖股数(T+1), "price": 现价}}
    prices:   未持有标的的参考价
    sent:     今日已提交的 (代码, 方向)，命中则跳过（重试幂等）
    返回 (委托清单, 说明)
    """
    limits = limits or Limits()
    prices = prices or {}
    sent = sent or set()
    lot = limits.lot_size
    notes: list[str] = []
    holdings = holdings or {}
    if equity <= 0:
        equity = cash + sum(int(h.get("qty") or 0) * float(h.get("price") or 0) for h in holdings.values())

    def px(code: str) -> float:
        h = holdings.get(code) or {}
        p = float(h.get("price") or 0)
        return p if p > 0 else float(prices.get(code) or 0)

    sells: list[dict] = []
    buys: list[dict] = []
    proceeds = 0.0

    # 1) 清仓：持仓里但不在目标权重中
    for code, h in holdings.items():
        if code in weights:
            continue
        price, qty = px(code), int(h.get("sellable") or 0)
        if price <= 0:
            notes.append(f"{code} 清仓跳过：无参考价")
            continue
        if qty <= 0:
            notes.append(f"{code} 清仓跳过：无可卖数量（T+1 或已冻结）")
            continue
        notional = qty * price
        if notional < limits.min_order_notional_cny:
            notes.append(f"{code} 清仓跳过：金额 {notional:.0f} < {limits.min_order_notional_cny:.0f}")
            continue
        sells.append({"code": code, "side": "sell", "qty": qty, "ref_price": round(price, 2),
                      "notional": round(notional, 2), "reason": "exit"})
        proceeds += notional

    # 2) 减仓（目标低于当前）
    for code, w in weights.items():
        price = px(code)
        if price <= 0:
            continue
        h = holdings.get(code) or {}
        cur_qty = int(h.get("qty") or 0)
        sellable = int(h.get("sellable") or 0)
        w = min(float(w), limits.max_position_weight)
        gap = equity * w - cur_qty * price
        if gap >= 0 or sellable <= 0:
            continue
        qty = min(int(-gap / price) // lot * lot, sellable // lot * lot)
        if qty <= 0:
            continue
        notional = qty * price
        if notional < limits.min_order_notional_cny:
            continue
        sells.append({"code": code, "side": "sell", "qty": qty, "ref_price": round(price, 2),
                      "notional": round(notional, 2), "reason": "trim"})
        proceeds += notional

    # 3) 加仓（卖出所得当日可继续买入）
    cash_left = cash + proceeds - cash * limits.cash_buffer
    for code, w in weights.items():
        price = px(code)
        if price <= 0:
            notes.append(f"{code} 加仓跳过：无参考价")
            continue
        w = min(float(w), limits.max_position_weight)
        h = holdings.get(code) or {}
        gap = equity * w - int(h.get("qty") or 0) * price
        if gap <= 0:
            continue
        qty = int(gap / price) // lot * lot
        if qty * price > cash_left:
            qty = int(cash_left / price) // lot * lot
        notional = qty * price
        if qty <= 0 or notional < limits.min_buy_notional_cny:
            continue
        cash_left -= notional
        buys.append({"code": code, "side": "buy", "qty": qty, "ref_price": round(price, 2),
                     "notional": round(notional, 2), "reason": "open" if int(h.get("qty") or 0) == 0 else "add"})

    def _tranches(order: dict) -> list[dict]:
        """单笔超上限时拆成多笔小单（A 股按 100 股整手），避免整票被丢掉。"""
        cap = limits.max_order_notional_cny
        if order["notional"] <= cap:
            return [order]
        cap_qty = int(cap / order["ref_price"]) // lot * lot
        if cap_qty <= 0:
            notes.append(f"{order['code']} 单笔超上限且不足一手，跳过")
            return []
        out, left = [], order["qty"]
        while left > 0:
            q = min(cap_qty, left) // lot * lot
            if q <= 0:
                break
            out.append({**order, "qty": q, "notional": round(q * order["ref_price"], 2)})
            left -= q
        return out

    plan: list[dict] = []
    total = 0.0
    for order in sells + buys:  # 先卖后买
        if (order["code"], order["side"]) in sent:
            notes.append(f"{order['code']} {order['side']} 今日已提交，跳过")
            continue
        added = False
        for tr in _tranches(order):
            if len(plan) >= limits.max_orders_per_run:
                notes.append(f"达到单轮上限 {limits.max_orders_per_run} 笔，其余顺延")
                return plan, notes
            if total + tr["notional"] > limits.max_notional_per_day_cny:
                notes.append(f"达到当日金额上限 {limits.max_notional_per_day_cny:.0f}，其余顺延")
                return plan, notes
            total += tr["notional"]
            plan.append(tr)
            added = True
        if not added:
            continue
    return plan, notes


def execute_target(weights: dict[str, float], *, dry_run: bool, limits: Limits | None = None,
                   day=None) -> dict:
    """按目标权重做一次完整再平衡（先卖后买），默认只算不下单。"""
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

    row = balance if isinstance(balance, dict) else (balance[0] if balance else {})

    def _num(d: dict, *keys: str) -> float:
        for k in keys:
            if k in d and d[k] not in ("", None):
                try:
                    return float(str(d[k]).replace(",", ""))
                except ValueError:
                    print(f"[cn] 数值字段不可解析：{k}={d[k]!r}，尝试下一个键")
                    continue
        return 0.0

    holdings = {}
    for p in raw_positions:
        code = str(p.get("证券代码") or "").strip()
        if not code:
            continue
        holdings[code] = {
            "qty": int(_num(p, "股票余额", "持仓数量", "当前持仓")),
            "sellable": int(_num(p, "可用余额", "可用数量")),
            "price": _num(p, "市价", "当前价", "最新价"),
        }
    cash = _num(row, "可用金额", "可用资金", "资金可用金额")
    equity = _num(row, "总资产", "资产总值") or (cash + sum(h["qty"] * h["price"] for h in holdings.values()))

    need_px = [c for c in weights if c not in holdings]
    plan, notes = plan_orders(weights, cash=cash, equity=equity, holdings=holdings,
                              prices=_ref_prices(need_px), limits=limits, sent=load_sent(day))
    snapshot = {"cash": round(cash, 2), "equity": round(equity, 2),
                "positions": len(holdings), "targets": len(weights),
                "planned_notional": round(sum(o["notional"] for o in plan), 2)}
    if dry_run or not plan:
        return {"ok": bool(plan), "dry_run": dry_run, "account": snapshot, "orders": plan, "notes": notes}

    results = []
    for o in plan:
        buf = limits.price_buffer_sell if o["side"] == "sell" else limits.price_buffer_buy
        limit_px = round(o["ref_price"] * (1 - buf if o["side"] == "sell" else 1 + buf), 2)
        record = {**o, "limit_price": limit_px, "ts": datetime.datetime.now().isoformat(timespec="seconds")}
        try:
            if o["side"] == "sell":
                acct.sell(o["code"], price=limit_px, amount=o["qty"])
            else:
                acct.buy(o["code"], price=limit_px, amount=o["qty"])
            record["status"] = "submitted"
        except Exception as exc:  # noqa: BLE001 - 单笔失败不影响其余，逐笔记账
            record["status"] = f"error: {str(exc)[:80]}"
        append_journal(record, day)
        results.append(record)

    # 回读「当日委托」确认券商真的收下了：easytrader 的 sell/buy 在休市或被拒时
    # 也可能正常返回，「返回成功」不等于「委托成立」。这是 A 股自动化的关键一步。
    accepted: set[tuple[str, str]] = set()
    try:
        entrusts = _read_entrusts_via_clipboard()
        for e in entrusts:
            code = str(e.get("证券代码") or "").strip()
            act = str(e.get("操作") or "")
            side = "buy" if "买" in act else ("sell" if "卖" in act else "")
            note = str(e.get("备注") or "") + str(e.get("状态") or "")
            if code and side and "撤" not in note and "废" not in note:
                accepted.add((code, side))
        print(f"[cn] 回读委托 {len(entrusts)} 条，确认 {len(accepted)} 个方向")
    except Exception as exc:  # noqa: BLE001 - 回读失败不能谎报成功
        print(f"[cn] 委托回读失败（本批全部记为未确认）: {type(exc).__name__} {str(exc)[:100]}")

    for r in results:
        ok = (r["code"], r["side"]) in accepted
        append_journal({"code": r["code"], "side": r["side"], "qty": r["qty"],
                        "status": "confirmed" if ok else "unconfirmed",
                        "checked_at": datetime.datetime.now().isoformat(timespec="seconds")}, day)
        r["confirmed"] = ok

    confirmed = [r for r in results if r.get("confirmed")]
    return {"ok": len(confirmed) == len(results), "dry_run": False, "account": snapshot,
            "orders": results, "notes": notes,
            "confirmed": len(confirmed), "total": len(results)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="A 股交易通道（默认只报告状态/算单，不下单）")
    ap.add_argument("--status", action="store_true", help="只打印通道状态")
    ap.add_argument("--target", help="目标权重 JSON 路径（{code: weight}）")
    ap.add_argument("--execute", action="store_true", help="真实下单（还需 CN_ALLOW_EXECUTION=1）")
    ap.add_argument("--max-orders", type=int, default=None, help="单轮最大委托笔数")
    args = ap.parse_args(argv)

    print(status_text())
    if args.status or not args.target:
        return 0
    weights = json.loads(Path(args.target).read_text(encoding="utf-8"))
    limits = Limits()
    if args.max_orders:
        limits.max_orders_per_run = args.max_orders
    result = execute_target(weights, dry_run=not args.execute, limits=limits)
    print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
    if "CN_ALLOW_EXECUTION" in str(result.get("reason", "")):
        return 3   # 未开真实下单开关：预期内跳过，不算故障
    return 0 if result.get("ok") or result.get("dry_run") else 1


if __name__ == "__main__":
    raise SystemExit(main())
