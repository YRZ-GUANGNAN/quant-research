"""风险识别与替换引擎（自动化风控核心）。

输入：当前持仓、报价、目标快照、近期行情统计
输出：结构化动作列表（trim/exit/replace/de_risk）+ 原因 + 建议替换标的

规则（可配置，默认保守）：
1. 单票超重：权重 > max_weight → trim 到目标
2. 止损：浮亏 < -stop_loss → exit
3. 组合回撤：账户回撤 < -max_drawdown → de_risk（按比例降仓）
4. 数据陈旧：标的最后行情日期落后 → exit（无法定价）
5. 流动性不足：20 日均成交额 < 阈值 → trim/exit
6. 波动突增：20 日波动 > vol_mult × 长期中位数 → trim
7. 替换候选：从目标快照中挑「未持有且未超重」的标的，供 replace 使用
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class RiskPolicy:
    max_weight: float = 0.20           # 单票权重上限（占该市场权益）
    stop_loss: float = 0.15            # 单票止损线（浮亏 15%）
    max_drawdown: float = 0.20         # 组合回撤触发降仓
    trim_to: float = 0.15              # 超重后目标权重
    de_risk_factor: float = 0.70       # 降仓后保留比例
    min_avg_turnover: float = 2e7      # 20 日均成交额下限（元/等值）
    vol_mult: float = 2.0              # 波动率突增倍数
    stale_days: int = 3                # 行情落后容忍天数


@dataclass
class Position:
    symbol: str
    qty: float
    cost: float
    last: float
    avg_turnover: float = 0.0          # 20 日均成交额
    vol_20: float = 0.0
    vol_median: float = 0.0
    last_date: str = ""

    @property
    def market_value(self) -> float:
        return self.qty * self.last

    @property
    def pnl_pct(self) -> float:
        return (self.last / self.cost - 1.0) if self.cost > 0 else 0.0

    @property
    def weight(self) -> float:
        return 0.0  # 由调用方按 equity 填充


@dataclass
class Action:
    symbol: str
    action: str                 # trim / exit / de_risk / hold
    reason: str
    target_qty: float = 0.0
    replace_with: str = ""
    severity: str = "info"      # info / warn / critical


def evaluate(
    positions: Iterable[Position],
    *,
    equity: float,
    drawdown: float,
    policy: RiskPolicy | None = None,
    candidates: Iterable[str] = (),
    held: set[str] | None = None,
    latest_session: str = "",
) -> list[Action]:
    """对持仓做风险体检，返回动作列表（按严重度排序）。"""
    policy = policy or RiskPolicy()
    held = held or {p.symbol for p in positions}
    actions: list[Action] = []

    # 组合级：回撤触发降仓
    if drawdown < -policy.max_drawdown:
        actions.append(Action(
            symbol="*", action="de_risk", severity="critical",
            reason=f"组合回撤 {drawdown:.1%} 超过阈值 {-policy.max_drawdown:.0%}，降仓至 {policy.de_risk_factor:.0%}",
            target_qty=policy.de_risk_factor,
        ))

    replace_pool = [c for c in candidates if c not in held]

    for p in positions:
        w = (p.market_value / equity) if equity > 0 else 0.0
        # 1) 数据陈旧
        if latest_session and p.last_date and p.last_date < latest_session:
            actions.append(Action(symbol=p.symbol, action="exit", severity="critical",
                                  reason=f"行情落后（{p.last_date} < {latest_session}），无法可靠定价"))
            continue
        # 2) 止损
        if p.pnl_pct < -policy.stop_loss:
            repl = replace_pool.pop(0) if replace_pool else ""
            actions.append(Action(symbol=p.symbol, action="exit", severity="critical",
                                  reason=f"浮亏 {p.pnl_pct:.1%} 触发止损 {-policy.stop_loss:.0%}",
                                  replace_with=repl))
            continue
        # 3) 单票超重
        if w > policy.max_weight:
            target_value = equity * policy.trim_to
            target_qty = min(p.qty, target_value / p.last) if p.last > 0 else 0
            actions.append(Action(symbol=p.symbol, action="trim", severity="warn",
                                  reason=f"权重 {w:.1%} 超过上限 {policy.max_weight:.0%}",
                                  target_qty=round(target_qty)))
            continue
        # 4) 流动性不足
        if p.avg_turnover and p.avg_turnover < policy.min_avg_turnover:
            repl = replace_pool.pop(0) if replace_pool else ""
            actions.append(Action(symbol=p.symbol, action="exit", severity="warn",
                                  reason=f"20 日均成交额 {p.avg_turnover:,.0f} 低于下限 {policy.min_avg_turnover:,.0f}",
                                  replace_with=repl))
            continue
        # 5) 波动突增
        if p.vol_median > 0 and p.vol_20 > policy.vol_mult * p.vol_median:
            target_qty = p.qty * 0.5
            actions.append(Action(symbol=p.symbol, action="trim", severity="warn",
                                  reason=f"波动 {p.vol_20:.2%} 为长期中位数 {p.vol_median:.2%} 的 "
                                         f"{p.vol_20 / p.vol_median:.1f} 倍，减半",
                                  target_qty=round(target_qty)))
            continue

    order = {"critical": 0, "warn": 1, "info": 2}
    actions.sort(key=lambda a: (order.get(a.severity, 3), a.symbol))
    return actions


def summarize(actions: Iterable[Action]) -> str:
    actions = list(actions)
    if not actions:
        return "风控体检：无异常"
    crit = sum(1 for a in actions if a.severity == "critical")
    warn = sum(1 for a in actions if a.severity == "warn")
    lines = [f"风控体检：{len(actions)} 项动作（critical {crit} / warn {warn}）"]
    for a in actions:
        repl = f" → 替换为 {a.replace_with}" if a.replace_with else ""
        lines.append(f"  [{a.severity}] {a.symbol} {a.action}: {a.reason}{repl}")
    return "\n".join(lines)
