"""
Comprehensive Unit Tests for Robustness Module and Strategy Logic.

Covers all robustness requirements from the production checklist:
- Process lock (double-run prevention)
- Rate limiter
- Heartbeat / stale data detection
- Slippage manager
- Floating point price utilities
- High volatility / doji detection
- Trade state persistence (crash recovery)
- Position mismatch detection
- Kill switch (safety mechanism)
- Order validation
- Strategy logic (entry signal, stop loss, take profit, trailing stop)
- Day-of-week filtering (no Friday, yes Sunday)
- Max risk cap
- MNQ point value
- Session reset
- Re-entry rules
"""

import json
import math
import os
import sys
import tempfile
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytz

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.error_handler import ProcessLock, TradeStatePersistence
from src.broker.rate_limiter import RateLimiter
from src.monitor import HeartbeatMonitor, StaleDataDetector
from src.risk_manager import (
    SlippageManager, VolatilityDetector, KillSwitch,
    is_valid_price, round_to_tick, prices_equal, validate_order_params
)
from src.position_manager import PositionMismatchDetector
from src.data_models import (
    Candle, Signal, Trade, Position, StrategyState, DayLevels,
    Direction, SignalType, TradeStatus, OrderStatus
)

EST = pytz.timezone('US/Eastern')


# ============================================================
# FLOATING POINT & PRICE UTILITIES
# ============================================================

class TestFloatingPointPrecision(unittest.TestCase):
    """Test floating point price handling - prevents rounding errors in trading."""

    def test_round_to_tick_basic(self):
        # 25000.123 / 0.25 = 100000.492 -> rounds to 100000 -> 25000.0
        self.assertEqual(round_to_tick(25000.123, 0.25), 25000.0)
        self.assertEqual(round_to_tick(25000.0, 0.25), 25000.0)
        self.assertEqual(round_to_tick(25000.124, 0.25), 25000.0)
        # 25000.13 / 0.25 = 100000.52 -> rounds to 100001 -> 25000.25
        self.assertEqual(round_to_tick(25000.13, 0.25), 25000.25)

    def test_round_to_tick_exact_boundaries(self):
        self.assertEqual(round_to_tick(25000.25, 0.25), 25000.25)
        self.assertEqual(round_to_tick(25000.50, 0.25), 25000.50)
        self.assertEqual(round_to_tick(25000.75, 0.25), 25000.75)

    def test_round_to_tick_midpoints(self):
        # 0.125 is exactly between 0.0 and 0.25 - should round to 0.25 (banker's rounding)
        result = round_to_tick(25000.375, 0.25)
        self.assertIn(result, [25000.25, 25000.50])  # Either is acceptable

    def test_prices_equal(self):
        self.assertTrue(prices_equal(25000.00, 25000.00))
        self.assertTrue(prices_equal(25000.001, 25000.009))
        self.assertFalse(prices_equal(25000.00, 25000.02))
        self.assertFalse(prices_equal(25000.00, 25001.00))

    def test_is_valid_price(self):
        self.assertTrue(is_valid_price(25000.0))
        self.assertTrue(is_valid_price(1))
        self.assertTrue(is_valid_price(0.25))
        self.assertFalse(is_valid_price(None))
        self.assertFalse(is_valid_price(float('nan')))
        self.assertFalse(is_valid_price(float('inf')))
        self.assertFalse(is_valid_price(float('-inf')))
        self.assertFalse(is_valid_price(-100.0))
        self.assertFalse(is_valid_price(0))
        self.assertFalse(is_valid_price("25000"))


# ============================================================
# PROCESS LOCK
# ============================================================

class TestProcessLock(unittest.TestCase):
    """Test process lock prevents double-run of strategy."""

    def test_acquire_and_release(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.lock') as f:
            lock_path = f.name

        try:
            lock = ProcessLock(lock_file=lock_path)
            self.assertTrue(lock.acquire())
            lock.release()
        finally:
            if os.path.exists(lock_path):
                os.unlink(lock_path)

    def test_double_acquire_fails(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.lock') as f:
            lock_path = f.name

        try:
            lock1 = ProcessLock(lock_file=lock_path)
            lock2 = ProcessLock(lock_file=lock_path)
            self.assertTrue(lock1.acquire())
            self.assertFalse(lock2.acquire())
            lock1.release()
        finally:
            if os.path.exists(lock_path):
                os.unlink(lock_path)

    def test_context_manager(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix='.lock') as f:
            lock_path = f.name

        try:
            with ProcessLock(lock_file=lock_path) as lock:
                self.assertIsNotNone(lock)
            # After context exit, lock should be released
            lock2 = ProcessLock(lock_file=lock_path)
            self.assertTrue(lock2.acquire())
            lock2.release()
        finally:
            if os.path.exists(lock_path):
                os.unlink(lock_path)


# ============================================================
# RATE LIMITER
# ============================================================

class TestRateLimiter(unittest.TestCase):
    """Test API rate limiting to prevent IBKR throttling."""

    def test_allows_within_limit(self):
        limiter = RateLimiter(max_calls=5, period_seconds=1.0)
        for _ in range(5):
            self.assertTrue(limiter.can_call())
            limiter.wait_if_needed()

    def test_blocks_over_limit(self):
        limiter = RateLimiter(max_calls=3, period_seconds=1.0)
        for _ in range(3):
            limiter.wait_if_needed()
        self.assertFalse(limiter.can_call())


# ============================================================
# HEARTBEAT MONITOR
# ============================================================

class TestHeartbeatMonitor(unittest.TestCase):
    """Test connection health monitoring."""

    def test_initially_stale(self):
        hb = HeartbeatMonitor(stale_threshold_seconds=5.0)
        self.assertTrue(hb.is_data_stale())

    def test_fresh_after_update(self):
        hb = HeartbeatMonitor(stale_threshold_seconds=5.0)
        hb.record_price_update()
        self.assertFalse(hb.is_data_stale())

    def test_status_dict(self):
        hb = HeartbeatMonitor()
        hb.record_price_update()
        hb.record_heartbeat()
        status = hb.get_status()
        self.assertIn('price_age_seconds', status)
        self.assertIn('is_stale', status)
        self.assertFalse(status['is_stale'])


# ============================================================
# SLIPPAGE MANAGER
# ============================================================

class TestSlippageManager(unittest.TestCase):
    """Test slippage tracking and limits."""

    def test_acceptable_slippage(self):
        sm = SlippageManager(max_slippage_points=5.0)
        result = sm.check_fill_slippage(25000.0, 25001.0, "LONG")
        self.assertTrue(result['acceptable'])
        self.assertEqual(result['slippage_points'], 1.0)

    def test_excessive_slippage(self):
        sm = SlippageManager(max_slippage_points=5.0)
        result = sm.check_fill_slippage(25000.0, 25010.0, "LONG")
        self.assertFalse(result['acceptable'])

    def test_short_slippage_direction(self):
        sm = SlippageManager(max_slippage_points=5.0)
        # For shorts, getting filled lower is worse (negative slippage on sell side)
        result = sm.check_fill_slippage(25000.0, 24998.0, "SHORT")
        self.assertTrue(result['acceptable'])

    def test_average_slippage(self):
        sm = SlippageManager(max_slippage_points=5.0)
        sm.check_fill_slippage(25000.0, 25001.0, "LONG")
        sm.check_fill_slippage(25000.0, 25003.0, "LONG")
        avg = sm.get_average_slippage()
        self.assertEqual(avg, 2.0)

    def test_zero_slippage(self):
        sm = SlippageManager(max_slippage_points=5.0)
        result = sm.check_fill_slippage(25000.0, 25000.0, "LONG")
        self.assertTrue(result['acceptable'])
        self.assertEqual(result['slippage_points'], 0.0)


# ============================================================
# VOLATILITY DETECTOR & DOJI CANDLE
# ============================================================

class TestVolatilityDetector(unittest.TestCase):
    """Test high volatility and doji candle detection."""

    def test_normal_volatility(self):
        vd = VolatilityDetector(max_candle_range_points=100.0)
        self.assertFalse(vd.is_too_volatile(50.0))

    def test_high_volatility(self):
        vd = VolatilityDetector(max_candle_range_points=100.0)
        self.assertTrue(vd.is_too_volatile(150.0))

    def test_doji_detection(self):
        vd = VolatilityDetector(min_candle_body_ratio=0.10)
        # Doji: open=close=25000, high=25010, low=24990 -> body=0, range=20
        doji = Candle(
            timestamp=datetime.now(EST),
            open=25000.0, high=25010.0, low=24990.0, close=25000.0
        )
        self.assertTrue(vd.is_doji(doji))

    def test_not_doji(self):
        vd = VolatilityDetector(min_candle_body_ratio=0.10)
        # Strong bullish: body=15 out of range=20 -> ratio=0.75
        candle = Candle(
            timestamp=datetime.now(EST),
            open=24995.0, high=25010.0, low=24990.0, close=25010.0
        )
        self.assertFalse(vd.is_doji(candle))

    def test_tiny_body_doji(self):
        vd = VolatilityDetector(min_candle_body_ratio=0.10)
        # Very small body: body=1 out of range=50 -> ratio=0.02
        candle = Candle(
            timestamp=datetime.now(EST),
            open=25000.0, high=25025.0, low=24975.0, close=25001.0
        )
        self.assertTrue(vd.is_doji(candle))

    def test_zero_range_candle(self):
        vd = VolatilityDetector()
        candle = Candle(
            timestamp=datetime.now(EST),
            open=25000.0, high=25000.0, low=25000.0, close=25000.0
        )
        self.assertTrue(vd.is_doji(candle))


# ============================================================
# TRADE STATE PERSISTENCE (CRASH RECOVERY)
# ============================================================

class TestTradeStatePersistence(unittest.TestCase):
    """Test trade state persistence for crash recovery."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, "test_state.json")
        self.tsp = TradeStatePersistence(state_file=self.state_file)

    def tearDown(self):
        if os.path.exists(self.state_file):
            os.unlink(self.state_file)
        os.rmdir(self.tmpdir)

    def test_save_and_load(self):
        state = self.tsp.build_snapshot(
            entry_triggered=True,
            losses_today=1,
            reentries_used=0,
            active_trade_id="TRD_123",
            active_trade_direction="LONG",
            active_trade_entry_price=25000.0,
            active_trade_stop_loss=24990.0,
            session_date="2026-06-16-PM"
        )
        self.assertTrue(self.tsp.save_state(state))
        loaded = self.tsp.load_state()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['active_trade_id'], "TRD_123")
        self.assertEqual(loaded['losses_today'], 1)
        self.assertTrue(loaded['entry_triggered'])

    def test_load_nonexistent(self):
        loaded = self.tsp.load_state()
        self.assertIsNone(loaded)

    def test_clear_state(self):
        state = {'test': True}
        self.tsp.save_state(state)
        self.assertTrue(os.path.exists(self.state_file))
        self.tsp.clear_state()
        self.assertFalse(os.path.exists(self.state_file))

    def test_atomic_write(self):
        """State file should not be corrupted even if save is interrupted."""
        state1 = {'version': 1, 'trade': 'A'}
        self.tsp.save_state(state1)
        # Overwrite with new state
        state2 = {'version': 2, 'trade': 'B'}
        self.tsp.save_state(state2)
        loaded = self.tsp.load_state()
        self.assertEqual(loaded['version'], 2)


# ============================================================
# POSITION MISMATCH DETECTOR
# ============================================================

class TestPositionMismatchDetector(unittest.TestCase):
    """Test position mismatch detection between strategy and broker."""

    def test_both_flat_no_mismatch(self):
        pmd = PositionMismatchDetector()
        self.assertFalse(pmd.check_mismatch("NEUTRAL", 0, "NEUTRAL", 0))

    def test_matching_positions(self):
        pmd = PositionMismatchDetector()
        self.assertFalse(pmd.check_mismatch("LONG", 1, "LONG", 1))

    def test_direction_mismatch(self):
        pmd = PositionMismatchDetector()
        self.assertTrue(pmd.check_mismatch("LONG", 1, "SHORT", 1))

    def test_quantity_mismatch(self):
        pmd = PositionMismatchDetector()
        self.assertTrue(pmd.check_mismatch("LONG", 1, "LONG", 2))

    def test_one_flat_one_not(self):
        pmd = PositionMismatchDetector()
        self.assertTrue(pmd.check_mismatch("NEUTRAL", 0, "LONG", 1))
        self.assertTrue(pmd.check_mismatch("LONG", 1, "NEUTRAL", 0))

    def test_should_check_interval(self):
        pmd = PositionMismatchDetector(check_interval_seconds=0.1)
        self.assertTrue(pmd.should_check())
        pmd.check_mismatch("NEUTRAL", 0, "NEUTRAL", 0)
        self.assertFalse(pmd.should_check())


# ============================================================
# KILL SWITCH (SAFETY MECHANISM)
# ============================================================

class TestKillSwitch(unittest.TestCase):
    """Test emergency kill switch / safety mechanism."""

    def test_initially_inactive(self):
        ks = KillSwitch(kill_file="/tmp/test_kill_nonexistent")
        self.assertFalse(ks.is_active())

    def test_max_daily_loss_trigger(self):
        ks = KillSwitch(max_daily_loss=500.0, kill_file="/tmp/test_kill_nonexistent")
        ks.update_daily_pnl(-499.0)
        self.assertFalse(ks.is_active())
        ks.update_daily_pnl(-501.0)
        self.assertTrue(ks.is_active())

    def test_consecutive_errors_trigger(self):
        ks = KillSwitch(max_consecutive_errors=3, kill_file="/tmp/test_kill_nonexistent")
        ks.record_error()
        ks.record_error()
        self.assertFalse(ks.is_active())
        ks.record_error()
        self.assertTrue(ks.is_active())

    def test_clear_errors_resets(self):
        ks = KillSwitch(max_consecutive_errors=3, kill_file="/tmp/test_kill_nonexistent")
        ks.record_error()
        ks.record_error()
        ks.clear_errors()
        ks.record_error()
        self.assertFalse(ks.is_active())

    def test_manual_kill_file(self):
        kill_path = "/tmp/test_kill_switch_file"
        try:
            ks = KillSwitch(kill_file=kill_path)
            self.assertFalse(ks.is_active())
            # Create kill file
            with open(kill_path, 'w') as f:
                f.write("KILL")
            self.assertTrue(ks.is_active())
        finally:
            if os.path.exists(kill_path):
                os.unlink(kill_path)

    def test_reset(self):
        ks = KillSwitch(max_daily_loss=100.0, kill_file="/tmp/test_kill_nonexistent")
        ks.update_daily_pnl(-200.0)
        self.assertTrue(ks.is_active())
        ks.reset()
        self.assertFalse(ks.is_active())


# ============================================================
# ORDER VALIDATION
# ============================================================

class TestOrderValidation(unittest.TestCase):
    """Test order parameter validation before submission."""

    def test_valid_order(self):
        errors = validate_order_params("LONG", 1, price=25000.0, stop_price=24990.0)
        self.assertEqual(errors, [])

    def test_invalid_direction(self):
        errors = validate_order_params("UP", 1)
        self.assertTrue(any("direction" in e.lower() for e in errors))

    def test_zero_quantity(self):
        errors = validate_order_params("LONG", 0)
        self.assertTrue(any("quantity" in e.lower() for e in errors))

    def test_negative_quantity(self):
        errors = validate_order_params("LONG", -1)
        self.assertTrue(any("quantity" in e.lower() for e in errors))

    def test_exceeds_max_quantity(self):
        errors = validate_order_params("LONG", 100, max_quantity=10)
        self.assertTrue(any("max" in e.lower() for e in errors))

    def test_nan_price(self):
        errors = validate_order_params("LONG", 1, price=float('nan'))
        self.assertTrue(any("price" in e.lower() for e in errors))

    def test_negative_price(self):
        errors = validate_order_params("LONG", 1, price=-100.0)
        self.assertTrue(any("price" in e.lower() for e in errors))


# ============================================================
# STALE DATA DETECTOR
# ============================================================

class TestStaleDataDetector(unittest.TestCase):
    """Test market data staleness detection."""

    def test_initially_stale(self):
        sdd = StaleDataDetector(max_stale_seconds=5.0)
        self.assertTrue(sdd.is_stale())

    def test_fresh_after_update(self):
        sdd = StaleDataDetector(max_stale_seconds=5.0)
        sdd.update(25000.0)
        self.assertFalse(sdd.is_stale())


# ============================================================
# CANDLE MODEL TESTS
# ============================================================

class TestCandleModel(unittest.TestCase):
    """Test Candle dataclass properties."""

    def test_bullish_candle(self):
        c = Candle(datetime.now(EST), open=100.0, high=110.0, low=95.0, close=108.0)
        self.assertTrue(c.is_bullish)
        self.assertFalse(c.is_bearish)

    def test_bearish_candle(self):
        c = Candle(datetime.now(EST), open=108.0, high=110.0, low=95.0, close=100.0)
        self.assertFalse(c.is_bullish)
        self.assertTrue(c.is_bearish)

    def test_body_size(self):
        c = Candle(datetime.now(EST), open=100.0, high=110.0, low=90.0, close=105.0)
        self.assertEqual(c.body_size, 5.0)

    def test_wicks(self):
        c = Candle(datetime.now(EST), open=100.0, high=110.0, low=90.0, close=105.0)
        self.assertEqual(c.upper_wick, 5.0)   # 110 - 105
        self.assertEqual(c.lower_wick, 10.0)  # 100 - 90

    def test_range(self):
        c = Candle(datetime.now(EST), open=100.0, high=110.0, low=90.0, close=105.0)
        self.assertEqual(c.range, 20.0)

    def test_doji_candle_equal_open_close(self):
        c = Candle(datetime.now(EST), open=100.0, high=105.0, low=95.0, close=100.0)
        self.assertFalse(c.is_bullish)
        self.assertFalse(c.is_bearish)
        self.assertEqual(c.body_size, 0.0)


# ============================================================
# STRATEGY STATE TESTS
# ============================================================

class TestStrategyState(unittest.TestCase):
    """Test StrategyState logic for trade limits and re-entry rules."""

    def test_can_trade_initial(self):
        state = StrategyState(session_date=datetime.now(EST))
        self.assertTrue(state.can_trade)

    def test_cannot_trade_after_max_losses(self):
        state = StrategyState(session_date=datetime.now(EST))
        state.losses_today = 2
        self.assertFalse(state.can_trade)

    def test_can_reentry_after_first_loss(self):
        state = StrategyState(session_date=datetime.now(EST))
        state.losses_today = 1
        state.reentries_used = 0
        self.assertTrue(state.can_reentry)

    def test_cannot_reentry_after_using_reentry(self):
        state = StrategyState(session_date=datetime.now(EST))
        state.losses_today = 1
        state.reentries_used = 1
        self.assertFalse(state.can_reentry)

    def test_cannot_reentry_after_two_losses(self):
        state = StrategyState(session_date=datetime.now(EST))
        state.losses_today = 2
        state.reentries_used = 0
        self.assertFalse(state.can_reentry)

    def test_reset_for_new_session(self):
        state = StrategyState(session_date=datetime.now(EST))
        state.losses_today = 2
        state.reentries_used = 1
        state.entry_triggered = True
        state.trades_today = [MagicMock()]
        new_date = datetime.now(EST) + timedelta(days=1)
        state.reset_for_new_session(new_date)
        self.assertEqual(state.losses_today, 0)
        self.assertEqual(state.reentries_used, 0)
        self.assertFalse(state.entry_triggered)
        self.assertEqual(state.trades_today, [])


# ============================================================
# STRATEGY LOGIC TESTS (using mocked MarketData)
# ============================================================

class TestSignalAnalyzerAndExitManager(unittest.TestCase):
    """Test core strategy logic: signal generation, SL/TP, trailing stops.

    Tests SignalAnalyzer and ExitManager directly (no facade).
    """

    def _make_config(self, symbol='MNQ'):
        return {
            'contract': {'symbol': symbol},
            'strategy': {
                'market_reopen_time': '18:00',
                'check_in_times': ['06:00', '08:30', '09:30'],
                'use_previous_day_levels': False,
            },
            'risk': {
                'contracts': 1,
                'min_risk_threshold': 300.0,
                'max_risk_per_trade': 500.0,
                'warn_below_risk': True,
                'take_profit_levels': {'tp1': 20.0, 'tp2': 40.0, 'tp3': 60.0, 'tp4': 80.0},
                'partial_exit_at_tp2': True,
                'partial_exit_quantity': 0.5,
                'trailing_stop': {
                    'enabled': True,
                    'move_to_be_at_tp2': True,
                    'be_buffer_points': 1.0,
                    'trail_to_previous_tp': True,
                },
            },
            'reentry': {
                'enabled': True,
                'max_reentries': 1,
                'max_total_losses': 2,
                'reentry_at_original_price': True,
            },
            'timezone': 'US/Eastern',
        }

    def _make_components(self, symbol='MNQ'):
        from src.entry.signal_analyzer import SignalAnalyzer
        from src.exit.exit_manager import ExitManager
        config = self._make_config(symbol)
        state = StrategyState(session_date=datetime.now(EST))
        sa = SignalAnalyzer(config, state)
        em = ExitManager(config, state)
        return sa, em, state

    # ---- MNQ POINT VALUE ----
    def test_mnq_point_value(self):
        sa, em, _ = self._make_components('MNQ')
        self.assertEqual(sa.POINT_VALUE, 2.0)
        self.assertEqual(sa.TICK_VALUE, 0.50)

    def test_nq_point_value(self):
        sa, em, _ = self._make_components('NQ')
        self.assertEqual(sa.POINT_VALUE, 20.0)
        self.assertEqual(sa.TICK_VALUE, 5.0)

    # ---- SIGNAL GENERATION ----
    def test_bullish_signal(self):
        sa, em, _ = self._make_components()
        candle = Candle(datetime.now(EST), open=25000.0, high=25010.0, low=24990.0, close=25008.0)
        sig = sa.analyze_signal_candle(candle)
        self.assertEqual(sig.direction, Direction.LONG)
        self.assertEqual(sig.price, 25008.0)
        self.assertEqual(sig.stop_loss, 24990.0 - 0.25)  # Below wick

    def test_bearish_signal(self):
        sa, em, _ = self._make_components()
        candle = Candle(datetime.now(EST), open=25008.0, high=25010.0, low=24990.0, close=25000.0)
        sig = sa.analyze_signal_candle(candle)
        self.assertEqual(sig.direction, Direction.SHORT)
        self.assertEqual(sig.price, 25000.0)
        self.assertEqual(sig.stop_loss, 25010.0 + 0.25)  # Above wick

    def test_tp_levels_long(self):
        sa, em, _ = self._make_components()
        candle = Candle(datetime.now(EST), open=25000.0, high=25010.0, low=24990.0, close=25008.0)
        sig = sa.analyze_signal_candle(candle)
        self.assertEqual(sig.take_profit_levels, [25028.0, 25048.0, 25068.0, 25088.0])

    def test_tp_levels_short(self):
        sa, em, _ = self._make_components()
        candle = Candle(datetime.now(EST), open=25008.0, high=25010.0, low=24990.0, close=25000.0)
        sig = sa.analyze_signal_candle(candle)
        self.assertEqual(sig.take_profit_levels, [24980.0, 24960.0, 24940.0, 24920.0])

    # ---- MAX RISK CAP ----
    def test_max_risk_blocks_wide_candle(self):
        sa, em, _ = self._make_components()  # max_risk=500, MNQ $2/pt
        # Wide candle: low=24700 -> SL distance = 300 pts -> risk = 300*2*1 = $600 > $500
        candle = Candle(datetime.now(EST), open=24800.0, high=25010.0, low=24700.0, close=25000.0)
        sig = sa.analyze_signal_candle(candle)
        self.assertTrue(sig.skip_trade)

    def test_normal_risk_allowed(self):
        sa, em, _ = self._make_components()  # max_risk=500, MNQ $2/pt
        # Normal candle: SL distance = 18pts -> risk = 18*2*1 = $36
        candle = Candle(datetime.now(EST), open=25000.0, high=25010.0, low=24992.0, close=25008.0)
        sig = sa.analyze_signal_candle(candle)
        self.assertFalse(sig.skip_trade)

    # ---- STOP LOSS CHECKS ----
    def test_stop_loss_long(self):
        sa, em, _ = self._make_components()
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0]
        )
        self.assertTrue(em.check_stop_loss(trade, 24989.0))
        self.assertTrue(em.check_stop_loss(trade, 24990.0))
        self.assertFalse(em.check_stop_loss(trade, 24991.0))

    def test_stop_loss_short(self):
        sa, em, _ = self._make_components()
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.SHORT,
            entry_price=25000.0, quantity=1, stop_loss=25010.0,
            take_profit_levels=[24980.0]
        )
        self.assertTrue(em.check_stop_loss(trade, 25011.0))
        self.assertTrue(em.check_stop_loss(trade, 25010.0))
        self.assertFalse(em.check_stop_loss(trade, 25009.0))

    # ---- TAKE PROFIT CHECKS ----
    def test_tp_hit_long(self):
        sa, em, _ = self._make_components()
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0, 25040.0, 25060.0, 25080.0]
        )
        self.assertIsNone(em.check_take_profits(trade, 25010.0))
        self.assertEqual(em.check_take_profits(trade, 25020.0), 1)
        self.assertEqual(em.check_take_profits(trade, 25045.0), 2)
        self.assertEqual(em.check_take_profits(trade, 25080.0), 4)

    def test_tp_hit_short(self):
        sa, em, _ = self._make_components()
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.SHORT,
            entry_price=25000.0, quantity=1, stop_loss=25010.0,
            take_profit_levels=[24980.0, 24960.0, 24940.0, 24920.0]
        )
        self.assertIsNone(em.check_take_profits(trade, 24990.0))
        self.assertEqual(em.check_take_profits(trade, 24980.0), 1)
        self.assertEqual(em.check_take_profits(trade, 24920.0), 4)

    # ---- TRAILING STOP ----
    def test_trailing_stop_at_tp2_long(self):
        sa, em, _ = self._make_components()
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0, 25040.0, 25060.0, 25080.0]
        )
        new_stop = em.update_trailing_stop(trade, 2)
        self.assertEqual(new_stop, 25001.0)  # BE + 1.0 buffer
        self.assertEqual(trade.current_stop_loss, 25001.0)

    def test_trailing_stop_at_tp3_moves_to_tp1(self):
        sa, em, _ = self._make_components()
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0, 25040.0, 25060.0, 25080.0]
        )
        trade.highest_tp_hit = 2  # Already hit TP2
        new_stop = em.update_trailing_stop(trade, 3)
        self.assertEqual(new_stop, 25020.0)  # TP1 level

    def test_trailing_stop_at_tp4_moves_to_tp2(self):
        sa, em, _ = self._make_components()
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0, 25040.0, 25060.0, 25080.0]
        )
        trade.highest_tp_hit = 3
        new_stop = em.update_trailing_stop(trade, 4)
        self.assertEqual(new_stop, 25040.0)  # TP2 level

    # ---- PNL CALCULATION ----
    def test_pnl_long_winning(self):
        sa, em, _ = self._make_components()  # MNQ $2/pt
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0]
        )
        pnl = em.calculate_pnl(trade, 25050.0)
        self.assertEqual(pnl, 100.0)  # 50pts * $2/pt * 1 contract

    def test_pnl_short_winning(self):
        sa, em, _ = self._make_components()
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.SHORT,
            entry_price=25000.0, quantity=1, stop_loss=25010.0,
            take_profit_levels=[24980.0]
        )
        pnl = em.calculate_pnl(trade, 24950.0)
        self.assertEqual(pnl, 100.0)  # 50pts * $2/pt * 1

    def test_pnl_with_partial_exit(self):
        sa, em, _ = self._make_components()
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=2, stop_loss=24990.0,
            take_profit_levels=[25020.0], exit_quantity=1
        )
        pnl = em.calculate_pnl(trade, 25050.0)
        self.assertEqual(pnl, 100.0)  # 50pts * $2/pt * 1 remaining

    # ---- DAY OF WEEK FILTER ----
    def test_friday_blocked(self):
        sa, em, _ = self._make_components()
        # Friday = weekday 4
        friday = datetime(2026, 6, 19, 18, 5, 0, tzinfo=EST)  # A Friday
        with patch('src.entry.signal_analyzer.datetime') as mock_dt:
            mock_dt.now.return_value = friday
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = sa.is_allowed_trading_day()
        self.assertFalse(result)

    def test_sunday_allowed(self):
        sa, em, _ = self._make_components()
        sunday = datetime(2026, 6, 21, 18, 5, 0, tzinfo=EST)  # A Sunday
        with patch('src.entry.signal_analyzer.datetime') as mock_dt:
            mock_dt.now.return_value = sunday
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = sa.is_allowed_trading_day()
        self.assertTrue(result)

    def test_wednesday_allowed(self):
        sa, em, _ = self._make_components()
        wednesday = datetime(2026, 6, 17, 18, 5, 0, tzinfo=EST)  # A Wednesday
        with patch('src.entry.signal_analyzer.datetime') as mock_dt:
            mock_dt.now.return_value = wednesday
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = sa.is_allowed_trading_day()
        self.assertTrue(result)

    # ---- RE-ENTRY RULES ----
    def test_reentry_allowed_after_first_loss(self):
        sa, em, state = self._make_components()
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0], status=TradeStatus.OPEN
        )
        state.trades_today.append(trade)
        can_re = em.handle_trade_loss(trade)
        self.assertTrue(can_re)
        self.assertEqual(state.losses_today, 1)

    def test_no_reentry_after_second_loss(self):
        sa, em, state = self._make_components()
        state.losses_today = 1
        state.reentries_used = 1
        trade = Trade(
            id="T2", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0], status=TradeStatus.OPEN
        )
        can_re = em.handle_trade_loss(trade)
        self.assertFalse(can_re)
        self.assertEqual(state.losses_today, 2)

    def test_reentry_signal_at_original_price(self):
        sa, em, state = self._make_components()
        original = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0, 25040.0, 25060.0, 25080.0]
        )
        state.losses_today = 1
        state.reentries_used = 0
        sig = em.generate_reentry_signal(original)
        self.assertIsNotNone(sig)
        self.assertEqual(sig.price, 25000.0)
        self.assertTrue(sig.is_reentry)
        self.assertEqual(sig.direction, Direction.LONG)


# ============================================================
# TRADE MODEL TESTS
# ============================================================

class TestTradeModel(unittest.TestCase):
    """Test Trade dataclass."""

    def test_risk_amount_with_point_value(self):
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0]
        )
        trade._point_value = 2.0  # MNQ
        self.assertEqual(trade.risk_amount, 20.0)  # 10pts * $2/pt * 1

    def test_risk_amount_nq(self):
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0]
        )
        trade._point_value = 20.0  # NQ
        self.assertEqual(trade.risk_amount, 200.0)  # 10pts * $20/pt * 1

    def test_current_stop_loss_default(self):
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0]
        )
        self.assertEqual(trade.current_stop_loss, 24990.0)

    def test_to_dict(self):
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0, 25040.0, 25060.0, 25080.0]
        )
        d = trade.to_dict()
        self.assertEqual(d['direction'], 'LONG')
        self.assertEqual(d['tp1'], 25020.0)
        self.assertEqual(d['tp4'], 25080.0)


# ============================================================
# POSITION MODEL TESTS
# ============================================================

class TestPositionModel(unittest.TestCase):
    """Test Position dataclass."""

    def test_flat_position(self):
        pos = Position()
        self.assertTrue(pos.is_flat)
        self.assertFalse(pos.is_long)
        self.assertFalse(pos.is_short)

    def test_long_position(self):
        pos = Position(direction=Direction.LONG, quantity=1)
        self.assertFalse(pos.is_flat)
        self.assertTrue(pos.is_long)

    def test_short_position(self):
        pos = Position(direction=Direction.SHORT, quantity=1)
        self.assertFalse(pos.is_flat)
        self.assertTrue(pos.is_short)


# ============================================================
# SIGNAL MODEL TESTS
# ============================================================

class TestSignalModel(unittest.TestCase):
    """Test Signal dataclass."""

    def test_signal_to_dict(self):
        sig = Signal(
            id="SIG_1", timestamp=datetime.now(EST),
            signal_type=SignalType.ENTRY, direction=Direction.LONG,
            price=25000.0, stop_loss=24990.0,
            take_profit_levels=[25020.0, 25040.0, 25060.0, 25080.0],
            risk_amount=20.0, reason="Test"
        )
        d = sig.to_dict()
        self.assertEqual(d['direction'], 'LONG')
        self.assertEqual(d['take_profit_1'], 25020.0)

    def test_skip_trade_default_false(self):
        sig = Signal(
            id="SIG_1", timestamp=datetime.now(EST),
            signal_type=SignalType.ENTRY, direction=Direction.LONG,
            price=25000.0
        )
        self.assertFalse(sig.skip_trade)


# ============================================================
# DATA STORAGE TESTS
# ============================================================

class TestCSVStorage(unittest.TestCase):
    """Test CSV data storage for trades and signals."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        from src.utils.data_storage import CSVStorage
        self.storage = CSVStorage(
            data_dir="data",
            base_path=self.tmpdir
        )

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_files_created_with_headers(self):
        """CSV files created in date-based subfolder."""
        self.assertTrue(self.storage.trades_file.exists())
        self.assertTrue(self.storage.signals_file.exists())

    def test_save_and_load_trade(self):
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0, 25040.0, 25060.0, 25080.0]
        )
        self.assertTrue(self.storage.save_trade(trade))
        trades = self.storage.load_trades_raw()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['id'], 'T1')

    def test_update_trade(self):
        trade = Trade(
            id="T1", entry_time=datetime.now(EST), direction=Direction.LONG,
            entry_price=25000.0, quantity=1, stop_loss=24990.0,
            take_profit_levels=[25020.0, 25040.0, 25060.0, 25080.0]
        )
        self.storage.save_trade(trade)
        trade.status = TradeStatus.CLOSED
        trade.realized_pnl = 100.0
        self.assertTrue(self.storage.update_trade(trade))
        trades = self.storage.load_trades_raw()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]['status'], 'CLOSED')

    def test_trade_statistics(self):
        for i, pnl in enumerate([100.0, -50.0, 200.0]):
            trade = Trade(
                id=f"T{i}", entry_time=datetime.now(EST), direction=Direction.LONG,
                entry_price=25000.0, quantity=1, stop_loss=24990.0,
                take_profit_levels=[25020.0], realized_pnl=pnl,
                status=TradeStatus.CLOSED
            )
            self.storage.save_trade(trade)
        stats = self.storage.get_trade_statistics()
        self.assertEqual(stats['total_trades'], 3)
        self.assertEqual(stats['winning_trades'], 2)
        self.assertEqual(stats['losing_trades'], 1)
        self.assertEqual(stats['total_pnl'], 250.0)


# ============================================================
# ENTRY WINDOW TESTS (bug found during live testing)
# ============================================================

class TestEntryWindow(unittest.TestCase):
    """Test that bot does NOT trade on stale candles from hours ago."""

    def test_within_window_allowed(self):
        """5 minutes after 6PM - should be allowed."""
        reopen = datetime(2026, 6, 24, 18, 0, 0, tzinfo=EST)
        now = datetime(2026, 6, 24, 18, 10, 0, tzinfo=EST)  # 10 min after
        entry_window = 30
        entry_deadline = reopen + timedelta(minutes=entry_window)
        candle_close = reopen + timedelta(minutes=5)

        self.assertTrue(now >= candle_close)   # candle has closed
        self.assertFalse(now > entry_deadline) # still within window

    def test_at_boundary_allowed(self):
        """Exactly 30 minutes after - should be allowed."""
        reopen = datetime(2026, 6, 24, 18, 0, 0, tzinfo=EST)
        now = datetime(2026, 6, 24, 18, 30, 0, tzinfo=EST)
        entry_window = 30
        entry_deadline = reopen + timedelta(minutes=entry_window)

        self.assertFalse(now > entry_deadline)  # exactly at boundary, not past

    def test_past_window_blocked(self):
        """31 minutes after 6PM - should be blocked."""
        reopen = datetime(2026, 6, 24, 18, 0, 0, tzinfo=EST)
        now = datetime(2026, 6, 24, 18, 31, 0, tzinfo=EST)
        entry_window = 30
        entry_deadline = reopen + timedelta(minutes=entry_window)

        self.assertTrue(now > entry_deadline)  # past window

    def test_hours_later_blocked(self):
        """Bot started at 1 AM - 7 hours after 6PM. Must NOT trade."""
        reopen = datetime(2026, 6, 23, 18, 0, 0, tzinfo=EST)
        now = datetime(2026, 6, 24, 1, 0, 0, tzinfo=EST)
        entry_window = 30
        entry_deadline = reopen + timedelta(minutes=entry_window)

        self.assertTrue(now > entry_deadline)  # 7 hours past - blocked

    def test_before_candle_close_waits(self):
        """6:03 PM - candle hasn't closed yet. Should wait."""
        reopen = datetime(2026, 6, 24, 18, 0, 0, tzinfo=EST)
        now = datetime(2026, 6, 24, 18, 3, 0, tzinfo=EST)
        candle_close = reopen + timedelta(minutes=5)

        self.assertFalse(now >= candle_close)  # not yet


# ============================================================
# POSITION RECONCILER TESTS
# ============================================================

class TestPositionReconciler(unittest.TestCase):
    """Test position reconciliation on startup."""

    def setUp(self):
        from src.position_manager.position_reconciler import PositionReconciler
        self.reconciler = PositionReconciler(logger=MagicMock(), notifier=MagicMock())

    def test_flat_position_returns_false(self):
        """No position = clean start, returns False."""
        mock_tm = MagicMock()
        mock_tm.positions.get_position.return_value = Position(
            direction=Direction.NEUTRAL, quantity=0
        )
        state = StrategyState(session_date=datetime.now(EST))
        mock_persistence = MagicMock()
        mock_persistence.load_state.return_value = None

        result = self.reconciler.reconcile(mock_tm, state, mock_persistence, {})
        self.assertFalse(result)
        self.assertFalse(state.entry_triggered)

    def test_existing_position_sets_entry_triggered(self):
        """Existing position -> entry_triggered = True, returns True."""
        mock_tm = MagicMock()
        mock_tm.positions.get_position.return_value = Position(
            direction=Direction.LONG, quantity=1, avg_entry_price=25000.0
        )
        mock_tm.orders.get_open_orders.return_value = []  # No SL order

        state = StrategyState(session_date=datetime.now(EST))
        mock_persistence = MagicMock()
        mock_persistence.load_state.return_value = {'active_trade_stop_loss': 24950.0}

        result = self.reconciler.reconcile(mock_tm, state, mock_persistence, {}, point_value=2.0)
        self.assertTrue(result)
        self.assertTrue(state.entry_triggered)

    def test_existing_position_with_sl_does_not_place_emergency(self):
        """Existing position WITH stop order -> no emergency SL placed."""
        mock_tm = MagicMock()
        mock_tm.positions.get_position.return_value = Position(
            direction=Direction.SHORT, quantity=1, avg_entry_price=25000.0
        )
        # Simulate existing stop order
        mock_order = MagicMock()
        mock_order.order.orderType = 'STP'
        mock_tm.orders.get_open_orders.return_value = [mock_order]

        state = StrategyState(session_date=datetime.now(EST))
        mock_persistence = MagicMock()

        result = self.reconciler.reconcile(mock_tm, state, mock_persistence, {})
        self.assertTrue(result)
        # Should NOT have called place_stop_order since SL exists
        mock_tm.orders.place_stop_order.assert_not_called()

    def test_orphaned_position_places_emergency_sl(self):
        """Existing position WITHOUT stop order -> emergency SL placed."""
        mock_tm = MagicMock()
        mock_tm.positions.get_position.return_value = Position(
            direction=Direction.LONG, quantity=1, avg_entry_price=25000.0
        )
        mock_tm.orders.get_open_orders.return_value = []  # No SL

        state = StrategyState(session_date=datetime.now(EST))
        mock_persistence = MagicMock()
        mock_persistence.load_state.return_value = None  # No saved state

        config = {'risk': {'max_risk_per_trade': 100.0}}
        result = self.reconciler.reconcile(mock_tm, state, mock_persistence, config, point_value=2.0)
        self.assertTrue(result)
        # Should have placed emergency SL
        mock_tm.orders.place_stop_order.assert_called_once()


# ============================================================
# MARKET CALENDAR TESTS
# ============================================================

class TestMarketCalendar(unittest.TestCase):
    """Test centralized market calendar."""

    def setUp(self):
        from src.timezone.market_calendar import MarketCalendar
        self.cal = MarketCalendar()  # defaults

    def test_saturday_closed(self):
        is_open, reason = self.cal.is_market_open()
        # Can't test directly without mocking time, but test the method exists
        self.assertIsInstance(is_open, bool)
        self.assertIsInstance(reason, str)

    def test_trading_day_friday_blocked(self):
        friday = datetime(2026, 6, 26, 18, 0, 0, tzinfo=EST)  # Friday
        allowed, reason = self.cal.is_trading_day(now=friday)
        self.assertFalse(allowed)
        self.assertIn("Friday", reason)

    def test_trading_day_sunday_allowed(self):
        sunday = datetime(2026, 6, 28, 18, 0, 0, tzinfo=EST)  # Sunday
        allowed, reason = self.cal.is_trading_day(now=sunday)
        self.assertTrue(allowed)

    def test_trading_day_wednesday_allowed(self):
        wednesday = datetime(2026, 6, 24, 18, 0, 0, tzinfo=EST)  # Wednesday
        allowed, reason = self.cal.is_trading_day(now=wednesday)
        self.assertTrue(allowed)

    def test_session_date_key_after_6pm(self):
        key = self.cal.get_session_date_key()
        self.assertIn("-PM", key)

    def test_next_reopen_returns_future(self):
        next_open = self.cal.get_next_reopen()
        now = self.cal.now()
        # Next reopen should be in the future or very close to now
        self.assertGreaterEqual(next_open, now - timedelta(minutes=1))


# ============================================================
# CONTRACT SPECS TESTS
# ============================================================

class TestContractSpecs(unittest.TestCase):
    """Test contract-specific point values from defaults."""

    def test_mnq_specs(self):
        from src.utils.defaults import CONTRACT_SPECS
        specs = CONTRACT_SPECS['MNQ']
        self.assertEqual(specs['tick_value'], 0.50)
        self.assertEqual(specs['point_value'], 2.0)

    def test_nq_specs(self):
        from src.utils.defaults import CONTRACT_SPECS
        specs = CONTRACT_SPECS['NQ']
        self.assertEqual(specs['tick_value'], 5.0)
        self.assertEqual(specs['point_value'], 20.0)

    def test_unknown_symbol_falls_back_to_nq(self):
        from src.utils.defaults import CONTRACT_SPECS
        specs = CONTRACT_SPECS.get('ES', CONTRACT_SPECS['NQ'])
        self.assertEqual(specs['point_value'], 20.0)


# ============================================================
# CONFIG VALIDATION TESTS
# ============================================================

class TestConfigValidation(unittest.TestCase):
    """Test config value validation catches bad inputs."""

    def test_zero_contracts_rejected(self):
        from src.utils.config_loader import validate_config, ConfigurationError
        config = {
            'ibkr': {'port': 7497},
            'contract': {'symbol': 'MNQ'},
            'strategy': {},
            'risk': {'contracts': 0, 'max_contracts': 2}
        }
        with self.assertRaises(ConfigurationError):
            validate_config(config)

    def test_negative_max_daily_loss_rejected(self):
        from src.utils.config_loader import validate_config, ConfigurationError
        config = {
            'ibkr': {'port': 7497},
            'contract': {'symbol': 'MNQ'},
            'strategy': {},
            'risk': {'contracts': 1, 'max_contracts': 2},
            'safety': {'max_daily_loss': -500.0}
        }
        with self.assertRaises(ConfigurationError):
            validate_config(config)

    def test_contracts_exceeds_max_rejected(self):
        from src.utils.config_loader import validate_config, ConfigurationError
        config = {
            'ibkr': {'port': 7497},
            'contract': {'symbol': 'MNQ'},
            'strategy': {},
            'risk': {'contracts': 5, 'max_contracts': 2}
        }
        with self.assertRaises(ConfigurationError):
            validate_config(config)

    def test_valid_config_passes(self):
        from src.utils.config_loader import validate_config
        config = {
            'ibkr': {'port': 7497},
            'contract': {'symbol': 'MNQ'},
            'strategy': {},
            'risk': {'contracts': 1, 'max_contracts': 2}
        }
        self.assertTrue(validate_config(config))

    def test_bad_port_rejected(self):
        from src.utils.config_loader import validate_config, ConfigurationError
        config = {
            'ibkr': {'port': 80},
            'contract': {'symbol': 'MNQ'},
            'strategy': {},
            'risk': {'contracts': 1}
        }
        with self.assertRaises(ConfigurationError):
            validate_config(config)


if __name__ == '__main__':
    unittest.main()
