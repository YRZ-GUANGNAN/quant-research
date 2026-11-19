"""等到目标市场进入交易时段再继续（用于把「开盘任务」和真实开盘对齐）。

背景：Windows 计划任务的时间是固定的，但美股开盘时间会随夏令时前后移动
1 小时。与其每年改两次触发器，不如提前触发、让脚本自己等到 Trading。

用法：python scripts/wait_for_session.py --market US --max-wait 4200
退出码：0 已进入交易时段 / 3 超时（与 execution 的 blocked 语义一致，任务视为跳过）
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quant.automation.execution import market_sessions  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="等待交易时段")
    ap.add_argument("--market", choices=["US", "HK", "CN"], required=True)
    ap.add_argument("--max-wait", type=int, default=4200, help="最长等待秒数")
    ap.add_argument("--interval", type=int, default=60, help="轮询间隔秒数")
    args = ap.parse_args(argv)

    t0 = time.time()
    while True:
        try:
            sess = market_sessions().get(args.market)
        except Exception as exc:  # noqa: BLE001 - 行情日历读不到就别等了
            print(f"[wait] 读取行情时段失败: {type(exc).__name__} {exc}")
            return 3
        elapsed = int(time.time() - t0)
        print(f"[wait] {args.market} 当前 {sess}（已等 {elapsed}s / 上限 {args.max_wait}s）", flush=True)
        if sess == "Trading":
            return 0
        if elapsed >= args.max_wait:
            print(f"[wait] 超时：{args.market} 仍未开盘，本次不下单")
            return 3
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
