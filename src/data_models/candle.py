"""Price candle (OHLCV) and day levels models."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Candle:
    """Represents a price candle (OHLCV)."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0

    @property
    def is_bullish(self) -> bool:
        """Return True if candle closed higher than open."""
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        """Return True if candle closed lower than open."""
        return self.close < self.open

    @property
    def body_size(self) -> float:
        """Return the absolute size of the candle body."""
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        """Return the length of the upper wick."""
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        """Return the length of the lower wick."""
        return min(self.open, self.close) - self.low

    @property
    def range(self) -> float:
        """Return the full high-to-low range of the candle."""
        return self.high - self.low

    def to_dict(self) -> dict:
        """Convert the candle to a dictionary representation."""
        return {
            'timestamp': self.timestamp.isoformat(),
            'open': self.open, 'high': self.high,
            'low': self.low, 'close': self.close,
            'volume': self.volume, 'is_bullish': self.is_bullish
        }


@dataclass
class DayLevels:
    """Previous day's high and low levels."""
    date: datetime
    high: float
    low: float

    @property
    def range(self) -> float:
        """Return the high-to-low range of the day levels."""
        return self.high - self.low

    @property
    def midpoint(self) -> float:
        """Return the midpoint between the day's high and low."""
        return (self.high + self.low) / 2
