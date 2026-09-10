"""Rate limiter to prevent IBKR API throttling."""

import time as time_module
from collections import deque

from ..logger import get_logger
from ..utils.defaults import RATE_LIMIT_MAX_CALLS, RATE_LIMIT_PERIOD_SECONDS


class RateLimiter:
    """
    Token-bucket rate limiter for IBKR API calls.
    IBKR limit: ~50 messages/second, ~60 historical data requests per 10 min.
    """

    def __init__(self, max_calls: int = RATE_LIMIT_MAX_CALLS, period_seconds: float = RATE_LIMIT_PERIOD_SECONDS):
        self.max_calls = max_calls
        self.period = period_seconds
        self._calls: deque = deque()
        self.logger = get_logger("RateLimiter")

    def wait_if_needed(self):
        """Sleep if the call rate would exceed the configured limit."""
        now = time_module.time()
        # Remove calls outside the window
        while self._calls and self._calls[0] < now - self.period:
            self._calls.popleft()

        if len(self._calls) >= self.max_calls:
            sleep_time = self._calls[0] + self.period - now + 0.01
            if sleep_time > 0:
                self.logger.debug(f"Rate limit: sleeping {sleep_time:.3f}s")
                time_module.sleep(sleep_time)

        self._calls.append(time_module.time())

    def can_call(self) -> bool:
        """Return True if a call can be made without exceeding the rate limit."""
        now = time_module.time()
        while self._calls and self._calls[0] < now - self.period:
            self._calls.popleft()
        return len(self._calls) < self.max_calls
