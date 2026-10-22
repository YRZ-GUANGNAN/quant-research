"""全局配置：路径、交易成本、宇宙与回测参数。

设计原则：所有「会影响回测结果」的数字都放在这里，便于逐个做敏感性测试；
不允许在策略或引擎里硬编码成本与日期。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = ROOT / "reports"
PAPER_DIR = ROOT / ".paper"

# 指数代码 -> 东方财富板块代码（用于取成分股）
INDEX_BOARDS: dict[str, str] = {
    "000300": "BK0500",  # 沪深300
    "000905": "BK0701",  # 中证500
    "000016": "BK0459",  # 上证50
}

INDEX_NAMES: dict[str, str] = {
    "000300": "沪深300",
    "000905": "中证500",
    "000016": "上证50",
}


@dataclass(frozen=True)
class Costs:
    """A 股交易成本模型（2023-08-28 之后的费率）。"""

    commission_rate: float = 2.5e-4      # 佣金 万2.5，双边
    commission_min: float = 5.0          # 单笔最低 5 元
    stamp_duty_sell: float = 5e-4        # 印花税，仅卖出 0.05%
    transfer_fee: float = 1e-5           # 过户费 0.001%，双边
    slippage: float = 5e-4               # 滑点 5bp（买加价、卖减价）

    def buy_cost(self, amount: float) -> float:
        """买入一笔成交额对应的费用（不含滑点，滑点体现在成交价里）。"""
        commission = max(amount * self.commission_rate, self.commission_min)
        return commission + amount * self.transfer_fee

    def sell_cost(self, amount: float) -> float:
        """卖出一笔成交额对应的费用。"""
        commission = max(amount * self.commission_rate, self.commission_min)
        return commission + amount * (self.transfer_fee + self.stamp_duty_sell)


@dataclass(frozen=True)
class BacktestConfig:
    """一次回测的完整参数快照。"""

    index_code: str = "000300"
    start: str = "2018-01-01"
    end: str = ""                        # 空字符串 = 最新
    initial_cash: float = 1_000_000.0
    costs: Costs = field(default_factory=Costs)

    # 调仓
    rebalance_days: int = 20             # 每 20 个交易日调仓一次
    top_k: int = 30                      # 持有股票数
    max_weight: float = 0.08             # 单票权重上限
    cash_buffer: float = 0.02            # 预留 2% 现金缓冲

    # 撮合
    min_amount: float = 5e7              # 近 20 日均成交额下限，过滤流动性差的票
    limit_pct_main: float = 0.10         # 主板涨跌停 10%
    limit_pct_growth: float = 0.20       # 创业板/科创板 20%
    lot_size: int = 100

    def with_(self, **kwargs) -> "BacktestConfig":
        """返回替换若干字段后的新配置（frozen dataclass 的便捷写法）。"""
        return replace(self, **kwargs)


# 便捷单例
DEFAULT_BT = BacktestConfig()


def ensure_dirs() -> None:
    """确保运行目录存在。"""
    for d in (DATA_DIR, CACHE_DIR, REPORTS_DIR, PAPER_DIR):
        d.mkdir(parents=True, exist_ok=True)
