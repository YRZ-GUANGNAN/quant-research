"""模型健康监控：判断「模型是否还在赚钱」，失效时建议降仓/停用。

三个维度：
1. 实盘净值：滚动收益、回撤、波动（来自 reports/nav_history.csv）
2. 与回测预期的偏离：实盘年化 vs 回测年化（超出容忍带 → 告警）
3. 因子有效性：近 60 日 IC vs 训练期 IC（衰减超过阈值 → 告警）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# 回测基线（4 相位，人民币口径，2026-10-24 汇率修复后）
BACKTEST = {
    "annual_return": 0.1564,
    "max_drawdown": -0.2265,
    "sharpe": 0.97,
}

# 因子训练期 IC_IR（2018-2022）
FACTOR_TRAIN_IC = {
    "amihud_20": 0.277,
    "rev_20": 0.120,
    "rev_5": 0.115,
    "log_amount": -0.243,
    "ma_gap_60": -0.139,
    "vol_20": -0.012,
}

THRESHOLD = {
    "live_return_floor": -0.10,     # 实盘滚动年化低于 -10% → 告警
    "drawdown_limit": -0.25,        # 实盘回撤超过 -25% → 告警
    "ic_decay_ratio": 0.30,         # 近期 IC 低于训练期 30% → 告警
    "divergence_band": 0.15,        # 实盘与回测年化差超过 15pp → 告警
}


@dataclass
class HealthReport:
    status: str                     # healthy / warning / degraded
    metrics: dict = field(default_factory=dict)
    alerts: list[str] = field(default_factory=list)
    action: str = "hold"            # hold / de_risk / pause

    def summary(self) -> str:
        lines = [f"模型健康：{self.status.upper()}（建议动作：{self.action}）"]
        for k, v in self.metrics.items():
            lines.append(f"  {k}: {v}")
        for a in self.alerts:
            lines.append(f"  [alert] {a}")
        return "\n".join(lines)


def _rolling_annual(equity: pd.Series) -> float:
    if len(equity) < 5:
        return 0.0
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    total = equity.iloc[-1] / equity.iloc[0] - 1.0
    return (1.0 + total) ** (365.0 / days) - 1.0


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float((equity / peak - 1.0).min())


def assess_live(nav_path: Path | str, market: str) -> dict:
    """从净值历史算实盘指标。"""
    p = Path(nav_path)
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    sub = df[df["market"] == market].copy()
    if sub.empty:
        return {}
    sub["date"] = pd.to_datetime(sub["date"])
    sub = sub.sort_values("date")
    eq = sub.set_index("date")["equity"].astype(float)
    return {
        "days": int(len(eq)),
        "live_annual": round(_rolling_annual(eq), 4),
        "live_drawdown": round(_max_drawdown(eq), 4),
        "latest_equity": round(float(eq.iloc[-1]), 2),
    }


def assess_factors(recent_ic: dict[str, float]) -> dict:
    """近期 IC 相对训练期的保留率。"""
    out = {}
    for f, train in FACTOR_TRAIN_IC.items():
        if f not in recent_ic:
            continue
        ratio = abs(recent_ic[f]) / abs(train) if train else 0.0
        out[f] = {"recent": round(recent_ic[f], 4), "train": train, "retention": round(ratio, 3)}
    return out


def evaluate_health(live: dict, factors: dict, *, thresholds: dict | None = None) -> HealthReport:
    th = {**THRESHOLD, **(thresholds or {})}
    alerts: list[str] = []
    metrics = dict(live)

    # 样本不足时不下结论（避免用 1-2 天净值误判模型失效）
    min_days = 20
    if live and live.get("days", 0) < min_days:
        metrics["note"] = f"净值样本仅 {live.get('days')} 天（<{min_days}），暂不评估收益与回撤"
        decayed = [f for f, v in factors.items() if v["retention"] < th["ic_decay_ratio"]]
        if decayed:
            alerts.append(f"因子有效性命中衰减：{', '.join(decayed)}")
        return HealthReport(status="insufficient_data" if not alerts else "warning",
                            metrics=metrics, alerts=alerts,
                            action="hold")

    if live:
        if live.get("live_annual", 0.0) < th["live_return_floor"]:
            alerts.append(f"实盘滚动年化 {live['live_annual']:.1%} 低于下限 {th['live_return_floor']:.0%}")
        if live.get("live_drawdown", 0.0) < th["drawdown_limit"]:
            alerts.append(f"实盘回撤 {live['live_drawdown']:.1%} 超过上限 {th['drawdown_limit']:.0%}")
        div = (live.get("live_annual", 0.0) - BACKTEST["annual_return"])
        metrics["divergence_vs_backtest"] = round(div, 4)
        if div < -th["divergence_band"]:
            alerts.append(f"实盘比回测低 {abs(div):.1%}，超出容忍带 {th['divergence_band']:.0%}")

    decayed = [f for f, v in factors.items() if v["retention"] < th["ic_decay_ratio"]]
    if decayed:
        alerts.append(f"因子有效性命中衰减：{', '.join(decayed)}")
    metrics["factors_tracked"] = len(factors)
    metrics["factors_decayed"] = len(decayed)

    critical = sum(1 for a in alerts if "回撤" in a or "低于下限" in a)
    if critical >= 2 or (critical >= 1 and decayed):
        status, action = "degraded", "de_risk"
    elif alerts:
        status, action = "warning", "hold"
    else:
        status, action = "healthy", "hold"
    return HealthReport(status=status, metrics=metrics, alerts=alerts, action=action)


def de_risk_from_health(health: dict, positions: list, *, keep_ratio: float = 0.7) -> list:
    """健康降级时，对所有持仓生成「降到 keep_ratio」的 de_risk 动作。"""
    from quant.automation.risk_guard import Action

    worst = (health or {}).get("worst", "healthy")
    if worst != "degraded":
        return []
    acts = []
    for p in positions:
        if getattr(p, "qty", 0) > 0:
            acts.append(Action(symbol=p.symbol, action="de_risk", severity="critical",
                               reason=f"模型健康降级（{worst}），仓位降至 {keep_ratio:.0%}",
                               target_qty=keep_ratio))
    return acts


def recent_factor_ic(lookback: int = 60) -> dict[str, float]:
    """用本地 A 股行情算最近 IC（简化：amihud/反转/动量）。"""
    try:
        from quant.data.panel import Panel
        from quant.factors.basic import build_factors
        from quant.factors.transform import forward_returns, ic_series
    except Exception:  # noqa: BLE001
        return {}
    try:
        panel = Panel.load(index_code="000300")
        f = build_factors(panel)
        fwd = forward_returns(panel.close, 5)
        out = {}
        for name in FACTOR_TRAIN_IC:
            if name not in f:
                continue
            ic = ic_series(f[name], fwd).dropna().tail(lookback)
            if len(ic) > 10 and ic.std() > 0:
                out[name] = float(ic.mean() / ic.std())
        return out
    except Exception:  # noqa: BLE001
        return {}
