"""High volatility detector."""

from ..logger import get_logger
from ..utils.defaults import MAX_CANDLE_RANGE_POINTS, MIN_CANDLE_BODY_RATIO


class VolatilityDetector:
    """
    Detects unusually high volatility that may make the strategy risky.
    Uses the signal candle range as a proxy.
    """

    def __init__(
        self,
        max_candle_range_points: float = MAX_CANDLE_RANGE_POINTS,
        min_candle_body_ratio: float = MIN_CANDLE_BODY_RATIO
    ):
        self.max_candle_range = max_candle_range_points
        self.min_body_ratio = min_candle_body_ratio
        self.logger = get_logger("Volatility")

    def is_too_volatile(self, candle_range: float) -> bool:
        """Return True if the candle range exceeds the configured volatility limit."""
        if candle_range > self.max_candle_range:
            self.logger.warning(
                f"HIGH VOLATILITY: Candle range {candle_range:.2f} pts "
                f"exceeds limit {self.max_candle_range:.2f} pts"
            )
            return True
        return False

    def is_doji(self, candle) -> bool:
        """
        Detect doji candle (body too small relative to range).
        Doji candles give ambiguous direction signals.
        """
        if candle.range == 0:
            return True
        body_ratio = candle.body_size / candle.range
        if body_ratio < self.min_body_ratio:
            self.logger.warning(
                f"DOJI CANDLE DETECTED: body/range ratio = {body_ratio:.3f} "
                f"(min: {self.min_body_ratio}). Signal unreliable."
            )
            return True
        return False
