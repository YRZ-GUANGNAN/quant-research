"""LightGBM 滚动训练选股（walk-forward）。

防未来函数的三道闸门：
1. **特征**：因子全部只用 t 日及以前的滚动窗口；
2. **标签**：y 是 t→t+h 的前瞻收益，因此 t 日训练样本最晚只能到 t-h 日
   （标签期必须已经结束）；
3. **训练窗口**：只用 t 之前的数据，绝不使用 t 之后任何一天。

训练策略：滚动窗口（默认 750 个交易日）+ 每 60 个交易日重训一次，
避免每次调仓都重训带来的时间开销，也避免模型频繁抖动。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..config import BacktestConfig
from ..data.panel import Panel
from ..factors.basic import build_factors
from ..factors.transform import forward_returns

DEFAULT_FEATURES = [
    "mom_20", "mom_60", "mom_120", "rev_5", "rev_20",
    "vol_20", "vol_60", "max_ret_20", "skew_60",
    "turnover_20", "turnover_ratio", "volume_ratio", "amihud_20",
    "ma_gap_20", "ma_gap_60", "rsi_14",
    "log_amount", "log_price", "excess_mom_60", "beta_60",
]


@dataclass
class LGBMStrategy:
    """用 LightGBM 预测未来 h 日截面收益排名，选 Top-K。"""

    panel: Panel
    cfg: BacktestConfig
    features: list[str] = field(default_factory=lambda: list(DEFAULT_FEATURES))
    horizon: int = 5
    train_window: int = 750
    retrain_every: int = 60
    warmup: int = 300
    num_leaves: int = 15
    n_estimators: int = 200
    learning_rate: float = 0.05
    min_child_samples: int = 60

    _X: pd.DataFrame = field(init=False, repr=False)      # MultiIndex (date, code) x features
    _y: pd.Series = field(init=False, repr=False)         # 前瞻收益的截面排名
    _dates: pd.DatetimeIndex = field(init=False, repr=False)
    _last_train: int = field(init=False, default=-10**9, repr=False)
    _model: object = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        factors = build_factors(self.panel)
        missing = [f for f in self.features if f not in factors]
        if missing:
            raise KeyError(f"未知特征: {missing}")

        # 1) 展平成 (date, code) x feature 的长表
        stacked = {
            name: factors[name].stack(future_stack=True) for name in self.features
        }
        X = pd.concat(stacked, axis=1)
        X.columns = self.features

        # 2) 自动剔除「整列全空」的特征：数据源缺少换手率时 turnover 类因子不可用。
        #    LightGBM 原生支持 NaN，所以不需要把缺失行整行丢掉。
        coverage = X.notna().mean()
        keep = coverage[coverage > 0.3].index.tolist()
        if len(keep) < 3:
            raise RuntimeError(f"可用特征不足：{keep}")
        dropped = [c for c in self.features if c not in keep]
        if dropped:
            print(f"[lgbm] 剔除缺失率过高的特征: {dropped}")
        self.features = keep
        self._X = X[keep]

        # 2) 标签：未来 h 日收益的截面排名（0~1），对异常值更稳健
        fwd = forward_returns(self.panel.close, self.horizon)
        y = fwd.rank(axis=1, pct=True).stack(future_stack=True)
        self._y = y.reindex(self._X.index)

        self._dates = self.panel.dates

    # ---------- 训练 ----------

    def _train(self, t: int) -> None:
        import lightgbm as lgb

        cutoff = self._dates[t - self.horizon]          # 标签期已结束的最后一天
        start = self._dates[max(0, t - self.train_window)]
        idx = self._X.index
        mask = (idx.get_level_values(0) <= cutoff) & (idx.get_level_values(0) >= start)
        X = self._X[mask]
        y = self._y[mask]
        # 标签必须存在；特征允许部分缺失（LightGBM 原生处理 NaN），
        # 但至少要有半数特征可算，避免整行是噪声。
        min_features = max(3, len(self.features) // 2)
        valid = y.notna() & (X.notna().sum(axis=1) >= min_features)
        X, y = X[valid], y[valid]
        if len(X) < 5000:
            raise RuntimeError(f"训练样本不足：{len(X)}")

        model = lgb.LGBMRegressor(
            num_leaves=self.num_leaves,
            n_estimators=self.n_estimators,
            learning_rate=self.learning_rate,
            min_child_samples=self.min_child_samples,
            subsample=0.8,
            colsample_bytree=0.8,
            reg_lambda=1.0,
            random_state=42,
            n_jobs=4,
            verbose=-1,
        )
        model.fit(X, y)
        self._model = model
        self._last_train = t

    # ---------- 预测 ----------

    def predict(self, date: pd.Timestamp) -> pd.Series:
        if self._model is None:
            raise RuntimeError("模型尚未训练")
        idx = self._X.index
        try:
            rows = self._X.loc[date]
        except KeyError:
            return pd.Series(dtype=float)
        rows = rows.dropna()
        if rows.empty:
            return pd.Series(dtype=float)
        return pd.Series(self._model.predict(rows), index=rows.index)

    def target_weights(self, date: pd.Timestamp) -> dict[str, float]:
        pred = self.predict(date)
        if pred.empty:
            return {}
        universe = self.panel.universe_at(date)
        pred = pred[pred.index.isin(universe)]
        amount = self.panel.get("amount").loc[date]
        pred = pred[amount.reindex(pred.index).fillna(0) >= self.cfg.min_amount]
        if pred.empty:
            return {}
        picks = list(pred.sort_values(ascending=False).head(self.cfg.top_k).index)
        gross = 1.0 - self.cfg.cash_buffer
        w = min(gross / len(picks), self.cfg.max_weight)
        return {c: w for c in picks}

    def weight_func(self):
        reb = self.cfg.rebalance_days

        def func(i: int) -> dict[str, float] | None:
            if i < self.warmup:
                return None
            if (i - self.warmup) % reb != 0:
                return None
            if self._model is None or (i - self._last_train) >= self.retrain_every:
                self._train(i)
            return self.target_weights(self._dates[i])

        return func

    def feature_importance(self) -> pd.Series:
        if self._model is None:
            return pd.Series(dtype=float)
        return pd.Series(
            self._model.feature_importances_, index=self.features
        ).sort_values(ascending=False)
