"""Order parameter validation."""

from typing import Optional, List

from .price_utils import is_valid_price
from ..utils.defaults import DEFAULT_TICK_SIZE


def validate_order_params(
    direction: str,
    quantity: int,
    price: Optional[float] = None,
    stop_price: Optional[float] = None,
    max_quantity: int = 10,
    tick_size: float = DEFAULT_TICK_SIZE
) -> List[str]:
    """
    Validate order parameters before submission.
    Returns list of error messages (empty = valid).
    """
    errors = []

    if direction not in ("LONG", "SHORT", "BUY", "SELL"):
        errors.append(f"Invalid direction: {direction}")

    if quantity <= 0:
        errors.append(f"Invalid quantity: {quantity}")
    elif quantity > max_quantity:
        errors.append(f"Quantity {quantity} exceeds max {max_quantity}")

    if price is not None:
        if not is_valid_price(price):
            errors.append(f"Invalid price: {price}")
        elif price % tick_size != 0:
            # Auto-round rather than reject
            pass

    if stop_price is not None:
        if not is_valid_price(stop_price):
            errors.append(f"Invalid stop price: {stop_price}")

    return errors
