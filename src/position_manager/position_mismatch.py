"""Position mismatch detector."""

import time as time_module
from typing import Optional

from ..logger import get_logger
from ..utils.defaults import POSITION_MISMATCH_CHECK_SECONDS


class PositionMismatchDetector:
    """
    Periodically compares strategy's internal position state with
    IBKR's reported position to detect mismatches.
    """

    def __init__(self, check_interval_seconds: float = POSITION_MISMATCH_CHECK_SECONDS):
        self.check_interval = check_interval_seconds
        self._last_check: Optional[float] = None
        self.logger = get_logger("PosMismatch")

    def should_check(self) -> bool:
        """Return True if enough time has elapsed since the last check."""
        if self._last_check is None:
            return True
        return (time_module.time() - self._last_check) >= self.check_interval

    def check_mismatch(
        self,
        strategy_direction: str,
        strategy_qty: int,
        broker_direction: str,
        broker_qty: int
    ) -> bool:
        """
        Returns True if there's a mismatch.
        """
        self._last_check = time_module.time()

        # Normalize "NEUTRAL" and 0 qty
        s_flat = (strategy_direction == "NEUTRAL" or strategy_qty == 0)
        b_flat = (broker_direction == "NEUTRAL" or broker_qty == 0)

        if s_flat and b_flat:
            return False  # Both flat, no mismatch

        if s_flat != b_flat:
            self.logger.error(
                f"POSITION MISMATCH: Strategy={strategy_direction}x{strategy_qty} "
                f"vs Broker={broker_direction}x{broker_qty}. "
                "One is flat, the other is not!"
            )
            return True

        if strategy_direction != broker_direction or strategy_qty != broker_qty:
            self.logger.error(
                f"POSITION MISMATCH: Strategy={strategy_direction}x{strategy_qty} "
                f"vs Broker={broker_direction}x{broker_qty}"
            )
            return True

        return False
