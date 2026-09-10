"""Heartbeat monitor for connection health."""

import time as time_module
from typing import Optional, Dict

from ..logger import get_logger


class HeartbeatMonitor:
    """
    Monitors connection health by tracking last activity timestamps.
    Detects stale connections where the socket is open but no data flows.
    """

    def __init__(self, stale_threshold_seconds: float = 60.0):
        self.stale_threshold = stale_threshold_seconds
        self._last_price_update: Optional[float] = None
        self._last_order_event: Optional[float] = None
        self._last_heartbeat: Optional[float] = None
        self.logger = get_logger("Heartbeat")

    def record_price_update(self):
        """Record the timestamp of the latest price update."""
        self._last_price_update = time_module.time()

    def record_order_event(self):
        """Record the timestamp of the latest order event."""
        self._last_order_event = time_module.time()

    def record_heartbeat(self):
        """Record the timestamp of the latest heartbeat."""
        self._last_heartbeat = time_module.time()

    def is_data_stale(self) -> bool:
        """Return True if the last price update exceeds the stale threshold."""
        if self._last_price_update is None:
            return True
        elapsed = time_module.time() - self._last_price_update
        return elapsed > self.stale_threshold

    def get_status(self) -> Dict:
        """Return a dictionary with the age of each monitored event."""
        now = time_module.time()
        return {
            'price_age_seconds': round(now - self._last_price_update, 1) if self._last_price_update else None,
            'order_event_age': round(now - self._last_order_event, 1) if self._last_order_event else None,
            'heartbeat_age': round(now - self._last_heartbeat, 1) if self._last_heartbeat else None,
            'is_stale': self.is_data_stale(),
        }
