"""
StrategyLogger - Enhanced trading logger with EST timestamps and trader-friendly messages.
"""

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Dict

from .formatters import ColoredFormatter, PlainFormatter, EST
from .session_handler import DailySessionFileHandler


class StrategyLogger:
    """
    Enhanced trading logger with EST timestamps and trader-friendly messages.
    Supports colorful console output, file logging with rotation, and daily session logs.
    """

    _instance: Optional['StrategyLogger'] = None
    _initialized: bool = False

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        name: str = "NQ_Strategy",
        level: str = "DEBUG",
        log_to_file: bool = True,
        log_to_console: bool = True,
        colorful_console: bool = True,
        log_file_path: str = "logs/strategy.log",
        max_file_size_mb: int = 10,
        backup_count: int = 5,
        show_timezone: bool = True,
        enable_session_logs: bool = True,
        session_log_dir: str = "logs/sessions"
    ):
        if self._initialized:
            return

        self.name = name
        self.level = getattr(logging, level.upper(), logging.DEBUG)
        self.log_to_file = log_to_file
        self.log_to_console = log_to_console
        self.colorful_console = colorful_console
        self.log_file_path = Path(log_file_path)
        self.max_file_size_mb = max_file_size_mb
        self.backup_count = backup_count
        self.show_timezone = show_timezone
        self.enable_session_logs = enable_session_logs
        self.session_log_dir = session_log_dir

        self._setup_logger()
        self._initialized = True

    def _setup_logger(self) -> None:
        """Configure the logger with handlers."""
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(self.level)
        self.logger.handlers.clear()

        # Console handler
        if self.log_to_console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(self.level)
            console_formatter = ColoredFormatter(
                use_colors=self.colorful_console,
                show_timezone=self.show_timezone
            )
            console_handler.setFormatter(console_formatter)
            self.logger.addHandler(console_handler)

        # Daily session file handler (one file per trading day - the only file log)
        if self.enable_session_logs:
            session_handler = DailySessionFileHandler(
                self.session_log_dir,
                self.show_timezone
            )
            session_handler.setLevel(self.level)
            self.logger.addHandler(session_handler)

    def get_logger(self, module_name: Optional[str] = None) -> logging.Logger:
        """Get a logger instance, optionally with a module-specific name."""
        if module_name:
            return logging.getLogger(f"{self.name}.{module_name}")
        return self.logger

    # Basic logging methods
    def debug(self, msg: str, *args, **kwargs) -> None:
        """Log a message at DEBUG level."""
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        """Log a message at INFO level."""
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        """Log a message at WARNING level."""
        self.logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        """Log a message at ERROR level."""
        self.logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        """Log a message at CRITICAL level."""
        self.logger.critical(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        """Log a message at ERROR level with exception traceback."""
        self.logger.exception(msg, *args, **kwargs)

    # =========================================================================
    # TRADING-SPECIFIC LOGGING METHODS
    # =========================================================================

    def trade(self, msg: str) -> None:
        """Log trade-related messages with INFO level."""
        self.logger.info(f"[TRADE] {msg}")

    def signal(self, msg: str) -> None:
        """Log signal-related messages with INFO level."""
        self.logger.info(f"[SIGNAL] {msg}")

    def order(self, msg: str) -> None:
        """Log order-related messages with INFO level."""
        self.logger.info(f"[ORDER] {msg}")

    def position(self, msg: str) -> None:
        """Log position-related messages with INFO level."""
        self.logger.info(f"[POSITION] {msg}")

    def market(self, msg: str) -> None:
        """Log market data messages with DEBUG level."""
        self.logger.debug(f"[MARKET] {msg}")

    # =========================================================================
    # ENHANCED TRADER-FRIENDLY LOGGING METHODS
    # =========================================================================

    def signal_entry(self, direction: str, entry: float, sl: float,
                     tp_levels: Dict[str, float], risk_points: float = None) -> None:
        """
        Log human-readable entry signal.

        Args:
            direction: LONG or SHORT
            entry: Entry price
            sl: Stop loss price
            tp_levels: Dict with tp1, tp2, etc.
            risk_points: Points at risk (entry - sl for LONG)
        """
        if risk_points is None:
            risk_points = abs(entry - sl)

        tp1 = tp_levels.get('tp1', tp_levels.get(1, 0))
        tp1_pts = abs(tp1 - entry) if tp1 else 0

        msg = (f"{direction} Entry @ {entry:.2f} | "
               f"SL: {sl:.2f} (-{risk_points:.2f} pts) | "
               f"TP1: {tp1:.2f} (+{tp1_pts:.2f} pts)")
        self.logger.info(f"[SIGNAL] {msg}")

    def market_state(self, is_open: bool, reason: str = "") -> None:
        """
        Log market open/closed state.

        Args:
            is_open: Whether market is open
            reason: Optional reason (e.g., "Sunday 6PM session started")
        """
        if is_open:
            state = "OPEN"
        else:
            state = "CLOSED"

        msg = f"Market {state}"
        if reason:
            msg += f" ({reason})"
        self.logger.info(f"[MARKET] {msg}")

    def countdown(self, event: str, hours: int, minutes: int, seconds: int = 0) -> None:
        """
        Log countdown to next event.

        Args:
            event: Event name (e.g., "6PM signal")
            hours: Hours remaining
            minutes: Minutes remaining
            seconds: Seconds remaining
        """
        if hours > 0:
            time_str = f"{hours}h {minutes}m"
        elif minutes > 0:
            time_str = f"{minutes}m {seconds}s"
        else:
            time_str = f"{seconds}s"

        self.logger.info(f"[TIMER] Next {event} in {time_str}")

    def order_fill(self, action: str, qty: int, symbol: str, price: float,
                   expected_price: float = None) -> None:
        """
        Log order fill with optional slippage.

        Args:
            action: BUY or SELL
            qty: Quantity filled
            symbol: Contract symbol
            price: Fill price
            expected_price: Expected price for slippage calculation
        """
        msg = f"{action} {qty} {symbol} @ {price:.2f}"

        if expected_price is not None:
            slippage = price - expected_price
            if abs(slippage) >= 0.01:
                msg += f" (Slippage: {slippage:+.2f})"

        self.logger.info(f"[FILL] {msg}")

    def position_snapshot(self, direction: str, qty: int, entry: float,
                          current: float, pnl: float, symbol: str = "") -> None:
        """
        Log position snapshot.

        Args:
            direction: LONG, SHORT, or FLAT
            qty: Position quantity
            entry: Entry price
            current: Current price
            pnl: Unrealized P&L in dollars
        """
        if direction == "FLAT" or qty == 0:
            self.logger.info("[SNAPSHOT] Position: FLAT")
        else:
            symbol_str = f" {symbol}" if symbol else ""
            self.logger.info(
                f"[SNAPSHOT] {direction} {qty}{symbol_str} @ {entry:.2f} | "
                f"Current: {current:.2f} | P&L: ${pnl:+.2f}"
            )

    def session_summary(self, session_date: str, trades: int, wins: int,
                        losses: int, total_pnl: float,
                        reentries_used: int = 0) -> None:
        """
        Log end-of-session summary.

        Args:
            session_date: Trading session date (YYYY-MM-DD)
            trades: Total trades taken
            wins: Winning trades
            losses: Losing trades
            total_pnl: Total P&L in dollars
            reentries_used: Number of re-entries used
        """
        self.logger.info(f"[SUMMARY] {'=' * 40}")
        self.logger.info(f"[SUMMARY] Session: {session_date}")
        self.logger.info(f"[SUMMARY] Trades: {trades} | Wins: {wins} | Losses: {losses}")
        if trades > 0:
            win_rate = (wins / trades) * 100
            self.logger.info(f"[SUMMARY] Win Rate: {win_rate:.1f}%")
        self.logger.info(f"[SUMMARY] Total P&L: ${total_pnl:+.2f}")
        if reentries_used > 0:
            self.logger.info(f"[SUMMARY] Re-entries Used: {reentries_used}")
        self.logger.info(f"[SUMMARY] {'=' * 40}")

    def lifecycle(self, trade_id: str, stage: str, details: str = "") -> None:
        """
        Log trade lifecycle stage.

        Args:
            trade_id: Trade identifier
            stage: Lifecycle stage (SIGNAL, ENTRY, FILLED, MONITORING, TP_HIT, SL_HIT, CLOSED)
            details: Additional details
        """
        STAGE_DESCRIPTIONS = {
            'SIGNAL': "Signal generated",
            'ENTRY': "Entry order placed",
            'FILLED': "FILLED - Position OPEN",
            'MONITORING': "Monitoring position",
            'TP1_HIT': "TP1 hit - Moving SL to breakeven",
            'TP2_HIT': "TP2 hit - Trailing stop updated",
            'TP3_HIT': "TP3 hit - Trailing stop updated",
            'TP4_HIT': "TP4 hit - Final target reached",
            'SL_HIT': "Stop loss triggered",
            'PARTIAL': "Partial exit executed",
            'CLOSED': "Position fully closed",
            'CANCELLED': "Order cancelled",
            'REJECTED': "Order rejected",
        }

        stage_desc = STAGE_DESCRIPTIONS.get(stage, stage)
        msg = f"Trade {trade_id}: {stage_desc}"
        # Only add details if it's not already part of the stage description
        if details and details not in stage_desc:
            msg += f" - {details}"
        self.logger.info(f"[LIFECYCLE] {msg}")

    def heartbeat(self, is_connected: bool, account: str = None) -> None:
        """
        Log connection heartbeat status.

        Args:
            is_connected: Connection status
            account: Account ID if connected
        """
        if is_connected:
            acct_str = f" (Account: {account})" if account else ""
            self.logger.debug(f"[HEARTBEAT] Connected to IBKR{acct_str}")
        else:
            self.logger.warning("[HEARTBEAT] Disconnected from IBKR")

    def ibkr_error(self, error_code: int, error_msg: str,
                   translated_msg: str = None) -> None:
        """
        Log IBKR error with human-readable translation.

        Args:
            error_code: IBKR error code
            error_msg: Original IBKR error message
            translated_msg: Human-readable translation
        """
        if translated_msg:
            self.logger.error(f"[IBKR] Error {error_code}: {translated_msg}")
        else:
            self.logger.error(f"[IBKR] Error {error_code}: {error_msg}")

    def waiting_for_signal(self, event: str = "6PM candle") -> None:
        """Log that we're waiting for the trading signal."""
        self.logger.info(f"[MARKET] Waiting for {event}...")

    def session_start(self, session_type: str = "Overnight") -> None:
        """Log session start."""
        now = datetime.now(EST)
        self.logger.info(f"[SESSION] {session_type} session started at {now.strftime('%H:%M')} EST")

    def trade_result(self, trade_id: str, direction: str, entry: float,
                     exit_price: float, pnl: float, result: str) -> None:
        """
        Log trade result.

        Args:
            trade_id: Trade identifier
            direction: LONG or SHORT
            entry: Entry price
            exit_price: Exit price
            pnl: Realized P&L
            result: WIN, LOSS, or BREAKEVEN
        """
        points = abs(exit_price - entry)
        self.logger.info(
            f"[RESULT] Trade {trade_id} {result}: {direction} "
            f"{entry:.2f} -> {exit_price:.2f} ({points:+.2f} pts) | P&L: ${pnl:+.2f}"
        )
