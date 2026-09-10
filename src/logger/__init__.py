"""
Enhanced Trading Logger Package
Provides EST-timezone logging with trader-friendly messages.
"""

import logging
from typing import Optional

from .colors import ColorCodes
from .formatters import (
    EST,
    ColoredFormatter,
    PlainFormatter,
    format_est_timestamp,
    get_trading_session_date,
)
from .session_handler import DailySessionFileHandler
from .strategy_logger import StrategyLogger


def get_logger(module_name: Optional[str] = None) -> logging.Logger:
    """
    Convenience function to get a logger instance.
    Must be called after StrategyLogger is initialized.
    """
    instance = StrategyLogger._instance
    if instance is None:
        # Return a basic logger if not initialized
        return logging.getLogger(module_name or "NQ_Strategy")
    return instance.get_logger(module_name)


def setup_logging(config: dict) -> StrategyLogger:
    """
    Setup logging from configuration dictionary.

    Args:
        config: Dictionary with logging configuration from YAML

    Returns:
        Configured StrategyLogger instance
    """
    log_config = config.get('logging', {})

    return StrategyLogger(
        name="NQ_Strategy",
        level=log_config.get('level', 'DEBUG'),
        log_to_file=log_config.get('log_to_file', True),
        log_to_console=log_config.get('log_to_console', True),
        colorful_console=log_config.get('colorful_console', True),
        log_file_path=log_config.get('log_file_path', 'logs/strategy.log'),
        max_file_size_mb=log_config.get('max_file_size_mb', 10),
        backup_count=log_config.get('backup_count', 5),
        show_timezone=log_config.get('show_timezone_suffix', True),
        enable_session_logs=log_config.get('enable_session_logs', True),
        session_log_dir=log_config.get('session_log_dir', 'logs/sessions')
    )


__all__ = [
    'ColorCodes',
    'ColoredFormatter',
    'DailySessionFileHandler',
    'EST',
    'PlainFormatter',
    'StrategyLogger',
    'format_est_timestamp',
    'get_logger',
    'get_trading_session_date',
    'setup_logging',
]
