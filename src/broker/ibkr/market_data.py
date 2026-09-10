"""
IBKR Market Data Handler
Handles real-time and historical market data from IBKR.
Implements BaseMarketData interface.
"""

from datetime import datetime, timedelta
from typing import Optional, Callable, List, Dict
from collections import deque
import pytz

from ib_insync import IB, Contract, Future, BarDataList, RealTimeBarList, Ticker

from ...data_models import Candle, DayLevels
from .connection import IBKRConnection
from ...logger import get_logger, StrategyLogger
from ..base import BaseMarketData
from ...timezone.market_calendar import MarketCalendar, get_default_tz


class MarketDataError(Exception):
    """Custom exception for market data errors."""
    pass


class IBKRMarketData(BaseMarketData):
    """
    Handles market data operations including:
    - Historical bar data
    - Real-time bars
    - Previous day high/low levels
    - Candle aggregation
    """

    def __init__(
        self,
        connection: IBKRConnection,
        contract: Future,
        timezone: str = None,
        market_calendar: MarketCalendar = None,
        market_data_type: int = 3
    ):
        self.connection = connection
        self.contract = contract
        self.ib = connection.ib
        self.market_data_type = market_data_type

        # Use provided MarketCalendar, or build one from timezone string
        if market_calendar is not None:
            self._market_calendar = market_calendar
        else:
            tz_name = timezone or str(get_default_tz())
            self._market_calendar = MarketCalendar({'timezone': tz_name})
        self.timezone = self._market_calendar.tz

        self._realtime_bars: Optional[RealTimeBarList] = None
        self._ticker: Optional[Ticker] = None
        self._candle_buffer: deque = deque(maxlen=100)
        self._current_candle: Optional[Candle] = None

        self._bar_callbacks: List[Callable] = []
        self._candle_callbacks: List[Callable] = []

        self.logger = get_logger("MarketData")

    @property
    def current_price(self) -> Optional[float]:
        """Get the current market price."""
        if self._ticker and self._ticker.last:
            return self._ticker.last
        if self._ticker and self._ticker.close:
            return self._ticker.close
        return None

    @property
    def bid(self) -> Optional[float]:
        """Get current bid price."""
        return self._ticker.bid if self._ticker else None

    @property
    def ask(self) -> Optional[float]:
        """Get current ask price."""
        return self._ticker.ask if self._ticker else None

    def get_historical_bars(
        self,
        duration: str = "2 D",
        bar_size: str = "5 mins",
        what_to_show: str = "TRADES",
        use_rth: bool = False
    ) -> List[Candle]:
        """Get historical bar data."""
        self.logger.info(f"Requesting historical bars: {duration}, {bar_size}")

        try:
            bars = self.ib.reqHistoricalData(
                contract=self.contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=2
            )

            candles = []
            for bar in bars:
                candle = Candle(
                    timestamp=bar.date.astimezone(self.timezone) if bar.date.tzinfo else self.timezone.localize(bar.date),
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume
                )
                candles.append(candle)

            self.logger.info(f"Retrieved {len(candles)} historical bars")
            return candles

        except Exception as e:
            self.logger.error(f"Failed to get historical bars: {e}")
            raise MarketDataError(f"Historical data request failed: {e}")

    def get_previous_day_levels(self) -> Optional[DayLevels]:
        """Get previous day's high and low levels."""
        self.logger.debug("Fetching previous day levels")

        try:
            bars = self.ib.reqHistoricalData(
                contract=self.contract,
                endDateTime="",
                durationStr="2 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=2
            )

            if len(bars) < 2:
                self.logger.warning("Insufficient daily data for previous day levels")
                return None

            prev_bar = bars[-2]

            levels = DayLevels(
                date=prev_bar.date,
                high=prev_bar.high,
                low=prev_bar.low
            )

            self.logger.info(
                f"Previous day levels: High={levels.high:.2f}, Low={levels.low:.2f}, "
                f"Range={levels.range:.2f}"
            )

            return levels

        except Exception as e:
            self.logger.error(f"Failed to get previous day levels: {e}")
            return None

    def subscribe_realtime_bars(self, bar_size: int = 5) -> None:
        """Subscribe to real-time bars."""
        self.logger.info(f"Subscribing to real-time {bar_size}s bars")

        try:
            self._realtime_bars = self.ib.reqRealTimeBars(
                contract=self.contract,
                barSize=bar_size,
                whatToShow="TRADES",
                useRTH=False
            )

            self._realtime_bars.updateEvent += self._on_realtime_bar

            self.logger.info("Successfully subscribed to real-time bars")

        except Exception as e:
            self.logger.error(f"Failed to subscribe to real-time bars: {e}")
            raise MarketDataError(f"Real-time bar subscription failed: {e}")

    def subscribe_ticker(self) -> None:
        """Subscribe to ticker data for current prices."""
        self.logger.debug("Subscribing to ticker data")

        try:
            # 1=Live, 2=Frozen, 3=Delayed, 4=Delayed+Frozen
            type_names = {1: "LIVE", 2: "FROZEN", 3: "DELAYED", 4: "DELAYED+FROZEN"}
            self.ib.reqMarketDataType(self.market_data_type)
            self.logger.info(f"Market data type: {type_names.get(self.market_data_type, self.market_data_type)}")

            self._ticker = self.ib.reqMktData(
                contract=self.contract,
                genericTickList="",
                snapshot=False,
                regulatorySnapshot=False
            )

            self.logger.info("Successfully subscribed to ticker data")

        except Exception as e:
            self.logger.error(f"Failed to subscribe to ticker: {e}")
            raise MarketDataError(f"Ticker subscription failed: {e}")

    def unsubscribe_all(self) -> None:
        """Unsubscribe from all market data."""
        self.logger.info("Unsubscribing from market data")

        if self._realtime_bars:
            self.ib.cancelRealTimeBars(self._realtime_bars)
            self._realtime_bars = None

        if self._ticker:
            self.ib.cancelMktData(self.contract)
            self._ticker = None

    def _on_realtime_bar(self, bars: RealTimeBarList, has_new_bar: bool) -> None:
        """Handle incoming real-time bars."""
        if not has_new_bar or not bars:
            return

        bar = bars[-1]
        self.logger.market(
            f"RT Bar: O={bar.open:.2f} H={bar.high:.2f} L={bar.low:.2f} C={bar.close:.2f} V={bar.volume}"
        )

        for callback in self._bar_callbacks:
            try:
                callback(bar)
            except Exception as e:
                self.logger.error(f"Error in bar callback: {e}")

    def get_5min_candle_at_time(self, target_time: datetime) -> Optional[Candle]:
        """Get the 5-minute candle that closes at/after a specific time."""
        self.logger.debug(f"Looking for 5-min candle at {target_time}")

        try:
            bars = self.ib.reqHistoricalData(
                contract=self.contract,
                endDateTime="",
                durationStr="1 D",
                barSizeSetting="5 mins",
                whatToShow="TRADES",
                useRTH=False,
                formatDate=2
            )

            for bar in bars:
                bar_time = bar.date
                if bar_time.tzinfo is None:
                    bar_time = self.timezone.localize(bar_time)
                else:
                    bar_time = bar_time.astimezone(self.timezone)

                if bar_time.hour == target_time.hour and bar_time.minute == target_time.minute:
                    candle = Candle(
                        timestamp=bar_time,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume
                    )

                    direction = "BULLISH" if candle.is_bullish else "BEARISH"
                    self.logger.info(
                        f"Found 6 PM candle: {direction} | "
                        f"O={candle.open:.2f} H={candle.high:.2f} L={candle.low:.2f} C={candle.close:.2f}"
                    )
                    return candle

            self.logger.debug(f"Candle not available yet at {target_time}")
            return None

        except Exception as e:
            self.logger.error(f"Failed to get 5-min candle: {e}")
            return None

    def wait_for_candle_close(
        self,
        target_time: datetime,
        timeout_seconds: int = 330
    ) -> Optional[Candle]:
        """Wait for a 5-minute candle to close at the target time."""
        self.logger.info(f"Waiting for 5-min candle close at {target_time}")

        candle_end_time = target_time + timedelta(minutes=5)
        now = datetime.now(self.timezone)

        if now >= candle_end_time:
            return self.get_5min_candle_at_time(target_time)

        wait_seconds = (candle_end_time - now).total_seconds() + 2
        wait_seconds = min(wait_seconds, timeout_seconds)

        self.logger.debug(f"Waiting {wait_seconds:.0f} seconds for candle close")
        self.connection.sleep(wait_seconds)

        return self.get_5min_candle_at_time(target_time)

    def add_bar_callback(self, callback: Callable) -> None:
        """Add a callback for real-time bar updates."""
        self._bar_callbacks.append(callback)

    def add_candle_callback(self, callback: Callable) -> None:
        """Add a callback for completed candles."""
        self._candle_callbacks.append(callback)

    def get_current_timestamp(self) -> datetime:
        """Get current timestamp in strategy timezone."""
        return datetime.now(self.timezone)

    def is_market_open(self, log_state_change: bool = False) -> bool:
        """Check if the futures market is open."""
        is_open, reason = self._market_calendar.is_market_open()

        if log_state_change:
            strategy_logger = StrategyLogger._instance
            if strategy_logger:
                strategy_logger.market_state(is_open, reason)

        return is_open

    def get_next_6pm_reopen(self, log_countdown: bool = False) -> datetime:
        """Get the next market reopen time."""
        next_reopen = self._market_calendar.get_next_reopen()

        if log_countdown:
            now = self.get_current_timestamp()
            time_until = next_reopen - now
            hours = int(time_until.total_seconds() // 3600)
            minutes = int((time_until.total_seconds() % 3600) // 60)
            seconds = int(time_until.total_seconds() % 60)

            strategy_logger = StrategyLogger._instance
            if strategy_logger:
                strategy_logger.countdown("6PM signal", hours, minutes, seconds)

        return next_reopen

    def get_current_session_6pm(self) -> Optional[datetime]:
        """Get current session's reopen time if we're in an overnight session."""
        return self._market_calendar.get_current_session_start()

    def is_in_overnight_session(self) -> bool:
        """Check if we're currently in an overnight trading session."""
        return self.get_current_session_6pm() is not None


def create_market_data_handler(
    connection: IBKRConnection,
    contract: Future,
    config: dict
) -> IBKRMarketData:
    """Create a IBKRMarketData from configuration."""
    cal = MarketCalendar(config)
    return IBKRMarketData(
        connection=connection,
        contract=contract,
        market_calendar=cal
    )
