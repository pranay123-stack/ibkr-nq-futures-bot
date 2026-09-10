"""
Session management logic for the NQ 6PM Reopen Strategy.

Handles session date-key generation, session rollovers,
scheduled check-in times, and end-of-session summaries.
"""

from datetime import datetime, time, timedelta
from typing import List, Optional, Set, Tuple

import pytz

from ..timezone.market_calendar import get_default_tz


class SessionManager:
    """Manages trading session lifecycle: rollovers, check-ins, and summaries.

    Accepts all dependencies via constructor parameters to avoid
    circular imports with StrategyRunner.
    """

    def __init__(self, logger, notifier, timezone=None,
                 reopen_hour: int = 18, check_in_times: Optional[List[time]] = None):
        """Initialise the session manager.

        Args:
            logger: StrategyLogger instance.
            notifier: TelegramNotifier (or compatible) instance.
            timezone: pytz timezone (defaults to US/Eastern).
            reopen_hour: Hour of market reopen (default 18 = 6 PM).
            check_in_times: List of time objects for scheduled check-ins.
        """
        self.logger = logger
        self._notifier = notifier
        self.timezone = timezone or get_default_tz()
        self._reopen_hour = reopen_hour
        self._check_in_times = check_in_times or []

    # ------------------------------------------------------------------
    # Check-in time detection (moved from NQ6PMStrategy)
    # ------------------------------------------------------------------

    def is_check_in_time(self) -> Tuple[bool, Optional[time]]:
        """Check if current time is a key check-in time."""
        now = datetime.now(self.timezone).time()

        for check_time in self._check_in_times:
            now_minutes = now.hour * 60 + now.minute
            check_minutes = check_time.hour * 60 + check_time.minute

            if abs(now_minutes - check_minutes) <= 1:
                return True, check_time

        return False, None

    # ------------------------------------------------------------------
    # Session initialization (moved from NQ6PMStrategy)
    # ------------------------------------------------------------------

    def initialize_session(self, state, market_data, strategy_config: dict) -> None:
        """Initialize for a new trading session.

        Args:
            state: StrategyState instance.
            market_data: Market data provider.
            strategy_config: strategy section of config dict.
        """
        now = datetime.now(self.timezone)
        state.reset_for_new_session(now)

        # Get previous day levels
        if strategy_config.get('use_previous_day_levels', True):
            levels = market_data.get_previous_day_levels()
            if levels:
                state.previous_day_levels = levels
                self.logger.info(
                    f"Previous day levels set: High={levels.high:.2f}, Low={levels.low:.2f}"
                )

        self.logger.info(f"Session initialized for {now.date()}")

    # ------------------------------------------------------------------
    # Session date key
    # ------------------------------------------------------------------

    def get_session_date_key(self, reopen_hour: Optional[int] = None) -> str:
        """Return a unique key for the current trading session."""
        now = datetime.now(self.timezone)
        hour = reopen_hour if reopen_hour is not None else self._reopen_hour
        if now.hour >= hour:
            return now.strftime("%Y-%m-%d-PM")
        return (now - timedelta(days=1)).strftime("%Y-%m-%d-PM")

    # ------------------------------------------------------------------
    # Session rollover
    # ------------------------------------------------------------------

    def check_session_rollover(self, current_session_date: Optional[str],
                               state, market_data, strategy_config: dict,
                               logged_check_ins: Set[str]
                               ) -> Optional[str]:
        """Detect a new trading session and reset state if needed.

        Args:
            current_session_date: Current session date key.
            state: StrategyState instance.
            market_data: Market data provider.
            strategy_config: strategy section of config dict.
            logged_check_ins: Set of already-logged check-in time keys.

        Returns:
            Updated session date key.
        """
        new_session = self.get_session_date_key()
        if new_session != current_session_date:
            self.logger.info(f"New trading session detected: {new_session}")
            logged_check_ins.clear()
            self.initialize_session(state, market_data, strategy_config)
            self.logger.info("Session state reset for new trading day")
            return new_session
        return current_session_date

    # ------------------------------------------------------------------
    # Check-in times
    # ------------------------------------------------------------------

    def handle_check_in_times(self, state, exit_manager, market_data,
                              logged_check_ins: Set[str]) -> None:
        """Process scheduled check-in times with deduplication.

        Args:
            state: StrategyState instance.
            exit_manager: ExitManager instance (for PnL calculation).
            market_data: Market data provider.
            logged_check_ins: Set of already-logged check-in time keys.
        """
        is_check, check_time = self.is_check_in_time()
        if not is_check or check_time is None:
            return

        time_key = check_time.strftime('%H:%M')
        if time_key in logged_check_ins:
            return

        logged_check_ins.add(time_key)
        self.logger.warning(
            f"CHECK-IN TIME: {time_key} - "
            "Review position for potential reversals"
        )
        self._log_check_in_position(state, exit_manager, market_data)

    def _log_check_in_position(self, state, exit_manager, market_data) -> None:
        """Log position details at a check-in time."""
        from ..data_models import TradeStatus

        if not state.trades_today:
            return

        active_trade = state.trades_today[-1]
        if active_trade.status != TradeStatus.OPEN:
            return

        current_price = market_data.current_price
        if current_price:
            pnl = exit_manager.calculate_pnl(active_trade, current_price)
            self.logger.warning(
                f"CHECK-IN: Position {active_trade.direction.value} | "
                f"Entry: {active_trade.entry_price:.2f} | "
                f"Current: {current_price:.2f} | "
                f"PnL: ${pnl:.2f}"
            )

    # ------------------------------------------------------------------
    # Session summary
    # ------------------------------------------------------------------

    def check_session_summary(self, state, config,
                              last_summary_date: Optional[str]
                              ) -> Optional[str]:
        """Log an end-of-session summary at market halt (5 PM ET).

        Args:
            state: StrategyState instance.
            config: Full strategy configuration dict.
            last_summary_date: Date string of last summary logged.

        Returns:
            Updated last_summary_date.
        """
        now = datetime.now(self.timezone)
        session_date = now.strftime("%Y-%m-%d")

        if last_summary_date == session_date:
            return last_summary_date

        log_config = config.get('logging', {})
        if not log_config.get('enable_session_summary', True):
            return last_summary_date

        trades = state.trades_today

        total_trades = len(trades)
        wins = sum(1 for t in trades if t.realized_pnl and t.realized_pnl > 0)
        losses = sum(
            1 for t in trades if t.realized_pnl and t.realized_pnl < 0
        )
        total_pnl = sum(t.realized_pnl or 0 for t in trades)
        reentries_used = state.reentries_used

        self.logger.session_summary(
            session_date=session_date,
            trades=total_trades,
            wins=wins,
            losses=losses,
            total_pnl=total_pnl,
            reentries_used=reentries_used
        )

        self._notifier.session_summary(
            date=session_date, trades=total_trades, wins=wins,
            losses=losses, pnl=total_pnl, reentries=reentries_used
        )

        return session_date
