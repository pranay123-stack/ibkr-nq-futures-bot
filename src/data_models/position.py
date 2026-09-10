"""Position model."""

from dataclasses import dataclass
from typing import Optional

from .enums import Direction
from .trade import Trade


@dataclass
class Position:
    """Represents the current position state."""
    direction: Direction = Direction.NEUTRAL
    quantity: int = 0
    avg_entry_price: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    active_trade: Optional[Trade] = None

    @property
    def is_flat(self) -> bool:
        """Return True if there is no open position."""
        return self.quantity == 0 or self.direction == Direction.NEUTRAL

    @property
    def is_long(self) -> bool:
        """Return True if holding a long position."""
        return self.direction == Direction.LONG and self.quantity > 0

    @property
    def is_short(self) -> bool:
        """Return True if holding a short position."""
        return self.direction == Direction.SHORT and self.quantity > 0
