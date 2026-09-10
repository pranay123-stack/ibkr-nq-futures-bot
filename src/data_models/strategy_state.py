"""Strategy session state model."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List

from .enums import Direction
from .candle import Candle, DayLevels
from .trade import Trade


@dataclass
class StrategyState:
    """Represents the overall strategy state for a trading session."""
    session_date: datetime
    is_active: bool = False
    signal_candle: Optional[Candle] = None
    signal_direction: Direction = Direction.NEUTRAL
    entry_triggered: bool = False
    trades_today: List[Trade] = field(default_factory=list)
    losses_today: int = 0
    reentries_used: int = 0
    previous_day_levels: Optional[DayLevels] = None
    last_entry_price: Optional[float] = None

    @property
    def can_trade(self) -> bool:
        """Return True if daily loss and re-entry limits have not been reached."""
        return self.losses_today < 2 and self.reentries_used <= 1

    @property
    def can_reentry(self) -> bool:
        """Return True if a re-entry is allowed (one loss, no re-entries yet)."""
        return self.losses_today == 1 and self.reentries_used == 0

    def reset_for_new_session(self, session_date: datetime) -> None:
        """Reset all session state for a new trading day."""
        self.session_date = session_date
        self.is_active = False
        self.signal_candle = None
        self.signal_direction = Direction.NEUTRAL
        self.entry_triggered = False
        self.trades_today = []
        self.losses_today = 0
        self.reentries_used = 0
        self.last_entry_price = None
