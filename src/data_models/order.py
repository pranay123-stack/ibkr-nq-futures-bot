"""Order model."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .enums import Direction, OrderStatus


@dataclass
class Order:
    """Represents a trading order."""
    id: str
    timestamp: datetime
    direction: Direction
    quantity: int
    order_type: str  # MARKET, LIMIT, STOP
    price: Optional[float] = None
    stop_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_price: Optional[float] = None
    filled_quantity: int = 0
    ib_order_id: Optional[int] = None
    parent_order_id: Optional[str] = None
