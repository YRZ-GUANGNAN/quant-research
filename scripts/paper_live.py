"""用 Longbridge 实时报价执行模拟盘挂单（真实行情的模拟成交）。

背景：本地模拟盘按「t 收盘算信号、t+1 开盘成交」运行，但腾讯日线接口当前限流，
10-23 的日线拿不到。Longbridge 的实时报价可用（历史 K 线配额为 0，但 quote 正常）。

做法：把模拟盘 pending 的目标权重，用**当前实时价**当作「次日开盘价」撮合，
走与回测完全相同的 broker 规则（T+1、整手、涨跌停、佣金/印花税/滑点），
成交后清空 pending 并记录净值。

这是「模拟盘实操」而非回测：价格来自真实市场，规则来自本项目引擎。
注意：用盘中价代替开盘价是近似，成交额与真实集合竞价会有差异，已在日志记录。

用法：
    python scripts/paper_live.py --dry-run   # 只看计划，不改账户
    python scripts/paper_live.py             # 真正执行
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402

from quant.config import BacktestConfig  # noqa: E402
from quant.broker import lb_bridge as lb  # noqa: E402
from quant.engine.backtest import execute_target  # noqa: E402
from quant.paper.state import PaperState  # noqa: E402


def lb_symbol(code: str) -> str | None:
    code = str(code).zfill(6)
    if code[0] in {"6", "9"}:
        return f"{code}.SH"
    if code[0] in {"0", "3"}:
        return f"{code}.SZ"
    return None


def fetch_quotes(codes: list[str]) -> dict[str, dict]:
    symbols = {c: lb_symbol(c) for c in codes}
    syms = [s for s in symbols.values() if s]
    out: dict[str, dict] = {}
    for i in range(0, len(syms), 20):
        batch = syms[i:i + 20]
        for q in lb.quote(batch):
            out[q["symbol"]] = q
    return {code: out[sym] for code, sym in symbols.items() if sym in out}


def main() -> int:
    ap = argparse.ArgumentParser(description="Longbridge 实时报价执行模拟盘挂单")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--date-label", default=None, help="nav 记录标签，默认用账户最后交易日")
    args = ap.parse_args()

    state = PaperState.load()
    if state is None:
        print("[live] 沒有模拟盘账户，请先运行 scripts/run_paper.py")
        return 1
    pending = dict(state.pending)
    if not pending:
        print("[live] 沒有待执行目标（pending 为空）")
        return 0

    print(f"[live] 待执行 {len(pending)} 只，拉取实时报价 ...")
    quotes = fetch_quotes(list(pending))
    print(f"[live] 拿到 {len(quotes)} 只报价")
    if not quotes:
        return 2

    last = pd.Series({c: float(q["last"]) for c, q in quotes.items() if q.get("last")})
    prev = pd.Series({c: float(q["prev_close"]) for c, q in quotes.items() if q.get("prev_close")})
    for code in list(pending):
        if code not in last.index:
            pending.pop(code)

    cfg = BacktestConfig(top_k=40, rebalance_days=35)
    broker = state.to_broker(cfg)
    broker.settle()
    eq_before = broker.equity()

    if args.dry_run:
        print(f"[live] dry-run：账户市值 {eq_before:,.0f} 元，将按实时价调整到 {len(pending)} 只目标")
        for code, q in list(quotes.items())[:8]:
            print(f"  {code} {lb_symbol(code)} last={q['last']} prev={q['prev_close']}")
        return 0

    date_label = args.date_label or str(state.last_date)
    execute_target(broker, cfg, date_label, last, prev, pending)
    state.sync_from_broker(broker)
    eq_after = state.equity()
    state.pending = {}
    # 保持 last_date 为最后一个有日线的交易日；live 成交只记入 nav_history
    state.nav_history = [h for h in state.nav_history if h.get("date") != date_label]
    state.nav_history.append(
        {"date": date_label, "equity": round(eq_after, 2), "cash": round(state.cash, 2),
         "n_positions": len(state.positions)}
    )
    state.save()

    n_buy = sum(1 for t in broker.trades if t.side == "buy")
    n_sell = sum(1 for t in broker.trades if t.side == "sell")
    fee = sum(t.fee for t in broker.trades)
    print(f"[live] 已执行：买入 {n_buy} 笔、卖出 {n_sell} 笔，费用 {fee:,.2f} 元")
    print(f"[live] 市值 {eq_before:,.0f} -> {eq_after:,.0f} 元，持仓 {len(state.positions)} 只")
    print(f"[live] 账户已保存：{PaperState.path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
