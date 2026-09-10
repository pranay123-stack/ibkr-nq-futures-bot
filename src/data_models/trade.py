"""Trade model."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

from .enums import Direction, TradeStatus


@dataclass
class Trade:
    """Represents a complete trade (entry + exit)."""
    id: str
    entry_time: datetime
    direction: Direction
    entry_price: float
    quantity: int
    stop_loss: float
    take_profit_levels: List[float]
    status: TradeStatus = TradeStatus.OPEN
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_quantity: int = 0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    current_stop_loss: Optional[float] = None
    highest_tp_hit: int = 0
    is_reentry: bool = False
    original_trade_id: Optional[str] = None
    partial_exits: List[dict] = field(default_factory=list)

    def __post_init__(self):
        if self.current_stop_loss is None:
            self.current_stop_loss = self.stop_loss

    @property
    def risk_amount(self) -> float:
        """Return the dollar risk amount based on stop distance and position size."""
        point_value = getattr(self, '_point_value', 2.0)
        risk_points = abs(self.entry_price - self.stop_loss)
        return risk_points * point_value * self.quantity

    @property
    def is_winning(self) -> bool:
        """Return True if the trade has positive unrealized or realized PnL."""
        return self.unrealized_pnl > 0 or self.realized_pnl > 0

    def to_dict(self) -> dict:
        """Convert the trade to a dictionary representation."""
        return {
            'id': self.id,
            'entry_time': self.entry_time.isoformat(),
            'exit_time': self.exit_time.isoformat() if self.exit_time else None,
            'direction': self.direction.value,
            'entry_price': self.entry_price,
            'exit_price': self.exit_price,
            'quantity': self.quantity,
            'exit_quantity': self.exit_quantity,
            'stop_loss': self.stop_loss,
            'current_stop_loss': self.current_stop_loss,
            'tp1': self.take_profit_levels[0] if len(self.take_profit_levels) > 0 else None,
            'tp2': self.take_profit_levels[1] if len(self.take_profit_levels) > 1 else None,
            'tp3': self.take_profit_levels[2] if len(self.take_profit_levels) > 2 else None,
            'tp4': self.take_profit_levels[3] if len(self.take_profit_levels) > 3 else None,
            'highest_tp_hit': self.highest_tp_hit,
            'realized_pnl': self.realized_pnl,
            'unrealized_pnl': self.unrealized_pnl,
            'risk_amount': self.risk_amount,
            'status': self.status.value,
            'is_reentry': self.is_reentry,
            'original_trade_id': self.original_trade_id
        }
