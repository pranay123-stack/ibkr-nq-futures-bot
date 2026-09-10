"""
Trade execution logic for the NQ 6PM Reopen Strategy.

Handles entry order placement, stop loss handling, take profit handling,
and partial position exits.
"""

from datetime import datetime
from typing import Optional

from ..data_models import Direction, TradeStatus
from ..timezone.market_calendar import get_default_tz
from ..risk_manager import (
    round_to_tick, validate_order_params
)


class TradeExecutor:
    """Executes trade entries, stop losses, take profits, and partial exits.

    Accepts all dependencies via constructor parameters to avoid
    circular imports with StrategyRunner.
    """

    def __init__(self, logger, notifier, slippage_mgr, kill_switch, timezone=None):
        """Initialise the trade executor.

        Args:
            logger: StrategyLogger instance.
            notifier: TelegramNotifier (or compatible) instance.
            slippage_mgr: SlippageManager for fill-slippage checks.
            kill_switch: KillSwitch for error/PnL tracking.
            timezone: pytz timezone (defaults to US/Eastern).
        """
        self.logger = logger
        self._notifier = notifier
        self._slippage_mgr = slippage_mgr
        self._kill_switch = kill_switch
        self.timezone = timezone or get_default_tz()

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    def execute_entry(self, signal, signal_analyzer, state, trade_manager,
                      data_storage, config, persist_state_fn) -> None:
        """Execute a trade entry based on a signal.

        Uses a LIMIT order for re-entries (at the original price) and a
        MARKET order for initial entries.

        Args:
            signal: Entry signal object from the strategy.
            signal_analyzer: SignalAnalyzer instance (for create_trade_from_signal, contracts).
            state: StrategyState instance.
            trade_manager: Broker trade manager.
            data_storage: CSVStorage for persisting trades.
            config: Strategy configuration dict.
            persist_state_fn: Callable to persist crash-recovery state.
        """
        # Import broker-agnostic error type
        from ..broker.base import TradeExecutionError as TradeManagerError

        self.logger.trade(
            f"Executing {signal.direction.value} entry @ {signal.price:.2f}"
        )

        try:
            # Validate order parameters before submission
            errors = validate_order_params(
                direction=signal.direction.value,
                quantity=signal_analyzer.contracts,
                price=signal.price if signal.is_reentry else None,
                stop_price=signal.stop_loss,
                max_quantity=config.get('risk', {}).get('max_contracts', 10)
            )
            if errors:
                self.logger.error(f"Order validation failed: {errors}")
                state.entry_triggered = True
                return

            # Round prices to valid ticks
            signal.price = round_to_tick(signal.price)
            signal.stop_loss = round_to_tick(signal.stop_loss)

            # Create trade object
            trade = signal_analyzer.create_trade_from_signal(
                signal, is_reentry=signal.is_reentry
            )

            # Re-entry uses MARKET order (SL just hit, enter immediately)
            if signal.is_reentry:
                self.logger.trade(
                    f"Re-entry: placing MARKET order (original price "
                    f"{signal.price:.2f})"
                )
                ib_trade = trade_manager.orders.place_market_order(
                    direction=signal.direction,
                    quantity=trade.quantity,
                    trade_ref=trade,
                    expected_price=signal.price
                )
            else:
                ib_trade = trade_manager.orders.place_market_order(
                    direction=signal.direction,
                    quantity=trade.quantity,
                    trade_ref=trade
                )

            if ib_trade:
                ibkr_cfg = config.get('ibkr', {})
                base_timeout = ibkr_cfg.get('fill_timeout', 30)
                reentry_timeout = ibkr_cfg.get('reentry_fill_timeout', 120)
                fill_timeout = (
                    reentry_timeout if signal.is_reentry else base_timeout
                )
                filled = trade_manager.tracker.wait_for_fill(
                    ib_trade, timeout=fill_timeout
                )

                if filled:
                    trade.entry_price = ib_trade.orderStatus.avgFillPrice
                    self._slippage_mgr.check_fill_slippage(
                        expected_price=signal.price,
                        fill_price=trade.entry_price,
                        direction=signal.direction.value
                    )
                    self.logger.lifecycle(
                        trade_id=trade.id,
                        stage='FILLED',
                        details=f"@ {trade.entry_price:.2f}"
                    )

                    sl_price = round_to_tick(signal.stop_loss)
                    trade_manager.orders.place_stop_order(
                        direction=signal.direction,
                        quantity=trade.quantity,
                        stop_price=sl_price
                    )

                    trade.status = TradeStatus.OPEN
                    data_storage.save_trade(trade)

                    persist_state_fn()
                    self._kill_switch.clear_errors()

                    slip = trade.entry_price - signal.price
                    self._notifier.trade_filled(
                        trade_id=trade.id,
                        direction=signal.direction.value,
                        price=trade.entry_price,
                        qty=trade.quantity,
                        slippage=slip
                    )
                else:
                    self.logger.error(
                        "Entry order not filled within timeout"
                    )
                    trade.status = TradeStatus.CANCELLED
                    try:
                        trade_manager.orders.cancel_order(
                            ib_trade.order.orderId
                        )
                    except Exception as e:
                        self.logger.error(
                            f"Failed to cancel unfilled order: {e}"
                        )

        except TradeManagerError as e:
            self.logger.error(f"Trade execution failed: {e}")

    # ------------------------------------------------------------------
    # Stop loss
    # ------------------------------------------------------------------

    def handle_stop_loss(self, trade, exit_manager, state, trade_manager,
                         data_storage, signal_analyzer, persist_state_fn,
                         config=None) -> None:
        """Handle a stop-loss hit for *trade*.

        Closes the position, updates PnL, logs, notifies, and triggers a
        re-entry check.

        Args:
            trade: The active Trade object that was stopped out.
            exit_manager: ExitManager instance.
            state: StrategyState instance.
            trade_manager: Broker trade manager.
            data_storage: CSVStorage for persisting trades.
            signal_analyzer: SignalAnalyzer instance (for re-entry execution).
            persist_state_fn: Callable to persist crash-recovery state.
            config: Strategy configuration dict (needed for re-entry).
        """
        self.logger.lifecycle(
            trade_id=trade.id,
            stage='SL_HIT',
            details=f"@ {trade.current_stop_loss:.2f}"
        )

        trade_manager.orders.cancel_all_orders()

        broker_pos = trade_manager.positions.get_position()
        if not broker_pos.is_flat:
            close_trade = trade_manager.positions.close_position(trade.direction, trade.quantity)
            if close_trade:
                filled = trade_manager.tracker.wait_for_fill(close_trade, timeout=10)
                if filled:
                    trade.exit_price = close_trade.orderStatus.avgFillPrice
                else:
                    trade.exit_price = trade.current_stop_loss
            else:
                trade.exit_price = trade.current_stop_loss
        else:
            trade.exit_price = trade.current_stop_loss

        trade.status = TradeStatus.CLOSED
        trade.exit_time = datetime.now(self.timezone)
        trade.realized_pnl = exit_manager.calculate_pnl(trade, trade.exit_price)

        self.logger.trade_result(
            trade_id=trade.id,
            direction=trade.direction.value,
            entry=trade.entry_price,
            exit_price=trade.exit_price,
            pnl=trade.realized_pnl,
            result="LOSS" if trade.realized_pnl < 0 else "BREAKEVEN"
        )

        data_storage.update_trade(trade)

        self._notifier.trade_stopped(
            trade_id=trade.id,
            direction=trade.direction.value,
            entry=trade.entry_price,
            exit_price=trade.exit_price,
            pnl=trade.realized_pnl
        )

        total_pnl = sum(
            t.realized_pnl or 0 for t in state.trades_today
        )
        self._kill_switch.update_daily_pnl(total_pnl)

        persist_state_fn()

        can_reentry = exit_manager.handle_trade_loss(trade)

        if can_reentry:
            reentry_signal = exit_manager.generate_reentry_signal(trade)
            if reentry_signal:
                data_storage.save_signal(reentry_signal)
                self.execute_entry(
                    reentry_signal, signal_analyzer, state,
                    trade_manager, data_storage, config or {},
                    persist_state_fn
                )

    # ------------------------------------------------------------------
    # Take profit
    # ------------------------------------------------------------------

    def handle_take_profit(self, trade, tp_level: int, current_price: float,
                           exit_manager, trade_manager, data_storage) -> None:
        """Handle a take-profit level hit.

        Updates the trailing stop and optionally triggers a partial exit
        at TP2.

        Args:
            trade: The active Trade object.
            tp_level: The TP level that was hit (1, 2, 3 ...).
            current_price: Current market price.
            exit_manager: ExitManager instance.
            trade_manager: Broker trade manager.
            data_storage: CSVStorage for persisting trades.
        """
        self.logger.lifecycle(
            trade_id=trade.id,
            stage=f'TP{tp_level}_HIT',
            details=f"@ {current_price:.2f}"
        )

        new_stop = exit_manager.update_trailing_stop(trade, tp_level)

        if new_stop:
            open_orders = trade_manager.orders.get_open_orders()
            for ib_trade in open_orders:
                if hasattr(ib_trade.order, 'stopPrice'):
                    trade_manager.orders.modify_stop_loss(
                        ib_trade.order.orderId, new_stop
                    )

        self._notifier.tp_hit(
            trade_id=trade.id,
            tp_level=tp_level,
            price=current_price,
            new_sl=new_stop
        )

        if tp_level == 2 and exit_manager.partial_exit_enabled:
            self.handle_partial_exit(trade, current_price, exit_manager,
                                     trade_manager)

        data_storage.update_trade(trade)

    # ------------------------------------------------------------------
    # Partial exit
    # ------------------------------------------------------------------

    def handle_partial_exit(self, trade, exit_price: float,
                            exit_manager, trade_manager) -> None:
        """Handle a partial position exit at TP2.

        Args:
            trade: The active Trade object.
            exit_price: Price at which the partial exit is executed.
            exit_manager: ExitManager instance.
            trade_manager: Broker trade manager.
        """
        from ..broker.base import TradeExecutionError as TradeManagerError

        partial_qty = exit_manager.calculate_partial_exit_quantity(trade)

        if partial_qty <= 0:
            return

        self.logger.trade(
            f"Partial exit: {partial_qty} contracts @ {exit_price:.2f}"
        )

        try:
            exit_direction = (
                Direction.SHORT
                if trade.direction == Direction.LONG
                else Direction.LONG
            )
            ib_trade = trade_manager.orders.place_market_order(
                exit_direction, partial_qty
            )

            if (ib_trade and
                    trade_manager.tracker.wait_for_fill(ib_trade, timeout=10)):
                trade.exit_quantity += partial_qty
                trade.partial_exits.append({
                    'quantity': partial_qty,
                    'price': ib_trade.orderStatus.avgFillPrice,
                    'time': datetime.now(self.timezone).isoformat()
                })
                trade.status = TradeStatus.PARTIALLY_CLOSED

        except TradeManagerError as e:
            self.logger.error(f"Partial exit failed: {e}")
