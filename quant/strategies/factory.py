"""策略工厂：把「预设 + 缓冲 + 风控叠加」组装成一个可回测对象。

回测 / 评估 / 模拟盘都从这里构造策略，保证三处行为一致。
"""

from __future__ import annotations

from ..config import BacktestConfig
from ..data.panel import Panel
from .overlay import RiskOverlay
from .presets import ICW_FACTORS, PRESETS
from .topk import TopKStrategy


def build_strategy(
    name: str,
    panel: Panel,
    cfg: BacktestConfig,
    *,
    warmup: int | None = None,
    buffer_ratio: float = 1.0,
    timing: bool = False,
    vol_target: float | None = None,
    ma_days: int = 200,
):
    """按名称构造策略，可选叠加持仓缓冲与风控层。"""
    base_warmup = warmup if warmup is not None else (150 if name == "icw" else 130)

    if name == "icw":
        from ..models.linear import ICWeightedStrategy

        base = ICWeightedStrategy(
            panel=panel, cfg=cfg, factors=ICW_FACTORS, horizon=5, warmup=base_warmup
        )
    else:
        if name not in PRESETS:
            raise KeyError(f"未知策略 {name}，可选 {sorted(PRESETS) + ['icw']}")
        base = TopKStrategy(
            panel=panel,
            cfg=cfg,
            factor_weights=PRESETS[name],
            warmup=base_warmup,
            buffer_ratio=buffer_ratio,
        )

    if timing or vol_target is not None:
        return RiskOverlay(
            panel=panel,
            cfg=cfg,
            base=base,
            timing=timing,
            vol_target=vol_target,
            ma_days=ma_days,
            warmup=base_warmup,
        )
    return base
