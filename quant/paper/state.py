"""模拟盘账户状态持久化。

账户状态存成 JSON（``.paper/state.json``），不进 git：
- 每次推进一个交易日就落盘一次，进程中断不会丢账户；
- 持仓、现金、净值历史、待执行目标权重全部显式保存；
- 与回测共用 ``execute_target``，所以两条路径的撮合逻辑一致。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..config import PAPER_DIR
from ..engine.broker import PaperBroker, Position


@dataclass
class PaperState:
    created_at: str
    last_date: str | None
    day_index: int
    cash: float
    positions: dict[str, dict] = field(default_factory=dict)
    nav_history: list[dict] = field(default_factory=list)
    pending: dict[str, float] = field(default_factory=dict)

    # ---------- 转换 ----------

    def to_broker(self, cfg) -> PaperBroker:
        broker = PaperBroker(self.cash, cfg)
        for code, p in self.positions.items():
            broker.positions[code] = Position(
                shares=int(p["shares"]),
                available=int(p.get("available", p["shares"])),
                cost=float(p.get("cost", 0.0)),
                last_price=float(p.get("last_price", 0.0)),
            )
        return broker

    def sync_from_broker(self, broker: PaperBroker) -> None:
        self.cash = float(broker.cash)
        self.positions = {
            code: {
                "shares": p.shares,
                "available": p.available,
                "cost": round(p.cost, 4),
                "last_price": round(p.last_price, 4),
            }
            for code, p in broker.positions.items()
            if p.shares > 0
        }

    def equity(self) -> float:
        return self.cash + sum(
            p["shares"] * p.get("last_price", 0.0) for p in self.positions.values()
        )

    # ---------- 持久化 ----------

    @classmethod
    def path(cls) -> Path:
        return PAPER_DIR / "state.json"

    @classmethod
    def load(cls, path: Path | None = None) -> "PaperState | None":
        path = path or cls.path()
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(**payload)

    @classmethod
    def create(cls, initial_cash: float, warmup: int) -> "PaperState":
        from datetime import datetime, timezone

        return cls(
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            last_date=None,
            day_index=warmup,
            cash=float(initial_cash),
        )

    def save(self, path: Path | None = None) -> None:
        path = path or self.path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(
                {
                    "created_at": self.created_at,
                    "last_date": self.last_date,
                    "day_index": self.day_index,
                    "cash": round(self.cash, 4),
                    "positions": self.positions,
                    "nav_history": self.nav_history,
                    "pending": self.pending,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
