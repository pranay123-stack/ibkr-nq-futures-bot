#!/usr/bin/env python3
"""
NQ 6PM Reopen Strategy Runner
Main entry point for the NASDAQ 100 E-mini Futures Trading Strategy.

Usage:
    python run_strategy.py [--config CONFIG_PATH] [--paper] [--backtest]
"""

import argparse
import signal
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pytz

# Add src to path
sys.path.insert(0, str(Path(__file__).parent))

from src.timezone.market_calendar import get_default_tz, MarketCalendar
from src.utils.config_loader import load_config, load_config_with_defaults, ConfigurationError
from src.logger import setup_logging, StrategyLogger
from src.entry.signal_analyzer import SignalAnalyzer
from src.exit.exit_manager import ExitManager
from src.data_models import Direction, TradeStatus, SignalType, StrategyState
from src.utils.data_storage import CSVStorage, create_data_storage
from src.notifier import TelegramNotifier, create_notifier
from src.utils.defaults import CONTRACT_SPECS, DEFAULT_TICK_SIZE, DEFAULT_SYMBOL

# Broker abstraction - import interfaces, not implementations
from src.broker.base import BaseConnection, BaseMarketData
from src.broker.broker_factory import create_broker, create_trade_manager, create_market_data, BrokerComponents
# Import broker-agnostic error types (IBKR's TradeManagerError inherits from this)
from src.broker.base import TradeExecutionError as TradeManagerError

from src.error_handler import ProcessLock, TradeStatePersistence, PreflightChecker
from src.monitor import HeartbeatMonitor, StaleDataDetector
from src.risk_manager import (
    SlippageManager, VolatilityDetector, KillSwitch,
    is_valid_price, round_to_tick, validate_order_params
)
from src.position_manager import PositionMismatchDetector, PositionReconciler

from src.utils.defaults import (
    MAX_DAILY_LOSS, MAX_CONSECUTIVE_ERRORS, MAX_SLIPPAGE_POINTS,
    MAX_CANDLE_RANGE_POINTS, MIN_CANDLE_BODY_RATIO,
    STALE_DATA_TIMEOUT_SECONDS, POSITION_MISMATCH_CHECK_SECONDS,
    POSITION_SNAPSHOT_INTERVAL_MINUTES
)

# Delegate modules
from src.execution.trade_executor import TradeExecutor
from src.position_manager.position_monitor import PositionMonitor
from src.monitor.session_manager import SessionManager


class StrategyRunner:
    """
    Main strategy runner that orchestrates all components.
    """

    def __init__(self, config_path: Optional[str] = None, paper_trading: bool = True):
        self.config_path = config_path or "config/strategy_config.yaml"
        self.paper_trading = paper_trading
        self.base_path = Path(__file__).parent

        # Components (initialized in setup)
        self.config: dict = {}
        self.logger: Optional[StrategyLogger] = None
        self.connection: Optional[BaseConnection] = None
        self.market_data: Optional[BaseMarketData] = None
        self.trade_manager: Optional[BrokerComponents] = None
        self.data_storage: Optional[CSVStorage] = None

        # Strategy components (replaces NQ6PMStrategy facade)
        self.state: Optional[StrategyState] = None
        self.signal_analyzer: Optional[SignalAnalyzer] = None
        self.exit_manager: Optional[ExitManager] = None

        # Runtime state
        self._running = False
        self._shutdown_requested = False
        # Use default tz before config is loaded; setup() will override
        self.timezone = get_default_tz()
        self._market_calendar: Optional[MarketCalendar] = None

        # Position snapshot tracking
        self._snapshot_interval_minutes = POSITION_SNAPSHOT_INTERVAL_MINUTES

        # Session summary tracking
        self._last_summary_date: Optional[str] = None

        # Check-in dedup: track which check-in times we already logged this session
        self._logged_check_in_times: set = set()

        # Track current session date to detect new sessions
        self._current_session_date: Optional[str] = None

        # Robustness components (defaults here, overridden from config in setup())
        self._process_lock = ProcessLock()
        self._heartbeat = HeartbeatMonitor()
        self._slippage_mgr = SlippageManager()
        self._volatility_detector = VolatilityDetector()
        self._state_persistence = TradeStatePersistence(
            state_file=str(self.base_path / "trading_data" / "trade_state.json")
        )
        self._pos_mismatch = PositionMismatchDetector()
        self._kill_switch = KillSwitch()
        self._stale_data = StaleDataDetector()
        self._notifier: Optional[TelegramNotifier] = None

        # Delegate objects (initialized after logger/notifier are ready)
        self._trade_executor: Optional[TradeExecutor] = None
        self._position_monitor: Optional[PositionMonitor] = None
        self._session_manager: Optional[SessionManager] = None

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals gracefully."""
        print("\nShutdown signal received...")
        self._shutdown_requested = True

    def setup(self) -> bool:
        """
        Initialize all strategy components.

        Returns:
            True if setup successful
        """
        try:
            # Acquire process lock (prevent double-run)
            if not self._process_lock.acquire():
                print("FATAL: Another instance is already running. Exiting.")
                return False

            # Load configuration
            config_file = self.base_path / self.config_path
            self.config = load_config_with_defaults(str(config_file))

            # Create MarketCalendar from config (single source of truth)
            self._market_calendar = MarketCalendar(self.config)
            self.timezone = self._market_calendar.tz

            # Setup logging
            self.logger = setup_logging(self.config)

            mode = "PAPER" if self.paper_trading else "LIVE"
            self.logger.info(f"NQ 6PM Strategy started ({mode})")

            # Setup data storage
            self.data_storage = create_data_storage(
                self.config, base_path=str(self.base_path)
            )
            self.logger.info("Data storage initialized")

            # Setup Telegram notifications
            self._notifier = create_notifier(self.config)

            # Configure robustness components from config
            safety = self.config.get('safety', {})
            self._kill_switch = KillSwitch(
                max_daily_loss=safety.get('max_daily_loss', MAX_DAILY_LOSS),
                max_consecutive_errors=safety.get('max_consecutive_errors', MAX_CONSECUTIVE_ERRORS)
            )
            self._slippage_mgr = SlippageManager(
                max_slippage_points=safety.get('max_slippage_points', MAX_SLIPPAGE_POINTS)
            )
            self._volatility_detector = VolatilityDetector(
                max_candle_range_points=safety.get('max_candle_range_points', MAX_CANDLE_RANGE_POINTS),
                min_candle_body_ratio=safety.get('min_candle_body_ratio', MIN_CANDLE_BODY_RATIO)
            )
            self._pos_mismatch = PositionMismatchDetector(
                check_interval_seconds=safety.get('position_mismatch_check_seconds', POSITION_MISMATCH_CHECK_SECONDS)
            )
            self._stale_data = StaleDataDetector(
                max_stale_seconds=safety.get('stale_data_timeout_seconds', STALE_DATA_TIMEOUT_SECONDS)
            )
            self._heartbeat = HeartbeatMonitor(
                stale_threshold_seconds=safety.get('stale_data_timeout_seconds', STALE_DATA_TIMEOUT_SECONDS)
            )

            # Parse reopen time and check-in times from config
            strategy_config = self.config.get('strategy', {})
            reopen_str = strategy_config.get('market_reopen_time', '18:00')
            reopen_hour, reopen_minute = map(int, reopen_str.split(':'))

            check_in_times = []
            for t_str in strategy_config.get('check_in_times', ['06:00', '08:30', '09:30']):
                h, m = map(int, t_str.split(':'))
                from datetime import time as dt_time
                check_in_times.append(dt_time(h, m))

            # Instantiate delegate objects
            self._trade_executor = TradeExecutor(
                logger=self.logger,
                notifier=self._notifier,
                slippage_mgr=self._slippage_mgr,
                kill_switch=self._kill_switch,
                timezone=self.timezone
            )
            self._position_monitor = PositionMonitor(
                logger=self.logger,
                notifier=self._notifier,
                kill_switch=self._kill_switch,
                timezone=self.timezone
            )
            self._session_manager = SessionManager(
                logger=self.logger,
                notifier=self._notifier,
                timezone=self.timezone,
                reopen_hour=reopen_hour,
                check_in_times=check_in_times
            )

            # Connect to broker (uses factory - broker type from config)
            broker_name = self.config.get('broker', 'ibkr')
            self.connection, contract = create_broker(self.config)

            # Setup market data handler (broker-agnostic via factory)
            self.market_data = create_market_data(
                broker_name, self.connection, contract, self.config
            )
            self.market_data.subscribe_ticker()

            # Setup trade manager (broker-agnostic via factory)
            self.trade_manager = create_trade_manager(
                broker_name, self.connection, contract, self.config
            )

            # Setup strategy components directly (no facade)
            self.state = StrategyState(session_date=datetime.now(self.timezone))
            self.signal_analyzer = SignalAnalyzer(self.config, self.state)
            self.exit_manager = ExitManager(self.config, self.state)

            # Get snapshot interval from config
            log_config = self.config.get('logging', {})
            self._snapshot_interval_minutes = log_config.get('position_snapshot_interval_minutes', 15)

            # Run preflight checks (clock sync, account/margin)
            preflight = PreflightChecker()
            ib_instance = getattr(self.connection, 'ib', None)
            if not preflight.run_all(ib=ib_instance):
                self.logger.error("Preflight checks failed - aborting startup")
                return False

            # Check for crash recovery state
            self._try_crash_recovery()

            # Check for existing positions on startup (position reconciliation)
            self._reconcile_existing_positions()

            self.logger.info("All components initialized successfully")

            # Notify bot started
            contract_sym = contract.localSymbol or contract.symbol
            account = self.connection.ib.managedAccounts()[0] if self.connection.ib.managedAccounts() else ""
            self._notifier.bot_started(
                mode="PAPER" if self.paper_trading else "LIVE",
                symbol=contract_sym,
                account=account
            )

            return True

        except ConfigurationError as e:
            print(f"Configuration error: {e}")
            return False
        except ConnectionError as e:
            print(f"Broker connection error: {e}")
            return False
        except Exception as e:
            print(f"Setup error: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _reconcile_existing_positions(self) -> None:
        """Delegate to PositionReconciler for startup position checks."""
        reconciler = PositionReconciler(
            logger=self.logger, notifier=self._notifier
        )
        reconciler.reconcile(
            trade_manager=self.trade_manager,
            state=self.state,
            state_persistence=self._state_persistence,
            config=self.config,
            point_value=self.signal_analyzer.POINT_VALUE
        )

    def _try_crash_recovery(self) -> None:
        """
        Check for saved trade state from a previous crash.
        If found, restore critical state so the bot doesn't double-enter or lose track.
        """
        saved = self._state_persistence.load_state()
        if saved is None:
            self.logger.info("No crash recovery state found - clean start")
            return

        self.logger.warning("=" * 50)
        self.logger.warning("CRASH RECOVERY: Previous state found!")
        self.logger.warning(f"  Session: {saved.get('session_date')}")
        self.logger.warning(f"  Entry triggered: {saved.get('entry_triggered')}")
        self.logger.warning(f"  Losses today: {saved.get('losses_today')}")
        self.logger.warning(f"  Re-entries used: {saved.get('reentries_used')}")
        self.logger.warning(f"  Active trade: {saved.get('active_trade_id')}")
        self.logger.warning(f"  Direction: {saved.get('active_trade_direction')}")
        self.logger.warning(f"  Entry price: {saved.get('active_trade_entry_price')}")
        self.logger.warning(f"  Stop loss: {saved.get('active_trade_stop_loss')}")
        self.logger.warning("=" * 50)

        # Check if the saved state is from the current session
        current_session = self._get_session_date_key()
        if saved.get('session_date') == current_session:
            self.logger.warning("Restoring state from current session...")
            self.state.entry_triggered = saved.get('entry_triggered', False)
            self.state.losses_today = saved.get('losses_today', 0)
            self.state.reentries_used = saved.get('reentries_used', 0)
            self.logger.warning(
                f"Restored: entry_triggered={self.state.entry_triggered}, "
                f"losses={self.state.losses_today}, "
                f"reentries={self.state.reentries_used}"
            )
        else:
            self.logger.info(
                f"Saved state is from old session ({saved.get('session_date')}), "
                f"current is {current_session} - ignoring"
            )

        # Clear the state file after recovery
        self._state_persistence.clear_state()

    def run(self) -> None:
        """
        Main strategy execution loop.
        """
        if not self.logger:
            print("Strategy not initialized. Call setup() first.")
            return

        self._running = True
        self.logger.info("Starting strategy execution loop")

        try:
            # Initialize session
            strategy_config = self.config.get('strategy', {})
            self._session_manager.initialize_session(
                self.state, self.market_data, strategy_config
            )
            self._current_session_date = self._get_session_date_key()

            while self._running and not self._shutdown_requested:
                try:
                    # Check for IBKR connection health
                    if not self.connection.is_connected:
                        self.logger.warning("IBKR connection lost - attempting reconnection")
                        self._notifier.connection_lost()
                        if not self._attempt_reconnect():
                            self.logger.error("Reconnection failed - sleeping 30s before retry")
                            time.sleep(30)
                            continue
                        self._notifier.connection_restored()

                    # Check for new trading session (reset state at each new 6PM)
                    self._check_session_rollover()

                    self._execution_cycle()
                except Exception as e:
                    self.logger.error(f"Error in execution cycle: {e}")
                    import traceback
                    self.logger.error(traceback.format_exc())
                    # Track consecutive errors for kill switch
                    self._kill_switch.record_error()
                    if self._kill_switch.is_active():
                        self._notifier.kill_switch_triggered(
                            f"Too many consecutive errors: {e}"
                        )
                    # Longer sleep on error to avoid tight error loops
                    time.sleep(10)

        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received")
        finally:
            self.shutdown()

    def _attempt_reconnect(self) -> bool:
        """Attempt to reconnect to IBKR with retries."""
        self.connection._reconnect_attempts = 0
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            self.logger.info(f"Reconnection attempt {attempt}/{max_attempts}")
            try:
                success = self.connection.reconnect()
                if success:
                    # Re-subscribe to market data
                    self.market_data.subscribe_ticker()
                    self.logger.info("Reconnected and re-subscribed to market data")
                    return True
            except Exception as e:
                self.logger.error(f"Reconnection attempt {attempt} failed: {e}")
            time.sleep(5 * attempt)  # Exponential backoff
        return False

    # ---- Delegated helpers (thin wrappers) ----------------------------

    def _get_session_date_key(self) -> str:
        """Get a unique key for the current trading session."""
        return self._session_manager.get_session_date_key()

    def _check_session_rollover(self) -> None:
        """Check if we've rolled into a new trading session and reset state."""
        strategy_config = self.config.get('strategy', {})
        self._current_session_date = self._session_manager.check_session_rollover(
            self._current_session_date, self.state, self.market_data,
            strategy_config, self._logged_check_in_times
        )

    def _execute_entry(self, signal) -> None:
        """Execute a trade entry based on signal."""
        self._trade_executor.execute_entry(
            signal, self.signal_analyzer, self.state,
            self.trade_manager, self.data_storage,
            self.config, self._persist_trade_state
        )

    def _handle_stop_loss(self, trade) -> None:
        """Handle stop loss hit."""
        self._trade_executor.handle_stop_loss(
            trade, self.exit_manager, self.state,
            self.trade_manager, self.data_storage,
            self.signal_analyzer, self._persist_trade_state,
            config=self.config
        )

    def _handle_take_profit(self, trade, tp_level: int, current_price: float) -> None:
        """Handle take profit level hit."""
        self._trade_executor.handle_take_profit(
            trade, tp_level, current_price,
            self.exit_manager, self.trade_manager, self.data_storage
        )

    def _handle_partial_exit(self, trade, current_price: float) -> None:
        """Handle partial position exit at TP2."""
        self._trade_executor.handle_partial_exit(
            trade, current_price, self.exit_manager, self.trade_manager
        )

    def _monitor_position(self) -> None:
        """Monitor active position for stop loss and take profit hits."""
        self._position_monitor.monitor_position(
            self.state, self.exit_manager, self.signal_analyzer,
            self.trade_manager, self.market_data,
            self._trade_executor, self.data_storage,
            self._persist_trade_state, self.config
        )

    def _check_position_mismatch(self) -> None:
        """Periodic check: does our internal state match IBKR's position?"""
        self._position_monitor.check_position_mismatch(
            self.state, self.trade_manager,
            self._pos_mismatch, self._kill_switch
        )

    def _maybe_log_position_snapshot(self) -> None:
        """Log position snapshot if enough time has passed."""
        self._position_monitor.log_position_snapshot(
            self.state, self.exit_manager,
            self.trade_manager, self.market_data,
            self._snapshot_interval_minutes
        )

    def _handle_check_in_times_deduped(self) -> None:
        """Check for check-in times with deduplication."""
        self._session_manager.handle_check_in_times(
            self.state, self.exit_manager,
            self.market_data, self._logged_check_in_times
        )

    def _check_session_summary(self) -> None:
        """Check and log session summary at market halt."""
        self._last_summary_date = self._session_manager.check_session_summary(
            self.state, self.config, self._last_summary_date
        )

    # ---- End delegated helpers ----------------------------------------

    def _persist_trade_state(self) -> None:
        """Save current trade state to disk for crash recovery."""
        active_trade = None
        if self.state.trades_today:
            last = self.state.trades_today[-1]
            if last.status == TradeStatus.OPEN:
                active_trade = last

        snapshot = self._state_persistence.build_snapshot(
            entry_triggered=self.state.entry_triggered,
            losses_today=self.state.losses_today,
            reentries_used=self.state.reentries_used,
            active_trade_id=active_trade.id if active_trade else None,
            active_trade_direction=active_trade.direction.value if active_trade else None,
            active_trade_entry_price=active_trade.entry_price if active_trade else None,
            active_trade_stop_loss=active_trade.current_stop_loss if active_trade else None,
            session_date=self._current_session_date or ""
        )
        self._state_persistence.save_state(snapshot)

    def _execution_cycle(self) -> None:
        """
        Single execution cycle of the strategy.
        """
        now = datetime.now(self.timezone)

        # Check if market is open
        if not self.market_data.is_market_open():
            # Check for session summary at 5 PM (market halt)
            halt_hour = self._market_calendar.halt_hour if self._market_calendar else 17
            if now.hour == halt_hour and now.minute < 5:
                self._check_session_summary()
            self.logger.debug("Market closed, waiting...")
            self.connection.sleep(60)
            return

        # Kill switch check
        if self._kill_switch.is_active():
            self.logger.critical("Kill switch active - no trading")
            self._notifier.kill_switch_triggered("Daily loss limit or error threshold exceeded")
            self.connection.sleep(30)
            return

        # Validate we have real price data (not NaN after disconnect)
        current_price = self.market_data.current_price
        if not is_valid_price(current_price):
            self.logger.warning("No valid price data available - checking connection")
            if not self.connection.is_connected:
                return  # Main loop will handle reconnection
            self.connection.sleep(5)
            return

        # Track data freshness
        self._stale_data.update(current_price)
        self._heartbeat.record_price_update()

        # Stale data check
        if self._stale_data.is_stale():
            self.logger.warning("Market data is STALE - price not updating")
            self.connection.sleep(5)
            return

        # Periodic position mismatch check
        if self._pos_mismatch.should_check():
            self._check_position_mismatch()

        # Check if we're in an active overnight session
        current_session_6pm = self.market_data.get_current_session_6pm()

        if current_session_6pm is not None:
            # We're in an overnight session - check if we need to trade
            if not self.state.entry_triggered:
                # Candle closes 5 min after reopen
                # Bot tries at 6:05 PM; if delayed data, retries every 30s until available
                candle_minutes = self.config.get('strategy', {}).get('candle_timeframe_minutes', 5)
                candle_ready_time = current_session_6pm + timedelta(minutes=candle_minutes)

                # Entry window: how long after candle close to still allow entry
                entry_window = self.config.get('strategy', {}).get('entry_window_minutes', 30)
                entry_deadline = current_session_6pm + timedelta(minutes=candle_minutes + entry_window)

                if now > entry_deadline:
                    self.logger.info(
                        f"Past entry window ({now.strftime('%H:%M')} EST, "
                        f"6PM candle was {int((now - current_session_6pm).total_seconds() / 60)} min ago). "
                        f"Waiting for next session."
                    )
                    self.state.entry_triggered = True  # Skip this session
                elif now >= candle_ready_time:
                    self._check_for_entry_signal(current_session_6pm)
                else:
                    # Wait for candle data to be available
                    wait_seconds = (candle_ready_time - now).total_seconds() + 2
                    mins_left = int(wait_seconds / 60)
                    self.logger.info(f"Waiting for 6 PM candle close ({mins_left}min)")
                    self.connection.sleep(min(wait_seconds, 60))
            else:
                # Check if we have an active position to monitor
                has_active_trade = (
                    self.state.trades_today
                    and self.state.trades_today[-1].status == TradeStatus.OPEN
                )
                if has_active_trade:
                    self._monitor_position()
                else:
                    # No position, entry already done/skipped - wait for next session
                    next_reopen = self.market_data.get_next_6pm_reopen(log_countdown=False)
                    time_to_next = (next_reopen - now).total_seconds()
                    # Log countdown once every 30 min, not every minute
                    if not getattr(self, '_last_countdown_log', None) or \
                       (now - self._last_countdown_log).total_seconds() > 1800:
                        hours = int(time_to_next // 3600)
                        mins = int((time_to_next % 3600) // 60)
                        self.logger.info(f"Next 6 PM signal in {hours}h {mins}m")
                        self._last_countdown_log = now
                    self.connection.sleep(min(60, max(1, time_to_next - 600)))
        else:
            # Not in an overnight session yet - wait for next 6 PM
            next_reopen = self.market_data.get_next_6pm_reopen(log_countdown=True)
            time_to_reopen = (next_reopen - now).total_seconds()

            if time_to_reopen > 600:
                # Log that we're waiting
                self.logger.waiting_for_signal("6PM candle")
                self.connection.sleep(min(60, time_to_reopen - 600))
                return

            # Close to 6 PM - wait for candle
            if not self.state.entry_triggered:
                self._check_for_entry_signal(next_reopen)

        # Check for check-in times (with dedup to avoid log spam)
        self._handle_check_in_times_deduped()

        # Log position snapshot if enough time has passed
        self._maybe_log_position_snapshot()

        # Small sleep to prevent tight loop
        self.connection.sleep(1)

    def _check_for_entry_signal(self, reopen_time: datetime) -> None:
        """
        Check for entry signal at 6 PM reopen.
        """
        now = datetime.now(self.timezone)

        # Wait for 5-min candle to complete (6:00 - 6:05)
        candle_close_time = reopen_time + timedelta(minutes=5)

        if now < candle_close_time:
            # Wait for candle close
            wait_seconds = (candle_close_time - now).total_seconds() + 2
            if wait_seconds > 0:
                self.logger.info(f"Waiting {wait_seconds:.0f}s for 6 PM candle to close")
                self.connection.sleep(wait_seconds)

        # Check if today is an allowed trading day (no Fridays)
        if not self.signal_analyzer.is_allowed_trading_day():
            self.state.entry_triggered = True  # Prevent re-checking
            return

        # Get the signal candle (may not be available yet with delayed data)
        candle = self.market_data.get_5min_candle_at_time(reopen_time)

        if candle is None:
            # Log only once, then silently retry
            if not getattr(self, '_candle_retry_logged', False):
                self.logger.info("Waiting for 6 PM candle data...")
                self._candle_retry_logged = True
            self.connection.sleep(30)
            return

        # Candle found - reset retry flag
        self._candle_retry_logged = False

        # Check for doji candle (ambiguous direction)
        if self._volatility_detector.is_doji(candle):
            self.logger.warning("Doji candle detected at 6PM - signal unreliable, skipping")
            self.state.entry_triggered = True
            return

        # Check for extreme volatility
        if self._volatility_detector.is_too_volatile(candle.range):
            self.logger.warning("Extreme volatility detected - skipping entry")
            self.state.entry_triggered = True
            return

        # Analyze candle and generate signal
        entry_signal = self.signal_analyzer.analyze_signal_candle(candle)

        # Save signal to CSV
        self.data_storage.save_signal(entry_signal)

        # Check if we can trade
        if not self.state.can_trade:
            self.logger.warning("Cannot trade: max losses reached")
            self.state.entry_triggered = True  # Prevent re-checking
            return

        # Check if risk is too high (skip_trade flag set by analyze_signal_candle)
        if getattr(entry_signal, 'skip_trade', False):
            self.logger.warning("Trade skipped: risk exceeds maximum cap")
            self.state.entry_triggered = True  # Prevent re-checking
            return

        # Execute entry
        self._execute_entry(entry_signal)

    def shutdown(self, flatten_positions: bool = True) -> None:
        """
        Gracefully shutdown the strategy.

        Args:
            flatten_positions: If True, close all open positions before shutdown.
                             This prevents leaving unmanaged positions overnight.
        """
        self._running = False

        if self.logger:
            self.logger.info("Shutting down strategy...")

        # Flatten positions first (before cancelling orders, as SL orders protect us)
        if flatten_positions and self.trade_manager:
            try:
                position = self.trade_manager.positions.get_position()
                if not position.is_flat:
                    self.logger.warning(
                        f"FLATTENING position on shutdown: {position.direction.value} "
                        f"{position.quantity} contracts"
                    )
                    self.trade_manager.positions.flatten_position()
                else:
                    self.logger.info("No open positions to flatten")
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error flattening positions: {e}")

        # Cancel remaining open orders
        if self.trade_manager:
            try:
                self.trade_manager.orders.cancel_all_orders()
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error cancelling orders: {e}")

        # Unsubscribe from market data
        if self.market_data:
            try:
                self.market_data.unsubscribe_all()
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error unsubscribing: {e}")

        # Disconnect from IBKR
        if self.connection:
            try:
                self.connection.disconnect()
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Error disconnecting: {e}")

        # Log final statistics
        if self.data_storage and self.logger:
            stats = self.data_storage.get_trade_statistics()
            self.logger.info("=" * 40)
            self.logger.info("SESSION STATISTICS")
            self.logger.info(f"Total Trades: {stats['total_trades']}")
            self.logger.info(f"Win Rate: {stats['win_rate']:.1f}%")
            self.logger.info(f"Total PnL: ${stats['total_pnl']:.2f}")
            self.logger.info("=" * 40)

        # Release process lock
        self._process_lock.release()

        # Clear trade state file (clean shutdown)
        self._state_persistence.clear_state()

        # Notify shutdown
        if self._notifier:
            self._notifier.bot_stopped("Strategy shutdown complete")

        if self.logger:
            self.logger.info("Strategy shutdown complete")

    def print_status(self) -> None:
        """
        Print current strategy status.
        """
        if not self.state:
            print("Strategy not initialized")
            return

        status = {
            'session_date': self.state.session_date.isoformat(),
            'is_active': self.state.is_active,
            'signal_direction': self.state.signal_direction.value if self.state.signal_direction else None,
            'entry_triggered': self.state.entry_triggered,
            'trades_today': len(self.state.trades_today),
            'losses_today': self.state.losses_today,
            'reentries_used': self.state.reentries_used,
            'can_trade': self.state.can_trade,
            'can_reentry': self.state.can_reentry,
            'previous_day_high': self.state.previous_day_levels.high if self.state.previous_day_levels else None,
            'previous_day_low': self.state.previous_day_levels.low if self.state.previous_day_levels else None
        }
        print("\n" + "=" * 40)
        print("STRATEGY STATUS")
        print("=" * 40)
        for key, value in status.items():
            print(f"  {key}: {value}")
        print("=" * 40 + "\n")


def main():
    """
    Main entry point.
    """
    parser = argparse.ArgumentParser(
        description="NQ 6PM Reopen Strategy Runner"
    )
    parser.add_argument(
        '--config', '-c',
        default='config/strategy_config.yaml',
        help='Path to configuration file'
    )
    parser.add_argument(
        '--paper',
        action='store_true',
        default=True,
        help='Use paper trading (default)'
    )
    parser.add_argument(
        '--live',
        action='store_true',
        help='Use live trading (CAUTION!)'
    )
    parser.add_argument(
        '--status',
        action='store_true',
        help='Print status and exit'
    )

    args = parser.parse_args()

    # Determine paper/live mode
    paper_mode = not args.live

    if not paper_mode:
        print("\n" + "!" * 60)
        print("WARNING: LIVE TRADING MODE")
        print("Real money will be used. Are you sure?")
        print("!" * 60)
        confirm = input("Type 'YES' to confirm: ")
        if confirm != 'YES':
            print("Aborted.")
            sys.exit(0)

    # Create and run strategy
    runner = StrategyRunner(
        config_path=args.config,
        paper_trading=paper_mode
    )

    if not runner.setup():
        print("Failed to setup strategy. Check logs for details.")
        sys.exit(1)

    if args.status:
        runner.print_status()
        runner.shutdown()
        sys.exit(0)

    # Run strategy
    runner.run()


if __name__ == "__main__":
    main()
