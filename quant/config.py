"""全局配置：路径、交易成本、市场规则、宇宙与回测参数。

设计原则：所有「会影响回测结果」的数字都放在这里，便于逐个做敏感性测试；
不允许在策略或引擎里硬编码成本与日期。

多市场支持：A 股 / 美股 / 港股 / 加密货币 共用一套引擎，
差异集中在 ``MARKET_RULES``——交易制度不同，但研究流程应当一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
REPORTS_DIR = ROOT / "reports"
PAPER_DIR = ROOT / ".paper"

# 指数代码 -> 东方财富板块代码（仅 A 股时点成分股使用，备用实现）
INDEX_BOARDS: dict[str, str] = {
    "000300": "BK0500",
    "000905": "BK0701",
    "000016": "BK0459",
}

INDEX_NAMES: dict[str, str] = {
    "000300": "沪深300",
    "000905": "中证500",
    "000016": "上证50",
    "us": "美股大盘",
    "hk": "港股",
    "crypto": "加密货币",
}

MARKETS = ("cn", "us", "hk", "sg", "crypto")


@dataclass(frozen=True)
class Costs:
    """单笔交易成本模型（比例均为单边，滑点体现在成交价里）。"""

    commission_rate: float = 2.5e-4      # 佣金率
    commission_min: float = 5.0          # 单笔最低佣金
    stamp_duty_sell: float = 5e-4        # 卖出印花税（A 股 0.05%）
    transfer_fee: float = 1e-5           # 过户费（A 股 0.001%）
    slippage: float = 5e-4               # 滑点

    def buy_cost(self, amount: float) -> float:
        commission = max(amount * self.commission_rate, self.commission_min) if self.commission_rate > 0 else self.commission_min
        return commission + amount * self.transfer_fee

    def sell_cost(self, amount: float) -> float:
        commission = max(amount * self.commission_rate, self.commission_min) if self.commission_rate > 0 else self.commission_min
        return commission + amount * (self.transfer_fee + self.stamp_duty_sell)


@dataclass(frozen=True)
class MarketRules:
    """一个市场的交易制度快照。"""

    market: str
    t_plus_one: bool          # 是否 T+1
    lot_size: int             # 最小交易单位
    limit_pct: float          # 涨跌停幅度，0 表示无涨跌停
    costs: Costs = field(default_factory=Costs)
    calendar: str = "equity"  # equity = 交易日历；crypto = 7×24

    @property
    def has_price_limit(self) -> bool:
        return self.limit_pct > 0 or self.market == "cn"


MARKET_RULES: dict[str, MarketRules] = {
    # A 股：T+1、100 股整手、主板 10% / 创业板科创板 20%、万2.5 佣金 + 印花税
    "cn": MarketRules("cn", True, 100, 0.10, Costs(), "equity"),
    # 美股：T+0、1 股起、无涨跌停、零佣金（滑点仍计）
    "us": MarketRules(
        "us", False, 1, 0.0,
        Costs(commission_rate=0.0, commission_min=0.0, stamp_duty_sell=0.0,
              transfer_fee=0.0, slippage=5e-4),
        "equity",
    ),
    # 港股：T+0、每手股数因股而异（这里统一 100 股，近似）、无涨跌停、
    # 佣金+交易所费约 0.1%，双边印花税各 0.1%（用卖出印花税 0.1% 近似一半）
    "hk": MarketRules(
        "hk", False, 100, 0.0,
        Costs(commission_rate=1e-3, commission_min=0.0, stamp_duty_sell=1e-3,
              transfer_fee=0.0, slippage=1e-3),
        "equity",
    ),
    # 新加坡：T+2 结算但无 T+1 卖出限制、100 股整手、无涨跌停、佣金约 0.1%
    "sg": MarketRules(
        "sg", False, 100, 0.0,
        Costs(commission_rate=1e-3, commission_min=0.0, stamp_duty_sell=0.0,
              transfer_fee=0.0, slippage=1e-3),
        "equity",
    ),
    # 加密货币：7×24、T+0、最小 1 单位、无涨跌停、手续费约 0.1%/边
    "crypto": MarketRules(
        "crypto", False, 1, 0.0,
        Costs(commission_rate=1e-3, commission_min=0.0, stamp_duty_sell=0.0,
              transfer_fee=0.0, slippage=1e-3),
        "crypto",
    ),
}


@dataclass(frozen=True)
class BacktestConfig:
    """一次回测的完整参数快照。"""

    market: str = "cn"
    index_code: str = "000300"
    start: str = "2018-01-01"
    end: str = ""
    initial_cash: float = 1_000_000.0
    costs: Costs = field(default_factory=Costs)

    # 调仓
    rebalance_days: int = 20
    top_k: int = 30
    max_weight: float = 0.08
    cash_buffer: float = 0.02

    # 撮合
    min_amount: float = 5e7              # 近 20 日均成交额下限（按市场货币单位）
    lot_size: int = 100
    t_plus_one: bool = True
    limit_pct: float = 0.10              # 0 = 无涨跌停

    def with_(self, **kwargs) -> "BacktestConfig":
        return replace(self, **kwargs)

    @classmethod
    def for_market(cls, market: str, **overrides) -> "BacktestConfig":
        """按市场规则生成配置。"""
        if market not in MARKET_RULES:
            raise ValueError(f"未知市场 {market}，可选 {MARKETS}")
        rules = MARKET_RULES[market]
        base = cls(
            market=market,
            index_code={"cn": "000300", "us": "SPY", "hk": "HSI", "sg": "STI", "crypto": "BTC"}[market],
            costs=rules.costs,
            lot_size=rules.lot_size,
            t_plus_one=rules.t_plus_one,
            limit_pct=rules.limit_pct,
            min_amount={"cn": 5e7, "us": 5e7, "hk": 5e7, "sg": 1e6, "crypto": 0.0}[market],
        )
        return replace(base, **overrides)


# 便捷单例
DEFAULT_BT = BacktestConfig()

# 兼容旧导入：决策文档与早期脚本里用过 DEFAULT_BT
DEFAULT_BT = BacktestConfig()


def ensure_dirs() -> None:
    """确保运行目录存在。"""
    for d in (DATA_DIR, CACHE_DIR, REPORTS_DIR, PAPER_DIR):
        d.mkdir(parents=True, exist_ok=True)
