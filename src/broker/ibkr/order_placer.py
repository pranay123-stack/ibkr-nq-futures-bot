"""
IBKR Order Placer.
Places market, limit, stop, and bracket orders via IBKR API.
"""

from typing import Optional
from ib_insync import MarketOrder, LimitOrder, StopOrder, Trade as IBTrade

from ...data_models import Direction, Trade, Signal
from ...logger import get_logger


class TradeManagerError(Exception):
    """IBKR-specific trade execution error."""
    pass


class IBKROrderPlacer:
    """Places orders on IBKR. Requires IB connection, contract, and rate limiter."""

    def __init__(self, ib, contract, throttle_fn, track_fn, expected_prices):
        self.ib = ib
        self.contract = contract
        self._throttle = throttle_fn
        self._track_order = track_fn
        self._expected_fill_prices = expected_prices
        self.logger = get_logger("OrderPlacer")

    def _symbol(self) -> str:
        """Return the contract's display symbol."""
        return self.contract.localSymbol or self.contract.symbol

    def place_market_order(
        self, direction: Direction, quantity: int,
        trade_ref: Optional[Trade] = None, expected_price: float = None
    ) -> Optional[IBTrade]:
        """Place a market order."""
        action = "BUY" if direction == Direction.LONG else "SELL"
        order = MarketOrder(action=action, totalQuantity=quantity, tif='GTC')
        self.logger.info(f"[ORDER] Placing MARKET {action} {quantity} {self._symbol()}")

        try:
            self._throttle()
            ib_trade = self.ib.placeOrder(self.contract, order)
            self._track_order(ib_trade, trade_ref)
            if expected_price is not None:
                self._expected_fill_prices[ib_trade.order.orderId] = expected_price
            return ib_trade
        except Exception as e:
            self.logger.error(f"Failed to place market order: {e}")
            raise TradeManagerError(f"Market order failed: {e}")

    def place_limit_order(
        self, direction: Direction, quantity: int, limit_price: float,
        trade_ref: Optional[Trade] = None
    ) -> Optional[IBTrade]:
        """Place a limit order."""
        action = "BUY" if direction == Direction.LONG else "SELL"
        order = LimitOrder(action=action, totalQuantity=quantity, lmtPrice=limit_price, tif='GTC')
        self.logger.info(f"[ORDER] Placing LIMIT {action} {quantity} {self._symbol()} @ {limit_price:.2f}")

        try:
            self._throttle()
            ib_trade = self.ib.placeOrder(self.contract, order)
            self._track_order(ib_trade, trade_ref)
            self._expected_fill_prices[ib_trade.order.orderId] = limit_price
            return ib_trade
        except Exception as e:
            self.logger.error(f"Failed to place limit order: {e}")
            raise TradeManagerError(f"Limit order failed: {e}")

    def place_stop_order(
        self, direction: Direction, quantity: int, stop_price: float,
        trade_ref: Optional[Trade] = None
    ) -> Optional[IBTrade]:
        """Place a stop order (action is opposite of position direction)."""
        action = "SELL" if direction == Direction.LONG else "BUY"
        order = StopOrder(action=action, totalQuantity=quantity, stopPrice=stop_price, tif='GTC')
        self.logger.info(f"[ORDER] Placing STOP {action} {quantity} {self._symbol()} @ {stop_price:.2f}")

        try:
            self._throttle()
            ib_trade = self.ib.placeOrder(self.contract, order)
            self._track_order(ib_trade, trade_ref)
            self._expected_fill_prices[ib_trade.order.orderId] = stop_price
            return ib_trade
        except Exception as e:
            self.logger.error(f"Failed to place stop order: {e}")
            raise TradeManagerError(f"Stop order failed: {e}")

    def place_bracket_order(self, signal: Signal, quantity: int) -> dict:
        """Place a bracket order (entry + stop loss + take profits)."""
        action = "BUY" if signal.direction == Direction.LONG else "SELL"
        exit_action = "SELL" if signal.direction == Direction.LONG else "BUY"
        self.logger.info(f"Placing bracket order: {action} {quantity} @ MARKET")
        orders = {}

        try:
            parent = MarketOrder(action=action, totalQuantity=quantity, tif='GTC', transmit=False)
            self._throttle()
            parent_trade = self.ib.placeOrder(self.contract, parent)
            orders['entry'] = parent_trade
            parent_id = parent.orderId

            stop_loss = StopOrder(
                action=exit_action, totalQuantity=quantity,
                stopPrice=signal.stop_loss, tif='GTC',
                parentId=parent_id, transmit=False
            )
            self._throttle()
            sl_trade = self.ib.placeOrder(self.contract, stop_loss)
            orders['stop_loss'] = sl_trade

            if signal.take_profit_levels:
                tp_price = signal.take_profit_levels[-1]
                take_profit = LimitOrder(
                    action=exit_action, totalQuantity=quantity,
                    lmtPrice=tp_price, tif='GTC',
                    parentId=parent_id, transmit=True
                )
                self._throttle()
                tp_trade = self.ib.placeOrder(self.contract, take_profit)
                orders['take_profit'] = tp_trade

            self.logger.info(
                f"[ORDER] Bracket order placed: Entry={parent_id}, "
                f"SL={signal.stop_loss:.2f}, TP={tp_price:.2f}"
            )
            return orders

        except Exception as e:
            self.logger.error(f"Failed to place bracket order: {e}")
            raise TradeManagerError(f"Bracket order failed: {e}")

    def modify_stop_loss(self, order_id: int, new_stop_price: float) -> bool:
        """Modify an existing stop loss order."""
        self.logger.info(f"Modifying stop loss {order_id} to {new_stop_price:.2f}")
        try:
            for trade in self.ib.openTrades():
                if trade.order.orderId == order_id:
                    trade.order.stopPrice = new_stop_price
                    self._throttle()
                    self.ib.placeOrder(self.contract, trade.order)
                    self.logger.info(f"[ORDER] Stop loss modified to {new_stop_price:.2f}")
                    return True
            self.logger.warning(f"Order {order_id} not found for modification")
            return False
        except Exception as e:
            self.logger.error(f"Failed to modify stop loss: {e}")
            return False

    def cancel_order(self, order_id: int) -> bool:
        """Cancel an open order."""
        self.logger.info(f"Cancelling order {order_id}")
        try:
            for trade in self.ib.openTrades():
                if trade.order.orderId == order_id:
                    self._throttle()
                    self.ib.cancelOrder(trade.order)
                    self.logger.info(f"[ORDER] Order {order_id} cancelled")
                    return True
            self.logger.warning(f"Order {order_id} not found for cancellation")
            return False
        except Exception as e:
            self.logger.error(f"Failed to cancel order: {e}")
            return False

    def cancel_all_orders(self) -> int:
        """Cancel all open orders for the contract."""
        self.logger.warning("Cancelling all open orders")
        cancelled = 0
        try:
            for trade in self.ib.openTrades():
                if trade.contract.symbol == self.contract.symbol:
                    self._throttle()
                    self.ib.cancelOrder(trade.order)
                    cancelled += 1
            self.logger.info(f"Cancelled {cancelled} orders")
            return cancelled
        except Exception as e:
            self.logger.error(f"Error cancelling orders: {e}")
            return cancelled

    def get_open_orders(self):
        """Get all open orders for the contract."""
        try:
            return [t for t in self.ib.openTrades() if t.contract.symbol == self.contract.symbol]
        except Exception as e:
            self.logger.error(f"Failed to get open orders: {e}")
            return []
