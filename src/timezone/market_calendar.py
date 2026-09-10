"""
Centralized Timezone & Market Hours Management.

ALL timezone logic lives here. No other file should hardcode
'US/Eastern', market open/close hours, or weekday checks.

Usage:
    from src.timezone.market_calendar import MarketCalendar, get_default_tz

    cal = MarketCalendar(config)
    cal.is_market_open()
    cal.get_next_reopen()
    cal.now()  # current time in strategy timezone
"""

from datetime import datetime, time, timedelta
from typing import Optional, Tuple
import pytz


class MarketCalendar:
    """
    Centralized market hours and timezone management for NQ futures.

    All market hour constants are configurable. Default NQ schedule:
    - Opens: Sunday 6 PM Eastern
    - Closes: Friday 5 PM Eastern
    - Daily halt: 5 PM - 6 PM Eastern (Mon-Thu)
    """

    def __init__(self, config: dict = None):
        config = config or {}

        # Timezone from config (single source of truth)
        tz_name = config.get('timezone', 'US/Eastern')
        self.tz = pytz.timezone(tz_name)

        # Market hours from config
        market = config.get('market_hours', {})
        self.reopen_hour = market.get('reopen_hour', 18)       # 6 PM
        self.halt_hour = market.get('halt_hour', 17)           # 5 PM
        self.weekend_close_day = market.get('close_day', 4)    # Friday (0=Mon)
        self.weekend_open_day = market.get('open_day', 6)      # Sunday (0=Mon)

    def now(self) -> datetime:
        """Return current time in the strategy timezone."""
        return datetime.now(self.tz)

    def localize(self, dt: datetime) -> datetime:
        """Localize a naive datetime to the strategy timezone."""
        if dt.tzinfo is None:
            return self.tz.localize(dt)
        return dt.astimezone(self.tz)

    def is_market_open(self) -> Tuple[bool, str]:
        """
        Check if the futures market is currently open.

        Returns:
            Tuple of (is_open, reason_string)
        """
        now = self.now()
        weekday = now.weekday()  # Monday=0, Sunday=6
        hour = now.hour

        # Saturday - closed all day
        if weekday == 5:
            return False, "Weekend - Saturday"

        # Sunday - opens at reopen_hour
        if weekday == self.weekend_open_day:
            if hour >= self.reopen_hour:
                return True, "Sunday session started"
            return False, "Weekend - Sunday before reopen"

        # Friday - closes at halt_hour
        if weekday == self.weekend_close_day:
            if hour < self.halt_hour:
                return True, "Friday session"
            return False, "Weekend - Friday after close"

        # Mon-Thu - daily halt between halt_hour and reopen_hour
        if weekday in (0, 1, 2, 3):
            if hour == self.halt_hour:
                return False, "Daily halt"
            return True, "Regular session"

        return False, "Unknown"

    def is_trading_day(self, now: datetime = None) -> Tuple[bool, str]:
        """
        Check if today is an allowed day for NEW entries at 6 PM.
        Friday 6 PM is blocked (would hold over weekend).

        Args:
            now: Optional pre-computed "now" datetime (for testability).

        Returns:
            Tuple of (allowed, reason_string)
        """
        if now is None:
            now = self.now()
        weekday = now.weekday()

        if weekday == self.weekend_close_day:  # Friday
            return False, "Friday - no new trades (weekend risk)"
        if weekday == 5:  # Saturday
            return False, "Saturday - market closed"
        return True, "Trading allowed"

    def get_session_date_key(self) -> str:
        """
        Get a unique key for the current trading session.
        Sessions start at reopen_hour, so 7 PM belongs to today's session.
        """
        now = self.now()
        if now.hour >= self.reopen_hour:
            return now.strftime("%Y-%m-%d-PM")
        return (now - timedelta(days=1)).strftime("%Y-%m-%d-PM")

    def get_next_reopen(self) -> datetime:
        """Get the next market reopen time."""
        now = self.now()
        today_reopen = now.replace(
            hour=self.reopen_hour, minute=0, second=0, microsecond=0
        )

        if now < today_reopen:
            # Skip Saturday
            if now.weekday() == 5:
                return today_reopen + timedelta(days=1)
            return today_reopen
        else:
            next_reopen = today_reopen + timedelta(days=1)
            # Skip Saturday
            if next_reopen.weekday() == 5:
                next_reopen += timedelta(days=1)
            return next_reopen

    def get_current_session_start(self) -> Optional[datetime]:
        """
        Get the current session's reopen time, or None if not in a session.
        """
        now = self.now()
        today_reopen = now.replace(
            hour=self.reopen_hour, minute=0, second=0, microsecond=0
        )

        # After today's reopen
        if now >= today_reopen:
            return today_reopen

        # Before halt - still in yesterday's session
        if now.hour < self.halt_hour:
            yesterday_reopen = today_reopen - timedelta(days=1)
            if yesterday_reopen.weekday() != 5:  # Not Saturday
                return yesterday_reopen

        return None

    def get_trading_session_date(self) -> str:
        """Get the trading session date string (for daily log files)."""
        now = self.now()
        if now.hour < self.reopen_hour:
            session_date = (now - timedelta(days=1)).date()
        else:
            session_date = now.date()
        return session_date.strftime("%Y-%m-%d")


# Module-level convenience: create a default timezone from 'US/Eastern'
# This is used by logger/formatters that need a timezone before config is loaded
_default_tz = pytz.timezone('US/Eastern')


def get_default_tz():
    """Get the default timezone (US/Eastern). Used before config is loaded."""
    return _default_tz
