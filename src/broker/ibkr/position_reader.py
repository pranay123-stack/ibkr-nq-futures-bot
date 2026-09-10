"""
IBKR Position Reader.
Reads current positions and provides flatten/close functionality.
"""

from typing import Optional
from ib_insync import Trade as IBTrade

from ...data_models import Direction, Position
from ...logger import get_logger


class IBKRPositionReader:
    """Reads and manages positions from IBKR."""

    def __init__(self, ib, contract, order_placer):
        self.ib = ib
        self.contract = contract
        self._order_placer = order_placer
        self.position = Position()
        self.logger = get_logger("PositionReader")

    def get_position(self, wait_for_data: bool = False) -> Position:
        """Get current position from IBKR.

        Args:
            wait_for_data: If True, wait up to 3s for IBKR to send position data.
                          Use on startup when positions may not be populated yet.
        """
        try:
            if wait_for_data:
                # IBKR needs time to send position data after connect
                self.ib.sleep(2)
                # Force a portfolio refresh
                self.ib.reqPositions()
                self.ib.sleep(1)

            positions = self.ib.positions()
            for pos in positions:
                if pos.contract.symbol == self.contract.symbol:
                    qty = pos.position
                    if qty > 0:
                        self.position.direction = Direction.LONG
                        self.position.quantity = int(qty)
                    elif qty < 0:
                        self.position.direction = Direction.SHORT
                        self.position.quantity = int(abs(qty))
                    else:
                        self.position.direction = Direction.NEUTRAL
                        self.position.quantity = 0
                    # avgCost is total cost per unit including multiplier
                    # Divide by contract multiplier to get price
                    multiplier = float(getattr(self.contract, 'multiplier', '1') or '1')
                    self.position.avg_entry_price = pos.avgCost / multiplier if multiplier else pos.avgCost
                    self.position.unrealized_pnl = pos.unrealizedPNL if hasattr(pos, 'unrealizedPNL') else 0
                    return self.position

            self.position.direction = Direction.NEUTRAL
            self.position.quantity = 0
            return self.position

        except Exception as e:
            self.logger.error(f"Failed to get position: {e}")
            return self.position

    def close_position(self, direction: Direction, quantity: int) -> Optional[IBTrade]:
        """Close an open position with a market order."""
        exit_direction = Direction.SHORT if direction == Direction.LONG else Direction.LONG
        self.logger.info(f"[POSITION] Closing {quantity} contracts ({direction.value} position)")
        return self._order_placer.place_market_order(exit_direction, quantity)

    def flatten_position(self) -> bool:
        """Flatten the entire position (cancel all orders + close)."""
        self.logger.warning("Flattening position")
        try:
            self._order_placer.cancel_all_orders()
            pos = self.get_position()

            if pos.is_flat:
                self.logger.info("Position already flat")
                return True

            trade = self.close_position(pos.direction, pos.quantity)
            if trade:
                from .order_tracker import IBKROrderTracker
                # Use ib.sleep directly for fill wait during flatten
                import time as time_module
                start = time_module.time()
                while time_module.time() - start < 10.0:
                    self.ib.sleep(0.1)
                    if trade.orderStatus.status == 'Filled':
                        self.logger.info("Position flattened successfully")
                        return True
                    elif trade.orderStatus.status in ['Cancelled', 'Inactive']:
                        return False

            return False

        except Exception as e:
            self.logger.error(f"Failed to flatten position: {e}")
            return False
