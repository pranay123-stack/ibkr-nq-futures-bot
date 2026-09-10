"""
Risk Management Package
Provides:
- Kill switch (safety mechanism)
- Slippage manager with limits
- High volatility detector
- Order validation
- Floating point price utilities
"""

from .kill_switch import KillSwitch
from .slippage import SlippageManager
from .volatility import VolatilityDetector
from .order_validation import validate_order_params
from .price_utils import round_to_tick, prices_equal, is_valid_price

__all__ = [
    'KillSwitch',
    'SlippageManager',
    'VolatilityDetector',
    'validate_order_params',
    'round_to_tick',
    'prices_equal',
    'is_valid_price',
]
