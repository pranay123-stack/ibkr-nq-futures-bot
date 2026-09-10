"""
Exit Manager Module
Handles exit logic: stop loss, take profit, trailing stops, partial exits, reentry.
Extracted from core/strategy.py.
"""

import uuid
from datetime import datetime
from typing import Optional, List

from ..timezone.market_calendar import get_default_tz, MarketCalendar
from ..utils.defaults import CONTRACT_SPECS, DEFAULT_TICK_SIZE, DEFAULT_SYMBOL
from ..data_models import (
    Signal, Trade, StrategyState,
    Direction, SignalType, TradeStatus
)
from ..logger import get_logger, StrategyLogger


class ExitManager:
    """
    Manages trade exits: stop loss, take profit, trailing stops, reentry.

    Extracted exit-related methods from NQ6PMStrategy.
    """

    def __init__(self, config: dict, state: StrategyState):
        self.config = config
        self.state = state
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

        self.logger = get_logger("ExitManager")

    def _parse_config(self) -> None:
        """Parse strategy configuration relevant to exits."""
        # Partial exit settings
        self.partial_exit_enabled = self.risk_config.get('partial_exit_at_tp2', True)
        self.partial_exit_qty = self.risk_config.get('partial_exit_quantity', 0.5)

        # Trailing stop settings
        trailing_config = self.risk_config.get('trailing_stop', {})
        self.trailing_enabled = trailing_config.get('enabled', True)
        self.move_to_be_at_tp2 = trailing_config.get('move_to_be_at_tp2', True)
        self.be_buffer = trailing_config.get('be_buffer_points', 1.0)
        self.trail_to_prev_tp = trailing_config.get('trail_to_previous_tp', True)

        # Re-entry settings
        self.reentry_enabled = self.reentry_config.get('enabled', True)
        self.max_reentries = self.reentry_config.get('max_reentries', 1)
        self.max_total_losses = self.reentry_config.get('max_total_losses', 2)

        # Contracts for PnL calculation
        self.contracts = self.risk_config.get('contracts', 1)

    def check_stop_loss(self, trade: Trade, current_price: float) -> bool:
        """
        Check if stop loss has been hit.

        Args:
            trade: Active trade
            current_price: Current market price

        Returns:
            True if stop loss hit
        """
        stop = trade.current_stop_loss

        if trade.direction == Direction.LONG:
            return current_price <= stop
        else:
            return current_price >= stop

    def check_take_profits(self, trade: Trade, current_price: float) -> Optional[int]:
        """
        Check which take profit levels have been hit.

        Args:
            trade: Active trade
            current_price: Current market price

        Returns:
            Highest TP level hit (1-4), or None if none hit
        """
        highest_tp = None

        for i, tp_price in enumerate(trade.take_profit_levels, 1):
            if trade.direction == Direction.LONG:
                if current_price >= tp_price:
                    highest_tp = i
            else:
                if current_price <= tp_price:
                    highest_tp = i

        return highest_tp

    def update_trailing_stop(self, trade: Trade, tp_level: int) -> Optional[float]:
        """
        Update trailing stop based on take profit level reached.

        Args:
            trade: Active trade
            tp_level: Take profit level that was hit

        Returns:
            New stop loss price, or None if no update needed
        """
        if not self.trailing_enabled:
            return None

        new_stop = None

        strategy_logger = StrategyLogger._instance

        # At TP2: Move to breakeven + buffer
        if tp_level >= 2 and self.move_to_be_at_tp2 and trade.highest_tp_hit < 2:
            if trade.direction == Direction.LONG:
                new_stop = trade.entry_price + self.be_buffer
            else:
                new_stop = trade.entry_price - self.be_buffer

            self.logger.info(f"Moving stop to BE+{self.be_buffer}: {new_stop:.2f}")
            if strategy_logger:
                strategy_logger.lifecycle(
                    trade_id=trade.id,
                    stage=f'TP{tp_level}_HIT',
                    details=f"Moving SL to breakeven @ {new_stop:.2f}"
                )

        # Trail to previous TP level
        elif tp_level >= 3 and self.trail_to_prev_tp:
            # When TP3 hit, move SL to TP1; when TP4 hit, move SL to TP2
            prev_tp_idx = tp_level - 3  # TP3 -> index 0 (TP1), TP4 -> index 1 (TP2)
            if 0 <= prev_tp_idx < len(trade.take_profit_levels):
                new_stop = trade.take_profit_levels[prev_tp_idx]
                tp_label = prev_tp_idx + 1
                self.logger.info(f"Trailing stop to TP{tp_label}: {new_stop:.2f}")
                if strategy_logger:
                    strategy_logger.lifecycle(
                        trade_id=trade.id,
                        stage=f'TP{tp_level}_HIT',
                        details=f"Trailing SL to TP{tp_label} @ {new_stop:.2f}"
                    )

        trade.highest_tp_hit = max(trade.highest_tp_hit, tp_level)

        if new_stop:
            trade.current_stop_loss = new_stop

        return new_stop

    def calculate_partial_exit_quantity(self, trade: Trade) -> int:
        """Calculate quantity for partial exit at TP2."""
        if not self.partial_exit_enabled:
            return 0

        remaining = trade.quantity - trade.exit_quantity
        if remaining <= 1:
            return 0

        partial_qty = int(trade.quantity * self.partial_exit_qty)
        return min(max(1, partial_qty), remaining - 1)

    def handle_trade_loss(self, trade: Trade) -> bool:
        """
        Handle a losing trade and determine if re-entry is allowed.

        Args:
            trade: The losing trade

        Returns:
            True if re-entry is allowed
        """
        self.state.losses_today += 1
        trade.status = TradeStatus.CLOSED

        # Use enhanced lifecycle logging
        strategy_logger = StrategyLogger._instance
        if strategy_logger:
            strategy_logger.lifecycle(
                trade_id=trade.id,
                stage='SL_HIT',
                details=f"Stopped out. Losses today: {self.state.losses_today}"
            )
        else:
            self.logger.warning(
                f"Trade {trade.id} stopped out. Losses today: {self.state.losses_today}"
            )

        if self.state.losses_today >= self.max_total_losses:
            self.logger.warning("Maximum losses reached. No more trading today.")
            return False

        if self.reentry_enabled and self.state.can_reentry:
            self.logger.info("Re-entry allowed at original entry price")
            return True

        return False

    def generate_reentry_signal(self, original_trade: Trade) -> Optional[Signal]:
        """
        Generate a re-entry signal based on the original trade.

        Args:
            original_trade: The original losing trade

        Returns:
            Re-entry signal or None if not allowed
        """
        if not self.state.can_reentry:
            return None

        signal_id = f"SIG_RE_{datetime.now(self.timezone).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        signal = Signal(
            id=signal_id,
            timestamp=datetime.now(self.timezone),
            signal_type=SignalType.REENTRY,
            direction=original_trade.direction,
            price=original_trade.entry_price,  # Re-entry at original price
            stop_loss=original_trade.stop_loss,
            take_profit_levels=original_trade.take_profit_levels,
            risk_amount=original_trade.risk_amount,
            reason=f"Re-entry after stopped out trade {original_trade.id}",
            is_reentry=True
        )

        self.logger.info(
            f"[SIGNAL] Generated RE-ENTRY signal: {signal.direction.value} @ {signal.price:.2f}"
        )

        return signal

    def calculate_pnl(self, trade: Trade, current_price: float) -> float:
        """
        Calculate unrealized PnL for a trade.

        Args:
            trade: Active trade
            current_price: Current market price

        Returns:
            PnL in dollars
        """
        if trade.direction == Direction.LONG:
            points = current_price - trade.entry_price
        else:
            points = trade.entry_price - current_price

        remaining_qty = trade.quantity - trade.exit_quantity
        pnl = points * self.POINT_VALUE * remaining_qty

        return round(pnl, 2)
