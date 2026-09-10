"""
Position monitoring logic for the NQ 6PM Reopen Strategy.

Monitors active positions for stop loss / take profit hits,
checks for broker-vs-strategy position mismatches, and logs
periodic position snapshots.
"""

from datetime import datetime
from typing import Optional

from ..data_models import TradeStatus
from ..timezone.market_calendar import get_default_tz


class PositionMonitor:
    """Monitors open positions and detects mismatches with the broker.

    Accepts all dependencies via constructor parameters to avoid
    circular imports with StrategyRunner.
    """

    def __init__(self, logger, notifier, kill_switch, timezone=None):
        """Initialise the position monitor.

        Args:
            logger: StrategyLogger instance.
            notifier: TelegramNotifier (or compatible) instance.
            kill_switch: KillSwitch for error/PnL tracking.
            timezone: pytz timezone (defaults to US/Eastern).
        """
        self.logger = logger
        self._notifier = notifier
        self._kill_switch = kill_switch
        self.timezone = timezone or get_default_tz()

        # Snapshot tracking
        self._last_snapshot_time: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Core position monitoring
    # ------------------------------------------------------------------

    def monitor_position(self, state, exit_manager, signal_analyzer,
                         trade_manager, market_data,
                         trade_executor, data_storage, persist_state_fn,
                         config) -> None:
        """Monitor the active position for SL / TP hits.

        Args:
            state: StrategyState instance.
            exit_manager: ExitManager instance.
            signal_analyzer: SignalAnalyzer instance.
            trade_manager: Broker trade manager.
            market_data: Market data provider.
            trade_executor: TradeExecutor instance.
            data_storage: CSVStorage for persisting trades.
            persist_state_fn: Callable to persist crash-recovery state.
            config: Strategy configuration dict.
        """
        position = trade_manager.positions.get_position()

        if not state.trades_today:
            return

        active_trade = state.trades_today[-1]
        if active_trade.status != TradeStatus.OPEN:
            return

        if position.is_flat:
            self.logger.warning(
                f"Broker position flat but trade {active_trade.id} still OPEN "
                f"- STP likely filled at broker"
            )
            active_trade.status = TradeStatus.CLOSED
            active_trade.exit_time = datetime.now(exit_manager.timezone)
            active_trade.exit_price = active_trade.current_stop_loss
            active_trade.realized_pnl = exit_manager.calculate_pnl(
                active_trade, active_trade.exit_price
            )
            data_storage.update_trade(active_trade)
            trade_executor.handle_stop_loss(
                active_trade, exit_manager, state, trade_manager,
                data_storage, signal_analyzer, persist_state_fn,
                config=config
            )
            return

        current_price = market_data.current_price
        if current_price is None or (
            isinstance(current_price, float) and current_price != current_price
        ):
            self.logger.warning(
                "Cannot monitor position: no valid price data"
            )
            return

        pnl = exit_manager.calculate_pnl(active_trade, current_price)
        active_trade.unrealized_pnl = pnl

        realized = sum(
            t.realized_pnl or 0 for t in state.trades_today
        )
        self._kill_switch.update_daily_pnl(realized + pnl)

        # Check stop loss
        if exit_manager.check_stop_loss(active_trade, current_price):
            trade_executor.handle_stop_loss(
                active_trade, exit_manager, state, trade_manager,
                data_storage, signal_analyzer, persist_state_fn,
                config=config
            )
            return

        # Check take profits
        tp_hit = exit_manager.check_take_profits(active_trade, current_price)
        if tp_hit and tp_hit > active_trade.highest_tp_hit:
            trade_executor.handle_take_profit(
                active_trade, tp_hit, current_price,
                exit_manager, trade_manager, data_storage
            )

    # ------------------------------------------------------------------
    # Position mismatch detection
    # ------------------------------------------------------------------

    def check_position_mismatch(self, state, trade_manager,
                                pos_mismatch, kill_switch) -> None:
        """Check whether internal state matches the broker's position.

        Args:
            state: StrategyState instance.
            trade_manager: Broker trade manager.
            pos_mismatch: PositionMismatchDetector instance.
            kill_switch: KillSwitch for error tracking.
        """
        try:
            broker_pos = trade_manager.positions.get_position()

            active_trade = None
            if state.trades_today:
                last = state.trades_today[-1]
                if last.status == TradeStatus.OPEN:
                    active_trade = last

            strat_dir = (
                active_trade.direction.value if active_trade else "NEUTRAL"
            )
            strat_qty = (
                (active_trade.quantity - active_trade.exit_quantity)
                if active_trade else 0
            )
            broker_dir = broker_pos.direction.value
            broker_qty = broker_pos.quantity

            mismatch = pos_mismatch.check_mismatch(
                strat_dir, strat_qty, broker_dir, broker_qty
            )
            if mismatch:
                self.logger.error(
                    "POSITION MISMATCH DETECTED - manual review required"
                )
                kill_switch.record_error()
            else:
                kill_switch.clear_errors()

        except Exception as e:
            self.logger.error(f"Position mismatch check failed: {e}")

    # ------------------------------------------------------------------
    # Position snapshot logging
    # ------------------------------------------------------------------

    def log_position_snapshot(self, state, exit_manager, trade_manager,
                              market_data,
                              snapshot_interval_minutes: int) -> None:
        """Log a periodic position snapshot if enough time has elapsed.

        Args:
            state: StrategyState instance.
            exit_manager: ExitManager instance.
            trade_manager: Broker trade manager.
            market_data: Market data provider.
            snapshot_interval_minutes: Minutes between snapshots.
        """
        now = datetime.now(self.timezone)

        if self._last_snapshot_time is None:
            self._last_snapshot_time = now
            return

        elapsed = (now - self._last_snapshot_time).total_seconds() / 60
        if elapsed < snapshot_interval_minutes:
            return

        current_price = market_data.current_price
        if current_price is None or (
            isinstance(current_price, float) and current_price != current_price
        ):
            return

        contract_symbol = ""
        if trade_manager.contract:
            contract_symbol = (
                trade_manager.contract.localSymbol
                or trade_manager.contract.symbol
            )

        active_trade = None
        if state.trades_today:
            last_trade = state.trades_today[-1]
            if last_trade.status == TradeStatus.OPEN:
                active_trade = last_trade

        if active_trade is None:
            self.logger.position_snapshot(
                direction="FLAT", qty=0, entry=0,
                current=current_price, pnl=0,
                symbol=contract_symbol
            )
        else:
            pnl = exit_manager.calculate_pnl(active_trade, current_price)
            self.logger.position_snapshot(
                direction=active_trade.direction.value,
                qty=active_trade.quantity,
                entry=active_trade.entry_price,
                current=current_price, pnl=pnl,
                symbol=contract_symbol
            )

        self._last_snapshot_time = now
