"""Slippage manager with limits."""

from datetime import datetime
from typing import Dict, List

from ..logger import get_logger
from ..utils.defaults import MAX_SLIPPAGE_POINTS, DEFAULT_TICK_SIZE
from ..timezone.market_calendar import get_default_tz

EST = get_default_tz()


class SlippageManager:
    """
    Tracks and limits slippage on order fills.
    Can reject entries if slippage is too high.
    """

    def __init__(self, max_slippage_points: float = MAX_SLIPPAGE_POINTS, tick_size: float = DEFAULT_TICK_SIZE):
        self.max_slippage_points = max_slippage_points
        self.tick_size = tick_size
        self._slippage_history: List[Dict] = []
        self.logger = get_logger("Slippage")

    def check_fill_slippage(
        self,
        expected_price: float,
        fill_price: float,
        direction: str
    ) -> Dict:
        """Calculate slippage for a fill and return a result dict with acceptability."""
        slippage = fill_price - expected_price
        # For shorts, positive slippage is worse (sold lower)
        if direction == "SHORT":
            slippage = -slippage

        slippage_ticks = abs(slippage) / self.tick_size
        is_acceptable = abs(slippage) <= self.max_slippage_points

        result = {
            'expected': expected_price,
            'filled': fill_price,
            'slippage_points': round(slippage, 2),
            'slippage_ticks': round(slippage_ticks, 1),
            'acceptable': is_acceptable,
            'direction': direction,
            'timestamp': datetime.now(EST).isoformat()
        }

        self._slippage_history.append(result)

        if not is_acceptable:
            self.logger.warning(
                f"EXCESSIVE SLIPPAGE: {direction} expected {expected_price:.2f} "
                f"filled {fill_price:.2f} (slippage: {slippage:+.2f} pts, "
                f"limit: {self.max_slippage_points} pts)"
            )
        else:
            self.logger.info(
                f"Slippage OK: {slippage:+.2f} pts ({slippage_ticks:.0f} ticks)"
            )

        return result

    def get_average_slippage(self) -> float:
        """Return the average absolute slippage across all recorded fills."""
        if not self._slippage_history:
            return 0.0
        return sum(abs(s['slippage_points']) for s in self._slippage_history) / len(self._slippage_history)
