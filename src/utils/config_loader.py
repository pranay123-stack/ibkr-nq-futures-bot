"""
Configuration Loader Module
Loads and validates YAML configuration files.
"""

import os
from pathlib import Path
from typing import Optional

import yaml

from ..logger import get_logger


class ConfigurationError(Exception):
    """Custom exception for configuration errors."""
    pass


def load_config(config_path: str) -> dict:
    """
    Load configuration from a YAML file.

    Args:
        config_path: Path to the configuration file

    Returns:
        Configuration dictionary

    Raises:
        ConfigurationError: If file not found or invalid
    """
    path = Path(config_path)

    if not path.exists():
        raise ConfigurationError(f"Configuration file not found: {config_path}")

    try:
        with open(path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        if config is None:
            raise ConfigurationError("Configuration file is empty")

        # Validate required sections
        validate_config(config)

        return config

    except yaml.YAMLError as e:
        raise ConfigurationError(f"Invalid YAML in configuration file: {e}")


def validate_config(config: dict) -> bool:
    """
    Validate configuration has all required sections.

    Args:
        config: Configuration dictionary

    Returns:
        True if valid

    Raises:
        ConfigurationError: If validation fails
    """
    required_sections = ['ibkr', 'contract', 'strategy', 'risk']

    for section in required_sections:
        if section not in config:
            raise ConfigurationError(f"Missing required configuration section: {section}")

    # Validate IBKR section
    ibkr = config['ibkr']
    if 'port' not in ibkr:
        raise ConfigurationError("Missing 'port' in ibkr configuration")

    # Validate contract section
    contract = config['contract']
    if 'symbol' not in contract:
        raise ConfigurationError("Missing 'symbol' in contract configuration")

    # Validate risk section
    risk = config['risk']
    if 'contracts' not in risk:
        raise ConfigurationError("Missing 'contracts' in risk configuration")

    # Validate value ranges (catch bad config before trading)
    _validate_positive(risk, 'contracts', 'risk')
    _validate_positive(risk, 'max_contracts', 'risk')
    _validate_positive(risk, 'min_risk_threshold', 'risk')
    _validate_positive(risk, 'max_risk_per_trade', 'risk')

    if risk.get('contracts', 1) > risk.get('max_contracts', 10):
        raise ConfigurationError(
            f"risk.contracts ({risk['contracts']}) exceeds risk.max_contracts ({risk['max_contracts']})"
        )

    safety = config.get('safety', {})
    _validate_positive(safety, 'max_daily_loss', 'safety')
    _validate_positive(safety, 'max_slippage_points', 'safety')
    _validate_positive(safety, 'stale_data_timeout_seconds', 'safety')

    ibkr_port = ibkr.get('port', 0)
    if not (1024 <= ibkr_port <= 65535):
        raise ConfigurationError(f"ibkr.port ({ibkr_port}) must be between 1024 and 65535")

    return True


def _validate_positive(section: dict, key: str, section_name: str) -> None:
    """Raise ConfigurationError if a config value is zero or negative."""
    value = section.get(key)
    if value is not None and value <= 0:
        raise ConfigurationError(
            f"{section_name}.{key} must be positive, got {value}"
        )


def get_default_config() -> dict:
    """
    Get default configuration dictionary.

    Returns:
        Default configuration
    """
    return {
        'ibkr': {
            'host': '127.0.0.1',
            'port': 7497,
            'client_id': 1,
            'timeout': 60,
            'readonly': False
        },
        'contract': {
            'symbol': 'NQ',
            'exchange': 'CME',
            'currency': 'USD',
            'sec_type': 'FUT',
            'expiry': ''
        },
        'strategy': {
            'name': 'NQ 6PM Reopen Strategy',
            'market_reopen_time': '18:00',
            'candle_timeframe_minutes': 5,
            'check_in_times': ['06:00', '08:30', '09:30'],
            'use_previous_day_levels': True
        },
        'risk': {
            'contracts': 1,
            'max_contracts': 2,
            'min_risk_threshold': 300.0,
            'max_risk_per_trade': 500.0,
            'warn_below_risk': True,
            'take_profit_levels': {
                'tp1': 20.0,
                'tp2': 40.0,
                'tp3': 60.0,
                'tp4': 80.0
            },
            'partial_exit_at_tp2': True,
            'partial_exit_quantity': 0.5,
            'trailing_stop': {
                'enabled': True,
                'move_to_be_at_tp2': True,
                'be_buffer_points': 1.0,
                'trail_to_previous_tp': True
            }
        },
        'reentry': {
            'enabled': True,
            'max_reentries': 1,
            'max_total_losses': 2,
            'reentry_at_original_price': True
        },
        'logging': {
            'level': 'DEBUG',
            'log_to_file': True,
            'log_to_console': True,
            'colorful_console': True,
            'log_file_path': 'logs/strategy.log',
            'max_file_size_mb': 10,
            'backup_count': 5
        },
        'safety': {
            'max_daily_loss': 1000.0,
            'max_consecutive_errors': 10,
            'max_slippage_points': 5.0,
            'max_candle_range_points': 100.0,
            'min_candle_body_ratio': 0.10,
            'position_mismatch_check_seconds': 60,
            'stale_data_timeout_seconds': 30
        },
        'data': {
            'data_dir': 'trading_data',
            'save_on_each_trade': True
        },
        'notifications': {
            'enabled': False,
            'telegram_token': '',
            'telegram_chat_id': ''
        },
        'timezone': 'US/Eastern'
    }


def merge_configs(base: dict, override: dict) -> dict:
    """
    Recursively merge two configuration dictionaries.

    Args:
        base: Base configuration
        override: Override configuration

    Returns:
        Merged configuration
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_configs(result[key], value)
        else:
            result[key] = value

    return result


def load_config_with_defaults(config_path: Optional[str] = None) -> dict:
    """
    Load configuration with defaults for missing values.

    Args:
        config_path: Optional path to configuration file

    Returns:
        Complete configuration dictionary
    """
    defaults = get_default_config()

    if config_path and Path(config_path).exists():
        user_config = load_config(config_path)
        return merge_configs(defaults, user_config)

    return defaults


def save_config(config: dict, config_path: str) -> bool:
    """
    Save configuration to a YAML file.

    Args:
        config: Configuration dictionary
        config_path: Path to save the file

    Returns:
        True if successful
    """
    try:
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        return True

    except Exception as e:
        raise ConfigurationError(f"Failed to save configuration: {e}")
