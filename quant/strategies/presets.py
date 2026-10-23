"""策略预设与因子集合（单一事实来源）。

避免 run_backtest / evaluate / run_paper 各自维护一份预设导致漂移。
"""

from __future__ import annotations

# 因子权重预设。符号来自训练期（2018-2022）IC 诊断，不是拍脑袋：
# - amihud_20 正 IC：越不流动，未来收益越高（流动性溢价）
# - rev_20 / rev_5 正 IC：短期反转
# - log_amount 负 IC：成交额越小（市值越小）未来收益越高
# - ma_gap_60 负 IC：偏离均线越多，未来回落
# - vol_20 负 IC：低波动溢价
PRESETS: dict[str, dict[str, float]] = {
    "momentum": {"mom_60": 1.0},
    "reversal": {"rev_5": 1.0},
    "multi": {"mom_60": 0.4, "rev_5": 0.3, "vol_20": -0.2, "turnover_20": -0.1},
    # v3 固定权重（决策 0005 冻结模型）
    "ic_tilt": {
        "amihud_20": 0.35,
        "rev_20": 0.20,
        "rev_5": 0.15,
        "log_amount": -0.20,
        "ma_gap_60": -0.10,
        "vol_20": -0.05,
    },
    # v5 候选：加入三段符号稳定的微观结构因子（量价相关/日内收益/隔夜跳空）
    "ic_tilt2": {
        "amihud_20": 0.30,
        "rev_20": 0.20,
        "rev_5": 0.12,
        "log_amount": -0.18,
        "ma_gap_60": -0.08,
        "vol_20": -0.05,
        "vol_price_corr_60": -0.15,
        "intraday_20": -0.10,
        "overnight_20": 0.05,
    },
}

ICW_FACTORS = [
    "amihud_20", "rev_20", "rev_5", "vol_20", "vol_60", "log_amount",
    "ma_gap_60", "ma_gap_20", "mom_120", "max_ret_20", "turnover_20",
    "beta_60", "excess_mom_60", "rsi_14", "skew_60", "volume_ratio",
]
