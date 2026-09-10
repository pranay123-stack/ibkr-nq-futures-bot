"""
Signal Analyzer Module
Handles entry signal generation for the NQ 6PM Reopen Strategy.
Extracted from core/strategy.py.
"""

import uuid
from datetime import datetime, time
from typing import Optional, List

from ..timezone.market_calendar import get_default_tz, MarketCalendar
from ..utils.defaults import CONTRACT_SPECS, DEFAULT_TICK_SIZE, DEFAULT_SYMBOL
from ..data_models import (
    Candle, Signal, Trade, StrategyState,
    Direction, SignalType
)
from ..logger import get_logger, StrategyLogger


class SignalAnalyzer:
    """
    Analyzes 6 PM candles and generates entry signals.

    Extracted entry-related methods from NQ6PMStrategy.
    """

    def __init__(self, config: dict, state: StrategyState):
        self.config = config
        self.state = state
        self.strategy_config = config.get('strategy', {})
        self.risk_config = config.get('risk', {})
        self.reentry_config = config.get('reentry', {})

        self._market_calendar = MarketCalendar(config)
        self.timezone = self._market_calendar.tz

        # Set contract-specific values from config
        contract_cfg = config.get('contract', {})
        symbol = contract_cfg.get('symbol', DEFAULT_SYMBOL).upper()
        self.TICK_SIZE = contract_cfg.get('tick_size', DEFAULT_TICK_SIZE)
        specs = CONTRACT_SPECS.get(symbol, CONTRACT_SPECS['NQ'])
        self.TICK_VALUE = specs['tick_value']
        self.POINT_VALUE = specs['point_value']

        # Parse configuration
        self._parse_config()

        self.logger = get_logger("SignalAnalyzer")

    def _parse_config(self) -> None:
        """Parse strategy configuration relevant to signal generation."""
        # Risk parameters
        self.contracts = self.risk_config.get('contracts', 1)
        self.min_risk_threshold = self.risk_config.get('min_risk_threshold', 300.0)
        self.max_risk_per_trade = self.risk_config.get('max_risk_per_trade', 500.0)
        self.warn_below_risk = self.risk_config.get('warn_below_risk', True)

        # Take profit levels (in points)
        tp_config = self.risk_config.get('take_profit_levels', {})
        self.tp_levels = [
            tp_config.get('tp1', 20.0),
            tp_config.get('tp2', 40.0),
            tp_config.get('tp3', 60.0),
            tp_config.get('tp4', 80.0)
        ]

    def is_allowed_trading_day(self) -> bool:
        """
        Check if today is an allowed trading day.
        - NO trading on Friday 6PM session (Friday's reopen leads into Saturday)
        - YES trading on Sunday 6PM session (Sunday's reopen starts the week)
        - YES trading Mon-Thu 6PM sessions

        Returns:
            True if trading is allowed today
        """
        now = datetime.now(self.timezone)
        allowed, reason = self._market_calendar.is_trading_day(now=now)
        if not allowed:
            self.logger.info(f"{reason} - no new trades allowed")
        return allowed

    def analyze_signal_candle(self, candle: Candle) -> Signal:
        """
        Analyze the 6 PM candle and generate entry signal.

        Args:
            candle: The first 5-minute candle after 6 PM

        Returns:
            Signal object with entry parameters
        """
        self.logger.info("Analyzing 6 PM signal candle...")

        # Determine direction based on candle close
        if candle.is_bullish:
            direction = Direction.LONG
            entry_price = candle.close
            stop_loss = candle.low - self.TICK_SIZE  # Below the wick
        else:
            direction = Direction.SHORT
            entry_price = candle.close
            stop_loss = candle.high + self.TICK_SIZE  # Above the wick

        # Calculate take profit levels
        tp_prices = self._calculate_tp_levels(entry_price, direction)

        # Calculate risk amount
        risk_points = abs(entry_price - stop_loss)
        risk_amount = risk_points * self.POINT_VALUE * self.contracts

        # Generate signal ID
        signal_id = f"SIG_{datetime.now(self.timezone).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        signal = Signal(
            id=signal_id,
            timestamp=datetime.now(self.timezone),
            signal_type=SignalType.ENTRY,
            direction=direction,
            price=entry_price,
            candle=candle,
            stop_loss=stop_loss,
            take_profit_levels=tp_prices,
            risk_amount=risk_amount,
            reason=f"6PM Reopen - {'Bullish' if candle.is_bullish else 'Bearish'} candle"
        )

        # Store signal candle in state
        self.state.signal_candle = candle
        self.state.signal_direction = direction

        # Log signal details
        self._log_signal(signal)

        # Warn if risk is below threshold
        if self.warn_below_risk and risk_amount < self.min_risk_threshold:
            self.logger.warning(
                f"Risk amount ${risk_amount:.2f} is below threshold ${self.min_risk_threshold:.2f}. "
                "These setups tend to be weaker."
            )

        # BLOCK trade if risk exceeds maximum cap
        if risk_amount > self.max_risk_per_trade:
            self.logger.warning(
                f"TRADE BLOCKED: Risk ${risk_amount:.2f} exceeds max ${self.max_risk_per_trade:.2f}. "
                f"Stop distance {risk_points:.2f} pts too wide. Skipping entry."
            )
            signal.skip_trade = True
        else:
            signal.skip_trade = False

        return signal

    def _calculate_tp_levels(self, entry: float, direction: Direction) -> List[float]:
        """Calculate take profit price levels."""
        tp_prices = []

        for tp_points in self.tp_levels:
            if direction == Direction.LONG:
                tp_price = entry + tp_points
            else:
                tp_price = entry - tp_points
            tp_prices.append(round(tp_price, 2))

        return tp_prices

    def _log_signal(self, signal: Signal) -> None:
        """Log signal details using enhanced trader-friendly format."""
        strategy_logger = StrategyLogger._instance

        if strategy_logger:
            # Use enhanced signal logging
            tp_dict = {}
            for i, tp in enumerate(signal.take_profit_levels or [], 1):
                tp_dict[f'tp{i}'] = tp

            risk_points = abs(signal.price - signal.stop_loss)
            strategy_logger.signal_entry(
                direction=signal.direction.value,
                entry=signal.price,
                sl=signal.stop_loss,
                tp_levels=tp_dict,
                risk_points=risk_points
            )

            # Log lifecycle event
            strategy_logger.lifecycle(
                trade_id=signal.id,
                stage='SIGNAL',
                details=f"{signal.direction.value} @ {signal.price:.2f}"
            )
        else:
            # Fallback to basic logging
            self.logger.info(
                f"[SIGNAL] Generated {signal.direction.value} signal at {signal.price:.2f}"
            )

        self.logger.info(f"  Stop Loss: {signal.stop_loss:.2f}")
        self.logger.info(f"  Take Profit Levels: {signal.take_profit_levels}")
        self.logger.info(f"  Risk Amount: ${signal.risk_amount:.2f}")

        if signal.candle:
            self.logger.debug(
                f"  Candle: O={signal.candle.open:.2f} H={signal.candle.high:.2f} "
                f"L={signal.candle.low:.2f} C={signal.candle.close:.2f}"
            )

    def create_trade_from_signal(self, signal: Signal, is_reentry: bool = False) -> Trade:
        """
        Create a trade object from a signal.

        Args:
            signal: Entry signal
            is_reentry: Whether this is a re-entry trade

        Returns:
            Trade object
        """
        trade_id = f"TRD_{datetime.now(self.timezone).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        trade = Trade(
            id=trade_id,
            entry_time=datetime.now(self.timezone),
            direction=signal.direction,
            entry_price=signal.price,
            quantity=self.contracts,
            stop_loss=signal.stop_loss,
            take_profit_levels=signal.take_profit_levels or [],
            is_reentry=is_reentry,
            original_trade_id=signal.id if is_reentry else None
        )
        # Set correct point value for risk calculation
        trade._point_value = self.POINT_VALUE

        self.state.trades_today.append(trade)
        self.state.last_entry_price = signal.price
        self.state.entry_triggered = True

        if is_reentry:
            self.state.reentries_used += 1

        # Use enhanced lifecycle logging
        strategy_logger = StrategyLogger._instance
        if strategy_logger:
            strategy_logger.lifecycle(
                trade_id=trade_id,
                stage='ENTRY',
                details=f"{'Re-entry ' if is_reentry else ''}{trade.direction.value} {trade.quantity} @ {trade.entry_price:.2f}"
            )
        else:
            self.logger.info(
                f"[TRADE] Created {'re-entry ' if is_reentry else ''}trade {trade_id}: "
                f"{trade.direction.value} {trade.quantity} @ {trade.entry_price:.2f}"
            )

        return trade
