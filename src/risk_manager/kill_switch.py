"""Kill switch / emergency stop safety mechanism."""

import os

from ..logger import get_logger
from ..utils.defaults import MAX_DAILY_LOSS, MAX_CONSECUTIVE_ERRORS


class KillSwitch:
    """
    Safety mechanism that halts all trading when triggered.
    Can be triggered by:
    - Max daily loss exceeded
    - Too many consecutive errors
    - Manual kill file
    """

    def __init__(
        self,
        max_daily_loss: float = MAX_DAILY_LOSS,
        max_consecutive_errors: int = MAX_CONSECUTIVE_ERRORS,
        kill_file: str = "/tmp/nq_strategy_kill"
    ):
        self.max_daily_loss = max_daily_loss
        self.max_consecutive_errors = max_consecutive_errors
        self.kill_file = kill_file
        self._consecutive_errors = 0
        self._daily_pnl = 0.0
        self._is_killed = False
        self.logger = get_logger("KillSwitch")

    def record_error(self):
        """Increment the error counter and trigger if threshold is reached."""
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.max_consecutive_errors:
            self.trigger(f"Too many consecutive errors: {self._consecutive_errors}")

    def clear_errors(self):
        """Reset the consecutive error counter to zero."""
        self._consecutive_errors = 0

    def update_daily_pnl(self, pnl: float):
        """Update daily PnL and trigger the kill switch if max loss exceeded."""
        self._daily_pnl = pnl
        if self._daily_pnl <= -self.max_daily_loss:
            self.trigger(f"Max daily loss exceeded: ${self._daily_pnl:.2f}")

    def trigger(self, reason: str):
        """Activate the kill switch and halt all trading."""
        self._is_killed = True
        self.logger.critical(f"KILL SWITCH TRIGGERED: {reason}")
        self.logger.critical("ALL TRADING HALTED - manual intervention required")

    def is_active(self) -> bool:
        """Return True if the kill switch has been triggered or a kill file exists."""
        # Check manual kill file
        if os.path.exists(self.kill_file):
            if not self._is_killed:
                self.trigger("Manual kill file detected")
            return True
        return self._is_killed

    def reset(self):
        """Reset the kill switch, clearing errors, PnL, and kill file."""
        self._is_killed = False
        self._consecutive_errors = 0
        self._daily_pnl = 0.0
        if os.path.exists(self.kill_file):
            os.unlink(self.kill_file)
        self.logger.info("Kill switch reset")
