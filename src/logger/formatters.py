"""
Log formatters and timestamp utilities for EST-timezone logging.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

import pytz

from .colors import ColorCodes

# EST timezone constant – sourced from the same default as utils.timezone
# We avoid importing from ..utils.timezone here to prevent a circular import
# (logger -> utils -> config_loader -> logger).  The canonical default is
# defined in src/utils/timezone.py; we replicate only the bootstrap value.
EST = pytz.timezone('US/Eastern')

# Default reopen hour used for session-date boundary (before config is loaded).
# Must match MarketCalendar's default reopen_hour.
_DEFAULT_REOPEN_HOUR = 18


def format_est_timestamp(unix_time: float, include_suffix: bool = True) -> str:
    """Convert Unix timestamp to EST with optional suffix."""
    utc_dt = datetime.utcfromtimestamp(unix_time).replace(tzinfo=pytz.UTC)
    est_dt = utc_dt.astimezone(EST)
    timestamp = est_dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if include_suffix:
        timestamp += " EST"
    return timestamp


def get_trading_session_date() -> str:
    """
    Get trading session date based on 6PM EST boundary.
    Before 6PM = previous day's session.
    """
    now = datetime.now(EST)
    if now.hour < _DEFAULT_REOPEN_HOUR:
        session_date = (now - timedelta(days=1)).date()
    else:
        session_date = now.date()
    return session_date.strftime("%Y-%m-%d")


class ColoredFormatter(logging.Formatter):
    """Custom formatter with EST timestamps and colors."""

    LEVEL_COLORS = {
        logging.DEBUG: ColorCodes.BRIGHT_CYAN,
        logging.INFO: ColorCodes.BRIGHT_GREEN,
        logging.WARNING: ColorCodes.BRIGHT_YELLOW,
        logging.ERROR: ColorCodes.BRIGHT_RED,
        logging.CRITICAL: ColorCodes.BOLD + ColorCodes.BG_RED + ColorCodes.WHITE,
    }

    LEVEL_ICONS = {
        logging.DEBUG: "[DBG]",
        logging.INFO: "[INF]",
        logging.WARNING: "[WRN]",
        logging.ERROR: "[ERR]",
        logging.CRITICAL: "[CRT]",
    }

    def __init__(self, fmt: Optional[str] = None, datefmt: Optional[str] = None,
                 use_colors: bool = True, show_timezone: bool = True):
        super().__init__(fmt, datefmt)
        self.use_colors = use_colors
        self.show_timezone = show_timezone

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record with colored output and EST timestamps."""
        if self.use_colors:
            color = self.LEVEL_COLORS.get(record.levelno, ColorCodes.RESET)
            icon = self.LEVEL_ICONS.get(record.levelno, "[???]")

            # Format timestamp in EST
            timestamp = format_est_timestamp(record.created, self.show_timezone)

            # Build colored message
            level_str = f"{color}{icon}{ColorCodes.RESET}"
            time_str = f"{ColorCodes.BRIGHT_BLUE}{timestamp}{ColorCodes.RESET}"
            name_str = f"{ColorCodes.MAGENTA}{record.name}{ColorCodes.RESET}"
            msg_color = color if record.levelno >= logging.WARNING else ColorCodes.WHITE
            msg_str = f"{msg_color}{record.getMessage()}{ColorCodes.RESET}"

            return f"{time_str} {level_str} {name_str}: {msg_str}"
        else:
            return super().format(record)


class PlainFormatter(logging.Formatter):
    """Plain formatter for file logging with EST timestamps."""

    def __init__(self, show_timezone: bool = True):
        super().__init__()
        self.show_timezone = show_timezone

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as plain text with EST timestamps."""
        timestamp = format_est_timestamp(record.created, self.show_timezone)
        level = f"[{record.levelname:<8}]"
        return f"{timestamp} {level} {record.name}: {record.getMessage()}"
