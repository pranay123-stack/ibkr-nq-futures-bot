"""
Daily session file handler for EST-based trading session logs.
"""

import logging
from pathlib import Path
from typing import Optional

from .formatters import PlainFormatter, get_trading_session_date


class DailySessionFileHandler(logging.Handler):
    """Handler that creates daily log files based on EST trading session date."""

    def __init__(self, log_dir: str = "logs/sessions", show_timezone: bool = True):
        super().__init__()
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._current_session_date: Optional[str] = None
        self._current_file = None
        self._file_handler: Optional[logging.FileHandler] = None
        self.show_timezone = show_timezone
        self.setFormatter(PlainFormatter(show_timezone))

    def _get_session_date(self) -> str:
        """Get trading session date (6PM EST starts new session)."""
        return get_trading_session_date()

    def _ensure_handler(self) -> None:
        """Ensure we have a file handler for the current session."""
        session_date = self._get_session_date()
        if session_date != self._current_session_date:
            if self._file_handler:
                self._file_handler.close()

            log_path = self.log_dir / f"session_{session_date}.log"
            self._file_handler = logging.FileHandler(log_path, encoding='utf-8')
            self._file_handler.setFormatter(self.formatter)
            self._current_session_date = session_date

    def emit(self, record: logging.LogRecord) -> None:
        """Write a log record to the current session's file."""
        try:
            self._ensure_handler()
            if self._file_handler:
                self._file_handler.emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """Close the current file handler and release resources."""
        if self._file_handler:
            self._file_handler.close()
        super().close()
