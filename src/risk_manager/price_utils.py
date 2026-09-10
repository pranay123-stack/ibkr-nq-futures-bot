"""Floating point price utilities."""

import math

from ..utils.defaults import DEFAULT_TICK_SIZE


def round_to_tick(price: float, tick_size: float = DEFAULT_TICK_SIZE) -> float:
    """Round a price to the nearest valid tick."""
    return round(round(price / tick_size) * tick_size, 2)


def prices_equal(a: float, b: float, tolerance: float = 0.01) -> bool:
    """Compare two prices with floating point tolerance."""
    return abs(a - b) < tolerance


def is_valid_price(price) -> bool:
    """Check if a price value is valid (not None, not NaN, not inf, positive)."""
    if price is None:
        return False
    if not isinstance(price, (int, float)):
        return False
    if isinstance(price, float) and (math.isnan(price) or math.isinf(price)):
        return False
    if price <= 0:
        return False
    return True
