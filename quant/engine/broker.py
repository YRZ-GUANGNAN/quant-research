"""A 股 / 美股 / 港股 / 加密 通用的模拟盘撮合器。

刻意**只实现规则，不做优化**：所有市场摩擦都在这里显式建模，
因为回测里最危险的不是策略写得不够聪明，而是撮合假设太乐观。

规则清单
--------
1. 整手买入：买入数量向下取整到 ``cfg.lot_size``（A 股 100，美股/加密 1）。
2. T+1：``cfg.t_plus_one`` 为真时，当日买入的股票当日不可卖（``settle()`` 次日解锁）。
3. 涨跌停：``cfg.limit_pct > 0`` 时，开盘价触及涨停无法买入、跌停无法卖出；
   A 股按板块细分（主板 10%、创业板/科创板 20%）。美股/港股/加密无涨跌停。
4. 停牌 / 缺失行情：当日无 open → 不可交易。
5. 费用：佣金（可设最低）+ 过户费 + 卖出印花税 + 滑点，全部来自 cfg.costs。
6. 现金约束：买入后现金不得为负，不足时按整手递减。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import BacktestConfig


def price_limit_pct(code: str, cfg: BacktestConfig) -> float:
    """返回涨跌停幅度；0 表示无限制。"""
    if cfg.limit_pct <= 0:
        return 0.0
    if cfg.market != "cn":
        return cfg.limit_pct
    code = str(code)
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    return cfg.limit_pct or 0.10


@dataclass
class Position:
    """单只标的持仓。"""

    shares: int = 0
    available: int = 0          # T+1 可卖数量
    cost: float = 0.0           # 累计买入成本（含费用）
    last_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.shares * self.last_price

    @property
    def avg_cost(self) -> float:
        return self.cost / self.shares if self.shares else 0.0


@dataclass
class Trade:
    """一笔成交记录。"""

    date: str
    code: str
    side: str                   # buy / sell
    shares: int
    price: float
    amount: float
    fee: float
    reason: str = ""


class PaperBroker:
    """模拟账户：现金 + 持仓 + 成交记录。"""

    def __init__(self, cash: float, cfg: BacktestConfig):
        self.cash = float(cash)
        self.cfg = cfg
        self.costs = cfg.costs
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []

    # ---------- 状态 ----------

    def settle(self) -> None:
        """新交易日开始：解锁可卖股份（T+1 市场）。"""
        for pos in self.positions.values():
            pos.available = pos.shares

    def mark(self, prices: dict[str, float]) -> None:
        """按收盘价（或开盘价）重估持仓。"""
        for code, pos in self.positions.items():
            px = prices.get(code)
            if px is not None and px > 0:
                pos.last_price = float(px)

    def equity(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    def position_weight(self, code: str) -> float:
        eq = self.equity()
        pos = self.positions.get(code)
        return (pos.market_value / eq) if (pos and eq > 0) else 0.0

    # ---------- 撮合 ----------

    def _can_buy(self, open_px: float, prev_close: float, code: str) -> bool:
        limit = price_limit_pct(code, self.cfg)
        if limit <= 0 or prev_close <= 0:
            return True
        return (open_px / prev_close - 1.0) < limit - 1e-3

    def _can_sell(self, open_px: float, prev_close: float, code: str) -> bool:
        limit = price_limit_pct(code, self.cfg)
        if limit <= 0 or prev_close <= 0:
            return True
        return (open_px / prev_close - 1.0) > -limit + 1e-3

    def buy(
        self,
        date: str,
        code: str,
        shares: int,
        open_px: float,
        prev_close: float,
        reason: str = "",
    ) -> int:
        """按开盘价买入，返回实际成交股数（可能为 0）。"""
        if shares <= 0 or open_px <= 0:
            return 0
        if not self._can_buy(open_px, prev_close, code):
            return 0
        shares = (shares // self.cfg.lot_size) * self.cfg.lot_size
        price = open_px * (1.0 + self.costs.slippage)

        while shares > 0:
            amount = shares * price
            fee = self.costs.buy_cost(amount)
            if amount + fee <= self.cash + 1e-9:
                break
            shares -= self.cfg.lot_size
        if shares <= 0:
            return 0

        amount = shares * price
        fee = self.costs.buy_cost(amount)
        self.cash -= amount + fee

        pos = self.positions.setdefault(code, Position())
        pos.shares += shares
        pos.cost += amount + fee
        pos.last_price = open_px
        if not self.cfg.t_plus_one:
            pos.available += shares          # T+0 市场：当日即可卖
        self.trades.append(Trade(date, code, "buy", shares, price, amount, fee, reason))
        return shares

    def sell(
        self,
        date: str,
        code: str,
        shares: int,
        open_px: float,
        prev_close: float,
        reason: str = "",
    ) -> int:
        """按开盘价卖出，返回实际成交股数。"""
        pos = self.positions.get(code)
        if not pos or shares <= 0:
            return 0
        shares = min(int(shares), pos.available)
        if shares <= 0 or open_px <= 0:
            return 0
        if not self._can_sell(open_px, prev_close, code):
            return 0

        price = open_px * (1.0 - self.costs.slippage)
        amount = shares * price
        fee = self.costs.sell_cost(amount)
        self.cash += amount - fee

        cost_out = pos.cost * (shares / pos.shares) if pos.shares else 0.0
        pos.cost -= cost_out
        pos.shares -= shares
        pos.available -= shares
        pos.last_price = open_px
        self.trades.append(Trade(date, code, "sell", shares, price, amount, fee, reason))
        if pos.shares == 0:
            del self.positions[code]
        return shares

    def close_all(self, date: str, open_px_map: dict[str, float], prev_close_map: dict[str, float]) -> None:
        """清仓（用于回测收尾或强制平仓）。"""
        for code in list(self.positions):
            px = open_px_map.get(code)
            if px is None or px <= 0:
                continue
            self.sell(date, code, self.positions[code].available, px, prev_close_map.get(code, px), "close_all")

    def snapshot(self) -> dict[str, float]:
        eq = self.equity()
        if eq <= 0:
            return {}
        return {c: p.market_value / eq for c, p in self.positions.items() if p.shares > 0}
