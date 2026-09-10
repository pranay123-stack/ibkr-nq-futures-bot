"""
Position Reconciler.

Handles startup position reconciliation:
- Detects existing positions from previous sessions or crashes
- Places emergency stop loss on orphaned positions
- Prevents duplicate entries by setting entry_triggered flag
"""

from typing import Optional
from ..logger import get_logger
from ..data_models import Direction
from ..risk_manager.price_utils import round_to_tick


class PositionReconciler:
    """Reconciles strategy state with broker positions on startup."""

    def __init__(self, logger=None, notifier=None):
        self.logger = logger or get_logger("PosReconciler")
        self._notifier = notifier

    def reconcile(
        self,
        trade_manager,
        state,
        state_persistence,
        config: dict,
        point_value: float = 20.0
    ) -> bool:
        """
        Check for existing broker positions and reconcile with strategy state.

        Args:
            trade_manager: BrokerComponents (orders, tracker, positions).
            state: StrategyState instance (to set entry_triggered).
            state_persistence: TradeStatePersistence for crash recovery SL.
            config: Full strategy config dict.
            point_value: Point value for the contract (e.g. 2.0 for MNQ, 20.0 for NQ).

        Returns:
            True if a position was found (entry_triggered set).
        """
        try:
            position = trade_manager.positions.get_position(wait_for_data=True)

            if position.is_flat:
                self.logger.info("No existing positions found - clean slate")
                return False

            self.logger.warning(
                f"EXISTING POSITION DETECTED on startup: "
                f"{position.direction.value} {position.quantity} contracts "
                f"@ avg {position.avg_entry_price:.2f}"
            )

            # Check if there's a stop loss order protecting this position
            has_stop = self._has_stop_order(trade_manager)

            if not has_stop:
                self._place_emergency_sl(
                    trade_manager, position, point_value,
                    state_persistence, config
                )
            else:
                self.logger.info("Existing SL order found - position is protected")

            self.logger.warning(
                "Setting entry_triggered=True to prevent duplicate entries. "
                "The bot will monitor this position but will NOT enter new trades "
                "until the position is closed and a new session begins."
            )
            state.entry_triggered = True
            return True

        except Exception as e:
            self.logger.error(f"Position reconciliation failed: {e}")
            return False

    def _has_stop_order(self, trade_manager) -> bool:
        """Check if any open stop order exists for the current position."""
        open_orders = trade_manager.orders.get_open_orders()
        return any(
            hasattr(t.order, 'orderType') and t.order.orderType == 'STP'
            for t in open_orders
        )

    def _place_emergency_sl(
        self, trade_manager, position, point_value,
        state_persistence, config
    ) -> None:
        """Place an emergency stop loss on an orphaned position."""
        # Try to recover SL from crash state
        saved_state = state_persistence.load_state()
        emergency_sl = None

        if saved_state and saved_state.get('active_trade_stop_loss'):
            emergency_sl = saved_state['active_trade_stop_loss']
            self.logger.warning(
                f"No SL order found! Placing emergency SL from crash state: {emergency_sl:.2f}"
            )
        else:
            # Calculate SL from max risk config
            max_risk = config.get('risk', {}).get('max_risk_per_trade', 500.0)
            max_points = max_risk / (point_value * position.quantity)

            if position.direction == Direction.LONG:
                emergency_sl = round_to_tick(position.avg_entry_price - max_points)
            else:
                emergency_sl = round_to_tick(position.avg_entry_price + max_points)

            self.logger.warning(
                f"No SL order found! No saved state! "
                f"Placing emergency SL at max risk distance: {emergency_sl:.2f}"
            )

        if emergency_sl:
            try:
                trade_manager.orders.place_stop_order(
                    direction=position.direction,
                    quantity=position.quantity,
                    stop_price=emergency_sl
                )
                self.logger.warning(f"Emergency SL placed at {emergency_sl:.2f}")
                if self._notifier:
                    self._notifier.error(
                        f"Emergency SL placed at {emergency_sl:.2f} for "
                        f"orphaned {position.direction.value} {position.quantity} position"
                    )
            except Exception as e:
                self.logger.error(f"FAILED to place emergency SL: {e}")
                if self._notifier:
                    self._notifier.error(f"FAILED to place emergency SL: {e}")
