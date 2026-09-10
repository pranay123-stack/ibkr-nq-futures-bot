"""Stale data detector for market data."""

import time as time_module
from typing import Optional

from ..logger import get_logger
from ..utils.defaults import STALE_DATA_TIMEOUT_SECONDS


class StaleDataDetector:
    """
    Detects when market data hasn't updated for too long.
    Different from heartbeat - this specifically checks price tick flow.
    """

    def __init__(self, max_stale_seconds: float = STALE_DATA_TIMEOUT_SECONDS):
        self.max_stale_seconds = max_stale_seconds
        self._last_price: Optional[float] = None
        self._last_price_time: Optional[float] = None
        self._price_change_count = 0
        self.logger = get_logger("StaleData")

    def update(self, price: float):
        """Record a new price tick and update the last-seen timestamp."""
        now = time_module.time()
        if self._last_price is not None and price != self._last_price:
            self._price_change_count += 1
        self._last_price = price
        self._last_price_time = now

    def is_stale(self) -> bool:
        """Return True if no price update has been received within the timeout."""
        if self._last_price_time is None:
            return True
        elapsed = time_module.time() - self._last_price_time
        return elapsed > self.max_stale_seconds
