"""组合层波动率目标：按已实现波动缩放袖套目标权重。

来源：2026-11-27 的实验（docs/analysis/exp-vol-target-us.md）——
美股袖套加 15% 波动率目标后，最大回撤 −24.5% → −20.6%，收益只降 1.2pp，夏普 1.02 → 1.07；
A 股袖套同样参数反而变差（夏普 0.74 → 0.57），所以**只对美股启用**，A 股维持静态仓位。

口径：
- 只用历史净值（不含未来信息），回看窗口默认 20 个交易日；
- 样本不足（< lookback + 2）时不缩放，返回 1.0 —— 不拿半桶水的数据硬调仓。
"""
from __future__ import annotations

import math


def realized_vol(values: list[float], lookback: int = 20) -> float | None:
    """年化已实现波动率（日收益标准差 × √252）。"""
    if len(values) < lookback + 1:
        return None
    window = values[-(lookback + 1):]
    rets = [window[i] / window[i - 1] - 1.0 for i in range(1, len(window)) if window[i - 1] > 0]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    vol = math.sqrt(var) * math.sqrt(252)
    return vol if vol > 0 and math.isfinite(vol) else None


def exposure_factor(values: list[float], target_vol: float | None, *,
                    lookback: int = 20, min_exposure: float = 0.5) -> tuple[float, dict]:
    """返回 (缩放系数, 诊断信息)。系数 ∈ [min_exposure, 1.0]。"""
    if not target_vol:
        return 1.0, {"reason": "未启用波动率目标"}
    vol = realized_vol(values, lookback)
    if vol is None:
        return 1.0, {"reason": f"净值样本不足（{len(values)} 天，需要 {lookback + 1} 天）"}
    factor = min(1.0, max(min_exposure, target_vol / vol))
    return round(factor, 4), {"realized_vol": round(vol, 4), "target_vol": target_vol,
                              "lookback": lookback, "min_exposure": min_exposure}
