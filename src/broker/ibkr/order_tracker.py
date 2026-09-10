"""
IBKR Order Tracker.
Tracks order state, handles fill/status callbacks from IBKR event system.
"""

from datetime import datetime
from typing import Optional, List, Callable
import time as time_module

from ib_insync import Trade as IBTrade

from ...data_models import Direction, Trade, Order, OrderStatus
from ...logger import get_logger, StrategyLogger


class IBKROrderTracker:
    """Tracks IBKR orders: state updates, fill callbacks, wait-for-fill."""

    def __init__(self, ib, contract):
        self.ib = ib
        self.contract = contract
        self.active_orders: dict = {}  # ib_order_id -> Order
        self._expected_fill_prices: dict = {}  # order_id -> expected_price
        self._fill_callbacks: List[Callable] = []
        self._order_status_callbacks: List[Callable] = []
        self.logger = get_logger("OrderTracker")

        # Register IBKR event handlers
        self.ib.orderStatusEvent += self._on_order_status
        self.ib.execDetailsEvent += self._on_execution
        self.ib.newOrderEvent += self._on_new_order

    def _on_order_status(self, trade: IBTrade) -> None:
        """Handle order status updates from IBKR."""
        order = trade.order
        status = trade.orderStatus

        self.logger.debug(
            f"Order status: {order.orderId} - {status.status} "
            f"(filled: {status.filled}, remaining: {status.remaining})"
        )

        if order.orderId in self.active_orders:
            internal_order = self.active_orders[order.orderId]

            if status.status == 'Filled':
                internal_order.status = OrderStatus.FILLED
                internal_order.filled_quantity = int(status.filled)
                internal_order.filled_price = status.avgFillPrice

                strategy_logger = StrategyLogger._instance
                expected_price = self._expected_fill_prices.get(order.orderId)
                action = order.action
                qty = int(status.filled)
                symbol = self.contract.localSymbol or self.contract.symbol

                if strategy_logger:
                    strategy_logger.order_fill(
                        action=action, qty=qty, symbol=symbol,
                        price=status.avgFillPrice, expected_price=expected_price
                    )
                else:
                    self.logger.info(f"[FILL] {action} {qty} {symbol} @ {status.avgFillPrice:.2f}")

                if order.orderId in self._expected_fill_prices:
                    del self._expected_fill_prices[order.orderId]

            elif status.status == 'Cancelled':
                internal_order.status = OrderStatus.CANCELLED
                self.logger.info(f"[ORDER] Order {order.orderId} CANCELLED")

            elif status.status == 'Submitted':
                internal_order.status = OrderStatus.SUBMITTED
                self.logger.info(f"[ORDER] Order {order.orderId} SUBMITTED")

        for callback in self._order_status_callbacks:
            try:
                callback(trade)
            except Exception as e:
                self.logger.error(f"Error in order status callback: {e}")

    def _on_execution(self, trade: IBTrade, fill) -> None:
        """Handle execution details from IBKR."""
        symbol = self.contract.localSymbol or self.contract.symbol
        self.logger.info(
            f"[EXEC] {fill.execution.side} {int(fill.execution.shares)} {symbol} "
            f"@ {fill.execution.price:.2f}"
        )
        for callback in self._fill_callbacks:
            try:
                callback(trade, fill)
            except Exception as e:
                self.logger.error(f"Error in fill callback: {e}")

    def _on_new_order(self, trade: IBTrade) -> None:
        """Handle new order event from IBKR."""
        self.logger.debug(f"New order placed: {trade.order.orderId}")

    def track_order(self, ib_trade: IBTrade, trade_ref: Optional[Trade] = None) -> None:
        """Register an order for internal tracking."""
        order_id = ib_trade.order.orderId
        internal_order = Order(
            id=str(order_id),
            timestamp=datetime.now(),
            direction=Direction.LONG if ib_trade.order.action == "BUY" else Direction.SHORT,
            quantity=int(ib_trade.order.totalQuantity),
            order_type=type(ib_trade.order).__name__.replace("Order", "").upper(),
            ib_order_id=order_id,
            status=OrderStatus.SUBMITTED
        )
        self.active_orders[order_id] = internal_order

    def wait_for_fill(self, ib_trade: IBTrade, timeout: float = 30.0) -> bool:
        """Wait for an order to be filled. Returns True if filled within timeout."""
        self.logger.debug(f"Waiting for fill on order {ib_trade.order.orderId}")
        start_time = time_module.time()

        while time_module.time() - start_time < timeout:
            self.ib.sleep(0.1)
            if ib_trade.orderStatus.status == 'Filled':
                return True
            elif ib_trade.orderStatus.status in ['Cancelled', 'Inactive']:
                return False

        self.logger.warning(f"Timeout waiting for fill on order {ib_trade.order.orderId}")
        return False

    def add_fill_callback(self, callback: Callable) -> None:
        """Add a callback for order fills."""
        self._fill_callbacks.append(callback)

    def add_order_status_callback(self, callback: Callable) -> None:
        """Add a callback for order status updates."""
        self._order_status_callbacks.append(callback)
