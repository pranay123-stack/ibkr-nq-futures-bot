"""Trading signal model."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

from .enums import Direction, SignalType
from .candle import Candle


@dataclass
class Signal:
    """Represents a trading signal."""
    id: str
    timestamp: datetime
    signal_type: SignalType
    direction: Direction
    price: float
    candle: Optional[Candle] = None
    stop_loss: Optional[float] = None
    take_profit_levels: Optional[List[float]] = None
    risk_amount: Optional[float] = None
    reason: str = ""
    is_reentry: bool = False
    skip_trade: bool = False

    def to_dict(self) -> dict:
        """Convert the signal to a dictionary representation."""
        return {
            'id': self.id,
            'timestamp': self.timestamp.isoformat(),
            'signal_type': self.signal_type.value,
            'direction': self.direction.value,
            'price': self.price,
            'stop_loss': self.stop_loss,
            'take_profit_1': self.take_profit_levels[0] if self.take_profit_levels and len(self.take_profit_levels) > 0 else None,
            'take_profit_2': self.take_profit_levels[1] if self.take_profit_levels and len(self.take_profit_levels) > 1 else None,
            'take_profit_3': self.take_profit_levels[2] if self.take_profit_levels and len(self.take_profit_levels) > 2 else None,
            'take_profit_4': self.take_profit_levels[3] if self.take_profit_levels and len(self.take_profit_levels) > 3 else None,
            'risk_amount': self.risk_amount,
            'reason': self.reason,
            'is_reentry': self.is_reentry,
            'candle_open': self.candle.open if self.candle else None,
            'candle_high': self.candle.high if self.candle else None,
            'candle_low': self.candle.low if self.candle else None,
            'candle_close': self.candle.close if self.candle else None
        }
